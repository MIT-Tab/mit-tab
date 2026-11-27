use std::{
    collections::VecDeque,
    sync::{LazyLock, Mutex},
    time::Instant,
};

use axum::{
    Extension,
    http::{HeaderValue, StatusCode, header},
    response::Response,
};
use minijinja::context;
use serde::Serialize;

use crate::{
    html::{self, Navigation},
    legacy_session::Actor,
    private, unavailable,
};

static ENABLED: LazyLock<bool> = LazyLock::new(|| {
    std::env::var("SILK_ENABLED").is_ok_and(|value| matches!(value.as_str(), "1" | "true" | "yes"))
        && crate::tournament_maintenance::simulation_enabled()
});
static REQUESTS: Mutex<VecDeque<Timing>> = Mutex::new(VecDeque::new());

#[derive(Clone, Serialize)]
struct Timing {
    method: String,
    route: String,
    status: u16,
    milliseconds: u128,
}

pub fn start() -> Option<Instant> {
    (*ENABLED).then(Instant::now)
}

pub fn record(started: Option<Instant>, method: &str, route: &str, status: StatusCode) {
    let Some(started) = started else { return };
    if let Ok(mut rows) = REQUESTS.lock() {
        if rows.len() == 256 {
            rows.pop_front();
        }
        rows.push_back(Timing {
            method: method.into(),
            route: route.into(),
            status: status.as_u16(),
            milliseconds: started.elapsed().as_millis(),
        });
    }
}

pub async fn page(Extension(actor): Extension<Actor>) -> Response {
    if !*ENABLED {
        return private((StatusCode::NOT_FOUND, "Request diagnostics are disabled."));
    }
    let Ok(rows) = REQUESTS.lock() else {
        return unavailable();
    };
    let rows: Vec<_> = rows.iter().rev().cloned().collect();
    html::render(
        "diagnostics.html",
        context! {
            title => "Request Diagnostics", nav => Navigation::new(actor, true), rows,
        },
        StatusCode::OK,
    )
}

pub async fn js_catalog() -> Response {
    private((
        [(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/javascript; charset=utf-8"),
        )],
        include_str!("jsi18n.js"),
    ))
}
