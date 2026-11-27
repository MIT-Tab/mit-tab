use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use minijinja::{Value, context};
use ring::hmac;
use serde::Serialize;

use crate::{App, access, legacy_html, legacy_session::cookie, login};

const COOKIE: &str = "mittab_roster_notice";
const TEAM_COOKIE: &str = "mittab_team_portal_notice";
const JUDGE_COOKIE: &str = "mittab_judge_portal_notice";
const LIFETIME: u32 = 60;
const ERROR_PREFIX: &str = "error.";

pub fn saved(app: &App, headers: &HeaderMap, target: &str, text: &str) -> Response {
    redirect(
        app,
        headers,
        target,
        text,
        "",
        COOKIE,
        identity(headers, COOKIE),
    )
}

pub fn failed(app: &App, headers: &HeaderMap, target: &str, text: &str) -> Response {
    redirect(
        app,
        headers,
        target,
        text,
        ERROR_PREFIX,
        COOKIE,
        identity(headers, COOKIE),
    )
}

#[derive(Clone, Copy)]
pub enum Portal {
    Team,
    Judge,
}

impl Portal {
    fn cookie(self) -> &'static str {
        match self {
            Self::Team => TEAM_COOKIE,
            Self::Judge => JUDGE_COOKIE,
        }
    }
}

pub fn portal_redirect(
    app: &App,
    headers: &HeaderMap,
    portal: Portal,
    target: &str,
    text: &str,
    error: bool,
) -> Response {
    let Ok(csrf) = login::TemplateCsrf::new(headers) else {
        return crate::unavailable();
    };
    let mut response = redirect(
        app,
        headers,
        target,
        text,
        if error { ERROR_PREFIX } else { "" },
        portal.cookie(),
        Some(csrf.secret()),
    );
    csrf.set_cookie(&mut response, headers);
    response
}

fn identity<'a>(headers: &'a HeaderMap, channel: &str) -> Option<&'a str> {
    if channel == COOKIE {
        cookie(headers, "sessionid")
    } else {
        cookie(headers, "csrftoken").filter(|value| login::valid_csrf_token(value))
    }
}

fn redirect(
    app: &App,
    headers: &HeaderMap,
    target: &str,
    text: &str,
    prefix: &str,
    channel: &str,
    session: Option<&str>,
) -> Response {
    let mut response = access::found(target);
    if let Some(session) = session {
        let message = URL_SAFE_NO_PAD.encode(text);
        let expires = Utc::now().timestamp() + i64::from(LIFETIME);
        let body = format!("{expires}.{prefix}{message}");
        let path = target.split(['?', '#']).next().unwrap_or("/");
        let signature = hmac::sign(
            &app.admin_notice_key,
            &signed_data(channel, session, path, &body),
        );
        let encoded = URL_SAFE_NO_PAD.encode(signature.as_ref());
        login::set_cookie(
            &mut response,
            channel,
            &format!("{body}.{encoded}"),
            LIFETIME,
            login::secure_request(headers),
            true,
        );
    }
    response
}

fn signed_data(channel: &str, session: &str, path: &str, body: &str) -> Vec<u8> {
    let purpose = match channel {
        TEAM_COOKIE => "team portal",
        JUDGE_COOKIE => "judge portal",
        _ => "staff roster",
    };
    format!("mit-tab {purpose} notice v1\0{session}\0{path}\0{body}").into_bytes()
}

#[derive(Serialize)]
pub struct Notice {
    text: String,
    error: bool,
}

pub fn message(app: &App, headers: &HeaderMap, path: &str) -> Option<Notice> {
    read(app, headers, path, COOKIE)
}

fn read(app: &App, headers: &HeaderMap, path: &str, channel: &str) -> Option<Notice> {
    let session = identity(headers, channel)?;
    let (body, signature) = cookie(headers, channel)?.rsplit_once('.')?;
    let signature = URL_SAFE_NO_PAD.decode(signature).ok()?;
    hmac::verify(
        &app.admin_notice_key,
        &signed_data(channel, session, path, body),
        &signature,
    )
    .ok()?;
    let (expires, message) = body.split_once('.')?;
    let expires: i64 = expires.parse().ok()?;
    if expires < Utc::now().timestamp() {
        return None;
    }
    let failure = message.strip_prefix(ERROR_PREFIX);
    let text = String::from_utf8(URL_SAFE_NO_PAD.decode(failure.unwrap_or(message)).ok()?).ok()?;
    Some(Notice {
        text,
        error: failure.is_some(),
    })
}

pub fn render(
    app: &App,
    headers: &HeaderMap,
    path: &str,
    template: &str,
    shell: Value,
    page: Value,
    status: StatusCode,
) -> Response {
    let notice = message(app, headers, path).map(|notice| (COOKIE, notice));
    render_notice(headers, template, shell, page, status, notice)
}

pub fn render_portal(
    app: &App,
    headers: &HeaderMap,
    portal: Portal,
    path: &str,
    template: &str,
    shell: Value,
    page: Value,
) -> Response {
    let channel = portal.cookie();
    let notice = read(app, headers, path, channel).map(|notice| (channel, notice));
    render_notice(headers, template, shell, page, StatusCode::OK, notice)
}

fn render_notice(
    headers: &HeaderMap,
    template: &str,
    shell: Value,
    page: Value,
    status: StatusCode,
    notice: Option<(&str, Notice)>,
) -> Response {
    let Some((channel, Notice { text, error })) = notice else {
        return legacy_html::render(template, shell, page, status);
    };
    let existing = page.get_attr("messages").unwrap_or_default();
    let mut messages: Vec<_> = existing
        .try_iter()
        .map(|messages| messages.collect())
        .unwrap_or_default();
    let (tags, level) = if error {
        ("error", 40)
    } else {
        ("success", 25)
    };
    messages.push(context! { text, tags, level });
    let mut response = legacy_html::render(template, shell, context! { messages, ..page }, status);
    if !response.status().is_server_error() {
        clear_channel(&mut response, headers, channel);
    }
    response
}

pub fn clear(response: &mut Response, headers: &HeaderMap) {
    clear_channel(response, headers, COOKIE);
}

fn clear_channel(response: &mut Response, headers: &HeaderMap, channel: &str) {
    login::set_cookie(
        response,
        channel,
        "",
        0,
        login::secure_request(headers),
        true,
    );
}
