use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use minijinja::{Value, context};
use ring::hmac;

use crate::{App, access, legacy_html, legacy_session::cookie, login};

const COOKIE: &str = "mittab_roster_notice";
const LIFETIME: u32 = 60;

pub fn saved(app: &App, headers: &HeaderMap, target: &str, text: &str) -> Response {
    let mut response = access::found(target);
    if let Some(session) = cookie(headers, "sessionid") {
        let message = URL_SAFE_NO_PAD.encode(text);
        let expires = Utc::now().timestamp() + i64::from(LIFETIME);
        let body = format!("{expires}.{message}");
        let path = target.split(['?', '#']).next().unwrap_or("/");
        let signature = hmac::sign(&app.admin_notice_key, &signed_data(session, path, &body));
        let encoded = URL_SAFE_NO_PAD.encode(signature.as_ref());
        login::set_cookie(
            &mut response,
            COOKIE,
            &format!("{body}.{encoded}"),
            LIFETIME,
            login::secure_request(headers),
            true,
        );
    }
    response
}

fn signed_data(session: &str, path: &str, body: &str) -> Vec<u8> {
    format!("mit-tab staff roster notice v1\0{session}\0{path}\0{body}").into_bytes()
}

pub fn message(app: &App, headers: &HeaderMap, path: &str) -> Option<String> {
    let session = cookie(headers, "sessionid")?;
    let (body, signature) = cookie(headers, COOKIE)?.rsplit_once('.')?;
    let signature = URL_SAFE_NO_PAD.decode(signature).ok()?;
    hmac::verify(
        &app.admin_notice_key,
        &signed_data(session, path, body),
        &signature,
    )
    .ok()?;
    let (expires, message) = body.split_once('.')?;
    let expires: i64 = expires.parse().ok()?;
    if expires < Utc::now().timestamp() {
        return None;
    }
    String::from_utf8(URL_SAFE_NO_PAD.decode(message).ok()?).ok()
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
    let Some(text) = message(app, headers, path) else {
        return legacy_html::render(template, shell, page, status);
    };
    let existing = page.get_attr("messages").unwrap_or_default();
    let mut messages: Vec<_> = existing
        .try_iter()
        .map(|messages| messages.collect())
        .unwrap_or_default();
    messages.push(context! { text, tags => "success", level => 25 });
    let mut response = legacy_html::render(template, shell, context! { messages, ..page }, status);
    clear(&mut response, headers);
    response
}

pub fn clear(response: &mut Response, headers: &HeaderMap) {
    login::set_cookie(
        response,
        COOKIE,
        "",
        0,
        login::secure_request(headers),
        true,
    );
}
