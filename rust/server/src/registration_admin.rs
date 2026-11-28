use axum::{
    Extension,
    body::Bytes,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::{DateTime, NaiveDateTime, TimeZone};
use chrono_tz::America::New_York;
use minijinja::context;
use sqlx::{AssertSqlSafe, MySqlConnection};

use crate::{
    App, access,
    admin_model_forms::AdminFormProof,
    administration, legacy_html,
    legacy_session::Actor,
    login::{self, TemplateCsrf},
    registration_admin_page::{self as view, FLAGS, Problems, Schedule, TIMES},
    registration_form::{Fields, integer_input, length, whole_number},
    registration_store::{self as store, Error},
    settings::{self, SettingValue},
    staff_pairing::{lock_round, lock_round_if_configured},
    staff_roster_notice, unavailable,
};

const MANAGE: &str = "/registration/manage/";
const FORMAT: &str = "%Y-%m-%dT%H:%M";
const SETTINGS_PROOF: &str = "registration settings edit";
const DELETE_PROOF: &str = "staff registration deletion";

pub(super) fn date_input(value: Option<NaiveDateTime>) -> String {
    value
        .map(|value| {
            New_York
                .from_utc_datetime(&value)
                .format(FORMAT)
                .to_string()
        })
        .unwrap_or_default()
}

fn parse_date(value: &str) -> Result<Option<NaiveDateTime>, String> {
    if value.is_empty() {
        return Ok(None);
    }
    if let Ok(value) = DateTime::parse_from_rfc3339(value) {
        return Ok(Some(value.naive_utc()));
    }
    for format in ["%Y-%m-%dT%H:%M%:z", "%Y-%m-%d %H:%M%:z"] {
        if let Ok(value) = DateTime::parse_from_str(value, format) {
            return Ok(Some(value.naive_utc()));
        }
    }
    let local = [
        FORMAT,
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S%.f",
    ]
    .iter()
    .find_map(|pattern| NaiveDateTime::parse_from_str(value, pattern).ok())
    .ok_or_else(|| "Enter a valid date/time.".to_owned())?;
    New_York.from_local_datetime(&local).single().map(|value| Some(value.naive_utc())).ok_or_else(|| {
        format!("{local} couldn’t be interpreted in time zone America/New_York; it may be ambiguous or it may not exist.")
    })
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    admin_page(&app, actor, &headers, None, &Problems::new()).await
}

async fn admin_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    posted: Option<&Fields>,
    problems: &Problems,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let actor = match access::settings_actor(&mut db, app, actor, headers).await {
        Ok(actor) => actor,
        Err(error) => {
            drop(db);
            return error.response(app, headers).await;
        }
    };
    let result = async {
        let schedule = view::schedule(&mut db).await?;
        let registrations = view::registrations(&mut db, None).await?;
        let (recent_changes, attached) = view::activities(&mut db).await?;
        let registration_links: Vec<_> = resources(&mut db, Resource::RegistrationLink).await?
            .into_iter().map(ResourceRow::context).collect();
        let shell = legacy_html::shell(&mut db, actor, MANAGE).await?;
        db.commit().await?;
        let registrations: Vec<_> = registrations.into_iter().map(|row| {
            let proof = AdminFormProof::for_target(headers, DELETE_PROOF, MANAGE, &row)
                .map(|proof| proof.token(app)).unwrap_or_default();
            row.context(view::hidden("registration_delete_revision", &proof), attached.get(&row.id).map_or(&[], Vec::as_slice))
        }).collect();
        let form = view::settings_form(&schedule, posted, problems);
        let revision = posted.map_or_else(
            || AdminFormProof::for_target(headers, SETTINGS_PROOF, MANAGE, &schedule)
                .map(|proof| proof.token(app)).unwrap_or_default(),
            |fields| fields.get("registration_settings_revision").to_owned(),
        );
        let messages = problems.get("__all__").map(|text| [context! { text, tags => "error", level => 40 }]);
        let status = if messages.is_some() { StatusCode::UNPROCESSABLE_ENTITY } else { StatusCode::OK };
        let mut response = staff_roster_notice::render(
            app, headers, MANAGE, "registration/admin_list.html", shell,
            context! {
                csrf_input => csrf.input(), form, registrations, recent_changes, registration_links, messages,
                config => schedule.context(),
                settings_revision => view::hidden("registration_settings_revision", &revision),
            },
            status,
        );
        csrf.set_cookie(&mut response, headers);
        Ok::<_, sqlx::Error>(response)
    }.await;
    result.unwrap_or_else(|_| unavailable())
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
    if !fields.get("registration_id").is_empty() {
        let result = match fields.get("registration_id").parse::<i32>() {
            Ok(id) => {
                delete_registration(
                    &app,
                    actor,
                    &headers,
                    id,
                    fields.get("registration_delete_revision"),
                )
                .await
            }
            Err(_) => Err(Error::NotFound),
        };
        return match result {
            Ok(()) => staff_roster_notice::saved(
                &app,
                &headers,
                MANAGE,
                "Registration and all associated data deleted.",
            ),
            Err(Error::NotFound) => {
                staff_roster_notice::failed(&app, &headers, MANAGE, "Registration not found.")
            }
            Err(Error::Invalid(message)) => {
                staff_roster_notice::failed(&app, &headers, MANAGE, message)
            }
            Err(Error::Access(response)) => *response,
            Err(_) => unavailable(),
        };
    }
    match update_settings(&app, actor, &headers, &fields).await {
        Ok(None) => {
            staff_roster_notice::saved(&app, &headers, MANAGE, "Registration settings updated!")
        }
        Ok(Some(problems)) => admin_page(&app, actor, &headers, Some(&fields), &problems).await,
        Err(Error::Access(response)) => *response,
        Err(_) => unavailable(),
    }
}

fn validate_settings(
    fields: &Fields,
    stored: &Schedule,
) -> Result<([Option<NaiveDateTime>; 4], u16), Problems> {
    let mut problems = Problems::new();
    let dates = std::array::from_fn(|index| {
        let key = TIMES[index];
        let posted = fields.get(key);
        let old = stored.dates[index];
        if posted == date_input(old) {
            return old;
        }
        parse_date(posted).unwrap_or_else(|error| {
            problems.insert(key, error);
            None
        })
    });
    for close in [1, 3] {
        if dates[close - 1]
            .zip(dates[close])
            .is_some_and(|(opens, closes)| opens >= closes)
        {
            problems.insert(
                TIMES[close],
                "Closing time must be after the opening time.".into(),
            );
        }
    }
    let key = "disc_scratch_quantity";
    let quantity = fields.get(key);
    let limit = if quantity.is_empty() {
        0
    } else {
        integer_input(quantity)
            .ok()
            .and_then(|number| u16::try_from(number).ok())
            .unwrap_or_else(|| {
                problems.insert(key, "Enter a valid disc scratch quantity.".into());
                0
            })
    };
    if fields.flag("disc_scratches_open")
        && !stored.judges_public
        && !fields.flag("confirm_judge_list_publication")
    {
        problems.insert(
            "disc_scratches_open",
            "Confirm publication of the judge list to open disc scratches.".into(),
        );
    }
    if problems.is_empty() {
        Ok((dates, limit))
    } else {
        Err(problems)
    }
}

async fn update_settings(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    fields: &Fields,
) -> Result<Option<Problems>, Error> {
    let mut tx = app.db.begin().await?;
    lock_round(&mut tx).await?;
    store::lock_config(&mut tx).await?;
    if let Err(error) = access::settings_actor(&mut tx, app, actor, headers).await {
        tx.rollback().await?;
        return Err(Error::Access(Box::new(error.response(app, headers).await)));
    }
    let stored = view::schedule(&mut tx).await?;
    let verified = AdminFormProof::for_target(headers, SETTINGS_PROOF, MANAGE, &stored)
        .is_some_and(|proof| proof.verified(app, fields.get("registration_settings_revision")));
    if !verified {
        return Ok(Some(Problems::from([(
            "__all__",
            "Registration settings changed. Reload the page before saving.".into(),
        )])));
    }
    let (dates, limit) = match validate_settings(fields, &stored) {
        Ok(parsed) => parsed,
        Err(problems) => return Ok(Some(problems)),
    };
    let flags = FLAGS.map(|key| fields.flag(key));
    if flags[4] && !stored.judges_public {
        settings::put(&mut tx, "judges_public", SettingValue::Number(1)).await?;
    }
    sqlx::query(
        "UPDATE registration_registrationconfig SET allow_new_registrations = ?, allow_registration_edits = ?,
          new_registrations_open_at = ?, new_registrations_close_at = ?, registration_edits_open_at = ?,
          registration_edits_close_at = ?, crash_housing_enabled = ?, team_name_changes_allowed = ?,
          disc_scratches_open = ?, disc_scratch_quantity = ?, updated_at = UTC_TIMESTAMP(6) WHERE id = 1",
    ).bind(flags[0]).bind(flags[1]).bind(dates[0]).bind(dates[1]).bind(dates[2]).bind(dates[3])
        .bind(flags[2]).bind(flags[3]).bind(flags[4]).bind(limit).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(None)
}

async fn delete_registration(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: i32,
    revision: &str,
) -> Result<(), Error> {
    let mut tx = app.db.begin().await?;
    lock_round(&mut tx).await?;
    store::lock_config(&mut tx).await?;
    if let Err(error) = access::settings_actor(&mut tx, app, actor, headers).await {
        tx.rollback().await?;
        return Err(Error::Access(Box::new(error.response(app, headers).await)));
    }
    let row = view::registrations(&mut tx, Some(id))
        .await?
        .pop()
        .ok_or(Error::NotFound)?;
    let verified = AdminFormProof::for_target(headers, DELETE_PROOF, MANAGE, &row)
        .is_some_and(|proof| proof.verified(app, revision));
    if !verified {
        return Err(Error::Invalid(
            "This registration changed. Reload the page before deleting it.",
        ));
    }
    let has_results = sqlx::query_scalar::<_, bool>(
        "WITH teams AS (SELECT id FROM tab_team WHERE registration_id = ?),
              judges AS (SELECT id FROM tab_judge WHERE registration_id = ?)
         SELECT EXISTS(
             SELECT 1 FROM tab_round
             WHERE gov_team_id IN (SELECT id FROM teams) OR opp_team_id IN (SELECT id FROM teams)
                OR chair_id IN (SELECT id FROM judges)
             UNION ALL
             SELECT 1 FROM tab_outround
             WHERE gov_team_id IN (SELECT id FROM teams) OR opp_team_id IN (SELECT id FROM teams)
                OR chair_id IN (SELECT id FROM judges)
             UNION ALL
             SELECT 1 FROM tab_round_judges WHERE judge_id IN (SELECT id FROM judges)
             UNION ALL
             SELECT 1 FROM tab_outround_judges WHERE judge_id IN (SELECT id FROM judges)
             UNION ALL
             SELECT 1 FROM tab_bye WHERE bye_team_id IN (SELECT id FROM teams)
             UNION ALL
             SELECT 1 FROM tab_noshow WHERE no_show_team_id IN (SELECT id FROM teams)
             UNION ALL
             SELECT 1 FROM tab_breakingteam WHERE team_id IN (SELECT id FROM teams)
             UNION ALL
             SELECT 1 FROM tab_outroundchallenge
             WHERE challenger_id IN (SELECT id FROM teams) OR challenged_id IN (SELECT id FROM teams)
             UNION ALL
             SELECT 1 FROM tab_publishedteamplacement WHERE team_id IN (SELECT id FROM teams)
             UNION ALL
             SELECT 1 FROM tab_manualjudgeassignment WHERE judge_id IN (SELECT id FROM judges)
             UNION ALL
             SELECT 1 FROM tab_roundstats stats
             WHERE EXISTS(SELECT 1 FROM tab_team_debaters membership
                          WHERE membership.debater_id = stats.debater_id
                            AND membership.team_id IN (SELECT id FROM teams))
               AND NOT EXISTS(SELECT 1 FROM tab_team_debaters membership
                              WHERE membership.debater_id = stats.debater_id
                                AND membership.team_id NOT IN (SELECT id FROM teams))
         )",
    )
    .bind(id)
    .bind(id)
    .fetch_one(&mut *tx)
    .await?;
    if has_results {
        return Err(Error::Invalid(
            "This registration has teams or judges in a pairing or tournament results. Review its dependencies in the admin interface.",
        ));
    }
    store::delete(&mut tx, id).await?;
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

    fn path(self) -> &'static str {
        match self {
            Self::RegistrationLink => MANAGE,
            Self::InfoLink | Self::InfoText => crate::homepage_admin::PATH,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::RegistrationLink => "Registration link",
            Self::InfoLink => "Info link",
            Self::InfoText => "Text information",
        }
    }

    fn is_text(self) -> bool {
        matches!(self, Self::InfoText)
    }
}

pub struct ResourceRow {
    id: i32,
    title: String,
    url: String,
    description: String,
    display_order: u32,
    active: bool,
}

impl ResourceRow {
    pub(super) fn context(self) -> minijinja::Value {
        context! { pk => self.id, title => self.title, url => self.url, description => &self.description,
        text => self.description, display_order => self.display_order, is_active => self.active }
    }
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
    Extension(actor): Extension<Actor>,
    Path(kind): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(kind) = Resource::link(&kind) else {
        return administration::not_found(State(app), Extension(actor)).await;
    };
    save_resource(&app, actor, &headers, &body, kind, None, false).await
}

pub async fn link_edit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((kind, id)): Path<(String, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(kind) = Resource::link(&kind) else {
        return administration::not_found(State(app), Extension(actor)).await;
    };
    save_resource(&app, actor, &headers, &body, kind, Some(id), false).await
}

pub async fn link_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((kind, id)): Path<(String, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(kind) = Resource::link(&kind) else {
        return administration::not_found(State(app), Extension(actor)).await;
    };
    save_resource(&app, actor, &headers, &body, kind, Some(id), true).await
}

pub async fn text_add(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    save_resource(
        &app,
        actor,
        &headers,
        &body,
        Resource::InfoText,
        None,
        false,
    )
    .await
}

pub async fn text_edit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    save_resource(
        &app,
        actor,
        &headers,
        &body,
        Resource::InfoText,
        Some(id),
        false,
    )
    .await
}

pub async fn text_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    save_resource(
        &app,
        actor,
        &headers,
        &body,
        Resource::InfoText,
        Some(id),
        true,
    )
    .await
}

fn validated_display_order(
    kind: Resource,
    title: &str,
    address: &str,
    text: &str,
    order: &str,
) -> Result<u32, String> {
    let mut errors = Vec::new();
    let mut validate = |label, value: &str, limit, required| {
        let count = value.chars().count();
        if count == 0 && required {
            errors.push(format!("{label}: This field is required."));
        } else if count > limit {
            errors.push(format!(
                "{label}: Ensure this value has at most {limit} characters (it has {count})."
            ));
        }
        if value.contains('\0') {
            errors.push(format!("{label}: Null characters are not allowed."));
        }
    };
    validate("Title", title, 80, true);
    if kind.is_text() {
        validate("Text", text, 500, true);
    } else {
        validate("Url", address, 500, true);
        validate("Description", text, 160, false);
    }
    let parsed = match whole_number(order).parse::<i128>() {
        _ if order.is_empty() => Err("This field is required."),
        Err(_) => Err("Enter a whole number."),
        Ok(value) if value < 0 => Err("Ensure this value is greater than or equal to 0."),
        Ok(value) => u32::try_from(value)
            .map_err(|_| "Ensure this value is less than or equal to 4294967295."),
    };
    if let Err(message) = parsed {
        errors.push(format!("Display order: {message}"));
    }
    if !kind.is_text()
        && !address.is_empty()
        && !address.contains('\0')
        && length(address, 500)
        && !valid_http_url(address)
    {
        errors.push("Url: Enter a valid URL. Url: Enter a valid URL.".into());
    }
    if errors.is_empty() {
        return parsed.map_err(str::to_owned);
    }
    Err(errors.join(" "))
}

async fn save_resource(
    app: &App,
    actor: Actor,
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
    let saved = |action| {
        staff_roster_notice::saved(
            app,
            headers,
            kind.path(),
            &format!("{} {action}.", kind.label()),
        )
    };
    let Ok(mut tx) = app.db.begin().await else {
        return unavailable();
    };
    if lock_round_if_configured(&mut tx).await.is_err() {
        return unavailable();
    }
    let actor = match access::settings_actor(&mut tx, app, actor, headers).await {
        Ok(actor) => actor,
        Err(error) => {
            drop(tx);
            return error.response(app, headers).await;
        }
    };
    if delete {
        let deleted = sqlx::query(AssertSqlSafe(format!(
            "DELETE FROM {} WHERE id = ?",
            kind.table()
        )))
        .bind(id)
        .execute(&mut *tx)
        .await;
        return match deleted {
            Ok(result) if result.rows_affected() > 0 => match tx.commit().await {
                Ok(()) => saved("removed"),
                Err(_) => unavailable(),
            },
            Ok(_) => {
                drop(tx);
                administration::not_found(State(app.clone()), Extension(actor)).await
            }
            Err(_) => unavailable(),
        };
    }
    let (title, address, text) = (
        fields.get("title"),
        fields.get("url"),
        fields.get(if kind.is_text() {
            "text"
        } else {
            "description"
        }),
    );
    if let Some(id) = id {
        match sqlx::query_scalar::<_, i32>(AssertSqlSafe(format!(
            "SELECT id FROM {} WHERE id = ? FOR UPDATE",
            kind.table(),
        )))
        .bind(id)
        .fetch_optional(&mut *tx)
        .await
        {
            Ok(Some(_)) => (),
            Ok(None) => {
                drop(tx);
                return administration::not_found(State(app.clone()), Extension(actor)).await;
            }
            Err(_) => return unavailable(),
        }
    }
    let order =
        match validated_display_order(kind, title, address, text, fields.get("display_order")) {
            Ok(order) => order,
            Err(message) => {
                return staff_roster_notice::failed(app, headers, kind.path(), &message);
            }
        };
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
    match query.execute(&mut *tx).await {
        Ok(_) => match tx.commit().await {
            Ok(()) => saved(if id.is_some() { "updated" } else { "added" }),
            Err(_) => unavailable(),
        },
        Err(_) => unavailable(),
    }
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
