use std::collections::{BTreeMap, BTreeSet};

use axum::{
    Extension, Json, Router,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::{get, post},
};
use minijinja::context;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::{
    App, access,
    board_snapshot::{self, Pairings, Snapshot},
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_form::Fields,
    settings::Settings,
    staff_elimination_assign::{self, Assignment},
    staff_elimination_store::{self as store, Error, Stage},
    unavailable,
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
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(settings) = Settings::load(&mut db, store::SETTINGS).await else {
        return unavailable();
    };
    let Ok(problems) = store::readiness(&mut db, &settings).await else {
        return unavailable();
    };
    let Ok(existing) = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM tab_outround)")
        .fetch_one(&mut *db)
        .await
    else {
        return unavailable();
    };
    let Ok(rooms) =
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM tab_roomcheckin WHERE round_number = 0")
            .fetch_one(&mut *db)
            .await
    else {
        return unavailable();
    };
    let [varsity, novice] = store::break_sizes(&settings);
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let result = html::render(
        "staff_break.html",
        context! {
            title => "Break Teams", nav => Navigation::new(actor, true), csrf, problems, rooms, existing,
            varsity, novice, error => message,
        },
        if message.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(result, headers, &csrf)
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
    match store::break_tournament(&app, user, fields.flag("confirm")).await {
        Ok(()) => access::found(ROOT),
        Err(Error::Invalid(message)) => show_break(&app, actor, &headers, Some(message)).await,
        Err(issue) => error(issue),
    }
}

async fn default_stage(State(app): State<App>) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(row) = sqlx::query_as::<_, (i32, i32)>(
        "SELECT type_of_round, MIN(num_teams) FROM tab_outround GROUP BY type_of_round ORDER BY type_of_round LIMIT 1",
    ).fetch_optional(&mut *db).await else { return unavailable(); };
    if let Some(stage) = row.and_then(|(division, size)| Stage::new(division, size)) {
        return access::found(stage.url());
    }
    let Ok(settings) = Settings::load(&mut db, store::SETTINGS).await else {
        return unavailable();
    };
    let counts = store::break_sizes(&settings);
    let stage = counts.into_iter().enumerate().find_map(|(kind, count)| {
        store::stage_size(count).and_then(|size| Stage::new(kind as i32, size))
    });
    match stage {
        Some(stage) => access::found(stage.url()),
        None => access::found(BREAK),
    }
}

#[derive(Default, Deserialize)]
struct Select {
    select_varsity: Option<String>,
    select_novice: Option<String>,
}

#[derive(Serialize)]
struct Round<'a> {
    id: i32,
    data: &'a Snapshot,
}

#[derive(Serialize)]
struct Section<'a> {
    stage: Stage,
    label: String,
    debates: Vec<Round<'a>>,
    released: bool,
    next: bool,
    first_challenge: bool,
    survivors: Vec<store::Entry>,
}

async fn stage_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((division, size)): Path<(i32, i32)>,
    Query(selection): Query<Select>,
    headers: HeaderMap,
) -> Response {
    let Some(fallback) = Stage::new(division, size) else {
        return error(Error::Missing);
    };
    match show_stage(&app, actor, &headers, selection, fallback).await {
        Ok(page) => page,
        Err(issue) => error(issue),
    }
}

async fn show_stage(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    selection: Select,
    fallback: Stage,
) -> Result<Response, Error> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, store::SETTINGS).await?;
    let snapshots = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let mut options = BTreeSet::new();
    for (division, count) in store::break_sizes(&settings).into_iter().enumerate() {
        if let Some(mut size) = store::stage_size(count) {
            while size >= 2 {
                options.insert((division as i32, -size));
                size /= 2;
            }
        }
    }
    for debate in snapshots.values() {
        if let Some((division, size)) = debate.division.zip(debate.num_teams) {
            options.insert((division, -size));
        }
    }
    options.insert((fallback.division, -fallback.size));
    let mut selected = Vec::new();
    for (kind, size) in [selection.select_varsity, selection.select_novice]
        .into_iter()
        .enumerate()
    {
        if let Some(stage) = size
            .and_then(|size| size.parse().ok())
            .and_then(|size| Stage::new(kind as i32, size))
            && options.contains(&(stage.division, -stage.size))
        {
            selected.push(stage);
        }
    }
    if selected.is_empty() {
        selected.push(fallback);
    }

    let mut sections = Vec::new();
    for stage in &selected {
        let debates: Vec<_> = snapshots
            .iter()
            .filter(|(_, value)| stage.matches(value))
            .map(|(&id, data)| Round { id, data })
            .collect();
        let released =
            settings.integer(store::VISIBILITY[stage.division as usize], 256) <= stage.size;
        let next = stage.size > 2
            && !debates.is_empty()
            && debates.iter().all(|entry| entry.data.victor != Some(0));
        let first_challenge = debates.is_empty()
            && Stage::new(stage.division, stage.size * 2).is_some()
            && store::prepare(
                &mut db,
                &settings,
                Stage {
                    division: stage.division,
                    size: stage.size * 2,
                },
            )
            .await
            .is_ok_and(|prepared| prepared.enabled);
        let present: BTreeSet<_> = debates
            .iter()
            .flat_map(|row| [row.data.gov_team.as_ref(), row.data.opp_team.as_ref()])
            .flatten()
            .map(|team| team.id)
            .collect();
        let survivors = store::eligible(&mut db, stage.division, &snapshots)
            .await?
            .into_iter()
            .filter(|seed| !present.contains(&seed.team))
            .collect();
        sections.push(Section {
            stage: *stage,
            label: stage.label(),
            debates,
            released,
            next,
            first_challenge,
            survivors,
        });
    }
    let stages: Vec<_> = options
        .into_iter()
        .map(|(division, size)| (division, -size))
        .collect();
    let selection: BTreeMap<_, _> = selected
        .into_iter()
        .map(|stage| (stage.division, stage.size))
        .collect();
    let vars = selection.get(&0).copied();
    let novs = selection.get(&1).copied();
    db.commit().await?;
    let csrf = html::page_csrf(headers).map_err(|_| Error::Database)?;
    let body = html::render(
        "staff_elimination.html",
        context! {
            title => "Elimination Pairings", nav => Navigation::new(actor, true), sections, stages, vars, novs, csrf,
        },
        StatusCode::OK,
    );
    Ok(html::form_response(body, headers, &csrf))
}

async fn pair_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((division, previous)): Path<(i32, i32)>,
    headers: HeaderMap,
) -> Response {
    let Some(prior) = Stage::new(division, previous) else {
        return error(Error::Missing);
    };
    show_pair(&app, actor, &headers, prior, None).await
}

async fn show_pair(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    prior: Stage,
    message: Option<&str>,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(settings) = Settings::load(&mut db, store::SETTINGS).await else {
        return unavailable();
    };
    let state = match store::prepare(&mut db, &settings, prior).await {
        Ok(state) => Some(state),
        Err(Error::Invalid(_)) => None,
        Err(issue) => return error(issue),
    };
    let pairs: Vec<_> = state
        .as_ref()
        .into_iter()
        .flat_map(|state| &state.picks)
        .filter_map(|(first, second)| {
            let state = state.as_ref()?;
            let a = state.entries.iter().find(|entry| entry.team == *first)?;
            let b = state.entries.iter().find(|entry| entry.team == *second)?;
            Some((a.team, &a.name, &b.name))
        })
        .collect();
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let response = html::render(
        "elimination_prepare.html",
        context! {
            title => format!("Prepare {} Round of {}", prior.division_name(), prior.size / 2),
            nav => Navigation::new(actor, true), csrf, pairs,
            challenge => state.as_ref().is_some_and(|state| state.enabled),
            teams => state.as_ref().map(|state| &state.entries), ready => state.is_some(),
            back => prior.url(), error => message,
        },
        if message.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
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
            show_pair(&app, actor, &headers, prior, Some(message)).await
        }
        Err(issue) => error(issue),
    }
}

async fn assign_judges(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    assign(&app, actor, &headers, &body, Assignment::Judges, None).await
}
async fn assign_judges_legacy(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(division): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    assign(
        &app,
        actor,
        &headers,
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
    body: Bytes,
) -> Response {
    assign(&app, actor, &headers, &body, Assignment::Rooms, None).await
}

async fn assign(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
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
    match staff_elimination_assign::assign(app, user, &stages, assignment).await {
        Ok(()) => {
            let Some(first) = stages.first() else {
                return access::found(ROOT);
            };
            let mut url = first.url();
            if stages.len() == 2 {
                url.push_str(&format!(
                    "?select_varsity={}&select_novice={}",
                    stages[0].size, stages[1].size
                ));
            }
            access::found(url)
        }
        Err(issue) => error(issue),
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
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(mut pairings) = board_snapshot::capture(&mut db, Pairings::Outround).await else {
        return unavailable();
    };
    let Some(pairing) = pairings.remove(&id) else {
        return error(Error::Missing);
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let revision = pairing.revision();
    let response = html::render(
        "elimination_ballot.html",
        context! {
            title => "Elimination Ballot", nav => Navigation::new(actor, true), csrf, pairing, id, revision, error => message,
        },
        if message.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
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
    match store::choice(&app, user, id).await {
        Ok((stage, _)) if fields.flag("redirect") => access::found(stage.url()),
        Ok((_, value)) => {
            let choice = match value {
                1 => "Gov",
                2 => "Opp",
                _ => "No",
            };
            private(Json(
                json!({"success": true, "data": format!("{choice} choice")}),
            ))
        }
        Err(issue) => error(issue),
    }
}

async fn forum(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(division): Path<i32>,
) -> Response {
    if !matches!(division, 0 | 1) {
        return error(Error::Missing);
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let pairs = board_snapshot::capture(&mut db, Pairings::Outround).await?;
        let rows = sqlx::query_as::<_, (i32, String, i32, String, String)>(
            "SELECT t.id, t.name, b.seed, CONCAT(s.name, IF(h.id IS NULL, '', CONCAT(' / ', h.name))),
              COALESCE(GROUP_CONCAT(d.name ORDER BY d.name SEPARATOR ', '), '')
             FROM tab_team t JOIN tab_breakingteam b ON b.team_id = t.id
             JOIN tab_school s ON s.id = t.school_id LEFT JOIN tab_school h ON h.id = t.hybrid_school_id
             LEFT JOIN tab_team_debaters members ON members.team_id = t.id LEFT JOIN tab_debater d ON d.id = members.debater_id
             WHERE b.type_of_team = ? GROUP BY t.id, b.seed, s.name, h.id, h.name",
        ).bind(division).fetch_all(&mut *db).await?;
        let lines: BTreeMap<_, _> = rows.into_iter().map(|(id, name, seed, school, people)| {
            (id, format!("[{seed}] {name} ({people}) from {school}"))
        }).collect();
        let mut rounds: BTreeMap<i32, Vec<String>> = BTreeMap::new();
        for pair in pairs.values().filter(|pair| pair.division == Some(division)) {
            let winner = pair.victor.unwrap_or_default();
            if !(1..=4).contains(&winner) { continue; }
            let (Some(gov), Some(opp)) = (&pair.gov_team, &pair.opp_team) else { continue; };
            let gov = lines.get(&gov.id).unwrap_or(&gov.name);
            let opp = lines.get(&opp.id).unwrap_or(&opp.name);
            let (winner, loser) = if matches!(winner, 1 | 3) {
                (format!("{gov} (GOV)"), format!("{opp} (OPP)"))
            } else { (format!("{opp} (OPP)"), format!("{gov} (GOV)")) };
            rounds.entry(pair.num_teams.unwrap_or_default()).or_default().push(format!("{loser} drops to\n{winner}"));
        }
        db.commit().await?;
        let rows = rounds.into_iter().rev().map(|(size, entries)| (format!("[{}] Ro{size}", if division == 0 { "V" } else { "N" }), entries))
            .collect::<Vec<_>>();
        Ok::<_, sqlx::Error>(html::render("elimination_forum.html", context! {
            title => "Elimination Results", nav => Navigation::new(actor, true), rows,
        }, StatusCode::OK))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}
