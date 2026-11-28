use std::collections::{BTreeMap, BTreeSet, HashMap};

use sqlx::{MySql, MySqlConnection, MySqlPool, pool::PoolConnection};

use crate::{
    codes::{self, Credential},
    email::{self, Email, Mailer},
    email_content::{self, Confirmations},
    judge_portal,
    registration_form::valid_email,
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
    Ok(send_report(db, mailer, deliveries, source).await?.failed == 0)
}

#[derive(Default)]
pub struct SendReport {
    pub sent: usize,
    pub failed: usize,
    pub last_error: &'static str,
}

pub async fn send_report(
    db: &mut MySqlConnection,
    mailer: Option<&'static Mailer>,
    deliveries: Vec<Delivery>,
    source: &str,
) -> Result<SendReport, sqlx::Error> {
    let mut report = SendReport::default();
    let mut batch = tokio::task::JoinSet::new();
    let mut queue = deliveries.into_iter().enumerate();
    let mut active = HashMap::new();
    let mut completed = BTreeMap::new();
    let mut next_log = 0;
    loop {
        while batch.len() < 4 {
            let Some((index, delivery)) = queue.next() else {
                break;
            };
            let task = batch.spawn(deliver(mailer, delivery));
            active.insert(task.id(), index);
        }
        let Some(joined) = batch.join_next_with_id().await else {
            break;
        };
        let (task, outcome) = match joined {
            Ok((task, outcome)) => (task, Some(outcome)),
            Err(error) => (error.id(), None),
        };
        let index = active.remove(&task).expect("queued email task");
        completed.insert(index, outcome);
        // Delivery is concurrent. History keeps the original selection order.
        while let Some(outcome) = completed.remove(&next_log) {
            let result = if let Some((delivery, result)) = outcome {
                delivery.record(db, result, source).await?;
                result
            } else {
                Err(UNSENT)
            };
            if let Err(error) = result {
                report.last_error = error;
                report.failed += 1;
            } else {
                report.sent += 1;
            }
            next_log += 1;
        }
    }
    Ok(report)
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
    let entries = Confirmations::load(db, Some(id)).await?;
    let Some(registration) = entries.registrations.first() else {
        return Ok(None);
    };
    Ok(entries
        .email(registration, origin, tournament)?
        .filter(|email| valid_email(&email.to_address))
        .map(|email| Delivery {
            log: Log::Confirmation { id },
            email,
        }))
}

pub struct TeamMail {
    pub id: i32,
    pub name: String,
    pub code: Option<String>,
    pub recipients: Vec<String>,
    debaters: Vec<String>,
}

impl TeamMail {
    pub fn path(&self) -> Option<String> {
        email_content::portal_path("team_portal", self.code.as_deref()?)
    }

    pub fn snapshot(&self, tournament: &str) -> serde_json::Value {
        let mut debaters: Vec<_> = self.debaters.iter().collect();
        debaters.sort();
        let mut recipients: Vec<_> = self
            .recipients
            .iter()
            .map(|email| email.to_lowercase())
            .collect();
        recipients.sort();
        serde_json::json!({
            "name": self.name, "team_code": self.code, "debaters": debaters,
            "recipients": recipients, "tournament_name": tournament,
        })
    }

    pub fn email(
        &self,
        address: &str,
        origin: &str,
        tournament: &str,
    ) -> Result<Option<Email>, sqlx::Error> {
        let Some(path) = self.path() else {
            return Ok(None);
        };
        email_content::team(
            address,
            &self.name,
            self.code.as_deref().unwrap_or_default(),
            &self.debaters,
            tournament,
            &format!("{origin}{path}"),
        )
        .map(Some)
    }
}

pub async fn team_mail(db: &mut MySqlConnection) -> Result<Vec<TeamMail>, sqlx::Error> {
    let mut members: BTreeMap<i32, Vec<(String, Option<String>)>> = BTreeMap::new();
    for (id, name, email) in sqlx::query_as::<_, (i32, String, Option<String>)>(
        "SELECT m.team_id, d.name, d.email FROM tab_debater d JOIN tab_team_debaters m ON m.debater_id = d.id
         ORDER BY d.name, d.id",
    ).fetch_all(&mut *db).await? {
        members.entry(id).or_default().push((name, email));
    }
    let teams = sqlx::query_as::<_, (i32, String, Option<String>)>(
        "SELECT id, name, team_code FROM tab_team ORDER BY name, id",
    )
    .fetch_all(db)
    .await?;
    Ok(teams
        .into_iter()
        .map(|(id, name, code)| {
            let mut seen = BTreeSet::new();
            let mut debaters = Vec::new();
            let mut recipients = Vec::new();
            for (debater, email) in members.remove(&id).unwrap_or_default() {
                debaters.push(debater);
                if let Some(address) = email
                    .as_deref()
                    .map(str::trim)
                    .filter(|email| !email.is_empty())
                    && seen.insert(address.to_lowercase())
                {
                    recipients.push(address.into());
                }
            }
            TeamMail {
                id,
                name,
                code,
                recipients,
                debaters,
            }
        })
        .collect())
}

pub async fn team_plan<'a>(
    db: &mut MySqlConnection,
    teams: impl IntoIterator<Item = &'a TeamMail>,
) -> Result<BTreeMap<i32, Vec<&'a str>>, sqlx::Error> {
    let recent = sqlx::query_as::<_, (i32, String)>(
        "SELECT team_id, email FROM tab_teamcodeemaillog
         WHERE successful AND sent_at >= UTC_TIMESTAMP(6) - INTERVAL 6 HOUR",
    )
    .fetch_all(db)
    .await?;
    let recent_teams: BTreeSet<_> = recent.iter().map(|&(id, _)| id).collect();
    let mut addresses: BTreeSet<_> = recent
        .into_iter()
        .map(|(_, email)| email.to_lowercase())
        .collect();
    let mut plan = BTreeMap::new();
    for team in teams {
        if !recent_teams.contains(&team.id) && team.path().is_some() {
            let recipients: Vec<_> = team
                .recipients
                .iter()
                .map(String::as_str)
                .filter(|email| valid_email(email) && addresses.insert(email.to_lowercase()))
                .collect();
            if !recipients.is_empty() {
                plan.insert(team.id, recipients);
            }
        }
    }
    Ok(plan)
}

pub async fn team_deliveries(
    db: &mut MySqlConnection,
    ids: &[i32],
    origin: &str,
    tournament: &str,
) -> Result<Vec<Delivery>, sqlx::Error> {
    let teams = team_mail(db).await?;
    let selected: BTreeSet<_> = ids.iter().copied().collect();
    let teams: Vec<_> = teams
        .iter()
        .filter(|team| selected.contains(&team.id))
        .collect();
    let plan = team_plan(db, teams.iter().copied()).await?;
    let mut messages = Vec::new();
    for team in teams {
        if let Some(addresses) = plan.get(&team.id) {
            let snapshot = team.snapshot(tournament).to_string();
            for address in addresses {
                if let Some(email) = team.email(address, origin, tournament)? {
                    messages.push(Delivery {
                        log: Log::Team {
                            id: team.id,
                            snapshot: snapshot.clone(),
                        },
                        email,
                    });
                }
            }
        }
    }
    Ok(messages)
}

pub struct JudgeMail {
    pub id: i32,
    pub name: String,
    pub email: String,
    pub code: Option<String>,
    rounds: Vec<i32>,
}

impl JudgeMail {
    pub fn preview(&self, origin: &str, tournament: &str) -> Result<Option<Email>, sqlx::Error> {
        let Some(code) = self
            .code
            .as_deref()
            .filter(|code| judge_portal::valid_code(code))
        else {
            return Ok(None);
        };
        if self.email.is_empty() {
            return Ok(None);
        }
        email_content::judge(
            &self.email,
            &self.name,
            code,
            &self.rounds,
            origin,
            tournament,
            false,
        )
        .map(Some)
    }
}

pub async fn judge_mail(db: &mut MySqlConnection) -> Result<Vec<JudgeMail>, sqlx::Error> {
    let mut rounds: BTreeMap<i32, Vec<i32>> = BTreeMap::new();
    for (id, round) in sqlx::query_as::<_, (i32, i32)>(
        "SELECT judge_id, round_number FROM tab_judgeexpectedcheckin ORDER BY round_number",
    )
    .fetch_all(&mut *db)
    .await?
    {
        rounds.entry(id).or_default().push(round);
    }
    let rows = sqlx::query_as::<_, (i32, String, Option<String>, Option<String>)>(
        "SELECT id, name, email, ballot_code FROM tab_judge ORDER BY name, id",
    )
    .fetch_all(db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(id, name, email, code)| JudgeMail {
            id,
            name,
            code,
            email: email.unwrap_or_default(),
            rounds: rounds.remove(&id).unwrap_or_default(),
        })
        .collect())
}

pub async fn judge_plan<'a>(
    db: &mut MySqlConnection,
    judges: impl IntoIterator<Item = &'a JudgeMail>,
) -> Result<BTreeMap<i32, &'static str>, sqlx::Error> {
    let recent = sqlx::query_as::<_, (i32, String)>(
        "SELECT judge_id, email FROM tab_judgecodeemaillog WHERE sent_at >= UTC_TIMESTAMP(6) - INTERVAL 6 HOUR",
    ).fetch_all(db).await?;
    let recent_judges: BTreeSet<_> = recent.iter().map(|&(id, _)| id).collect();
    let mut addresses: BTreeSet<_> = recent
        .into_iter()
        .map(|(_, email)| email.to_lowercase())
        .collect();
    Ok(judges
        .into_iter()
        .map(|judge| {
            let email = judge.email.trim();
            let status = if email.is_empty() {
                "Missing email"
            } else if !valid_email(email) {
                "Invalid email"
            } else if judge
                .code
                .as_deref()
                .is_some_and(|code| !code.is_empty() && !judge_portal::valid_code(code))
            {
                "Invalid code"
            } else if recent_judges.contains(&judge.id) || !addresses.insert(email.to_lowercase()) {
                "Recently emailed"
            } else {
                "Ready"
            };
            (judge.id, status)
        })
        .collect())
}

pub async fn judge_deliveries(
    db: &mut MySqlConnection,
    ids: &[i32],
    origin: &str,
    tournament: &str,
    registration: bool,
) -> Result<Vec<Delivery>, sqlx::Error> {
    judge_delivery_plan(db, ids, origin, tournament, registration)
        .await
        .map(|(deliveries, _)| deliveries)
}

pub async fn judge_delivery_plan(
    db: &mut MySqlConnection,
    ids: &[i32],
    origin: &str,
    tournament: &str,
    registration: bool,
) -> Result<(Vec<Delivery>, usize), sqlx::Error> {
    let judges = judge_mail(db).await?;
    let selected: BTreeSet<_> = ids.iter().copied().collect();
    let plan = judge_plan(
        db,
        judges
            .iter()
            .filter(|judge| !registration || selected.contains(&judge.id)),
    )
    .await?;
    let skipped = plan.values().filter(|&&status| status != "Ready").count();
    let mut judges: BTreeMap<_, _> = judges.into_iter().map(|judge| (judge.id, judge)).collect();
    let mut output = Vec::new();
    for id in ids.iter().filter(|&id| plan.get(id) == Some(&"Ready")) {
        let Some(judge) = judges.remove(id) else {
            continue;
        };
        let code = match judge.code.filter(|code| !code.is_empty()) {
            Some(code) => code,
            None => ensure_judge_code(db, judge.id).await?,
        };
        if judge_portal::valid_code(&code) {
            let email = email_content::judge(
                judge.email.trim(),
                &judge.name,
                &code,
                &judge.rounds,
                origin,
                tournament,
                registration,
            )?;
            output.push(Delivery {
                log: Log::Judge { id: judge.id, code },
                email,
            });
        }
    }
    Ok((output, skipped))
}

pub async fn ensure_judge_code(db: &mut MySqlConnection, id: i32) -> Result<String, sqlx::Error> {
    let code = codes::unique(db, Credential::Judge).await?;
    sqlx::query("UPDATE tab_judge SET ballot_code = ? WHERE id = ? AND (ballot_code IS NULL OR ballot_code = '')")
        .bind(&code).bind(id).execute(&mut *db).await?;
    sqlx::query_scalar("SELECT ballot_code FROM tab_judge WHERE id = ?")
        .bind(id)
        .fetch_one(db)
        .await
}
