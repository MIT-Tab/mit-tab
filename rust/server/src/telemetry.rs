use std::borrow::Cow;

use axum::{
    extract::{MatchedPath, Request},
    middleware::Next,
    response::Response,
};
use sentry::{
    ClientInitGuard, ClientOptions, TransactionContext,
    protocol::{Event, Level, SpanStatus},
    types::Dsn,
};

pub fn init() -> Option<ClientInitGuard> {
    let address = std::env::var("SENTRY_DSN")
        .ok()
        .filter(|value| !value.is_empty())?;
    let dsn = match address.parse::<Dsn>() {
        Ok(dsn) => dsn,
        Err(_) => {
            eprintln!("Invalid Sentry configuration; telemetry is disabled.");
            return None;
        }
    };
    let traces_sample_rate = std::env::var("SENTRY_TRACES_SAMPLE_RATE")
        .ok()
        .and_then(|value| value.parse::<f32>().ok())
        .filter(|value| value.is_finite())
        .unwrap_or(1.0)
        .clamp(0.0, 1.0);
    let environment = std::env::var("SENTRY_ENVIRONMENT")
        .or_else(|_| std::env::var("MITTAB_ENV"))
        .ok()
        .map(Cow::Owned);
    let release = std::env::var("SENTRY_RELEASE").ok().map(Cow::Owned);
    let mut options = ClientOptions::default()
        .traces_sample_rate(traces_sample_rate)
        .default_integrations(false)
        .send_default_pii(false);
    options.dsn = Some(dsn);
    options.environment = environment;
    options.release = release;
    Some(sentry::init(options))
}

pub async fn request(request: Request, next: Next) -> Response {
    let route = request
        .extensions()
        .get::<MatchedPath>()
        .map(|matched| matched.as_str().to_owned());
    let Some(route) = route.filter(|route| route != "/healthz" && route != "/500/") else {
        return next.run(request).await;
    };
    if !sentry::Hub::current()
        .client()
        .is_some_and(|client| client.is_enabled())
    {
        return next.run(request).await;
    }
    let method = request.method().to_string();
    let transaction = sentry::start_transaction(TransactionContext::new(
        &format!("{method} {route}"),
        "http.server",
    ));
    let response = next.run(request).await;
    let status = response.status();
    transaction.set_data("http.status_code", serde_json::Value::from(status.as_u16()));
    if status.is_server_error() {
        transaction.set_status(SpanStatus::InternalError);
        sentry::capture_event(Event {
            level: Level::Error,
            message: Some("MIT-Tab request failed".into()),
            transaction: Some(format!("{method} {route}")),
            fingerprint: Cow::Owned(vec![Cow::Borrowed("mittab-http-error"), Cow::Owned(route)]),
            ..Default::default()
        });
    } else if status.is_success() || status.is_redirection() {
        transaction.set_status(SpanStatus::Ok);
    }
    transaction.finish();
    response
}
