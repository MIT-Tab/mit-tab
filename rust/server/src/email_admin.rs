use std::collections::{BTreeMap, BTreeSet};

use axum::{
    Extension,
    body::Bytes,
    extract::{Query, State},
    http::{HeaderMap, StatusCode, header},
    response::Response,
};
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::{MySqlConnection, Row};

use crate::{
    App, access,
    email::{self, Email},
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_emails::{self as deliveries, Delivery, Log},
    registration_form::{Fields, valid_email},
    settings::Settings,
    unavailable,
};

const PATH: &str = "/email_management/";

#[derive(Deserialize, Default)]
pub struct QueryData {
    sent: Option<usize>,
    error: Option<bool>,
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(query): Query<QueryData>,
) -> Response {
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    match render(&app, actor, &csrf, query).await {
        Ok(response) => html::form_response(response, &headers, &csrf),
        Err(_) => unavailable(),
    }
}

#[derive(Serialize)]
struct Recipient {
    id: i32,
    label: String,
    recipients: String,
    status: &'static str,
    ready: bool,
    selected: bool,
}

impl Recipient {
    fn new(
        id: i32,
        label: String,
        recipients: String,
        status: &'static str,
        ready: bool,
        selected: bool,
    ) -> Self {
        Self {
            id,
            label,
            recipients,
            status,
            ready,
            selected,
        }
    }
}

async fn render(
    app: &App,
    actor: Actor,
    csrf: &str,
    query: QueryData,
) -> Result<Response, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    let team_rows = teams(&mut db).await?;
    let judge_rows = judges(&mut db).await?;
    let registrations = registrations(&mut db).await?;
    let written = written_rows(&mut db).await?;
    let history = sqlx::query_as::<_, (String, String, bool, String, String, String)>(
        "SELECT category, email, successful, error_message, DATE_FORMAT(sent_at, '%Y-%m-%d %H:%i UTC'), details
         FROM (
          SELECT 'Registration confirmation' AS category, email, successful, error_message, sent_at,
            CAST(email_payload AS CHAR) AS details FROM registration_registrationconfirmationemaillog
          UNION ALL
          SELECT 'Team code', email, successful, error_message, sent_at, CAST(email_payload AS CHAR) FROM tab_teamcodeemaillog
          UNION ALL
          SELECT 'Judge code', email, TRUE, '', sent_at, CAST(email_payload AS CHAR) FROM tab_judgecodeemaillog
          UNION ALL
          SELECT 'Written RFD', email, TRUE, '', sent_at, CAST(email_payload AS CHAR) FROM tab_writtenrfdemaillog
          UNION ALL
          SELECT 'Staff invite', email, successful, error_message, sent_at, CAST(email_payload AS CHAR) FROM tab_staffinviteemaillog
         ) history ORDER BY sent_at DESC LIMIT 500",
    ).fetch_all(&mut *db).await?;
    let history: Vec<_> = history
        .into_iter()
        .map(|(category, email, successful, error, date, raw)| {
            let payload = serde_json::from_str::<serde_json::Value>(&raw).unwrap_or_default();
            let subject = payload["subject"].as_str().unwrap_or_default().to_owned();
            let text = payload["text_body"].as_str().unwrap_or_default().to_owned();
            (category, email, successful, error, date, subject, text)
        })
        .collect();
    let written: Vec<_> = written.into_iter().map(Written::recipient).collect();
    Ok(html::render(
        "email_admin.html",
        context! {
            title => "Email Management", nav => Navigation::new(actor, true), csrf, sent => query.sent,
            has_error => query.error.unwrap_or_default(), teams => team_rows, judges => judge_rows, registrations, written, history,
        },
        StatusCode::OK,
    ))
}

async fn teams(db: &mut MySqlConnection) -> Result<Vec<Recipient>, sqlx::Error> {
    let rows = sqlx::query(
        "SELECT t.id, t.name, t.team_code,
          (SELECT GROUP_CONCAT(DISTINCT d.email ORDER BY d.email SEPARATOR ', ') FROM tab_debater d
           JOIN tab_team_debaters m ON m.debater_id = d.id WHERE m.team_id = t.id AND d.email <> '') AS emails,
          EXISTS(SELECT 1 FROM tab_teamcodeemaillog l WHERE l.team_id = t.id AND l.successful) AS sent,
          EXISTS(SELECT 1 FROM tab_teamcodeemaillog l WHERE l.team_id = t.id AND l.successful
           AND l.sent_at >= UTC_TIMESTAMP(6) - INTERVAL 6 HOUR) AS cooling
         FROM tab_team t ORDER BY t.name, t.id",
    ).fetch_all(&mut *db).await?;
    rows.into_iter()
        .map(|row| {
            let id = row.try_get("id")?;
            let name = row.try_get("name")?;
            let code: Option<String> = row.try_get("team_code")?;
            let emails = row
                .try_get::<Option<String>, _>("emails")?
                .unwrap_or_default();
            let sent: bool = row.try_get("sent")?;
            let cooling: bool = row.try_get("cooling")?;
            let missing = code.is_none_or(|code| code.is_empty());
            let status = if missing {
                "Missing team code"
            } else if emails.is_empty() {
                "Missing email"
            } else if cooling {
                "Cooldown"
            } else if sent {
                "Sent"
            } else {
                "Never sent"
            };
            let ready = !missing && !emails.is_empty() && !cooling;
            Ok(Recipient::new(
                id,
                name,
                emails,
                status,
                ready,
                ready && !sent,
            ))
        })
        .collect()
}

async fn judges(db: &mut MySqlConnection) -> Result<Vec<Recipient>, sqlx::Error> {
    let rows = sqlx::query(
        "SELECT j.id, j.name, j.email,
          EXISTS(SELECT 1 FROM tab_judgecodeemaillog l WHERE l.judge_id = j.id) AS sent,
          EXISTS(SELECT 1 FROM tab_judgecodeemaillog l WHERE (l.judge_id = j.id OR l.email = j.email)
             AND l.sent_at >= UTC_TIMESTAMP(6) - INTERVAL 6 HOUR) AS cooling
         FROM tab_judge j ORDER BY j.name, j.id",
    ).fetch_all(db).await?;
    rows.into_iter()
        .map(|row| {
            let id = row.try_get("id")?;
            let name = row.try_get("name")?;
            let emails = row
                .try_get::<Option<String>, _>("email")?
                .unwrap_or_default();
            let sent: bool = row.try_get("sent")?;
            let cooling: bool = row.try_get("cooling")?;
            let valid = valid_email(&emails);
            let status = if !valid {
                "Missing or invalid email"
            } else if cooling {
                "Cooldown"
            } else if sent {
                "Sent"
            } else {
                "Never sent"
            };
            Ok(Recipient::new(
                id,
                name,
                emails,
                status,
                valid && !cooling,
                valid && !cooling && !sent,
            ))
        })
        .collect()
}

async fn registrations(db: &mut MySqlConnection) -> Result<Vec<Recipient>, sqlx::Error> {
    let rows = sqlx::query(
        "SELECT r.id, s.name, r.email,
         (SELECT l.successful FROM registration_registrationconfirmationemaillog l
          WHERE l.registration_id = r.id ORDER BY l.sent_at DESC, l.id DESC LIMIT 1) AS last
         FROM registration_registration r JOIN tab_school s ON s.id = r.school_id ORDER BY s.name, r.id",
    ).fetch_all(db).await?;
    rows.into_iter()
        .map(|row| {
            let id = row.try_get("id")?;
            let name = row.try_get("name")?;
            let emails: String = row.try_get("email")?;
            let last: Option<bool> = row.try_get("last")?;
            let valid = valid_email(&emails);
            let status = if !valid {
                "Invalid email"
            } else {
                match last {
                    Some(true) => "Sent",
                    Some(false) => "Failed",
                    None => "Never sent",
                }
            };
            Ok(Recipient::new(
                id,
                name,
                emails,
                status,
                valid,
                valid && last != Some(true),
            ))
        })
        .collect()
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
    sent: bool,
}

impl Written {
    fn label(&self) -> String {
        format!("Round {} · {} vs {}", self.round, self.gov, self.opp)
    }

    fn recipient(self) -> Recipient {
        let ready = !self.rfd.is_empty() && !self.emails.is_empty();
        let status = if self.rfd.is_empty() {
            "Missing RFD"
        } else if self.emails.is_empty() {
            "Missing email"
        } else if self.sent {
            "Sent"
        } else {
            "Never sent"
        };
        Recipient::new(
            self.id,
            self.label(),
            self.emails.join(", "),
            status,
            ready,
            ready && !self.sent,
        )
    }

    fn messages(self, tournament: &str) -> Vec<Delivery> {
        if self.rfd.is_empty() {
            return Vec::new();
        }
        let winner = if matches!(self.victor, 1 | 3) {
            &self.gov
        } else {
            &self.opp
        };
        let summary = format!(
            "{}\n\nWinner: {winner}\nJudge: {}\n\nWritten RFD\n{}",
            self.label(),
            self.chair,
            self.rfd
        );
        self.emails
            .into_iter()
            .map(|email| Delivery {
                log: Log::Written { id: self.id },
                email: Email::simple(
                    email,
                    format!("Round {} written RFD — {tournament}", self.round),
                    summary.clone(),
                ),
            })
            .collect()
    }
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
          r.gov_team_id, r.opp_team_id, EXISTS(SELECT 1 FROM tab_writtenrfdemaillog l WHERE l.round_id = r.id) AS sent
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_judge j ON j.id = r.chair_id
         WHERE r.round_number >= ? AND r.victor IN (1, 2, 3, 4)
         AND EXISTS(SELECT 1 FROM tab_roundstats s WHERE s.round_id = r.id)
         ORDER BY r.round_number DESC, g.name, o.name",
    ).bind(first).fetch_all(&mut *db).await?;
    let mut contacts: BTreeMap<i32, Vec<String>> = BTreeMap::new();
    for (team, email) in sqlx::query_as::<_, (i32, String)>(
        "SELECT m.team_id, d.email FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id
         WHERE d.email IS NOT NULL ORDER BY d.name, d.id",
    ).fetch_all(db).await? {
        if valid_email(email.trim()) { contacts.entry(team).or_default().push(email.trim().into()); }
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
                sent: row.try_get("sent")?,
            })
        })
        .collect()
}

pub async fn send(State(app): State<App>, headers: HeaderMap, body: Bytes) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Some(origin) = email::origin(&headers) else {
        return access::found(format!("{PATH}?error=true"));
    };
    let Ok(Some(mut db)) = deliveries::lock(&app.db).await else {
        return unavailable();
    };
    match prepare(&mut db, &fields, &origin).await {
        Ok(messages) if messages.is_empty() => access::found(format!("{PATH}?error=true")),
        Ok(messages) => {
            let count = messages.len();
            match deliveries::send_all(&mut db, email::service(), messages, "manual").await {
                Ok(true) => access::found(format!("{PATH}?sent={count}")),
                Ok(false) => access::found(format!("{PATH}?error=true")),
                Err(_) => unavailable(),
            }
        }
        Err(_) => unavailable(),
    }
}

async fn prepare(
    db: &mut MySqlConnection,
    fields: &Fields,
    origin: &str,
) -> Result<Vec<Delivery>, sqlx::Error> {
    let tournament = Settings::load(db, &["tournament_name"])
        .await?
        .string("tournament_name", "Tournament");
    let ids = |key| {
        fields
            .all(key)
            .iter()
            .filter_map(|value| value.parse::<i32>().ok())
            .filter(|&id| id > 0)
            .collect::<BTreeSet<_>>()
    };
    match fields.get("email_action") {
        "team_codes" => {
            deliveries::team_deliveries(
                db,
                &ids("team_ids").into_iter().collect::<Vec<_>>(),
                origin,
                &tournament,
            )
            .await
        }
        "judge_codes" | "" => {
            deliveries::judge_deliveries(
                db,
                &ids("judge_ids").into_iter().collect::<Vec<_>>(),
                origin,
                &tournament,
                false,
            )
            .await
        }
        "registration_confirmations" => {
            let mut output = Vec::new();
            for id in ids("registration_ids") {
                if let Some(message) = deliveries::confirmation(db, id, origin, &tournament).await?
                {
                    output.push(message);
                }
            }
            Ok(output)
        }
        "written_rfds" => {
            let ids = ids("round_ids");
            Ok(written_rows(db)
                .await?
                .into_iter()
                .filter(|round| ids.contains(&round.id))
                .flat_map(|round| round.messages(&tournament))
                .collect())
        }
        _ => Ok(Vec::new()),
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
