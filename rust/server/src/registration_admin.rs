use axum::{
    Extension,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::{NaiveDateTime, TimeZone};
use chrono_tz::America::New_York;
use minijinja::context;
use serde::Serialize;
use sqlx::{AssertSqlSafe, MySqlConnection, Row};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_form::{Fields, length},
    registration_store::{self as store, Error},
    settings::{self, SettingValue, Settings},
    staff_pairing::lock_round,
    unavailable,
};

const MANAGE: &str = "/registration/manage/";
const HOMEPAGE: &str = "/settings/public-homepage/";
const FORMAT: &str = "%Y-%m-%dT%H:%M";
const FLAGS: &[(&str, &str)] = &[
    ("allow_new_registrations", "Allow new registrations"),
    ("allow_registration_edits", "Allow registration updates"),
    ("crash_housing_enabled", "Ask about crash housing"),
    ("team_name_changes_allowed", "Allow team name changes"),
    ("disc_scratches_open", "Disc scratches open"),
];
const TIMES: &[(&str, &str)] = &[
    ("new_registrations_open_at", "New registrations open"),
    ("new_registrations_close_at", "New registrations close"),
    ("registration_edits_open_at", "Registration updates open"),
    ("registration_edits_close_at", "Registration updates close"),
];

fn date_input(value: Option<NaiveDateTime>) -> String {
    value
        .map(|value| {
            New_York
                .from_utc_datetime(&value)
                .format(FORMAT)
                .to_string()
        })
        .unwrap_or_default()
}

fn parse_date(value: &str) -> Result<Option<NaiveDateTime>, Error> {
    if value.is_empty() {
        return Ok(None);
    }
    let local = NaiveDateTime::parse_from_str(value, FORMAT)
        .ok()
        .and_then(|value| New_York.from_local_datetime(&value).single())
        .ok_or(Error::Invalid(
            "Enter an unambiguous Eastern date and time.",
        ))?;
    Ok(Some(local.naive_utc()))
}

#[derive(serde::Deserialize)]
pub struct Feedback {
    saved: Option<u8>,
    error: Option<u8>,
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(feedback): Query<Feedback>,
) -> Response {
    let message = if feedback.saved == Some(1) {
        Some(("Registration settings updated.", false))
    } else if feedback.saved == Some(2) {
        Some(("Registration and all associated data deleted.", false))
    } else if feedback.saved == Some(3) {
        Some(("Registration link updated.", false))
    } else if feedback.error.is_some() {
        Some((
            "Enter a title, a valid HTTP(S) link and a display order.",
            true,
        ))
    } else {
        None
    };
    admin_page(&app, actor, &headers, message, None).await
}

async fn admin_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    feedback: Option<(&str, bool)>,
    posted: Option<&Fields>,
) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    match render(app, actor, &csrf, feedback, posted).await {
        Ok(response) => html::form_response(response, headers, &csrf),
        Err(_) => unavailable(),
    }
}

async fn render(
    app: &App,
    actor: Actor,
    csrf: &str,
    feedback: Option<(&str, bool)>,
    posted: Option<&Fields>,
) -> Result<Response, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    let config = sqlx::query("SELECT * FROM registration_registrationconfig WHERE id = 1")
        .fetch_optional(&mut *db)
        .await?;
    let mut flags = Vec::new();
    for &(key, label) in FLAGS {
        let stored: bool = if let Some(config) = &config {
            config.try_get(key)?
        } else {
            matches!(key, "allow_new_registrations" | "allow_registration_edits")
        };
        flags.push((key, label, posted.map_or(stored, |fields| fields.flag(key))));
    }
    let mut dates = Vec::new();
    for &(key, label) in TIMES {
        let stored = config
            .as_ref()
            .map(|row| row.try_get::<Option<NaiveDateTime>, _>(key))
            .transpose()?
            .flatten();
        dates.push((
            key,
            label,
            posted.map_or_else(|| date_input(stored), |fields| fields.get(key).into()),
        ));
    }
    let limit: u16 = config
        .as_ref()
        .map(|row| row.try_get("disc_scratch_quantity"))
        .transpose()?
        .unwrap_or_default();
    let limit = posted.map_or_else(
        || limit.to_string(),
        |fields| fields.get("disc_scratch_quantity").to_string(),
    );
    let judges_public = Settings::load(&mut db, &["judges_public"])
        .await?
        .flag("judges_public", false);

    let registrations = sqlx::query_as::<_, (i32, String, String, String, i64, i64)>(
        "SELECT r.id, r.herokunator_code, s.name, r.email,
           (SELECT COUNT(*) FROM tab_team t WHERE t.registration_id = r.id),
           (SELECT COUNT(*) FROM tab_judge j WHERE j.registration_id = r.id)
         FROM registration_registration r JOIN tab_school s ON r.school_id = s.id ORDER BY r.created_at DESC, r.id DESC",
    ).fetch_all(&mut *db).await?;
    let audits = sqlx::query_as::<_, (String, String, String, String, String, String)>(
        "SELECT school_name, registration_code, action, summary, DATE_FORMAT(created_at, '%Y-%m-%d %H:%i UTC'),
          CAST(changes AS CHAR) FROM registration_registrationchangelog ORDER BY created_at DESC, id DESC LIMIT 25",
    ).fetch_all(&mut *db).await?;
    let links = resources(&mut db, Resource::RegistrationLink).await?;
    let (message, has_error) =
        feedback.map_or((None, false), |(message, error)| (Some(message), error));
    Ok(html::render(
        "registration_admin.html",
        context! {
            title => "Registration Management", nav => Navigation::new(actor, true), csrf, flags, dates, limit,
            registrations, audits, links, judges_public, message, has_error,
        },
        if has_error {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    ))
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let (result, saved) = if fields.get("registration_id").is_empty() {
        (update_settings(&app, &fields).await, 1)
    } else {
        let Ok(id) = fields.get("registration_id").parse::<i32>() else {
            return missing();
        };
        (store::delete(&app.db, id).await, 2)
    };
    match result {
        Ok(()) => access::found(format!("{MANAGE}?saved={saved}")),
        Err(Error::Invalid(message)) => {
            admin_page(&app, actor, &headers, Some((message, true)), Some(&fields)).await
        }
        Err(Error::NotFound) => missing(),
        Err(_) => unavailable(),
    }
}

async fn update_settings(app: &App, fields: &Fields) -> Result<(), Error> {
    let new_open = parse_date(fields.get("new_registrations_open_at"))?;
    let new_close = parse_date(fields.get("new_registrations_close_at"))?;
    let edits_open = parse_date(fields.get("registration_edits_open_at"))?;
    let edits_close = parse_date(fields.get("registration_edits_close_at"))?;
    if [(new_open, new_close), (edits_open, edits_close)]
        .into_iter()
        .any(|(opens, closes)| {
            opens
                .zip(closes)
                .is_some_and(|(opens, closes)| opens >= closes)
        })
    {
        return Err(Error::Invalid(
            "Closing time must be after the opening time.",
        ));
    }
    let quantity = fields.get("disc_scratch_quantity");
    let limit = if quantity.is_empty() {
        0
    } else {
        quantity
            .parse::<u16>()
            .map_err(|_| Error::Invalid("Enter a valid disc scratch quantity."))?
    };
    let disc_open = fields.flag("disc_scratches_open");
    let mut tx = app.db.begin().await?;
    lock_round(&mut tx).await?;
    store::lock_config(&mut tx).await?;
    let judges_public = Settings::load(&mut tx, &["judges_public"])
        .await?
        .flag("judges_public", false);
    if disc_open && !judges_public {
        if !fields.flag("confirm_judge_list_publication") {
            return Err(Error::Invalid(
                "Confirm publication of the judge list to open disc scratches.",
            ));
        }
        settings::put(&mut tx, "judges_public", SettingValue::Number(1)).await?;
    }
    sqlx::query(
        "UPDATE registration_registrationconfig SET allow_new_registrations = ?, allow_registration_edits = ?,
          new_registrations_open_at = ?, new_registrations_close_at = ?, registration_edits_open_at = ?,
          registration_edits_close_at = ?, crash_housing_enabled = ?, team_name_changes_allowed = ?,
          disc_scratches_open = ?, disc_scratch_quantity = ?, updated_at = UTC_TIMESTAMP(6) WHERE id = 1",
    ).bind(fields.flag("allow_new_registrations")).bind(fields.flag("allow_registration_edits"))
        .bind(new_open).bind(new_close).bind(edits_open).bind(edits_close)
        .bind(fields.flag("crash_housing_enabled")).bind(fields.flag("team_name_changes_allowed"))
        .bind(disc_open).bind(limit).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(())
}

pub(super) fn valid_http_url(value: &str) -> bool {
    let Some((scheme, rest)) = value.split_once("://") else {
        return false;
    };
    if !(scheme.eq_ignore_ascii_case("http") || scheme.eq_ignore_ascii_case("https"))
        || value
            .chars()
            .any(|character| character.is_whitespace() || character.is_control())
    {
        return false;
    }
    let Some(authority) = rest.split(['/', '?', '#']).next() else {
        return false;
    };
    if authority.contains('\\') || url::Url::parse(value).is_err() {
        return false;
    }
    let host_port = if let Some((credentials, host_port)) = authority.split_once('@') {
        let (user, password) = credentials.split_once(':').unwrap_or((credentials, ""));
        if user.is_empty() || password.contains(':') {
            return false;
        }
        host_port
    } else {
        authority
    };
    if host_port.contains('@') {
        return false;
    }
    let (host, port) = if let Some(bracketed) = host_port.strip_prefix('[') {
        let Some((ipv6, suffix)) = bracketed.split_once(']') else {
            return false;
        };
        if ipv6.parse::<std::net::Ipv6Addr>().is_err() {
            return false;
        }
        return suffix.is_empty() || suffix.strip_prefix(':').is_some_and(valid_url_port);
    } else {
        host_port
            .split_once(':')
            .map_or((host_port, None), |(host, port)| (host, Some(port)))
    };
    port.is_none_or(valid_url_port) && valid_url_host(host)
}

fn valid_url_port(port: &str) -> bool {
    (1..=5).contains(&port.len()) && port.bytes().all(|byte| byte.is_ascii_digit())
}

fn valid_url_host(host: &str) -> bool {
    if host.eq_ignore_ascii_case("localhost") || host.parse::<std::net::Ipv4Addr>().is_ok() {
        return true;
    }
    if host.chars().count() > 253 {
        return false;
    }
    let host = host.strip_suffix('.').unwrap_or(host);
    let Some((subdomains, tld)) = host.rsplit_once('.') else {
        return false;
    };
    let label = |label: &str, valid_character: fn(char) -> bool| {
        !label.is_empty()
            && label.chars().count() <= 63
            && !label.starts_with('-')
            && !label.ends_with('-')
            && label.chars().all(valid_character)
    };
    let top_level_valid = if tld
        .get(..4)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("xn--"))
    {
        (5..=63).contains(&tld.len()) && tld[4..].bytes().all(|byte| byte.is_ascii_alphanumeric())
    } else {
        tld.chars().count() >= 2
            && label(tld, |character| {
                character.is_ascii_alphabetic()
                    || ('\u{a1}'..='\u{ffff}').contains(&character)
                    || character == '-'
            })
    };
    top_level_valid
        && subdomains.split('.').all(|value| {
            label(value, |character| {
                character.is_ascii_alphanumeric()
                    || ('\u{a1}'..='\u{ffff}').contains(&character)
                    || character == '-'
            })
        })
}

#[derive(Clone, Copy)]
pub enum Resource {
    RegistrationLink,
    InfoLink,
    InfoText,
}

impl Resource {
    fn link(kind: &str) -> Option<Self> {
        match kind {
            "registration" => Some(Self::RegistrationLink),
            "info" => Some(Self::InfoLink),
            _ => None,
        }
    }

    fn table(self) -> &'static str {
        match self {
            Self::RegistrationLink => "registration_registrationlink",
            Self::InfoLink => "registration_infolink",
            Self::InfoText => "registration_infotext",
        }
    }

    fn next(self, success: bool) -> String {
        let path = if matches!(self, Self::RegistrationLink) {
            MANAGE
        } else {
            HOMEPAGE
        };
        let state = if success { "saved=3" } else { "error=1" };
        format!("{path}?{state}")
    }

    fn is_text(self) -> bool {
        matches!(self, Self::InfoText)
    }
}

#[derive(Serialize)]
pub struct ResourceRow {
    id: i32,
    title: String,
    url: String,
    description: String,
    display_order: u32,
    active: bool,
}

pub async fn resources(
    db: &mut MySqlConnection,
    kind: Resource,
) -> Result<Vec<ResourceRow>, sqlx::Error> {
    let columns = if kind.is_text() {
        "'', text"
    } else {
        "url, description"
    };
    let sql = format!(
        "SELECT id, title, {columns}, display_order, is_active FROM {} ORDER BY display_order, created_at, id",
        kind.table(),
    );
    Ok(
        sqlx::query_as::<_, (i32, String, String, String, u32, bool)>(AssertSqlSafe(sql))
            .fetch_all(db)
            .await?
            .into_iter()
            .map(
                |(id, title, url, description, display_order, active)| ResourceRow {
                    id,
                    title,
                    url,
                    description,
                    display_order,
                    active,
                },
            )
            .collect(),
    )
}

pub async fn link_add(
    State(app): State<App>,
    Path(kind): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(kind) = Resource::link(&kind) else {
        return missing();
    };
    save_resource(&app, &headers, &body, kind, None, false).await
}

pub async fn link_edit(
    State(app): State<App>,
    Path((kind, id)): Path<(String, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(kind) = Resource::link(&kind) else {
        return missing();
    };
    save_resource(&app, &headers, &body, kind, Some(id), false).await
}

pub async fn link_delete(
    State(app): State<App>,
    Path((kind, id)): Path<(String, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(kind) = Resource::link(&kind) else {
        return missing();
    };
    save_resource(&app, &headers, &body, kind, Some(id), true).await
}

pub async fn text_add(State(app): State<App>, headers: HeaderMap, body: Bytes) -> Response {
    save_resource(&app, &headers, &body, Resource::InfoText, None, false).await
}

pub async fn text_edit(
    State(app): State<App>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    save_resource(&app, &headers, &body, Resource::InfoText, Some(id), false).await
}

pub async fn text_delete(
    State(app): State<App>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    save_resource(&app, &headers, &body, Resource::InfoText, Some(id), true).await
}

async fn save_resource(
    app: &App,
    headers: &HeaderMap,
    body: &[u8],
    kind: Resource,
    id: Option<i32>,
    delete: bool,
) -> Response {
    let fields = Fields::parse(body);
    if !login::valid_csrf(headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    if delete {
        let deleted = sqlx::query(AssertSqlSafe(format!(
            "DELETE FROM {} WHERE id = ?",
            kind.table()
        )))
        .bind(id)
        .execute(&app.db)
        .await;
        return match deleted {
            Ok(result) if result.rows_affected() > 0 => access::found(kind.next(true)),
            Ok(_) => missing(),
            Err(_) => unavailable(),
        };
    }
    let title = fields.get("title");
    let address = fields.get("url");
    let text = fields.get(if kind.is_text() {
        "text"
    } else {
        "description"
    });
    let order = fields.get("display_order");
    let source = if order.is_empty() {
        Some(0)
    } else {
        order.parse::<u32>().ok()
    };
    let Some(order) = source else {
        return access::found(kind.next(false));
    };
    let content_ok = if kind.is_text() {
        !text.is_empty() && length(text, 500)
    } else {
        length(text, 160)
            && length(address, 500)
            && url::Url::parse(address).is_ok_and(|url| {
                matches!(url.scheme(), "http" | "https")
                    && url.host().is_some()
                    && url.username().is_empty()
            })
    };
    if title.is_empty() || !length(title, 80) || !content_ok {
        return access::found(kind.next(false));
    }
    let active = fields.flag("is_active");
    let columns = if kind.is_text() {
        "text = ?"
    } else {
        "description = ?, url = ?"
    };
    let from = format!(
        "{} SET title = ?, display_order = ?, is_active = ?, updated_at = UTC_TIMESTAMP(6), {columns}",
        kind.table()
    );
    let statement = if id.is_some() {
        format!("UPDATE {from} WHERE id = ?")
    } else {
        format!("INSERT INTO {from}, created_at = UTC_TIMESTAMP(6)")
    };
    let mut query = sqlx::query(AssertSqlSafe(statement))
        .bind(title)
        .bind(order)
        .bind(active)
        .bind(text);
    if !kind.is_text() {
        query = query.bind(address);
    }
    if let Some(id) = id {
        query = query.bind(id);
    }
    match query.execute(&app.db).await {
        Ok(result) if result.rows_affected() == 0 && id.is_some() => missing(),
        Ok(_) => access::found(kind.next(true)),
        Err(_) => unavailable(),
    }
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Item not found."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eastern_schedule_rejects_missing_and_ambiguous_dst_times() {
        assert!(parse_date("2026-03-08T02:30").is_err());
        assert!(parse_date("2026-11-01T01:30").is_err());
        for (clock, utc) in [
            ("2026-01-17T17:30", "2026-01-17T22:30:00"),
            ("2026-07-17T17:30", "2026-07-17T21:30:00"),
        ] {
            let date = parse_date(clock).unwrap();
            assert_eq!(date.unwrap().to_string().replace(' ', "T"), utc);
            assert_eq!(date_input(date), clock);
        }
    }
}
