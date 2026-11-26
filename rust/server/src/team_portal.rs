use std::collections::{BTreeSet, HashMap};

use axum::{
    Extension, Form,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    ballot_entry::Submitted,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    settings::Settings,
    staff_pairing::lock_round,
    unavailable,
};

const SEARCH: &str = "/team_portal/";
const SETTINGS: &[&str] = &["cur_round", "judges_public", "motions_enabled"];

struct Access {
    name: bool,
    scratches: bool,
    slots: u16,
}

async fn config(db: &mut MySqlConnection) -> Result<Access, sqlx::Error> {
    let (name, scratches, slots) = sqlx::query_as::<_, (bool, bool, u16)>(
        "SELECT team_name_changes_allowed, disc_scratches_open, disc_scratch_quantity
         FROM registration_registrationconfig WHERE id = 1",
    )
    .fetch_optional(db)
    .await?
    .unwrap_or((false, false, 0));
    Ok(Access {
        name,
        scratches,
        slots,
    })
}

#[derive(Serialize)]
struct Team {
    id: i32,
    name: String,
    code: String,
    school: String,
    seed: &'static str,
}

fn seed_name(seed: i32) -> &'static str {
    match seed {
        3 => "Full seed",
        2 => "Half seed",
        1 => "Free seed",
        _ => "Unseeded",
    }
}

fn valid_code(code: &str) -> bool {
    !code.is_empty()
        && code.len() <= 255
        && code
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

async fn find(
    db: &mut MySqlConnection,
    code: &str,
    lock: bool,
) -> Result<Option<Team>, sqlx::Error> {
    let query = if lock {
        "SELECT t.id, t.name, t.team_code, s.name, t.seed FROM tab_team t JOIN tab_school s ON s.id = t.school_id
         WHERE t.team_code = ? FOR UPDATE"
    } else {
        "SELECT t.id, t.name, t.team_code, s.name, t.seed FROM tab_team t JOIN tab_school s ON s.id = t.school_id
         WHERE t.team_code = ?"
    };
    Ok(
        sqlx::query_as::<_, (i32, String, String, String, i32)>(query)
            .bind(code)
            .fetch_optional(db)
            .await?
            .map(|(id, name, code, school, seed)| Team {
                id,
                name,
                code,
                school,
                seed: seed_name(seed),
            }),
    )
}

fn search_page(actor: Actor, headers: &HeaderMap, invalid: bool) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let response = html::render(
        "code_lookup.html",
        context! {
            title => "Team Portal", label => "Team code", field => "team_code", button => "Open Team Portal",
            csrf, message => invalid.then_some("No team was found for that code."),
            has_error => invalid, nav => Navigation::new(actor, true),
        },
        if invalid {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn search(Extension(actor): Extension<Actor>, headers: HeaderMap) -> Response {
    search_page(actor, &headers, false)
}

pub async fn lookup(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(fields): Form<Submitted>,
) -> Response {
    if !valid_csrf(&headers, &fields) {
        return csrf_error();
    }
    let code = fields.get("team_code").map_or("", |code| code.trim());
    if !valid_code(code) {
        return search_page(actor, &headers, true);
    }
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    match find(&mut db, code, false).await {
        Ok(Some(team)) if valid_code(&team.code) => access::found(target(&team.code)),
        Ok(_) => search_page(actor, &headers, true),
        Err(_) => unavailable(),
    }
}

fn target(code: &str) -> String {
    format!("{SEARCH}{code}/")
}

fn valid_csrf(headers: &HeaderMap, fields: &Submitted) -> bool {
    login::valid_csrf(
        headers,
        fields.get("csrfmiddlewaretoken").map_or("", String::as_str),
    )
}

fn csrf_error() -> Response {
    private((StatusCode::FORBIDDEN, "CSRF verification failed."))
}

#[derive(Serialize)]
struct Member {
    name: String,
    school: Option<String>,
    novice: bool,
}

#[derive(Serialize)]
struct Judge {
    id: i32,
    name: String,
}

#[derive(Serialize)]
struct Pairing {
    round: i32,
    opponent: String,
    members: String,
    side: &'static str,
    room: Option<String>,
    chair: Option<String>,
    wings: Vec<String>,
}

async fn recent(
    db: &mut MySqlConnection,
    team: i32,
    through: i32,
) -> Result<Vec<Pairing>, sqlx::Error> {
    if through < 1 {
        return Ok(Vec::new());
    }
    let mut panels = HashMap::<_, Vec<_>>::new();
    for (id, wing) in sqlx::query_as::<_, (i32, String)>(
        "SELECT r.id, j.name FROM tab_round r JOIN tab_round_judges x ON x.round_id = r.id
         JOIN tab_judge j ON j.id = x.judge_id WHERE r.round_number <= ?
         AND ? IN (r.gov_team_id, r.opp_team_id) AND (r.chair_id IS NULL OR j.id <> r.chair_id)
         ORDER BY j.name, j.id",
    )
    .bind(through)
    .bind(team)
    .fetch_all(&mut *db)
    .await?
    {
        panels.entry(id).or_default().push(wing);
    }
    type Row = (
        i32,
        i32,
        String,
        String,
        bool,
        Option<String>,
        Option<String>,
    );
    Ok(sqlx::query_as::<_, Row>(
        "SELECT r.id, r.round_number, o.name, COALESCE((
           SELECT GROUP_CONCAT(d.name ORDER BY d.name SEPARATOR ', ') FROM tab_team_debaters x
           JOIN tab_debater d ON d.id = x.debater_id WHERE x.team_id = o.id
         ), ''), r.gov_team_id = ?, m.name, j.name
         FROM tab_round r JOIN tab_team o ON o.id = IF(r.gov_team_id = ?, r.opp_team_id, r.gov_team_id)
         LEFT JOIN tab_room m ON m.id = r.room_id LEFT JOIN tab_judge j ON j.id = r.chair_id
         WHERE r.round_number <= ? AND ? IN (r.gov_team_id, r.opp_team_id)
         ORDER BY r.round_number, r.id",
    ).bind(team).bind(team).bind(through).bind(team).fetch_all(db).await?.into_iter().map(
        |(id, round, opponent, members, govt, room, chair)| Pairing {
            round, opponent, members, side: if govt { "Gov" } else { "Opp" },
            room, chair, wings: panels.remove(&id).unwrap_or_default(),
        }
    ).collect())
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(code): Path<String>,
) -> Response {
    view(&app, actor, &headers, &code, None).await
}

async fn view(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    code: &str,
    error: Option<&'static str>,
) -> Response {
    if !valid_code(code) {
        return not_found();
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let Some(team) = find(&mut db, code, false).await? else { return Ok(not_found()); };
        let settings = Settings::load(&mut db, SETTINGS).await?;
        let config = config(&mut db).await?;
        let cur = settings.integer("cur_round", 1);
        let locked = cur > 1;
        let judges_public = settings.flag("judges_public", false);
        let disc_open = config.scratches && judges_public;
        let can_scratch = disc_open && config.slots > 0 && !locked;
        let can_name = config.name && !locked;
        let people: Vec<_> = sqlx::query_as::<_, (String, Option<String>, i32)>(
            "SELECT d.name, s.name, d.novice_status FROM tab_team_debaters x
             JOIN tab_debater d ON d.id = x.debater_id LEFT JOIN tab_school s ON s.id = d.school_id
             WHERE x.team_id = ? ORDER BY d.name, d.id",
        ).bind(team.id).fetch_all(&mut *db).await?.into_iter().map(
            |(name, school, status)| Member { name, school, novice: status == 1 }
        ).collect();
        let current = if judges_public {
            sqlx::query_as::<_, (i32, String)>(
                "SELECT j.id, j.name FROM tab_scratch x JOIN tab_judge j ON j.id = x.judge_id
                 WHERE x.team_id = ? AND x.scratch_type = 0 ORDER BY j.name, j.id",
            ).bind(team.id).fetch_all(&mut *db).await?
        } else { Vec::new() };
        let eligible = if can_scratch {
            sqlx::query_as::<_, (i32, String)>(
                "SELECT j.id, j.name FROM tab_judge j WHERE NOT EXISTS(
                    SELECT 1 FROM tab_scratch s WHERE s.judge_id = j.id AND s.team_id = ? AND s.scratch_type = 1
                 ) ORDER BY j.name, j.id",
            ).bind(team.id).fetch_all(&mut *db).await?
        } else { Vec::new() };
        let judges: Vec<_> = eligible.into_iter().map(|(id, name)| Judge { id, name }).collect();
        let scratch_slots: Vec<_> = if can_scratch {
            (0..usize::from(config.slots)).map(|index| current.get(index).map(|(id, _)| *id)).collect()
        } else { Vec::new() };
        let history = recent(&mut db, team.id, cur.saturating_sub(2)).await?;
        db.commit().await?;
        let Ok(csrf) = html::page_csrf(headers) else { return Ok(unavailable()); };
        let response = html::render("team_portal.html", context! {
            title => "Team Portal", team, people, current, eligible => judges, scratch_slots, history, csrf,
            can_name, can_scratch, disc_open, judges_public, locked, message => error, has_error => error.is_some(),
            nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        }, if error.is_some() { StatusCode::UNPROCESSABLE_ENTITY } else { StatusCode::OK });
        Ok::<_, sqlx::Error>(html::form_response(response, headers, &csrf))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

fn not_found() -> Response {
    private((StatusCode::NOT_FOUND, "Team code not found."))
}

enum UpdateError {
    Storage,
    NotFound,
    Invalid(&'static str),
}

impl From<sqlx::Error> for UpdateError {
    fn from(_: sqlx::Error) -> Self {
        Self::Storage
    }
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(code): Path<String>,
    Form(fields): Form<Submitted>,
) -> Response {
    if !valid_csrf(&headers, &fields) {
        return csrf_error();
    }
    if !valid_code(&code) {
        return not_found();
    }
    match save(&app, &code, &fields).await {
        Ok(code) => access::found(target(&code)),
        Err(UpdateError::Storage) => unavailable(),
        Err(UpdateError::NotFound) => not_found(),
        Err(UpdateError::Invalid(error)) => view(&app, actor, &headers, &code, Some(error)).await,
    }
}

async fn save(app: &App, code: &str, fields: &Submitted) -> Result<String, UpdateError> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    sqlx::query("SELECT id FROM registration_registrationconfig WHERE id = 1 FOR UPDATE")
        .fetch_optional(&mut *db)
        .await?;
    let team = find(&mut db, code, true)
        .await?
        .ok_or(UpdateError::NotFound)?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    if settings.integer("cur_round", 1) > 1 {
        return Err(UpdateError::Invalid(
            "Edits close once Round 1 is paired. Contact tab for changes.",
        ));
    }
    let config = config(&mut db).await?;
    match fields.get("action").map(String::as_str) {
        Some("update_name") => {
            if !config.name {
                return Err(UpdateError::Invalid(
                    "Team name changes are currently disabled.",
                ));
            }
            let name = fields.get("name").map_or("", |name| name.trim());
            if name.is_empty() || name.chars().count() > 35 {
                return Err(UpdateError::Invalid(
                    "Enter a team name of at most 35 characters.",
                ));
            }
            let exists = sqlx::query_scalar::<_, bool>(
                "SELECT EXISTS(SELECT 1 FROM tab_team WHERE name = ? AND id <> ?)",
            )
            .bind(name)
            .bind(team.id)
            .fetch_one(&mut *db)
            .await?;
            if exists {
                return Err(UpdateError::Invalid(
                    "A team with this name already exists.",
                ));
            }
            sqlx::query("UPDATE tab_team SET name = ? WHERE id = ?")
                .bind(name)
                .bind(team.id)
                .execute(&mut *db)
                .await?;
        }
        Some("update_scratches") => {
            if !config.scratches || config.slots == 0 || !settings.flag("judges_public", false) {
                return Err(UpdateError::Invalid(
                    "Discretionary scratches are currently closed.",
                ));
            }
            replace_scratches(&mut db, team.id, config.slots, fields).await?;
        }
        _ => return Err(UpdateError::Invalid("Choose a team portal action.")),
    }
    db.commit().await?;
    Ok(team.code)
}

async fn replace_scratches(
    db: &mut MySqlConnection,
    id: i32,
    limit: u16,
    fields: &Submitted,
) -> Result<(), UpdateError> {
    let mut wanted = BTreeSet::new();
    const INVALID: UpdateError =
        UpdateError::Invalid("One or more selected judges cannot be scratched.");
    for index in 0..limit {
        let Some(value) = fields
            .get(&format!("scratch_{index}"))
            .filter(|value| !value.is_empty())
        else {
            continue;
        };
        let judge = value
            .parse::<i32>()
            .ok()
            .filter(|&id| id > 0)
            .ok_or(INVALID)?;
        if !wanted.insert(judge) {
            return Err(UpdateError::Invalid("Select each judge at most once."));
        }
        let eligible = sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM tab_judge j WHERE j.id = ? AND NOT EXISTS(
                SELECT 1 FROM tab_scratch s WHERE s.judge_id = j.id AND s.team_id = ? AND s.scratch_type = 1))",
        ).bind(judge).bind(id).fetch_one(&mut *db).await?;
        if !eligible {
            return Err(INVALID);
        }
    }
    let existing: BTreeSet<_> = sqlx::query_scalar::<_, i32>(
        "SELECT judge_id FROM tab_scratch WHERE team_id = ? AND scratch_type = 0 FOR UPDATE",
    )
    .bind(id)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    for judge in existing.difference(&wanted) {
        sqlx::query(
            "DELETE FROM tab_scratch WHERE team_id = ? AND judge_id = ? AND scratch_type = 0",
        )
        .bind(id)
        .bind(judge)
        .execute(&mut *db)
        .await?;
    }
    for judge in wanted.difference(&existing) {
        sqlx::query("INSERT INTO tab_scratch (team_id, judge_id, scratch_type, created_at) VALUES (?, ?, 0, UTC_TIMESTAMP(6))")
            .bind(id).bind(judge).execute(&mut *db).await?;
    }
    Ok(())
}
