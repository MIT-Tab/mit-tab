mod access;
mod bundle;
mod config;
mod legacy_session;
mod live_results;
mod placements;
mod public_api;
mod publication;
mod settings;
mod speakers;

use std::{process::ExitCode, sync::Arc};

use axum::{
    Router,
    http::{HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use sqlx::MySqlPool;
use tokio::net::TcpListener;

use crate::config::Config;

#[derive(Clone)]
struct App {
    db: MySqlPool,
    django_secret: Option<Arc<str>>,
}

#[tokio::main]
async fn main() -> ExitCode {
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<(), &'static str> {
    let config = Config::from_env()?;
    let db = config.connect().await?;
    let listener = TcpListener::bind(config.listen)
        .await
        .map_err(|_| "Could not bind the MIT-Tab HTTP listener")?;
    let app = router(App {
        db,
        django_secret: config.django_secret.map(Arc::from),
    });
    eprintln!(
        "MIT-Tab listening on {}",
        listener
            .local_addr()
            .map_err(|_| "Could not determine listen address")?
    );
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await
        .map_err(|_| "The MIT-Tab HTTP server stopped unexpectedly")
}

fn router(app: App) -> Router {
    Router::new()
        .route("/api/debater-counts", get(public_api::counts))
        .route("/api/new-debater-data", get(public_api::new_debaters))
        .route("/api/new-schools", get(public_api::new_schools))
        .route("/api/varsity-team-placements", get(placements::varsity))
        .route("/api/novice-team-placements", get(placements::novice))
        .route("/api/non-placing-teams", get(placements::non_placing))
        .route("/api/varsity-speaker-awards", get(speakers::varsity))
        .route("/api/novice-speaker-awards", get(speakers::novice))
        .route("/api/live-results", get(live_results::get))
        .route("/api/import-results", get(live_results::import))
        .route_layer(axum::middleware::from_fn_with_state(
            app.clone(),
            access::public_api,
        ))
        .with_state(app)
}

fn private(response: impl IntoResponse) -> Response {
    let mut response = response.into_response();
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, no-store"),
    );
    response
}

fn unavailable() -> Response {
    private((
        StatusCode::SERVICE_UNAVAILABLE,
        "Unable to complete this request.",
    ))
}
