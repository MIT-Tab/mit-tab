use std::collections::BTreeMap;

use axum::{
    Extension, Json, Router,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
    routing::{get, post},
};
use minijinja::context;
use serde_json::json;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    board_snapshot::{self, Pairings},
    legacy_html,
    legacy_session::Actor,
    login, private,
    registration_form::Fields,
    settings::Settings,
    staff_elimination_assign::{self, Assignment},
    staff_elimination_page::{self, Selection},
    staff_elimination_store::{self as store, Error, Stage},
    staff_roster::form_return_target,
    staff_roster_notice, unavailable,
};

const ROOT: &str = "/outround_pairing";
const BREAK: &str = "/break/";

pub fn routes() -> Router<App> {
    Router::new()
        .route(BREAK, get(break_page).post(break_submit))
        .route(ROOT, get(default_stage))
        .route("/outround_pairing/{division}/{size}", get(stage_page))
        .route(
            "/outround_pairing/pair/{division}/{previous}/",
            get(pair_page).post(pair_submit),
        )
        .route(
            "/outround_pairing/pair/{division}/{previous}",
            get(pair_redirect),
        )
        .route("/outround_pairing/assign_judges/", post(assign_judges))
        .route(
            "/outround_pairing/assign_judges/{division}/",
            post(assign_judges_legacy),
        )
        .route("/outround_pairing/assign_rooms/", post(assign_rooms))
        .route(
            "/outround_pairing/release/{size}/{division}/",
            post(release),
        )
        .route(
            "/outround/{id}/result/",
            get(ballot_page).post(ballot_submit),
        )
        .route("/outround_choice/{id}", post(choice))
        .route("/outround_result/{division}", get(forum))
}

fn user(actor: Actor) -> Option<i32> {
    match actor {
        Actor::User {
            id, board: false, ..
        } => Some(id),
        _ => None,
    }
}

fn error(error: Error) -> Response {
    match error {
        Error::Invalid(message) => private((StatusCode::UNPROCESSABLE_ENTITY, message)),
        Error::Assignment(message) => private((StatusCode::UNPROCESSABLE_ENTITY, message)),
        Error::Missing => private((StatusCode::NOT_FOUND, "Elimination stage not found.")),
        Error::Database => unavailable(),
    }
}

fn checked(headers: &HeaderMap, fields: &Fields) -> bool {
    let token = headers
        .get("x-csrftoken")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_else(|| fields.get("csrfmiddlewaretoken"));
    login::valid_csrf(headers, token)
}

async fn break_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    show_break(&app, actor, &headers, None).await
}

async fn show_break(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    message: Option<&str>,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, store::SETTINGS).await?;
        let rooms = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*) FROM tab_roomcheckin WHERE round_number = 0",
        )
        .fetch_one(&mut *db)
        .await?;
        let [varsity, novice] = store::break_sizes(&settings);
        let needed = i64::from(varsity.div_euclid(2)) + i64::from(novice.div_euclid(2));
        let final_round = settings.integer("tot_rounds", 5);
        let previous_error = break_preview_error(&mut db).await?;
        let break_revision = if message == Some(store::CONFIRM_REPLACEMENT) {
            let pairings = board_snapshot::capture(&mut db, Pairings::Outround).await?;
            store::break_revision(&mut db, &pairings, &settings).await?
        } else {
            None
        };
        let shell = legacy_html::shell(&mut db, actor, BREAK).await?;
        db.commit().await?;
        let Ok(csrf) = login::TemplateCsrf::new(headers) else {
            return Ok(unavailable());
        };
        let checks = [
            (
                format!("All Rounds properly entered for Round {final_round}"),
                if previous_error.is_some() {
                    "No"
                } else {
                    "Yes"
                },
                previous_error.as_deref().unwrap_or("All rounds look good"),
            ),
            (
                format!("N/2 Rooms available Round Out-rounds? Need {needed}, have {rooms}"),
                if rooms >= needed { "Yes" } else { "No" },
                if rooms >= needed {
                    "Rooms are checked in"
                } else {
                    "Not enough rooms"
                },
            ),
        ];
        let mut page = legacy_html::render(
            "pairing/pair_round.html",
            shell,
            context! {
                title => "Pairing Outrounds", check_status => checks, csrf_input => csrf.input(),
                checked_in_judges => Vec::<minijinja::Value>::new(),
                break_revision,
                messages => message.map(|text| [context! {text, tags => "danger", level => 40}]),
                DEFAULT_MESSAGE_LEVELS => context! {ERROR => 40},
            },
            if message.is_some() {
                StatusCode::UNPROCESSABLE_ENTITY
            } else {
                StatusCode::OK
            },
        );
        csrf.set_cookie(&mut page, headers);
        Ok::<_, sqlx::Error>(page)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn break_preview_error(db: &mut MySqlConnection) -> Result<Option<String>, sqlx::Error> {
    // The reference gives the pairing check stage 0, so its preview reads
    // round -1. The write transaction checks the actual preliminary rounds.
    type Row = (i32, String, bool, bool, String, bool, bool);
    let rows = sqlx::query_as::<_, Row>(
        "SELECT r.victor, g.name,
                EXISTS(SELECT 1 FROM tab_bye b WHERE b.round_number = -1 AND b.bye_team_id = g.id),
                EXISTS(SELECT 1 FROM tab_noshow n WHERE n.round_number = -1 AND n.no_show_team_id = g.id),
                o.name,
                EXISTS(SELECT 1 FROM tab_bye b WHERE b.round_number = -1 AND b.bye_team_id = o.id),
                EXISTS(SELECT 1 FROM tab_noshow n WHERE n.round_number = -1 AND n.no_show_team_id = o.id)
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         WHERE r.round_number = -1 ORDER BY r.id",
    ).fetch_all(db).await?;
    for (outcome, gov, gov_bye, gov_absent, opp, opp_bye, opp_absent) in rows {
        if outcome == 0 {
            return Ok(Some("Not all rounds are entered. 'You have not entered all the results from the previous round.'".into()));
        }
        for (team, bye, absent) in [(gov, gov_bye, gov_absent), (opp, opp_bye, opp_absent)] {
            if bye {
                return Ok(Some(format!(
                    "You have a bye and results. {team} both had a bye and debated last round"
                )));
            }
            if absent {
                return Ok(Some(format!(
                    "You have a noshow and results. {team} both debated and had a no show"
                )));
            }
        }
    }
    Ok(None)
}

async fn break_submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !checked(&headers, &fields) {
        return login::csrf_error();
    }
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    let confirmation = fields.flag("confirm").then(|| fields.get("break_revision"));
    match store::break_tournament(&app, user, confirmation).await {
        Ok(()) => access::found(ROOT),
        Err(Error::Invalid(message)) => show_break(&app, actor, &headers, Some(message)).await,
        Err(issue) => error(issue),
    }
}

async fn default_stage(State(app): State<App>) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(settings) = Settings::load(&mut db, store::SETTINGS).await else {
        return unavailable();
    };
    let counts = store::break_sizes(&settings);
    if counts.iter().any(|count| *count < 0) {
        return access::found("/");
    }
    let stage = counts.into_iter().enumerate().find_map(|(kind, count)| {
        store::stage_size(count).and_then(|size| Stage::new(kind as i32, size))
    });
    match stage {
        Some(stage) => access::found(stage.url()),
        None => access::found("/"),
    }
}

async fn stage_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((division, size)): Path<(i32, i32)>,
    Query(selection): Query<Selection>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let Some(fallback) = Stage::new(division, size) else {
        return error(Error::Missing);
    };
    let return_path = uri
        .path_and_query()
        .map_or(uri.path(), |path| path.as_str());
    let selected = selection.sizes();
    match staff_elimination_page::render(&app, actor, &headers, return_path, selected, fallback)
        .await
    {
        Ok(page) => page,
        Err(issue) => error(issue),
    }
}

async fn pair_redirect(Path((division, previous)): Path<(i32, i32)>) -> Response {
    access::found(format!("/outround_pairing/pair/{division}/{previous}/"))
}

#[derive(serde::Deserialize, Default)]
struct PrepareQuery {
    stage: Option<String>,
}

async fn pair_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((division, previous)): Path<(i32, i32)>,
    Query(query): Query<PrepareQuery>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let Some(prior) = Stage::new(division, previous) else {
        return error(Error::Missing);
    };
    show_pair(
        &app,
        actor,
        &headers,
        prior,
        query.stage.as_deref() == Some("pairing"),
        None,
        uri.path_and_query().map(|path| path.as_str()),
    )
    .await
}

async fn show_pair(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    prior: Stage,
    checks: bool,
    message: Option<&str>,
    return_path: Option<&str>,
) -> Response {
    match staff_elimination_page::prepare(app, actor, headers, prior, checks, message, return_path)
        .await
    {
        Ok(response) => response,
        Err(issue) => error(issue),
    }
}

async fn pair_submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((division, previous)): Path<(i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !checked(&headers, &fields) {
        return login::csrf_error();
    }
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    let Some(prior) = Stage::new(division, previous) else {
        return error(Error::Missing);
    };
    let result = match fields.get("action") {
        "set_challenge" | "delete_challenge" => {
            let challenger = fields.get("challenger_id").parse::<i32>().ok();
            let opponent = fields.get("challenged_id").parse::<i32>().ok();
            let remove = fields.get("action") == "delete_challenge";
            if let Some(challenger) = challenger.filter(|_| remove || opponent.is_some()) {
                store::set_pick(
                    &app,
                    prior,
                    challenger,
                    if remove { None } else { opponent },
                )
                .await
                .map(|_| format!("/outround_pairing/pair/{division}/{previous}/"))
            } else {
                Err(Error::Invalid("Choose valid teams for the challenge."))
            }
        }
        _ => store::pair_next(&app, user, prior).await.map(Stage::url),
    };
    match result {
        Ok(url) => access::found(url),
        Err(Error::Invalid(message)) => {
            show_pair(&app, actor, &headers, prior, false, Some(message), None).await
        }
        Err(issue) => error(issue),
    }
}

async fn assign_judges(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    body: Bytes,
) -> Response {
    assign(&app, actor, &headers, &uri, &body, Assignment::Judges, None).await
}
async fn assign_judges_legacy(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(division): Path<i32>,
    headers: HeaderMap,
    uri: Uri,
    body: Bytes,
) -> Response {
    assign(
        &app,
        actor,
        &headers,
        &uri,
        &body,
        Assignment::Judges,
        Some(division),
    )
    .await
}
async fn assign_rooms(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    body: Bytes,
) -> Response {
    assign(&app, actor, &headers, &uri, &body, Assignment::Rooms, None).await
}

async fn assign(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    uri: &Uri,
    body: &[u8],
    assignment: Assignment,
    fallback: Option<i32>,
) -> Response {
    let fields = Fields::parse(body);
    if !checked(headers, &fields) {
        return login::csrf_error();
    }
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    let mut stages: Vec<_> = ["selected_varsity", "selected_novice"]
        .iter()
        .enumerate()
        .filter_map(|(kind, key)| Stage::new(kind as i32, fields.get(key).parse().ok()?))
        .collect();
    if stages.is_empty()
        && let Some(division) = fallback.filter(|division| matches!(division, 0 | 1))
    {
        let Ok(size) = sqlx::query_scalar::<_, Option<i32>>(
            "SELECT MIN(num_teams) FROM tab_outround WHERE type_of_round = ?",
        )
        .bind(division)
        .fetch_one(&app.db)
        .await
        else {
            return unavailable();
        };
        if let Some(stage) = size.and_then(|size| Stage::new(division, size)) {
            stages.push(stage);
        }
    }
    let target = form_return_target(uri, headers, Some(&fields)).unwrap_or_else(|| "/".into());
    let (success, failure) = match assignment {
        Assignment::Judges => (
            "Outround judges assigned successfully.",
            "Got error during judge assignment",
        ),
        Assignment::Rooms => (
            "Outround rooms assigned successfully.",
            "Got error during room assignment",
        ),
    };
    match staff_elimination_assign::assign(app, user, &stages, assignment).await {
        Ok(()) => staff_roster_notice::saved(app, headers, &target, success),
        Err(Error::Assignment(message)) => {
            staff_roster_notice::failed(app, headers, &target, &message)
        }
        Err(_) => staff_roster_notice::failed(app, headers, &target, failure),
    }
}

async fn release(
    State(app): State<App>,
    Path((size, division)): Path<(i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !checked(&headers, &fields) {
        return login::csrf_error();
    }
    let Some(stage) = Stage::new(division, size) else {
        return error(Error::Missing);
    };
    match store::release(&app, stage).await {
        Ok(_) if fields.flag("redirect") => access::found(stage.url()),
        Ok(released) => private(Json(json!({"success": true, "pairing_released": released}))),
        Err(issue) => error(issue),
    }
}

async fn ballot_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    show_ballot(&app, actor, &headers, id, None).await
}

async fn show_ballot(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: i32,
    message: Option<&str>,
) -> Response {
    match staff_elimination_page::ballot(app, actor, headers, id, message).await {
        Ok(response) => response,
        Err(issue) => error(issue),
    }
}

async fn ballot_submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !checked(&headers, &fields) {
        return login::csrf_error();
    }
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    let same = fields.get("round_instance").is_empty()
        || fields.get("round_instance").parse::<i32>() == Ok(id);
    let result = if same {
        store::ballot(
            &app,
            user,
            id,
            fields.get("winner").parse().unwrap_or(0),
            fields.get("revision"),
        )
        .await
    } else {
        Err(Error::Invalid("The ballot specifies a different debate."))
    };
    match result {
        Ok(stage) => access::found(stage.url()),
        Err(Error::Invalid(message)) => show_ballot(&app, actor, &headers, id, Some(message)).await,
        Err(issue) => error(issue),
    }
}

async fn choice(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !checked(&headers, &fields) {
        return login::csrf_error();
    }
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    match store::choice(&app, user, id, fields.get("revision")).await {
        Ok((stage, _, _)) if fields.flag("redirect") => access::found(stage.url()),
        Ok((_, value, revision)) => {
            let choice = match value {
                1 => "Gov",
                2 => "Opp",
                _ => "No",
            };
            private(Json(
                json!({"success": true, "data": format!("{choice} choice"), "revision": revision}),
            ))
        }
        Err(issue) => error(issue),
    }
}

async fn forum(State(app): State<App>, Path(division): Path<i32>) -> Response {
    if !matches!(division, 0 | 1) {
        return error(Error::Missing);
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let (rows, _) = forum_results(&mut db, division).await?;
        db.commit().await?;
        let results: Vec<_> = rows
            .into_iter()
            .map(|(label, results)| context! {label, results})
            .collect();
        Ok::<_, sqlx::Error>(legacy_html::render(
            "outrounds/forum_result.html",
            minijinja::Value::UNDEFINED,
            context! {results},
            StatusCode::OK,
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

type ForumRows = Vec<(String, Vec<String>)>;

pub async fn forum_results(
    db: &mut sqlx::MySqlConnection,
    division: i32,
) -> Result<(ForumRows, Option<String>), sqlx::Error> {
    let pairs = board_snapshot::capture(&mut *db, Pairings::Outround).await?;
    let rows = sqlx::query_as::<_, (i32, String, i32, String, String)>(
            "SELECT t.id, t.name, b.seed, CONCAT(s.name, IF(h.id IS NULL, '', CONCAT(' / ', h.name))),
              COALESCE(GROUP_CONCAT(d.name ORDER BY d.name SEPARATOR ', '), '')
             FROM tab_team t JOIN tab_breakingteam b ON b.team_id = t.id
             JOIN tab_school s ON s.id = t.school_id LEFT JOIN tab_school h ON h.id = t.hybrid_school_id
             LEFT JOIN tab_team_debaters members ON members.team_id = t.id LEFT JOIN tab_debater d ON d.id = members.debater_id
             WHERE b.type_of_team = ? GROUP BY t.id, b.seed, s.name, h.id, h.name",
        ).bind(division).fetch_all(&mut *db).await?;
    let lines: BTreeMap<_, _> = rows
        .into_iter()
        .map(|(id, name, seed, school, people)| {
            (id, format!("[{seed}] {name} ({people}) from {school}"))
        })
        .collect();
    let mut champion = None;
    let mut rounds: BTreeMap<i32, Vec<String>> = BTreeMap::new();
    for pair in pairs
        .values()
        .filter(|pair| pair.division == Some(division))
    {
        let winner = pair.victor.unwrap_or_default();
        if !(1..=4).contains(&winner) {
            continue;
        }
        let (Some(gov_team), Some(opp_team)) = (&pair.gov_team, &pair.opp_team) else {
            continue;
        };
        let gov = lines.get(&gov_team.id).unwrap_or(&gov_team.name);
        let opp = lines.get(&opp_team.id).unwrap_or(&opp_team.name);
        if pair.num_teams == Some(2) {
            champion.get_or_insert_with(|| {
                if matches!(winner, 1 | 3) {
                    gov_team.name.clone()
                } else {
                    opp_team.name.clone()
                }
            });
        }
        let (winner, loser) = if matches!(winner, 1 | 3) {
            (format!("{gov} (GOV)"), format!("{opp} (OPP)"))
        } else {
            (format!("{opp} (OPP)"), format!("{gov} (GOV)"))
        };
        rounds
            .entry(pair.num_teams.unwrap_or_default())
            .or_default()
            .push(format!("{loser} drops to\n{winner}"));
    }
    let rows = rounds
        .into_iter()
        .rev()
        .map(|(size, entries)| {
            (
                format!("[{}] Ro{size}", if division == 0 { "V" } else { "N" }),
                entries,
            )
        })
        .collect::<Vec<_>>();
    Ok((rows, champion))
}
