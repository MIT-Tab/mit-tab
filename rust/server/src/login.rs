use axum::{
    Form,
    extract::{Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, Uri, header},
    response::Response,
};
use minijinja::context;
use serde::Deserialize;
use unicode_normalization::UnicodeNormalization;

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::{Actor, cookie},
    native_session, passwords, private, unavailable,
};

#[derive(Deserialize)]
pub struct Continue {
    #[serde(default)]
    next: String,
}

#[derive(Deserialize)]
pub struct Credentials {
    #[serde(default)]
    username: String,
    #[serde(default)]
    password: String,
    #[serde(default)]
    csrfmiddlewaretoken: String,
    #[serde(default)]
    next: String,
}

struct Account {
    id: i32,
    password: String,
    staff: bool,
    superuser: bool,
    board: bool,
}

pub async fn page(Query(destination): Query<Continue>, headers: HeaderMap) -> Response {
    render(&destination.next, false, secure_request(&headers))
}

pub async fn sign_in(
    State(app): State<App>,
    headers: HeaderMap,
    Form(form): Form<Credentials>,
) -> Response {
    let secure = secure_request(&headers);
    let next = local_next(&form.next);
    if !valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return csrf_error();
    }

    let username: String = form.username.trim().nfkc().collect();
    let account = match verified_account(&app, &username, form.password).await {
        Ok(Some(account)) => account,
        Ok(None) => return render(next, true, secure),
        Err(_) => return unavailable(),
    };
    if account.board {
        match access::board_access_open(&app).await {
            Ok(true) => {}
            Ok(false) => return render(next, true, secure),
            Err(_) => return unavailable(),
        }
    }
    let Ok(target) = destination(&app, &account, next).await else {
        return unavailable();
    };
    let Ok(token) = native_session::create(&app.db, account.id, &account.password).await else {
        return unavailable();
    };
    if let Some(previous) = cookie(&headers, "sessionid")
        && native_session::remove(&app.db, previous).await.is_err()
    {
        // The new session has not been sent to the user. Revoke it before
        // returning an error so a failed rotation cannot leave it active.
        let _ = native_session::remove(&app.db, &token).await;
        return unavailable();
    }

    let mut response = private((StatusCode::FOUND, [(header::LOCATION, target)]));
    set_cookie(
        &mut response,
        "sessionid",
        &token,
        native_session::MAX_AGE,
        secure,
        true,
    );
    if let Ok(csrf) = passwords::random_secret() {
        set_cookie(&mut response, "csrftoken", &csrf, 31_449_600, secure, false);
    }
    response
}

pub async fn sign_out(State(app): State<App>, headers: HeaderMap) -> Response {
    if let Some(token) = cookie(&headers, "sessionid")
        && native_session::remove(&app.db, token).await.is_err()
    {
        return unavailable();
    }
    let mut response = private((StatusCode::FOUND, [(header::LOCATION, "/")]));
    set_cookie(
        &mut response,
        "sessionid",
        "",
        0,
        secure_request(&headers),
        true,
    );
    response
}

async fn verified_account(
    app: &App,
    username: &str,
    password: String,
) -> Result<Option<Account>, sqlx::Error> {
    let row = sqlx::query_as::<_, (i32, String, bool, bool, i64)>(
        "SELECT u.id, u.password, u.is_staff, u.is_superuser,
                EXISTS(SELECT 1 FROM auth_user_groups membership
                       JOIN auth_group g ON g.id = membership.group_id
                       WHERE membership.user_id = u.id AND g.name = 'APDA Board')
         FROM auth_user u
         WHERE u.username = ? AND u.is_active = TRUE",
    )
    .bind(username)
    .fetch_optional(&app.db)
    .await?;
    let account = tokio::task::spawn_blocking(move || match row {
        Some((id, encoded, staff, superuser, board))
            if passwords::verify(password.as_bytes(), &encoded) =>
        {
            Some(Account {
                id,
                password: encoded,
                staff,
                superuser,
                board: board != 0,
            })
        }
        Some(_) => None,
        None => {
            passwords::dummy_verify(password.as_bytes());
            None
        }
    })
    .await
    .map_err(|_| sqlx::Error::Protocol("Credential verification failed".into()))?;
    Ok(account)
}

async fn destination(app: &App, account: &Account, next: &str) -> Result<String, sqlx::Error> {
    if account.board && (next.is_empty() || next == "/setup/tournament-todo/") {
        return Ok("/apda-board/".into());
    }
    if account.superuser && !access::wizard_complete(app).await? {
        return Ok("/setup/start-tournament/".into());
    }
    if account.staff && !account.superuser && !account.board {
        if next.starts_with("/public/") {
            return Ok(next.into());
        }
        let preset = crate::staff_roles::preset(&app.db, account.id).await?;
        return Ok(preset
            .map_or("/403/", crate::staff_roles::Preset::landing)
            .into());
    }
    if !next.is_empty() {
        return Ok(next.into());
    }
    if !account.staff
        && !account.superuser
        && !account.board
        && access::legacy_entry_user(app, account.id).await?
    {
        return Ok("/pairings/status/".into());
    }
    let hide = sqlx::query_scalar::<_, bool>(
        "SELECT hide_tournament_todo FROM tab_usertournamentsetuppreference WHERE user_id = ?",
    )
    .bind(account.id)
    .fetch_optional(&app.db)
    .await?
    .unwrap_or(false);
    Ok(if hide { "/" } else { "/setup/tournament-todo/" }.into())
}

fn render(next: &str, invalid: bool, secure: bool) -> Response {
    let Ok(csrf) = passwords::random_secret() else {
        return unavailable();
    };
    let mut response = html::render(
        "staff_login.html",
        context! {
            title => "Staff Login", nav => Navigation::new(Actor::Anonymous, false),
            csrf => &csrf, next => local_next(next), invalid,
        },
        StatusCode::OK,
    );
    set_cookie(&mut response, "csrftoken", &csrf, 31_449_600, secure, false);
    response
}

pub fn set_cookie(
    response: &mut Response,
    name: &str,
    value: &str,
    age: u32,
    secure: bool,
    http_only: bool,
) {
    let secure = if secure { "; Secure" } else { "" };
    let http_only = if http_only { "; HttpOnly" } else { "" };
    let cookie = format!("{name}={value}; Path=/; Max-Age={age}; SameSite=Lax{secure}{http_only}");
    let value = HeaderValue::from_str(&cookie).expect("session cookie contains only valid ASCII");
    response.headers_mut().append(header::SET_COOKIE, value);
}

pub fn local_next(value: &str) -> &str {
    if value.starts_with('/')
        && !value.starts_with("//")
        && !value.starts_with("/%2f")
        && !value.starts_with("/%2F")
        && !value.contains('\\')
        && !value.chars().any(char::is_control)
        && HeaderValue::from_str(value).is_ok()
    {
        value
    } else {
        ""
    }
}

pub fn secure_request(headers: &HeaderMap) -> bool {
    headers
        .get("x-forwarded-proto")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(',').next())
        .is_some_and(|value| value.trim() == "https")
}

pub fn valid_csrf(headers: &HeaderMap, submitted: &str) -> bool {
    same_origin(headers, secure_request(headers))
        && cookie(headers, "csrftoken")
            .is_some_and(|value| valid_csrf_token(value) && value == submitted)
}

pub fn valid_csrf_token(value: &str) -> bool {
    value.len() == 43
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}

pub fn csrf_error() -> Response {
    private((StatusCode::FORBIDDEN, "CSRF verification failed."))
}

fn same_origin(headers: &HeaderMap, secure: bool) -> bool {
    let scheme = if secure { "https" } else { "http" };
    let origin = headers.get(header::ORIGIN);
    let referer = headers.get(header::REFERER);
    let submitted = origin.or(if secure { referer } else { None });
    let Some(submitted) = submitted else {
        return !secure;
    };
    let uri = submitted
        .to_str()
        .ok()
        .and_then(|value| value.parse::<Uri>().ok());
    let host = headers
        .get(header::HOST)
        .and_then(|value| value.to_str().ok());
    uri.is_some_and(|uri| {
        uri.scheme_str() == Some(scheme)
            && uri.authority().is_some_and(|actual| {
                !actual.as_str().contains('@')
                    && host.is_some_and(|host| actual.as_str().eq_ignore_ascii_case(host))
            })
            && (origin.is_none() || (uri.path() == "/" && uri.query().is_none()))
    })
}
