mod access;
mod admin_accounts;
mod administration;
mod aws_http;
mod backup_dump;
mod backup_objects;
mod backup_restore;
mod backup_views;
mod backups;
mod ballot_entry;
mod board;
mod board_ids;
mod board_outrounds;
mod board_snapshot;
mod bracket_display;
mod bundle;
mod checkin;
mod checkin_data;
mod checklist;
mod codes;
mod config;
mod dev_fixture;
mod elimination_seeds;
mod email;
mod email_admin;
mod entity_store;
mod home;
mod homepage_admin;
mod html;
mod judge_assignment;
mod judge_portal;
mod judge_report;
mod legacy_archive;
mod legacy_session;
mod live_results;
mod login;
mod maintenance_cli;
mod motions;
mod native_session;
mod passwords;
mod placements;
mod public_api;
mod public_outrounds;
mod public_pairings;
mod public_rankings;
mod publication;
mod registration_admin;
mod registration_directory;
mod registration_emails;
mod registration_form;
mod registration_store;
mod registration_views;
mod room_assignment;
mod roster_groups;
mod roster_log;
mod roster_log_admin;
mod roster_spreadsheet_data;
mod roster_spreadsheet_layout;
mod roster_spreadsheets;
mod rosters;
mod round_audit;
mod schema;
mod school_directory;
mod settings;
mod settings_form;
mod speakers;
mod spreadsheets;
mod staff_elimination;
mod staff_elimination_assign;
mod staff_elimination_store;
mod staff_invites;
mod staff_pairing;
mod staff_rankings;
mod staff_roles;
mod staff_roster;
mod staff_roster_data;
mod staff_round_choices;
mod staff_round_store;
mod staff_round_tools;
mod staff_rounds;
mod staff_scratches;
mod tab_cards;
mod team_portal;
mod telemetry;
mod tournament_data;
mod tournament_maintenance;
mod tournament_setup;

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
    let mut args = std::env::args().skip(1);
    let command = args.next().unwrap_or_else(|| "serve".into());
    if !matches!(
        command.as_str(),
        "serve" | "migrate" | "init" | "simulate-rounds" | "export-stats" | "load-test"
    ) {
        return Err(
            "Usage: mittab-server [serve|migrate|init|simulate-rounds|export-stats|load-test]",
        );
    }
    let config = Config::from_env()?;
    let db = config.connect().await?;
    if command == "export-stats" {
        return maintenance_cli::export_stats(&db, &args.collect::<Vec<_>>()).await;
    }
    if command == "load-test" {
        return maintenance_cli::load_test(&db, &args.collect::<Vec<_>>()).await;
    }
    if command == "simulate-rounds" {
        let count = tournament_maintenance::simulate(&db).await?;
        eprintln!("Saved simulated results for {count} debates.");
        return Ok(());
    }
    if command != "serve" {
        schema::prepare(&db, command == "init").await?;
        eprintln!("MIT-Tab database is ready");
        return Ok(());
    }
    let _telemetry = telemetry::init();
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
        .with_graceful_shutdown(shutdown())
        .await
        .map_err(|_| "The MIT-Tab HTTP server stopped unexpectedly")
}

async fn shutdown() {
    #[cfg(unix)]
    if let Ok(mut terminate) =
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
    {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = terminate.recv() => {}
        }
        return;
    }
    let _ = tokio::signal::ctrl_c().await;
}

async fn health(axum::extract::State(app): axum::extract::State<App>) -> Response {
    if sqlx::query_scalar::<_, i32>("SELECT 1")
        .fetch_one(&app.db)
        .await
        .is_ok()
    {
        private((StatusCode::OK, "OK"))
    } else {
        unavailable()
    }
}

fn router(app: App) -> Router {
    Router::new()
        .route("/healthz", get(health))
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
                .route("/public/team-rankings/", get(public_rankings::teams))
                .route(
                    "/public/speaker-rankings/",
                    get(public_rankings::speaker_page),
                )
                .route("/public/ballots/", get(public_rankings::ballots))
                .route("/public/tab-cards/", get(tab_cards::public))
                .route("/public/outrounds/{division}/", get(public_outrounds::page))
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
                .route(
                    "/public/staff-invite/{uid}/{token}/",
                    get(staff_invites::confirm).post(staff_invites::accept),
                )
                .route(
                    "/public/staff-invite/complete/",
                    get(staff_invites::complete),
                )
                .route(
                    "/registration/",
                    get(registration_views::new_page).post(registration_views::create),
                )
                .route(
                    "/registration/edit/",
                    get(registration_views::search).post(registration_views::lookup),
                )
                .route(
                    "/registration/{code}/",
                    get(registration_views::edit_page).post(registration_views::edit),
                )
                .route(
                    "/registration/form.js",
                    get(registration_views::form_script),
                )
                .route(
                    "/registration/api/debaters/{school}/",
                    get(registration_views::debaters),
                )
                .route(
                    "/registration/api/debater-email-status/",
                    axum::routing::post(registration_views::email_status),
                )
                .route(
                    "/team_portal/",
                    get(team_portal::search).post(team_portal::lookup),
                )
                .route(
                    "/team_portal/{code}/",
                    get(team_portal::page).post(team_portal::update),
                )
                .route(
                    "/public/e-ballots/",
                    get(judge_portal::search).post(judge_portal::search_submit),
                )
                .route(
                    "/public/e-ballots/{code}/",
                    get(judge_portal::portal).post(judge_portal::availability),
                )
                .route(
                    "/public/e-ballots/{code}/current/",
                    get(judge_portal::current).post(judge_portal::submit),
                )
                .route(
                    "/public/e-ballots/{code}/previous/",
                    get(judge_portal::previous),
                )
                .route(
                    "/public/e-ballots/{code}/submitted/",
                    get(judge_portal::submitted).post(judge_portal::edit_reason),
                )
                .route(
                    "/public/e-ballots/{code}/submitted/{id}/",
                    get(judge_portal::submitted_one).post(judge_portal::edit_reason_one),
                )
                .route_layer(axum::middleware::from_fn_with_state(
                    app.clone(),
                    access::public_tournament,
                ))
                .layer(axum::middleware::map_response(
                    judge_portal::protect_response,
                )),
        )
        .merge(
            Router::new()
                .route(
                    "/email_management/",
                    get(email_admin::page).post(email_admin::send),
                )
                .route("/download_judge_codes/", get(email_admin::judge_codes))
                .route(
                    "/staff/invite/",
                    get(staff_invites::page).post(staff_invites::submit),
                )
                .route(
                    "/settings/public-homepage/",
                    get(homepage_admin::page)
                        .post(homepage_admin::update)
                        .layer(axum::extract::DefaultBodyLimit::max(
                            homepage_admin::MAX_BODY,
                        )),
                )
                .route(
                    "/registration/manage/",
                    get(registration_admin::page).post(registration_admin::update),
                )
                .route(
                    "/registration/manage/links/{kind}/",
                    axum::routing::post(registration_admin::link_add),
                )
                .route(
                    "/registration/manage/links/{kind}/{id}/",
                    axum::routing::post(registration_admin::link_edit),
                )
                .route(
                    "/registration/manage/links/{kind}/{id}/delete/",
                    axum::routing::post(registration_admin::link_delete),
                )
                .route(
                    "/registration/manage/info-text/",
                    axum::routing::post(registration_admin::text_add),
                )
                .route(
                    "/registration/manage/info-text/{id}/",
                    axum::routing::post(registration_admin::text_edit),
                )
                .route(
                    "/registration/manage/info-text/{id}/delete/",
                    axum::routing::post(registration_admin::text_delete),
                )
                .route(
                    "/setup/start-tournament/",
                    get(tournament_setup::page).post(tournament_setup::save),
                )
                .route(
                    "/setup/tournament-todo/",
                    get(checklist::page).post(checklist::save),
                )
                .route(
                    "/setup/tournament-todo/toggle/",
                    axum::routing::post(checklist::toggle),
                )
                .route(
                    "/settings_form",
                    get(settings_form::page).post(settings_form::save),
                )
                .route(
                    "/settings_form/",
                    get(settings_form::page).post(settings_form::save),
                )
                .route(
                    "/settings_form/viewed/",
                    axum::routing::post(settings_form::viewed),
                )
                .merge(staff_rounds::routes())
                .merge(staff_round_tools::routes())
                .merge(staff_elimination::routes())
                .merge(backup_views::routes())
                .route("/archive/download/", get(legacy_archive::download))
                .merge(roster_groups::routes())
                .merge(staff_rankings::routes())
                .merge(tournament_maintenance::routes())
                .route("/judges/speaker-points/", get(judge_report::page))
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
                .route(
                    "/rankings/public-control/",
                    get(public_rankings::control).post(public_rankings::update),
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
        .merge(
            Router::new()
                .route("/apda-board/", get(board::home))
                .route(
                    "/apda-board/schools/export/",
                    get(board_ids::export_schools),
                )
                .route(
                    "/apda-board/debaters/export/",
                    get(board_ids::export_debaters),
                )
                .route(
                    "/apda-board/schools/import/",
                    axum::routing::post(board_ids::import_schools),
                )
                .route(
                    "/apda-board/debaters/import/",
                    axum::routing::post(board_ids::import_debaters),
                )
                .route(
                    "/apda-board/school/{id}/",
                    get(board_ids::school).post(board_ids::school_save),
                )
                .route(
                    "/apda-board/debater/{id}/",
                    get(board_ids::debater).post(board_ids::debater_save),
                )
                .route("/apda-board/inrounds/", get(board::inrounds))
                .route("/apda-board/inrounds/{round}/", get(board::inround))
                .route("/apda-board/outrounds/", get(board_outrounds::list))
                .route(
                    "/apda-board/outrounds/pair/",
                    axum::routing::post(board_outrounds::pair),
                )
                .route(
                    "/apda-board/outround/{id}/",
                    axum::routing::post(board_outrounds::correct),
                )
                .route(
                    "/apda-board/judges/",
                    get(board::judges).post(board::update_judge),
                )
                .route("/apda-board/tab-cards/", get(tab_cards::all))
                .route("/apda-board/tab-card/{id}/", get(tab_cards::single))
                .route_layer(axum::middleware::from_fn_with_state(
                    app.clone(),
                    access::board,
                )),
        )
        .merge(
            staff_roster::routes()
                .merge(roster_spreadsheets::routes())
                .route_layer(axum::middleware::from_fn_with_state(
                    app.clone(),
                    access::data_entry,
                )),
        )
        .merge(
            staff_scratches::routes().route_layer(axum::middleware::from_fn_with_state(
                app.clone(),
                staff_scratches::middleware,
            )),
        )
        .merge(
            Router::new()
                .route("/batch_checkin/", get(checkin::page))
                .route("/bulk_check_in/", axum::routing::post(checkin::update))
                .route_layer(axum::middleware::from_fn_with_state(
                    app.clone(),
                    access::checkins,
                )),
        )
        .merge(
            Router::new()
                .route("/all_tab_cards/", get(tab_cards::all))
                .route("/team/card/{id}/", get(tab_cards::single))
                .route("/team/card/{id}/pretty/", get(tab_cards::single))
                .route("/archive/black_rod_bundle/", get(tab_cards::bundle))
                .route_layer(axum::middleware::from_fn_with_state(
                    app.clone(),
                    access::staff_or_board,
                )),
        )
        .merge(
            roster_log_admin::routes().route_layer(axum::middleware::from_fn_with_state(
                app.clone(),
                access::roster_log,
            )),
        )
        .merge(
            administration::routes().route_layer(axum::middleware::from_fn_with_state(
                app.clone(),
                access::administration,
            )),
        )
        .route("/404/", get(administration::not_found))
        .route("/500/", get(administration::internal_error))
        .route("/theme.css", get(homepage_admin::theme))
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
        .fallback(administration::fallback)
        .layer(axum::middleware::from_fn(telemetry::request))
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
