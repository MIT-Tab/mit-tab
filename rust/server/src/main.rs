mod access;
mod backups;
mod ballot_entry;
mod bundle;
mod config;
mod home;
mod html;
mod judge_assignment;
mod legacy_session;
mod live_results;
mod login;
mod motions;
mod native_session;
mod passwords;
mod placements;
mod public_api;
mod public_pairings;
mod publication;
mod room_assignment;
mod rosters;
mod round_audit;
mod schema;
mod settings;
mod speakers;
mod staff_pairing;
mod tournament_data;

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
    backups: Arc<backups::Backups>,
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
    let command = std::env::args().nth(1).unwrap_or_else(|| "serve".into());
    if !matches!(command.as_str(), "serve" | "migrate" | "init") {
        return Err("Usage: mittab-server [serve|migrate|init]");
    }
    let config = Config::from_env()?;
    let db = config.connect().await?;
    if command != "serve" {
        schema::prepare(&db, command == "init").await?;
        eprintln!("MIT-Tab database is ready");
        return Ok(());
    }
    let listener = TcpListener::bind(config.listen)
        .await
        .map_err(|_| "Could not bind the MIT-Tab HTTP listener")?;
    let app = router(App {
        db,
        backups: Arc::new(config.backups),
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
        .merge(
            Router::new()
                .route("/", get(home::index))
                .route("/public/", get(home::public_home))
                .route("/favicon.ico", get(home::favicon))
                .route("/tournament-logo/", get(home::logo))
                .route("/public/motions/", get(motions::public))
                .route("/public/pairings/", get(public_pairings::current))
                .route("/public/pairings/{round}/", get(public_pairings::previous))
                .route("/public/missing-ballots/", get(public_pairings::missing))
                .route("/public/teams/", get(rosters::teams))
                .route("/public/judges/", get(rosters::judges))
                .route("/public/teams/expected-records/", get(rosters::records))
                .route("/public/access-error/", get(motions::public_error))
                .route("/403/", get(motions::forbidden))
                .route_layer(axum::middleware::from_fn_with_state(
                    app.clone(),
                    access::public_tournament,
                )),
        )
        .merge(
            Router::new()
                .route("/pairings/status/", get(staff_pairing::status))
                .route("/round/{round}/", get(staff_pairing::previous))
                .route(
                    "/round/{round}/result/",
                    get(ballot_entry::single_page).post(ballot_entry::single),
                )
                .route(
                    "/round/{round}/result/{count}/",
                    get(ballot_entry::panel_page).post(ballot_entry::panel),
                )
                .route(
                    "/pairing/pair_round/",
                    get(staff_pairing::pair_page).post(staff_pairing::pair),
                )
                .route(
                    "/pairing/re_pair_round/",
                    get(staff_pairing::repair_page).post(staff_pairing::repair),
                )
                .route(
                    "/pairing/assign_rooms_to_pairing/",
                    get(staff_pairing::back_to_status).post(staff_pairing::rooms),
                )
                .route(
                    "/pairing/assign_judges/",
                    get(staff_pairing::back_to_status).post(staff_pairing::judges),
                )
                .route(
                    "/pairing/release/",
                    axum::routing::post(staff_pairing::release),
                )
                .route("/motions/", get(motions::manage))
                .route(
                    "/motions/add/",
                    get(motions::back_to_manage).post(motions::add),
                )
                .route(
                    "/motions/{id}/edit/",
                    get(motions::edit).post(motions::save),
                )
                .route(
                    "/motions/{id}/delete/",
                    get(motions::back_to_manage).post(motions::delete),
                )
                .route(
                    "/motions/{id}/toggle-published/",
                    get(motions::back_to_manage).post(motions::toggle),
                )
                .route(
                    "/motions/publish-all/",
                    get(motions::back_to_manage).post(motions::publish_all),
                )
                .route(
                    "/motions/unpublish-all/",
                    get(motions::back_to_manage).post(motions::unpublish_all),
                )
                .route_layer(axum::middleware::from_fn_with_state(
                    app.clone(),
                    access::staff_settings,
                )),
        )
        .route("/public/login/", get(login::page).post(login::sign_in))
        .route(
            "/accounts/logout/",
            get(login::sign_out).post(login::sign_out),
        )
        .route("/admin/logout/", get(login::sign_out).post(login::sign_out))
        .nest_service(
            "/static",
            tower_http::services::ServeDir::new(
                std::env::var_os("MITTAB_STATIC_ROOT").unwrap_or_else(|| "staticfiles".into()),
            ),
        )
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
