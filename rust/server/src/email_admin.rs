use std::collections::{BTreeMap, BTreeSet};

use axum::{
    Extension,
    body::Bytes,
    extract::State,
    http::{HeaderMap, StatusCode, header},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::{Value, context};
use serde::{Deserialize, Serialize};
use sqlx::{MySqlConnection, Row};

use crate::{
    App, access,
    admin_model_forms::AdminFormProof,
    email::{self, Email},
    email_content::{self, Confirmations},
    legacy_html,
    legacy_session::{Actor, cookie},
    login::{self, TemplateCsrf},
    private,
    registration_emails::{self as deliveries, Delivery, Log},
    registration_form::{Fields, valid_email},
    settings::Settings,
    unavailable,
};

const PATH: &str = "/email_management/";

const NOTICE_COOKIE: &str = "mittab_email_notice";

#[derive(Serialize, Deserialize)]
struct Notice {
    text: String,
    error: bool,
}

impl Notice {
    fn read(app: &App, headers: &HeaderMap) -> Option<Self> {
        let (encoded, tag) = cookie(headers, NOTICE_COOKIE)?.split_once('.')?;
        let proof = AdminFormProof::for_target(headers, "staff email notice", PATH, encoded)?;
        if !proof.verified(app, tag) {
            return None;
        }
        serde_json::from_slice(&hex::decode(encoded).ok()?).ok()
    }

    fn redirect(self, app: &App, headers: &HeaderMap) -> Response {
        let encoded = hex::encode(serde_json::to_vec(&self).expect("serializable email notice"));
        let mut response = access::found(PATH);
        if let Some(proof) =
            AdminFormProof::for_target(headers, "staff email notice", PATH, &encoded)
        {
            let value = format!("{encoded}.{}", proof.token(app));
            login::set_cookie(
                &mut response,
                NOTICE_COOKIE,
                &value,
                60,
                login::secure_request(headers),
                true,
            );
        }
        response
    }
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(&headers) else {
        return unavailable();
    };
    let origin = email::origin(&headers).unwrap_or_default();
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    match render(&mut db, actor, &csrf, &origin, Notice::read(&app, &headers)).await {
        Ok(mut response) => {
            csrf.set_cookie(&mut response, &headers);
            if cookie(&headers, NOTICE_COOKIE).is_some() {
                login::set_cookie(
                    &mut response,
                    NOTICE_COOKIE,
                    "",
                    0,
                    login::secure_request(&headers),
                    true,
                );
            }
            response
        }
        Err(_) => unavailable(),
    }
}

async fn render(
    db: &mut MySqlConnection,
    actor: Actor,
    csrf: &TemplateCsrf,
    origin: &str,
    notice: Option<Notice>,
) -> Result<Response, sqlx::Error> {
    let settings = Settings::load(db, &["tournament_name"]).await?;
    let tournament = settings.string("tournament_name", "Tournament");
    let judge_tournament = settings.string("tournament_name", "your tournament");
    let team_email = teams(db, origin, &tournament).await?;
    let judge_email = judges(db, origin, &judge_tournament).await?;
    let registration_email = registrations(db, origin, &tournament).await?;
    let written_rfd_email = written_context(db, &judge_tournament).await?;
    let history_rows = history(db).await?;
    let shell = legacy_html::shell(db, actor, PATH).await?;
    let messages = notice.map(|notice| {
        [context! {
            text => notice.text, tags => if notice.error { "error" } else { "success" },
            level => if notice.error { 40 } else { 25 },
        }]
    });
    Ok(legacy_html::render(
        "tab/email_management.html",
        shell,
        context! {
            csrf_input => csrf.input(), team_email, judge_email, registration_email,
            written_rfd_email, history_rows, messages,
        },
        StatusCode::OK,
    ))
}

#[derive(Serialize)]
struct Attempt {
    sent_at: NaiveDateTime,
    successful: bool,
    error_message: String,
}

struct TeamAttempt {
    attempt: Attempt,
    address: String,
    snapshot: serde_json::Value,
}

async fn teams(
    db: &mut MySqlConnection,
    origin: &str,
    tournament: &str,
) -> Result<Value, sqlx::Error> {
    let teams = deliveries::team_mail(db).await?;
    let plan = deliveries::team_plan(db, &teams).await?;
    let mut logs: BTreeMap<i32, Vec<TeamAttempt>> = BTreeMap::new();
    for (id, address, successful, error_message, sent_at, snapshot) in
        sqlx::query_as::<_, (i32, String, bool, String, NaiveDateTime, String)>(
            "SELECT team_id, email, successful, error_message, sent_at, CAST(team_snapshot AS CHAR)
             FROM tab_teamcodeemaillog ORDER BY sent_at DESC, id DESC",
        )
        .fetch_all(db)
        .await?
    {
        logs.entry(id).or_default().push(TeamAttempt {
            attempt: Attempt {
                successful,
                error_message,
                sent_at,
            },
            address,
            snapshot: serde_json::from_str(&snapshot).unwrap_or_default(),
        });
    }
    let mut rows = Vec::new();
    for team in &teams {
        let log = logs.get(&team.id).map_or(&[][..], Vec::as_slice);
        let last_log = log.first().map(|log| &log.attempt);
        let mut success_by_recipient = BTreeMap::new();
        let mut latest_success = None;
        for sent in log.iter().filter(|log| log.attempt.successful) {
            latest_success.get_or_insert(sent);
            success_by_recipient
                .entry(sent.address.to_lowercase())
                .or_insert(&sent.snapshot);
        }
        let snapshot = team.snapshot(tournament);
        let never_received = latest_success.is_none();
        let needs_resend = latest_success.is_some_and(|previous| {
            previous.snapshot != snapshot
                || team.recipients.iter().any(|address| {
                    success_by_recipient.get(&address.to_lowercase()) != Some(&&snapshot)
                })
        });
        let preview = match team.recipients.first() {
            Some(address) => team.email(address, origin, tournament)?,
            None => None,
        };
        let status = if team.code.as_deref().is_none_or(str::is_empty) {
            "Missing team code"
        } else if preview.is_none() {
            if team.recipients.is_empty() {
                "Missing email"
            } else {
                "Invalid team code"
            }
        } else if needs_resend {
            "Needs resend"
        } else if last_log.is_some_and(|attempt| !attempt.successful) {
            "Failed"
        } else if never_received {
            "Never sent"
        } else {
            "Sent"
        };
        let sendable_recipients = plan.get(&team.id).map_or(0, Vec::len);
        let can_send = sendable_recipients > 0;
        rows.push(context! {
            team => context! {id => team.id, name => team.name, team_code => team.code},
            status, needs_resend, never_received, can_send, checked => can_send && never_received,
            cooldown => preview.is_some() && !can_send, preview,
            recipients => if team.path().is_some() { team.recipients.join(", ") } else { String::new() },
            last_log, sendable_recipients,
        });
    }
    Ok(context! { rows, sendable_count => plan.len() })
}

async fn last_sends(
    db: &mut MySqlConnection,
    kind: &'static str,
) -> Result<BTreeMap<i32, NaiveDateTime>, sqlx::Error> {
    let query = match kind {
        "judge" => "SELECT judge_id, MAX(sent_at) FROM tab_judgecodeemaillog GROUP BY judge_id",
        _ => "SELECT round_id, MAX(sent_at) FROM tab_writtenrfdemaillog GROUP BY round_id",
    };
    Ok(sqlx::query_as(query)
        .fetch_all(db)
        .await?
        .into_iter()
        .collect())
}

async fn judges(
    db: &mut MySqlConnection,
    origin: &str,
    tournament: &str,
) -> Result<Value, sqlx::Error> {
    let judges = deliveries::judge_mail(db).await?;
    let plan = deliveries::judge_plan(db, &judges).await?;
    let last = last_sends(db, "judge").await?;
    let mut sendable_count = 0;
    let mut never_received_sendable_count = 0;
    let mut missing_email_count = 0;
    let mut rows = Vec::new();
    for mut judge in judges {
        let status = plan[&judge.id];
        let can_send = status == "Ready";
        let last_sent = last.get(&judge.id);
        let never_received = last_sent.is_none();
        let checked = can_send && never_received;
        if can_send && judge.code.as_deref().is_none_or(str::is_empty) {
            judge.code = Some(deliveries::ensure_judge_code(db, judge.id).await?);
        }
        let preview = judge.preview(origin, tournament)?;
        sendable_count += usize::from(can_send);
        never_received_sendable_count += usize::from(checked);
        missing_email_count += usize::from(judge.email.is_empty());
        rows.push(context! {
            judge => context! { id => judge.id, name => judge.name }, email => judge.email,
            ballot_code => judge.code, checked, last_sent, status, can_send, never_received, preview,
        });
    }
    Ok(
        context! { rows, sendable_count, never_received_sendable_count, missing_email_count, rate_limit_hours => 6 },
    )
}

async fn registrations(
    db: &mut MySqlConnection,
    origin: &str,
    tournament: &str,
) -> Result<Value, sqlx::Error> {
    let entries = Confirmations::load(db, None).await?;
    let mut last = BTreeMap::new();
    let mut successful_ids = BTreeSet::new();
    for (id, successful, error_message, sent_at) in
        sqlx::query_as::<_, (i32, bool, String, NaiveDateTime)>(
            "SELECT registration_id, successful, error_message, sent_at
         FROM registration_registrationconfirmationemaillog ORDER BY sent_at DESC, id",
        )
        .fetch_all(db)
        .await?
    {
        if successful {
            successful_ids.insert(id);
        }
        last.entry(id).or_insert(Attempt {
            successful,
            error_message,
            sent_at,
        });
    }
    let mut sendable_count = 0;
    let mut never_received_sendable_count = 0;
    let mut rows = Vec::new();
    for entry in &entries.registrations {
        let preview = entries.email(entry, origin, tournament)?;
        let can_send = preview.is_some() && valid_email(&entry.email);
        let never_received = !successful_ids.contains(&entry.id);
        let checked = can_send && never_received;
        let last_log = last.get(&entry.id);
        let status = if entry.email.is_empty() {
            "Missing email"
        } else if !can_send {
            "Invalid email or code"
        } else {
            match last_log {
                Some(log) if log.successful => "Sent",
                Some(_) => "Failed",
                None => "Never sent",
            }
        };
        sendable_count += usize::from(can_send);
        never_received_sendable_count += usize::from(checked);
        rows.push(context! {
            registration => context! {
                id => entry.id, school => context! { name => &entry.school }, email => &entry.email,
                herokunator_code => &entry.code, url => email_content::portal_path("registration", &entry.code),
            },
            status, can_send, never_received, checked, last_log, preview,
        });
    }
    Ok(context! { rows, sendable_count, never_received_sendable_count })
}

struct Written {
    id: i32,
    round: i32,
    gov: String,
    opp: String,
    victor: i32,
    chair: String,
    rfd: String,
    emails: Vec<String>,
}

impl Written {
    fn winner(&self) -> &str {
        if matches!(self.victor, 1 | 3) {
            &self.gov
        } else {
            &self.opp
        }
    }

    fn email(&self, address: &str, tournament: &str) -> Result<Email, sqlx::Error> {
        email_content::written(
            address,
            self.round,
            [&self.gov, &self.opp],
            self.winner(),
            &self.chair,
            &self.rfd,
            tournament,
        )
    }
}

async fn written_context(db: &mut MySqlConnection, tournament: &str) -> Result<Value, sqlx::Error> {
    let written = written_rows(db).await?;
    let last = last_sends(db, "written").await?;
    let mut sendable_count = 0;
    let mut never_received_sendable_count = 0;
    let mut rows = Vec::new();
    for row in written {
        let status = if row.emails.is_empty() {
            "Missing email"
        } else if row.rfd.is_empty() {
            "Missing RFD"
        } else {
            "Ready"
        };
        let can_send = status == "Ready";
        let last_sent = last.get(&row.id);
        let checked = can_send && last_sent.is_none();
        let preview = if can_send {
            Some(row.email(&row.emails[0], tournament)?)
        } else {
            None
        };
        sendable_count += usize::from(can_send);
        never_received_sendable_count += usize::from(checked);
        rows.push(context! {
            round => context! {
                id => row.id, round_number => row.round,
                gov_team => context! {display => &row.gov}, opp_team => context! {display => &row.opp},
            },
            winner => row.winner(), recipient_count => row.emails.len(), recipients => row.emails.join(", "),
            status, can_send, checked, last_sent, preview,
        });
    }
    Ok(context! { rows, sendable_count, never_received_sendable_count })
}

async fn written_rows(db: &mut MySqlConnection) -> Result<Vec<Written>, sqlx::Error> {
    let first = Settings::load(db, &["written_rfd_first_round"])
        .await?
        .integer("written_rfd_first_round", -1);
    if first < 1 {
        return Ok(Vec::new());
    }
    let rows = sqlx::query(
        "SELECT r.id, r.round_number, g.name AS gov, o.name AS opp, r.victor, r.rfd, COALESCE(j.name, 'Unknown') AS chair,
          r.gov_team_id, r.opp_team_id
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_judge j ON j.id = r.chair_id
         WHERE r.round_number >= ? AND r.victor IN (1, 2, 3, 4)
         AND EXISTS(SELECT 1 FROM tab_roundstats s WHERE s.round_id = r.id)
         ORDER BY r.round_number DESC, g.name, o.name, r.id",
    ).bind(first).fetch_all(&mut *db).await?;
    let mut contacts: BTreeMap<i32, Vec<String>> = BTreeMap::new();
    for (team, email) in sqlx::query_as::<_, (i32, String)>(
        "SELECT m.team_id, d.email FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id
         WHERE d.email IS NOT NULL ORDER BY d.name, d.id",
    ).fetch_all(db).await? {
        let email = email.trim();
        if valid_email(email) {
            contacts.entry(team).or_default().push(email.into());
        }
    }
    rows.into_iter()
        .map(|row| {
            let gov_id = row.try_get::<i32, _>("gov_team_id")?;
            let opp_id = row.try_get::<i32, _>("opp_team_id")?;
            let mut seen = BTreeSet::new();
            let emails = [gov_id, opp_id]
                .iter()
                .flat_map(|id| contacts.get(id).into_iter().flatten())
                .filter(|email| seen.insert(email.to_lowercase()))
                .cloned()
                .collect();
            Ok(Written {
                id: row.try_get("id")?,
                round: row.try_get("round_number")?,
                gov: row.try_get("gov")?,
                opp: row.try_get("opp")?,
                victor: row.try_get("victor")?,
                chair: row.try_get("chair")?,
                rfd: row.try_get::<String, _>("rfd")?.trim().into(),
                emails,
            })
        })
        .collect()
}

async fn history(db: &mut MySqlConnection) -> Result<Vec<Value>, sqlx::Error> {
    let rows = sqlx::query(
        "SELECT category, target, owner, code, email, detail, sent_at, payload
         FROM (
           SELECT 1 AS category, l.id, j.name AS target, j.id AS owner, '' AS code, l.email,
                  CONCAT(CASE l.source WHEN 'manual' THEN 'Manual' WHEN 'registration' THEN 'Registration'
                    ELSE l.source END, ' send; ballot code ', l.ballot_code) AS detail,
                  l.sent_at, CAST(l.email_payload AS CHAR) AS payload
           FROM tab_judgecodeemaillog l JOIN tab_judge j ON j.id = l.judge_id
           UNION ALL
           SELECT 2, l.id, CONCAT('Round ', r.round_number), 0, '', l.email, CONCAT(g.name, ' vs ', o.name),
                  l.sent_at, CAST(l.email_payload AS CHAR)
           FROM tab_writtenrfdemaillog l JOIN tab_round r ON r.id = l.round_id
             JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
           UNION ALL
           SELECT 3, l.id, s.name, 0, r.herokunator_code, l.email,
                  CONCAT(IF(l.successful, 'Sent successfully', 'Failed'),
                    IF(l.error_message = '', '', CONCAT(': ', LEFT(l.error_message, 120)))),
                  l.sent_at, CAST(l.email_payload AS CHAR)
           FROM registration_registrationconfirmationemaillog l JOIN registration_registration r ON r.id = l.registration_id
             JOIN tab_school s ON s.id = r.school_id
           UNION ALL
           SELECT 4, l.id, COALESCE(JSON_UNQUOTE(JSON_EXTRACT(l.team_snapshot, '$.name')), t.name), t.id, '', l.email,
                  CONCAT(UPPER(LEFT(l.source, 1)), LOWER(SUBSTRING(l.source, 2)), ': ',
                    IF(l.successful, 'Sent successfully', CONCAT('Failed: ', l.error_message))),
                  l.sent_at, CAST(l.email_payload AS CHAR)
           FROM tab_teamcodeemaillog l JOIN tab_team t ON t.id = l.team_id
           UNION ALL
           SELECT 5, l.id, l.username, 0, '', l.email, IF(l.successful, 'Sent successfully', CONCAT('Failed: ', l.error_message)),
                  l.sent_at, CAST(l.email_payload AS CHAR)
           FROM tab_staffinviteemaillog l
         ) history ORDER BY sent_at DESC, category, id",
    ).fetch_all(db).await?;
    rows.into_iter().map(|row| {
        let (kind, target_url) = match row.try_get::<i32, _>("category")? {
            1 => ("Judge code", format!("/judge/{}/", row.try_get::<i32, _>("owner")?)),
            2 => ("Written RFD", String::new()),
            3 => ("Registration confirmation", email_content::portal_path("registration", &row.try_get::<String, _>("code")?).unwrap_or_default()),
            4 => ("Team code", format!("/team/{}/", row.try_get::<i32, _>("owner")?)),
            _ => ("Staff invitation", String::new()),
        };
        let payload: String = row.try_get("payload")?;
        let email_payload: serde_json::Value = serde_json::from_str(&payload).unwrap_or_default();
        Ok(context! {
            type => kind, target => row.try_get::<String, _>("target")?, target_url,
            recipient => row.try_get::<String, _>("email")?, detail => row.try_get::<String, _>("detail")?,
            sent_at => row.try_get::<NaiveDateTime, _>("sent_at")?, email_payload,
        })
    }).collect()
}

#[derive(Clone, Copy)]
enum Batch {
    Team,
    Judge,
    Registration,
    Written,
}

impl Batch {
    fn from_fields(fields: &Fields) -> Self {
        match fields.get("email_action") {
            "team_codes" => Self::Team,
            "registration_confirmations" => Self::Registration,
            "written_rfds" => Self::Written,
            _ => Self::Judge,
        }
    }

    fn selection(self, fields: &Fields) -> Vec<i32> {
        let name = match self {
            Self::Team => "team_ids",
            Self::Judge => "judge_ids",
            Self::Registration => "registration_ids",
            Self::Written => "round_ids",
        };
        let mut seen = BTreeSet::new();
        fields
            .all(name)
            .iter()
            .filter(|value| value.bytes().all(|byte| byte.is_ascii_digit()))
            .filter_map(|value| value.parse::<i32>().ok())
            .filter(|&id| id > 0 && seen.insert(id))
            .collect()
    }

    fn success(self, count: usize, skipped: usize) -> String {
        let plural = if count == 1 { "" } else { "s" };
        match self {
            Self::Team => {
                format!("Sent {count} team code email(s). Unavailable recipients were skipped.")
            }
            Self::Judge => {
                let suffix = if skipped > 0 {
                    format!(" Skipped {skipped} due to invalid codes or rate limiting.")
                } else {
                    String::new()
                };
                format!("Sent judge portal links to {count} judge{plural}.{suffix}")
            }
            Self::Registration => format!("Sent {count} registration confirmation email{plural}."),
            Self::Written => format!("Sent {count} written RFD email{plural}."),
        }
    }

    fn no_selection(self, skipped: usize) -> String {
        match self {
            Self::Team => "No team code emails were sent. Check selection, addresses and the six-hour cooldown.",
            Self::Judge if skipped > 0 => "No judge codes were sent due to rate limiting or invalid data.",
            Self::Judge => "No judge codes were sent.",
            Self::Registration => "No registration confirmation emails were sent.",
            Self::Written => "No written RFD emails were sent.",
        }.into()
    }

    fn failure(self, sent: usize, reason: &str) -> String {
        let (name, item) = match self {
            Self::Team => return format!("Unable to send all team codes ({sent} sent): {reason}"),
            Self::Judge => ("judge codes", "judge code"),
            Self::Registration => ("registration confirmations", "confirmation"),
            Self::Written => ("written RFDs", "written RFD email"),
        };
        if sent == 0 {
            format!("Unable to send {name}: {reason}")
        } else {
            let plural = if sent == 1 { "" } else { "s" };
            format!("Unable to send {name} after sending {sent} {item}{plural}: {reason}")
        }
    }
}

pub async fn send(State(app): State<App>, headers: HeaderMap, body: Bytes) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let batch = Batch::from_fields(&fields);
    let origin = email::origin(&headers);
    if origin.is_none() && !matches!(batch, Batch::Written) {
        return Notice {
            text: batch.failure(0, "Tournament email links are unavailable."),
            error: true,
        }
        .redirect(&app, &headers);
    }
    let Ok(Some(mut db)) = deliveries::lock(&app.db).await else {
        return unavailable();
    };
    let selected = batch.selection(&fields);
    let Ok((messages, skipped)) = prepare(
        &mut db,
        batch,
        &selected,
        origin.as_deref().unwrap_or_default(),
    )
    .await
    else {
        return unavailable();
    };
    if messages.is_empty() {
        return Notice {
            text: batch.no_selection(skipped),
            error: true,
        }
        .redirect(&app, &headers);
    }
    let mailer = email::service();
    let Ok(deliveries::SendReport {
        sent,
        failed,
        last_error,
    }) = deliveries::send_report(&mut db, mailer, messages, "manual").await
    else {
        return unavailable();
    };
    let (text, error) = if failed == 0 {
        (batch.success(sent, skipped), false)
    } else {
        let reason = if mailer.is_some() {
            format!("Failed to send {failed} email(s): {last_error}")
        } else {
            last_error.into()
        };
        (batch.failure(sent, &reason), true)
    };
    Notice { text, error }.redirect(&app, &headers)
}

async fn prepare(
    db: &mut MySqlConnection,
    batch: Batch,
    selected: &[i32],
    origin: &str,
) -> Result<(Vec<Delivery>, usize), sqlx::Error> {
    let fallback = if matches!(batch, Batch::Judge | Batch::Written) {
        "your tournament"
    } else {
        "Tournament"
    };
    let tournament = Settings::load(db, &["tournament_name"])
        .await?
        .string("tournament_name", fallback);
    match batch {
        Batch::Team => deliveries::team_deliveries(db, selected, origin, &tournament)
            .await
            .map(|rows| (rows, 0)),
        Batch::Judge => {
            deliveries::judge_delivery_plan(db, selected, origin, &tournament, false).await
        }
        Batch::Registration => {
            let entries = Confirmations::load(db, None).await?;
            let by_id: BTreeMap<_, _> = entries
                .registrations
                .iter()
                .map(|entry| (entry.id, entry))
                .collect();
            let mut output = Vec::new();
            for entry in selected
                .iter()
                .filter_map(|id| by_id.get(id))
                .filter(|entry| valid_email(&entry.email))
            {
                if let Some(email) = entries.email(entry, origin, &tournament)? {
                    output.push(Delivery {
                        log: Log::Confirmation { id: entry.id },
                        email,
                    });
                }
            }
            Ok((output, 0))
        }
        Batch::Written => {
            let rounds: BTreeMap<_, _> = written_rows(db)
                .await?
                .into_iter()
                .map(|round| (round.id, round))
                .collect();
            let mut output = Vec::new();
            for round in selected
                .iter()
                .filter_map(|id| rounds.get(id))
                .filter(|round| !round.rfd.is_empty())
            {
                for address in &round.emails {
                    output.push(Delivery {
                        log: Log::Written { id: round.id },
                        email: round.email(address, &tournament)?,
                    });
                }
            }
            Ok((output, 0))
        }
    }
}

pub async fn judge_codes(State(app): State<App>) -> Response {
    let rows = sqlx::query_as::<_, (String, Option<String>)>(
        "SELECT name, ballot_code FROM tab_judge ORDER BY name, id",
    )
    .fetch_all(&app.db)
    .await;
    match rows {
        Ok(rows) => private((
            [
                (header::CONTENT_TYPE, "text/plain; charset=utf-8"),
                (
                    header::CONTENT_DISPOSITION,
                    "attachment; filename=judge_codes.txt",
                ),
            ],
            rows.into_iter()
                .map(|(name, code)| format!("{name}: {}", code.as_deref().unwrap_or("None")))
                .collect::<Vec<_>>()
                .join("\n"),
        )),
        Err(_) => unavailable(),
    }
}
