use std::collections::{BTreeMap, BTreeSet};

use sqlx::{MySql, MySqlConnection, MySqlPool, pool::PoolConnection};

use crate::{
    codes::{self, Credential},
    email::{self, Email, Mailer},
    registration_form::valid_email,
    registration_store,
    settings::Settings,
};

const UNSENT: &str = "Email delivery is unavailable.";

pub async fn send(pool: &MySqlPool, registration: i32, origin: Option<String>) -> bool {
    let (Some(mailer), Some(origin)) = (email::service(), origin) else {
        return false;
    };
    let Ok(Some(mut db)) = lock(pool).await else {
        return false;
    };
    send_registration(&mut db, mailer, registration, &origin)
        .await
        .unwrap_or(false)
}

pub async fn lock(pool: &MySqlPool) -> Result<Option<PoolConnection<MySql>>, sqlx::Error> {
    let mut db = pool.acquire().await?;
    // MySQL advisory locks live on a connection. Closing it also releases
    // the lock when a request is cancelled or a database operation fails.
    db.close_on_drop();
    let acquired = sqlx::query_scalar::<_, Option<i64>>(
        "SELECT GET_LOCK(SHA2(CONCAT('mit-tab email:', DATABASE()), 256), 5)",
    )
    .fetch_one(&mut *db)
    .await?;
    Ok((acquired == Some(1)).then_some(db))
}

pub enum Log {
    Confirmation { id: i32 },
    Team { id: i32, snapshot: String },
    Judge { id: i32, code: String },
    Written { id: i32 },
}

pub struct Delivery {
    pub log: Log,
    pub email: Email,
}

impl Delivery {
    pub async fn record(
        &self,
        db: &mut MySqlConnection,
        result: Result<(), &'static str>,
        source: &str,
    ) -> Result<(), sqlx::Error> {
        let snapshot = self.email.snapshot();
        let successful = result.is_ok();
        let problem = result.err().unwrap_or_default();
        let address = &self.email.to_address;
        match &self.log {
            Log::Confirmation { id } => {
                sqlx::query(
                    "INSERT INTO registration_registrationconfirmationemaillog
                        (registration_id, email, successful, error_message, email_payload, sent_at)
                     VALUES (?, ?, ?, ?, ?, UTC_TIMESTAMP(6))",
                )
                .bind(id)
                .bind(address)
                .bind(successful)
                .bind(problem)
                .bind(snapshot)
                .execute(db)
                .await?;
            }
            Log::Team {
                id,
                snapshot: team_snapshot,
            } => {
                sqlx::query(
                    "INSERT INTO tab_teamcodeemaillog
                        (team_id, email, source, successful, error_message, email_payload, team_snapshot, sent_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, UTC_TIMESTAMP(6))",
                ).bind(id).bind(address).bind(source).bind(successful).bind(problem).bind(snapshot)
                    .bind(team_snapshot).execute(db).await?;
            }
            Log::Judge { id, code } if successful => {
                sqlx::query(
                    "INSERT INTO tab_judgecodeemaillog
                        (judge_id, email, source, ballot_code, email_payload, sent_at)
                     VALUES (?, ?, ?, ?, ?, UTC_TIMESTAMP(6))",
                )
                .bind(id)
                .bind(address)
                .bind(source)
                .bind(code)
                .bind(snapshot)
                .execute(db)
                .await?;
            }
            Log::Written { id } if successful => {
                sqlx::query(
                    "INSERT INTO tab_writtenrfdemaillog (round_id, email, email_payload, sent_at) VALUES (?, ?, ?, UTC_TIMESTAMP(6))",
                ).bind(id).bind(address).bind(snapshot).execute(db).await?;
            }
            Log::Judge { .. } | Log::Written { .. } => {}
        }
        Ok(())
    }
}

async fn send_registration(
    db: &mut MySqlConnection,
    mailer: &'static Mailer,
    registration: i32,
    origin: &str,
) -> Result<bool, sqlx::Error> {
    let tournament = Settings::load(db, &["tournament_name"])
        .await?
        .string("tournament_name", "Tournament");
    let Some(confirmation) = confirmation(db, registration, origin, &tournament).await? else {
        return Ok(false);
    };
    let mut deliveries = vec![confirmation];
    let teams =
        sqlx::query_scalar("SELECT id FROM tab_team WHERE registration_id = ? ORDER BY name, id")
            .bind(registration)
            .fetch_all(&mut *db)
            .await?;
    deliveries.extend(team_deliveries(db, &teams, origin, &tournament).await?);
    let judges =
        sqlx::query_scalar("SELECT id FROM tab_judge WHERE registration_id = ? ORDER BY name, id")
            .bind(registration)
            .fetch_all(&mut *db)
            .await?;
    deliveries.extend(judge_deliveries(db, &judges, origin, &tournament, true).await?);
    send_all(db, Some(mailer), deliveries, "registration").await
}

pub async fn send_all(
    db: &mut MySqlConnection,
    mailer: Option<&'static Mailer>,
    deliveries: Vec<Delivery>,
    source: &str,
) -> Result<bool, sqlx::Error> {
    let mut all_sent = true;
    let mut batch = tokio::task::JoinSet::new();
    let mut deliveries = deliveries.into_iter();
    for _ in 0..4 {
        if let Some(delivery) = deliveries.next() {
            batch.spawn(deliver(mailer, delivery));
        }
    }
    while let Some(result) = batch.join_next().await {
        if let Ok((delivery, result)) = result {
            all_sent &= result.is_ok();
            delivery.record(db, result, source).await?;
        } else {
            all_sent = false;
        }
        if let Some(delivery) = deliveries.next() {
            batch.spawn(deliver(mailer, delivery));
        }
    }
    Ok(all_sent)
}

async fn deliver(
    mailer: Option<&'static Mailer>,
    delivery: Delivery,
) -> (Delivery, Result<(), &'static str>) {
    let result = if let Some(mailer) = mailer {
        mailer.send(&delivery.email).await
    } else {
        Err(UNSENT)
    };
    (delivery, result)
}

pub async fn confirmation(
    db: &mut MySqlConnection,
    id: i32,
    origin: &str,
    tournament: &str,
) -> Result<Option<Delivery>, sqlx::Error> {
    let Some(entry) = registration_store::entry(db, id).await? else {
        return Ok(None);
    };
    let code: String =
        sqlx::query_scalar("SELECT herokunator_code FROM registration_registration WHERE id = ?")
            .bind(id)
            .fetch_one(&mut *db)
            .await?;
    let link = format!("{origin}/registration/{code}/");
    let form = entry.form;
    let links: Vec<(String, String, String)> = sqlx::query_as(
        "SELECT title, url, description FROM registration_registrationlink WHERE is_active = TRUE ORDER BY display_order, created_at, id",
    ).fetch_all(db).await?;
    let mut body = format!(
        "Hi {},\n\nYour registration for {tournament} has been received.\n\nRegistration code: {code}\nEdit your registration: {link}\n\nContact: {}\nDietary requests: {}\nHousing requests: {}\n",
        form.school.name,
        form.email,
        present(&form.dietary_requests),
        present(&form.housing_requests),
    );
    for (title, url, description) in links {
        if url::Url::parse(&url).is_ok_and(|url| matches!(url.scheme(), "http" | "https")) {
            body.push_str(&format!("\n{title}: {url} {description}"));
        }
    }
    body.push_str("\n\nTeams\n");
    if form.teams.is_empty() {
        body.push_str("None\n");
    }
    for team in form.teams {
        let seed = match team.seed {
            1 => "Free seed",
            2 => "Half seed",
            3 => "Full seed",
            _ => "Unseeded",
        };
        body.push_str(&format!("- {} ({seed})\n", team.name));
        for member in &team.members {
            let class = if member.novice_status == 1 {
                "Novice"
            } else {
                "Varsity"
            };
            let id = member
                .apda_id
                .filter(|&id| id > 0)
                .map_or_else(|| "None".into(), |id| id.to_string());
            let qualified = if member.qualified { "yes" } else { "no" };
            body.push_str(&format!(
                "  - {} ({}, {class}, APDA ID {id}, Qualified {qualified})\n",
                member.name, member.school.name
            ));
        }
    }
    body.push_str("\nJudges\n");
    if form.judges.is_empty() {
        body.push_str("None\n");
    }
    for judge in form.judges {
        let schools = judge
            .schools
            .into_iter()
            .map(|school| school.name)
            .collect::<Vec<_>>()
            .join(", ");
        body.push_str(&format!(
            "- {} ({} years, {}, {})\n",
            judge.name,
            judge.experience,
            schools,
            availability(&judge.rounds)
        ));
    }
    body.push_str("\nThanks,\nThe Tab Team");
    Ok(Some(Delivery {
        log: Log::Confirmation { id },
        email: Email::simple(
            form.email,
            format!("Registration confirmed: {tournament}"),
            body,
        )
        .with_link(&link, "Edit Registration"),
    }))
}

fn present(value: &str) -> &str {
    if value.is_empty() { "None" } else { value }
}

fn availability(rounds: &[i32]) -> String {
    if rounds.is_empty() {
        return "None selected".into();
    }
    rounds
        .iter()
        .map(|round| {
            if *round == 0 {
                "Outrounds".into()
            } else {
                format!("Round {round}")
            }
        })
        .collect::<Vec<_>>()
        .join(", ")
}

pub async fn team_deliveries(
    db: &mut MySqlConnection,
    ids: &[i32],
    origin: &str,
    tournament: &str,
) -> Result<Vec<Delivery>, sqlx::Error> {
    let mut output = Vec::new();
    let recent: Vec<(i32, String)> = sqlx::query_as(
        "SELECT team_id, email FROM tab_teamcodeemaillog
         WHERE successful = TRUE AND sent_at >= UTC_TIMESTAMP(6) - INTERVAL 6 HOUR",
    )
    .fetch_all(&mut *db)
    .await?;
    let previous_teams: BTreeSet<_> = recent.iter().map(|&(id, _)| id).collect();
    let mut addresses: BTreeSet<_> = recent
        .into_iter()
        .map(|(_, address)| address.to_lowercase())
        .collect();
    for &id in ids {
        if previous_teams.contains(&id) {
            continue;
        }
        let Some((name, code)) = sqlx::query_as::<_, (String, Option<String>)>(
            "SELECT name, team_code FROM tab_team WHERE id = ?",
        )
        .bind(id)
        .fetch_optional(&mut *db)
        .await?
        else {
            continue;
        };
        let Some(code) = code.filter(|code| valid_code(code)) else {
            continue;
        };
        let members: Vec<(String, Option<String>)> = sqlx::query_as(
            "SELECT d.name, d.email FROM tab_debater d JOIN tab_team_debaters m ON m.debater_id = d.id
             WHERE m.team_id = ? ORDER BY d.name, d.id",
        ).bind(id).fetch_all(&mut *db).await?;
        let names: Vec<_> = members.iter().map(|(name, _)| name.as_str()).collect();
        let recipients: BTreeMap<_, _> = members
            .iter()
            .filter_map(|(_, email)| {
                let address = email.as_deref()?.trim();
                valid_email(address).then(|| (address.to_lowercase(), address))
            })
            .collect();
        let snapshot = serde_json::json!({
            "name": name, "team_code": code, "debaters": names,
            "recipients": recipients.keys().collect::<Vec<_>>(), "tournament_name": tournament,
        })
        .to_string();
        let link = format!("{origin}/team_portal/{code}/");
        let body = format!(
            "Hello,\n\nYour team is registered for {tournament}. Use the team portal to manage your team name and discretionary judge scratches when they are open.\n\nTeam: {name}\nDebaters: {}\nTeam code: {code}\nTeam portal: {link}\n\nThanks,\nThe Tab Team",
            names.join(", "),
        );
        for (key, address) in recipients {
            if !addresses.insert(key) {
                continue;
            }
            output.push(Delivery {
                log: Log::Team {
                    id,
                    snapshot: snapshot.clone(),
                },
                email: Email::simple(
                    address.into(),
                    format!("Your team portal for {tournament}"),
                    body.clone(),
                )
                .with_link(&link, "Open Team Portal"),
            });
        }
    }
    Ok(output)
}

pub async fn judge_deliveries(
    db: &mut MySqlConnection,
    ids: &[i32],
    origin: &str,
    tournament: &str,
    registration: bool,
) -> Result<Vec<Delivery>, sqlx::Error> {
    let recent: Vec<(i32, String)> = sqlx::query_as(
        "SELECT judge_id, email FROM tab_judgecodeemaillog WHERE sent_at >= UTC_TIMESTAMP(6) - INTERVAL 6 HOUR",
    ).fetch_all(&mut *db).await?;
    let previous_judges: BTreeSet<_> = recent.iter().map(|&(id, _)| id).collect();
    let mut addresses: BTreeSet<_> = recent
        .into_iter()
        .map(|(_, address)| address.to_lowercase())
        .collect();
    let mut output = Vec::new();
    for &id in ids {
        if previous_judges.contains(&id) {
            continue;
        }
        let Some((name, email, code)) =
            sqlx::query_as::<_, (String, Option<String>, Option<String>)>(
                "SELECT name, email, ballot_code FROM tab_judge WHERE id = ?",
            )
            .bind(id)
            .fetch_optional(&mut *db)
            .await?
        else {
            continue;
        };
        let Some(address) = email.filter(|email| valid_email(email)) else {
            continue;
        };
        let code = match code.filter(|code| !code.is_empty()) {
            Some(code) if valid_code(&code) && code.len() <= 30 => code,
            Some(_) => continue,
            None => {
                let code = codes::unique(db, Credential::Judge).await?;
                sqlx::query("UPDATE tab_judge SET ballot_code = ? WHERE id = ? AND (ballot_code IS NULL OR ballot_code = '')")
                    .bind(&code).bind(id).execute(&mut *db).await?;
                sqlx::query_scalar("SELECT ballot_code FROM tab_judge WHERE id = ?")
                    .bind(id)
                    .fetch_one(&mut *db)
                    .await?
            }
        };
        if !addresses.insert(address.to_lowercase()) {
            continue;
        }
        let link = format!("{origin}/public/e-ballots/{code}/");
        let message = if registration {
            let rounds: Vec<i32> = sqlx::query_scalar(
                "SELECT round_number FROM tab_judgeexpectedcheckin WHERE judge_id = ? ORDER BY round_number",
            ).bind(id).fetch_all(&mut *db).await?;
            format!(
                "Your judge registration for {tournament} has been received.\nAvailability: {}\n\n",
                availability(&rounds)
            )
        } else {
            String::new()
        };
        let message = format!(
            "Hi {name},\n\n{message}Your ballot code is {code}.\nJudge portal: {link}\nJudge lookup: {origin}/public/e-ballots/\n\nThanks,\nThe Tab Team"
        );
        output.push(Delivery {
            log: Log::Judge { id, code },
            email: Email::simple(
                address,
                format!("Your judge ballot code for {tournament}"),
                message,
            )
            .with_link(&link, "Open Judge Portal"),
        });
    }
    Ok(output)
}

fn valid_code(code: &str) -> bool {
    !code.is_empty()
        && code
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}
