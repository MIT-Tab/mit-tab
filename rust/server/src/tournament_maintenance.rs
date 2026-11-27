use axum::{
    Extension, Router,
    body::Bytes,
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use minijinja::context;
use ring::rand::{SecureRandom, SystemRandom};
use serde::Deserialize;
use sqlx::{AssertSqlSafe, MySqlPool};

use crate::{
    App, access, ballot_entry,
    html::{self, Navigation},
    legacy_session::Actor,
    login, passwords, private,
    registration_form::Fields,
    settings::{self, SettingValue, Settings},
    staff_pairing::lock_round,
    unavailable,
};

const CONFIRM: &str = "/pairing/confirm_start_tourny/";
const RESET: &str = "/pairing/start_tourny/";
const REVISION: &str = "tournament_reset_revision";

// Delete children first. Auth users, their assigned permissions and sessions,
// and the installed schema belong to this installation across tournaments.
const TOURNAMENT_TABLES: &[&str] = &[
    "mittab_public_logo",
    "mittab_registration_private_email",
    "mittab_staff_invite_token",
    "tab_auditevent",
    "tab_bulkcheckinchange",
    "tab_judgecodeemaillog",
    "tab_teamcodeemaillog",
    "tab_writtenrfdemaillog",
    "tab_staffinviteemaillog",
    "tab_publicrosterchange",
    "tab_publicrosterlogstate",
    "tab_manualjudgeassignment",
    "tab_outround_judges",
    "tab_outroundchallenge",
    "tab_outround",
    "tab_publishedteamplacement",
    "tab_publishedoutroundstage",
    "tab_breakingteam",
    "tab_publicdisplaysetting",
    "tab_bye",
    "tab_noshow",
    "tab_checkin",
    "tab_roomcheckin",
    "tab_judgeexpectedcheckin",
    "tab_roundstats",
    "tab_round_judges",
    "tab_round",
    "tab_scratch",
    "tab_motion",
    "tab_team_debaters",
    "tab_team_required_room_tags",
    "tab_judge_required_room_tags",
    "tab_judge_schools",
    "tab_room_tags",
    "tab_rankinggroup_debaters",
    "tab_rankinggroup_teams",
    "tab_team",
    "tab_judge",
    "tab_room",
    "tab_debater",
    "registration_registrationconfirmationemaillog",
    "registration_registrationchangelog",
    "registration_registration",
    "tab_school",
    "tab_publichomepage",
    "tab_publichomeshortcut",
    "tab_tournamentstaffrole",
    "tab_usertournamentsetuppreference",
    "registration_infotext",
];

pub fn routes() -> Router<App> {
    Router::new()
        .route(CONFIRM, get(confirm))
        .route(RESET, get(confirm).post(reset))
        .route(
            "/pairings/simulate_rounds/",
            get(simulation_page).post(simulate_http),
        )
        .route("/cache_refresh", get(cache_refresh))
        .route("/cache_refresh/", get(cache_refresh))
}

async fn confirm(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(settings) = Settings::load(&mut db, &[REVISION, "tournament_name"]).await else {
        return unavailable();
    };
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    let response = html::render(
        "tournament_maintenance.html",
        context! {
            title => "Create New Tournament", action => RESET, reset => true,
            tournament => settings.string("tournament_name", "Tournament"),
            revision => settings.string(REVISION, "initial"), csrf, nav => Navigation::new(actor, true),
        },
        StatusCode::OK,
    );
    html::form_response(response, &headers, &csrf)
}

async fn reset(State(app): State<App>, headers: HeaderMap, body: Bytes) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    if !fields.flag("confirm") {
        return private((
            StatusCode::UNPROCESSABLE_ENTITY,
            "Confirm the new tournament before continuing.",
        ));
    }
    match reset_tournament(&app, fields.get("revision")).await {
        Ok(true) => access::found("/setup/start-tournament/"),
        Ok(false) => private((
            StatusCode::CONFLICT,
            "This confirmation has expired. Open the confirmation page again.",
        )),
        Err(stage) => {
            eprintln!("Tournament reset failed: {stage}");
            unavailable()
        }
    }
}

async fn reset_tournament(app: &App, revision: &str) -> Result<bool, &'static str> {
    let mut db = app
        .db
        .begin()
        .await
        .map_err(|_| "Could not open tournament database")?;
    let setting = lock_round(&mut db)
        .await
        .map_err(|_| "Could not lock current round")?;
    let settings = Settings::load(&mut db, &["cur_round", REVISION])
        .await
        .map_err(|_| "Could not read tournament settings")?;
    if settings.string(REVISION, "initial") != revision {
        return Ok(false);
    }
    app.backups
        .save_named(
            "Before New Tournament",
            2,
            &settings.integer("cur_round", 1).to_string(),
        )
        .await?;
    for table in TOURNAMENT_TABLES {
        sqlx::query(AssertSqlSafe(format!("DELETE FROM `{table}`")))
            .execute(&mut *db)
            .await
            .map_err(|error| {
                let code = error.as_database_error().and_then(|error| error.code());
                eprintln!("Could not clear table {table} (database code {code:?})");
                "Could not clear tournament data"
            })?;
    }
    sqlx::query("DELETE FROM tab_tabsettings WHERE id <> ?")
        .bind(setting)
        .execute(&mut *db)
        .await
        .map_err(|_| "Could not reset tournament settings")?;
    crate::schema::tournament_defaults(&mut db).await?;
    let next_revision = passwords::random_secret()?;
    settings::put(&mut db, REVISION, SettingValue::Text(&next_revision))
        .await
        .map_err(|_| "Could not update tournament revision")?;
    db.commit()
        .await
        .map_err(|_| "Could not finish tournament reset")?;
    Ok(true)
}

pub fn simulation_enabled() -> bool {
    std::env::var("MITTAB_ENV")
        .is_ok_and(|value| matches!(value.as_str(), "development" | "test-deployment"))
}

async fn simulation_page(Extension(actor): Extension<Actor>, headers: HeaderMap) -> Response {
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    let response = html::render(
        "tournament_maintenance.html",
        context! {
            title => "Simulate Round Results", action => "/pairings/simulate_rounds/", csrf,
            nav => Navigation::new(actor, true), enabled => simulation_enabled(),
        },
        StatusCode::OK,
    );
    html::form_response(response, &headers, &csrf)
}

async fn simulate_http(State(app): State<App>, headers: HeaderMap, body: Bytes) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    match simulate(&app.db).await {
        Ok(_) => access::found("/pairings/status/"),
        Err(message) => private((StatusCode::UNPROCESSABLE_ENTITY, message)),
    }
}

pub async fn simulate(pool: &MySqlPool) -> Result<usize, &'static str> {
    if !simulation_enabled() {
        return Err("Simulated rounds are disabled.");
    }
    let error = "Could not simulate the current round.";
    let mut db = pool.begin().await.map_err(|_| error)?;
    lock_round(&mut db).await.map_err(|_| error)?;
    let current = Settings::load(&mut db, &["cur_round"])
        .await
        .map_err(|_| error)?
        .integer("cur_round", 1);
    let ids = sqlx::query_scalar::<_, i32>(
        "SELECT id FROM tab_round WHERE round_number = ? AND victor = 0 ORDER BY id FOR UPDATE",
    )
    .bind(current.saturating_sub(1))
    .fetch_all(&mut *db)
    .await
    .map_err(|_| error)?;
    let mut bytes = [0; 8];
    SystemRandom::new().fill(&mut bytes).map_err(|_| error)?;
    let mut random =
        mittab_tabulation::legacy_random::LegacyRandom::seeded(u64::from_ne_bytes(bytes));
    for &id in &ids {
        let Some(debate) = ballot_entry::load(&mut db, id).await.map_err(|_| error)? else {
            return Err(error);
        };
        let ballot = ballot_entry::simulate(&debate, &mut random)?;
        ballot_entry::save(&mut db, &debate, ballot)
            .await
            .map_err(|_| error)?;
    }
    db.commit().await.map_err(|_| error)?;
    Ok(ids.len())
}

#[derive(Deserialize)]
struct Refresh {
    next: Option<String>,
}

async fn cache_refresh(Query(params): Query<Refresh>) -> Response {
    let target = login::local_next(params.next.as_deref().unwrap_or_default());
    access::found(if target.is_empty() { "/" } else { target })
}
