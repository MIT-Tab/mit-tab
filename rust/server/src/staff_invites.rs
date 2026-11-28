use axum::{
    Extension, Form,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{NaiveDate, NaiveDateTime, Utc};
use minijinja::context;
use ring::{digest, hmac};
use serde::Deserialize;
use sqlx::{MySqlConnection, Row};

use crate::{
    App, access,
    email::{self, Email},
    html::{self, Navigation},
    legacy_session::Actor,
    login, passwords,
    registration_form::{length, valid_email},
    settings::{self, SettingValue, Settings},
    staff_pairing::lock_round,
    unavailable,
};

const PATH: &str = "/staff/invite/";
const DONE: &str = "/public/staff-invite/complete/";
pub const INVITE_COUNT: &str = "start_tournament_staff_invites_sent";

pub const ROLES: &[(&str, &str, Option<&str>)] = &[
    ("full_tab_staff", "Full power tab staff", None),
    (
        "add_scratches",
        "Add scratches",
        Some("MIT-TAB: Add scratches"),
    ),
    (
        "manage_scratches",
        "Create and view scratches",
        Some("MIT-TAB: Create and view scratches"),
    ),
    (
        "checkin_helper",
        "Check-in helper",
        Some("MIT-TAB: Check-in helper"),
    ),
    (
        "data_entry_helper",
        "Data entry helper",
        Some("MIT-TAB: Data entry helper"),
    ),
    (
        "tab_assistant",
        "Tab assistant",
        Some("MIT-TAB: Tab assistant"),
    ),
];

#[derive(Deserialize)]
pub struct InviteForm {
    #[serde(default)]
    csrfmiddlewaretoken: String,
    pub email: String,
    pub username: String,
    #[serde(default)]
    pub permission_preset: String,
}

impl InviteForm {
    pub fn setup(email: &str, username: &str, preset: &str) -> Self {
        Self {
            csrfmiddlewaretoken: String::new(),
            email: email.into(),
            username: username.into(),
            permission_preset: preset.into(),
        }
    }
}

#[derive(Deserialize)]
pub struct Feedback {
    sent: Option<bool>,
}

pub async fn page(
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(query): Query<Feedback>,
) -> Response {
    if !superuser(actor) {
        return access::access_denied();
    }
    render(actor, &headers, None, query.sent == Some(true), None)
}

fn superuser(actor: Actor) -> bool {
    matches!(
        actor,
        Actor::User {
            board: false,
            superuser: true,
            ..
        }
    )
}

fn render(
    actor: Actor,
    headers: &HeaderMap,
    error: Option<&str>,
    sent: bool,
    form: Option<&InviteForm>,
) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let response = html::render(
        "staff_invite.html",
        context! {
            title => "Invite Tab Staff", nav => Navigation::new(actor, true), csrf, roles => ROLES,
            sent, error, email => form.map(|form| &form.email), username => form.map(|form| &form.username),
            choice => form.map(|form| form.permission_preset.as_str()).filter(|role| !role.is_empty()).unwrap_or("full_tab_staff"),
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<InviteForm>,
) -> Response {
    if !superuser(actor) {
        return access::access_denied();
    }
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    match send(&app, &headers, actor, &form, false).await {
        Ok(_) => access::found(format!("{PATH}?sent=true")),
        Err(error) => render(actor, &headers, Some(error), false, Some(&form)),
    }
}

pub async fn send(
    app: &App,
    headers: &HeaderMap,
    actor: Actor,
    input: &InviteForm,
    setup: bool,
) -> Result<i32, &'static str> {
    const DATABASE: &str = "Could not prepare the staff invite.";
    let Actor::User {
        id: inviter,
        board: false,
        superuser: true,
        ..
    } = actor
    else {
        return Err("Staff invitations require full tab staff access.");
    };
    let username = input.username.trim();
    let email = input.email.trim();
    if !valid_email(email)
        || !length(username, 150)
        || username.is_empty()
        || !username
            .chars()
            .all(|ch| ch.is_alphanumeric() || "@.+-_".contains(ch))
    {
        return Err("Enter a valid email address and username.");
    }
    let preset = if input.permission_preset.is_empty() {
        "full_tab_staff"
    } else {
        input.permission_preset.as_str()
    };
    let (_, _, group) = ROLES
        .iter()
        .find(|&&(key, _, _)| key == preset)
        .ok_or("Select a staff permission preset.")?;
    let origin = email::origin(headers).ok_or("The tournament URL could not be determined.")?;
    let mut tx = app.db.begin().await.map_err(|_| DATABASE)?;
    lock_round(&mut tx).await.map_err(|_| DATABASE)?;
    let settings = Settings::load(
        &mut tx,
        &["start_tournament_wizard_completed", INVITE_COUNT],
    )
    .await
    .map_err(|_| DATABASE)?;
    let completed = ["1", "yes", "true", "on"].contains(
        &settings
            .string("start_tournament_wizard_completed", "1")
            .to_ascii_lowercase()
            .trim(),
    );
    if setup && completed {
        return Err("Tournament setup is already complete.");
    }

    let conflicting = sqlx::query(
        "SELECT u.id, u.username, u.email, u.is_staff, EXISTS(
             SELECT 1 FROM auth_user_groups m JOIN auth_group g ON g.id = m.group_id
             WHERE m.user_id = u.id AND g.name = 'APDA Board') AS is_board
         FROM auth_user u WHERE u.email = ? OR u.username = ? ORDER BY u.id FOR UPDATE",
    )
    .bind(email)
    .bind(username)
    .fetch_all(&mut *tx)
    .await
    .map_err(|_| DATABASE)?;
    if conflicting.len() > 1 {
        return Err("The email or username already belongs to a different account.");
    }
    let id = if let Some(row) = conflicting.first() {
        let id: i32 = row.try_get("id").map_err(|_| DATABASE)?;
        let staff: bool = row.try_get("is_staff").map_err(|_| DATABASE)?;
        let board: bool = row.try_get("is_board").map_err(|_| DATABASE)?;
        let previous_name: String = row.try_get("username").map_err(|_| DATABASE)?;
        let previous_email: String = row.try_get("email").map_err(|_| DATABASE)?;
        if !staff || board {
            return Err("The existing account cannot be invited as tab staff.");
        }
        if id == inviter {
            return Err("Enter another staff member’s email and username.");
        }
        if previous_name != username || !previous_email.eq_ignore_ascii_case(email) {
            return Err("The email or username already belongs to a different account.");
        }
        sqlx::query("UPDATE auth_user SET is_active = TRUE, is_superuser = ? WHERE id = ?")
            .bind(group.is_none())
            .bind(id)
            .execute(&mut *tx)
            .await
            .map_err(|_| DATABASE)?;
        id
    } else {
        let unusable = format!("!{}", passwords::random_secret()?);
        sqlx::query(
            "INSERT INTO auth_user (username, password, email, first_name, last_name, is_staff, is_superuser, is_active, date_joined)
             VALUES (?, ?, ?, '', '', TRUE, ?, TRUE, UTC_TIMESTAMP(6))",
        ).bind(username).bind(unusable).bind(email).bind(group.is_none()).execute(&mut *tx).await.map_err(|_| DATABASE)?
            .last_insert_id() as i32
    };
    sqlx::query(
        "DELETE m FROM auth_user_groups m JOIN auth_group g ON m.group_id = g.id WHERE m.user_id = ?
         AND g.name IN ('MIT-TAB: Add scratches', 'MIT-TAB: Create and view scratches', 'MIT-TAB: Check-in helper',
                        'MIT-TAB: Data entry helper', 'MIT-TAB: Tab assistant')",
    ).bind(id).execute(&mut *tx).await.map_err(|_| DATABASE)?;
    if let Some(group) = group {
        sqlx::query("INSERT INTO auth_group (name) VALUES (?) ON DUPLICATE KEY UPDATE name = name")
            .bind(group)
            .execute(&mut *tx)
            .await
            .map_err(|_| DATABASE)?;
        sqlx::query("INSERT INTO auth_user_groups (user_id, group_id) SELECT ?, id FROM auth_group WHERE name = ?")
            .bind(id).bind(group).execute(&mut *tx).await.map_err(|_| DATABASE)?;
    }
    let token = passwords::random_secret()?;
    let hash = digest::digest(&digest::SHA256, token.as_bytes());
    sqlx::query(
        "INSERT INTO mittab_staff_invite_token (user_id, token_hash, expires_at)
         VALUES (?, ?, UTC_TIMESTAMP(6) + INTERVAL 3 DAY)
         ON DUPLICATE KEY UPDATE token_hash = ?, expires_at = UTC_TIMESTAMP(6) + INTERVAL 3 DAY",
    )
    .bind(id)
    .bind(hash.as_ref())
    .bind(hash.as_ref())
    .execute(&mut *tx)
    .await
    .map_err(|_| DATABASE)?;
    let uid = URL_SAFE_NO_PAD.encode(id.to_string());
    let url = format!("{origin}/public/staff-invite/{uid}/{token}/");
    let message = Email::simple(email.into(), "MIT-TAB staff invitation".into(), format!(
        "Hello {username},\n\nYou have been invited to join the tournament staff in MIT-TAB.\n\nUsername: {username}\nSet your password: {url}\n\nThis link expires in three days."
    )).with_link(&url, "Set Your Password");
    let result = if let Some(mailer) = email::service() {
        mailer.send(&message).await
    } else {
        Err("Email delivery is unavailable.")
    };
    match result {
        Ok(()) => {
            invite_log(&mut tx, username, email, &message, None)
                .await
                .map_err(|_| DATABASE)?;
            let count = settings
                .integer(INVITE_COUNT, 0)
                .saturating_add(i32::from(setup));
            if setup {
                settings::put(&mut tx, INVITE_COUNT, SettingValue::Number(count))
                    .await
                    .map_err(|_| DATABASE)?;
            }
            tx.commit().await.map_err(|_| DATABASE)?;
            Ok(count)
        }
        Err(error) => {
            tx.rollback().await.map_err(|_| DATABASE)?;
            if let Ok(mut db) = app.db.acquire().await {
                let _ = invite_log(&mut db, username, email, &message, Some(error)).await;
            }
            Err(error)
        }
    }
}

async fn invite_log(
    db: &mut MySqlConnection,
    username: &str,
    address: &str,
    message: &Email,
    error: Option<&str>,
) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO tab_staffinviteemaillog (email_payload, username, email, successful, error_message, sent_at)
                 VALUES (?, ?, ?, ?, ?, UTC_TIMESTAMP(6))")
        .bind(message.snapshot()).bind(username).bind(address).bind(error.is_none()).bind(error.unwrap_or_default())
        .execute(db).await?;
    Ok(())
}

async fn valid_user(
    app: &App,
    db: &mut MySqlConnection,
    id: i32,
    token: &str,
    lock: bool,
) -> Result<Option<String>, sqlx::Error> {
    let query = if lock {
        "SELECT username, email, password, last_login FROM auth_user u
         WHERE id = ? AND u.is_active = TRUE AND u.is_staff = TRUE AND NOT EXISTS(
            SELECT 1 FROM auth_group g JOIN auth_user_groups m ON m.group_id = g.id
            WHERE m.user_id = u.id AND g.name = 'APDA Board') FOR UPDATE"
    } else {
        "SELECT username, email, password, last_login FROM auth_user u
         WHERE id = ? AND u.is_active = TRUE AND u.is_staff = TRUE AND NOT EXISTS(
            SELECT 1 FROM auth_group g JOIN auth_user_groups m ON m.group_id = g.id
            WHERE m.user_id = u.id AND g.name = 'APDA Board')"
    };
    let Some((username, email, password, last_login)) =
        sqlx::query_as::<_, (String, String, String, Option<NaiveDateTime>)>(query)
            .bind(id)
            .fetch_optional(&mut *db)
            .await?
    else {
        return Ok(None);
    };
    if login::valid_csrf_token(token) {
        let hash = digest::digest(&digest::SHA256, token.as_bytes());
        let found: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM mittab_staff_invite_token WHERE user_id = ? AND token_hash = ? AND expires_at > UTC_TIMESTAMP(6))",
        ).bind(id).bind(hash.as_ref()).fetch_one(db).await?;
        return Ok(found.then_some(username));
    }
    Ok(app
        .django_secret
        .as_ref()
        .filter(|secret| legacy_valid(secret.as_bytes(), id, token, &password, &email, last_login))
        .map(|_| username))
}

fn legacy_valid(
    secret: &[u8],
    id: i32,
    token: &str,
    password: &str,
    email: &str,
    last_login: Option<NaiveDateTime>,
) -> bool {
    let Some((stamp, signature)) = token.split_once('-') else {
        return false;
    };
    let Ok(seconds) = i64::from_str_radix(stamp, 36) else {
        return false;
    };
    let epoch = NaiveDate::from_ymd_opt(2001, 1, 1)
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap();
    let age = (Utc::now().naive_utc() - epoch)
        .num_seconds()
        .saturating_sub(seconds);
    if !(0..=259_200).contains(&age) || signature.len() != 32 {
        return false;
    }
    let login = last_login
        .map(|value| value.format("%Y-%m-%d %H:%M:%S").to_string())
        .unwrap_or_default();
    let mut salt = b"django.contrib.auth.tokens.PasswordResetTokenGenerator".to_vec();
    salt.extend_from_slice(secret);
    let key = digest::digest(&digest::SHA256, &salt);
    let key = hmac::Key::new(hmac::HMAC_SHA256, key.as_ref());
    let value = format!("{id}{password}{login}{seconds}{email}");
    let expected = hmac::sign(&key, value.as_bytes());
    let encoded = hex::encode(expected.as_ref());
    let compact: String = encoded.chars().step_by(2).collect();
    let comparison = hmac::Key::new(hmac::HMAC_SHA256, secret);
    hmac::verify(
        &comparison,
        compact.as_bytes(),
        hmac::sign(&comparison, signature.as_bytes()).as_ref(),
    )
    .is_ok()
}

fn user_id(uid: &str) -> Option<i32> {
    if uid.len() > 16 {
        return None;
    }
    let bytes = URL_SAFE_NO_PAD.decode(uid).ok()?;
    let id = std::str::from_utf8(&bytes).ok()?.parse::<i32>().ok()?;
    (id > 0).then_some(id)
}

pub async fn confirm(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((uid, token)): Path<(String, String)>,
    headers: HeaderMap,
) -> Response {
    let Some(id) = user_id(&uid) else {
        return confirmation(actor, &headers, false, None);
    };
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    match valid_user(&app, &mut db, id, &token, false).await {
        Ok(user) => confirmation(actor, &headers, user.is_some(), None),
        Err(_) => unavailable(),
    }
}

#[derive(Deserialize)]
pub struct PasswordForm {
    csrfmiddlewaretoken: String,
    new_password1: String,
    new_password2: String,
}

pub async fn accept(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((uid, token)): Path<(String, String)>,
    headers: HeaderMap,
    Form(form): Form<PasswordForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    let Some(id) = user_id(&uid) else {
        return confirmation(actor, &headers, false, None);
    };
    let Ok(mut tx) = app.db.begin().await else {
        return unavailable();
    };
    let username = match valid_user(&app, &mut tx, id, &token, true).await {
        Ok(Some(name)) => name,
        Ok(None) => return confirmation(actor, &headers, false, None),
        Err(_) => return unavailable(),
    };
    let password = form.new_password1;
    let valid = password == form.new_password2
        && (8..=256).contains(&password.chars().count())
        && !password.chars().all(char::is_numeric)
        && !password.eq_ignore_ascii_case(&username)
        && !matches!(
            password.to_ascii_lowercase().as_str(),
            "password" | "password1" | "12345678" | "letmein123" | "qwerty123"
        );
    if !valid {
        return confirmation(
            actor,
            &headers,
            true,
            Some(
                "Enter matching passwords with at least 8 characters. Choose a password different from your username.",
            ),
        );
    }
    let Ok(Ok(encoded)) =
        tokio::task::spawn_blocking(move || passwords::encode(password.as_bytes())).await
    else {
        return unavailable();
    };
    let result = async {
        sqlx::query("UPDATE auth_user SET password = ? WHERE id = ?")
            .bind(encoded)
            .bind(id)
            .execute(&mut *tx)
            .await?;
        sqlx::query("DELETE FROM mittab_staff_invite_token WHERE user_id = ?")
            .bind(id)
            .execute(&mut *tx)
            .await?;
        tx.commit().await
    }
    .await;
    if result.is_err() {
        return unavailable();
    }
    access::found(DONE)
}

fn confirmation(actor: Actor, headers: &HeaderMap, valid: bool, error: Option<&str>) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let response = html::render(
        "staff_invite_confirm.html",
        context! {
            title => "Set Staff Password", nav => Navigation::new(actor, true), csrf, valid, error,
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn complete(Extension(actor): Extension<Actor>) -> Response {
    html::render(
        "staff_invite_confirm.html",
        context! {
            title => "Staff Password Set", nav => Navigation::new(actor, true), complete => true,
        },
        StatusCode::OK,
    )
}
