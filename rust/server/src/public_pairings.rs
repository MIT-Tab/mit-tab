use std::collections::{HashMap, HashSet};

use axum::{
    Extension,
    extract::{Path, State},
    http::StatusCode,
    response::Response,
};
use minijinja::context;
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    settings::Settings,
    unavailable,
};

const DISPLAY_SETTINGS: &[&str] = &[
    "cur_round",
    "pairing_released",
    "debaters_public",
    "teams_public",
    "judges_public",
    "motions_enabled",
    "var_teams_visible",
    "nov_teams_visible",
];

#[derive(Serialize)]
struct Team {
    id: i32,
    name: String,
    debaters: String,
}

#[derive(Serialize)]
struct Judge {
    name: String,
    chair: bool,
}

#[derive(Serialize)]
struct Pairing<'a> {
    government: &'a Team,
    opposition: &'a Team,
    judges: Vec<Judge>,
    room: Option<String>,
}

#[derive(Serialize)]
struct Missing {
    government: String,
    opposition: String,
    chair: Option<String>,
    room: Option<String>,
}

pub async fn current(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    match pairings(&app, actor, None).await {
        Ok(response) => response,
        Err(_) => unavailable(),
    }
}

pub async fn previous(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(round): Path<i32>,
) -> Response {
    match pairings(&app, actor, Some(round)).await {
        Ok(response) => response,
        Err(_) => unavailable(),
    }
}

async fn pairings(app: &App, actor: Actor, selected: Option<i32>) -> Result<Response, sqlx::Error> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, DISPLAY_SETTINGS).await?;
    let current = settings.integer("cur_round", 1).saturating_sub(1);
    let round = selected.unwrap_or(current);
    if selected.is_some() && (round < 1 || round >= current) {
        return Ok(html::render(
            "error.html",
            context! {
                title => "Page Not Found", heading => "Page Not Found",
                nav => nav(actor, &settings),
            },
            StatusCode::NOT_FOUND,
        ));
    }
    let numbers = sqlx::query_scalar::<_, i32>(
        "SELECT DISTINCT round_number FROM tab_round WHERE round_number <= ? ORDER BY round_number",
    )
    .bind(current)
    .fetch_all(&mut *db)
    .await?;
    if selected.is_none() && numbers.is_empty() && !has_inrounds(&mut db).await? {
        return Ok(access::found("/public/access-error/"));
    }
    let (varsity, novice) = sqlx::query_as::<_, (i64, i64)>(
        "SELECT EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = 0 AND num_teams >= ?),
                EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = 1 AND num_teams >= ?)",
    )
    .bind(settings.integer("var_teams_visible", 256))
    .bind(settings.integer("nov_teams_visible", 256))
    .fetch_one(&mut *db)
    .await?;
    let published = selected.is_some() || settings.integer("pairing_released", 0) == 1;
    let members_public = settings.flag("debaters_public", true);
    let (team_count, byes, unpaired, rows, teams) = if published {
        let teams = paired_teams(&mut db, members_public).await?;
        let rows = assigned_pairs(&mut db, round).await?;
        let mut paired: HashSet<_> = rows
            .iter()
            .flat_map(|row| [row.government, row.opposition])
            .collect();
        let byes: Vec<_> = sqlx::query_scalar::<_, i32>(
            "SELECT bye_team_id FROM tab_bye WHERE round_number = ? ORDER BY id",
        )
        .bind(round)
        .fetch_all(&mut *db)
        .await?;
        paired.extend(byes.iter().copied());
        let checked = sqlx::query_scalar::<_, i32>(
            "SELECT id FROM tab_team WHERE checked_in = TRUE ORDER BY id",
        )
        .fetch_all(&mut *db)
        .await?;
        let unpaired: Vec<_> = checked
            .into_iter()
            .filter(|id| !paired.contains(id))
            .collect();
        (
            (rows.len() * 2 + byes.len()) as i64,
            byes,
            unpaired,
            rows,
            teams,
        )
    } else {
        let size = sqlx::query_scalar::<_, i64>(
            "SELECT 2 * (SELECT COUNT(*) FROM tab_round WHERE round_number = ?)
                   + (SELECT COUNT(*) FROM tab_bye WHERE round_number = ?)",
        )
        .bind(round)
        .bind(round)
        .fetch_one(&mut *db)
        .await?;
        (size, Vec::new(), Vec::new(), Vec::new(), HashMap::new())
    };
    db.commit().await?;
    let rows: Vec<_> = rows
        .into_iter()
        .map(|row| Pairing {
            government: &teams[&row.government],
            opposition: &teams[&row.opposition],
            room: row.room,
            judges: row.judges,
        })
        .collect();
    Ok(html::render(
        "pairings.html",
        context! {
            title => format!("Pairings for Round {round}"), nav => nav(actor, &settings),
            round, current, numbers, varsity => varsity != 0, novice => novice != 0,
            team_count, published, members_public, rows,
            byes => byes.iter().filter_map(|id| teams.get(id)).collect::<Vec<_>>(),
            unpaired => unpaired.iter().filter_map(|id| teams.get(id)).collect::<Vec<_>>(),
        },
        StatusCode::OK,
    ))
}

async fn has_inrounds(db: &mut MySqlConnection) -> Result<bool, sqlx::Error> {
    let exists = sqlx::query_scalar::<_, i64>("SELECT EXISTS(SELECT 1 FROM tab_round)")
        .fetch_one(db)
        .await?;
    Ok(exists != 0)
}

async fn paired_teams(
    db: &mut MySqlConnection,
    members_public: bool,
) -> Result<HashMap<i32, Team>, sqlx::Error> {
    let mut teams = HashMap::new();
    for (id, name) in sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_team")
        .fetch_all(&mut *db)
        .await?
    {
        teams.insert(
            id,
            Team {
                id,
                name,
                debaters: String::new(),
            },
        );
    }
    if !members_public {
        return Ok(teams);
    }
    for (id, name) in sqlx::query_as::<_, (i32, String)>(
        "SELECT m.team_id, d.name FROM tab_team_debaters m
         JOIN tab_debater d ON d.id = m.debater_id ORDER BY m.team_id, d.name",
    )
    .fetch_all(db)
    .await?
    {
        if let Some(team) = teams.get_mut(&id) {
            if !team.debaters.is_empty() {
                team.debaters.push_str(", ");
            }
            team.debaters.push_str(&name);
        }
    }
    Ok(teams)
}

struct Assignment {
    government: i32,
    opposition: i32,
    room: Option<String>,
    judges: Vec<Judge>,
}

async fn assigned_pairs(
    db: &mut MySqlConnection,
    number: i32,
) -> Result<Vec<Assignment>, sqlx::Error> {
    let mut judges_by_round: HashMap<_, Vec<_>> = HashMap::new();
    for (id, name, chair) in sqlx::query_as::<_, (i32, String, i64)>(
        "SELECT r.id, j.name, COALESCE(j.id = r.chair_id, 0)
         FROM tab_round r JOIN tab_round_judges m ON m.round_id = r.id
         JOIN tab_judge j ON j.id = m.judge_id
         WHERE r.round_number = ? ORDER BY r.id, j.name",
    )
    .bind(number)
    .fetch_all(&mut *db)
    .await?
    {
        judges_by_round.entry(id).or_default().push(Judge {
            name,
            chair: chair != 0,
        });
    }
    let mut rows = sqlx::query_as::<_, (i32, i32, i32, String, Option<String>)>(
        "SELECT r.id, r.gov_team_id, r.opp_team_id, government.name, room.name
         FROM tab_round r JOIN tab_team government ON government.id = r.gov_team_id
         LEFT JOIN tab_room room ON room.id = r.room_id
         WHERE r.round_number = ? ORDER BY r.id",
    )
    .bind(number)
    .fetch_all(db)
    .await?;
    rows.sort_by(|left, right| left.3.cmp(&right.3));
    Ok(rows
        .into_iter()
        .map(|(id, government, opposition, _, room)| Assignment {
            government,
            opposition,
            room,
            judges: judges_by_round.remove(&id).unwrap_or_default(),
        })
        .collect())
}

pub async fn missing(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    match missing_ballots(&app, actor).await {
        Ok(response) => response,
        Err(_) => unavailable(),
    }
}

async fn missing_ballots(app: &App, actor: Actor) -> Result<Response, sqlx::Error> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, DISPLAY_SETTINGS).await?;
    if settings.integer("pairing_released", 0) != 1 || !has_inrounds(&mut db).await? {
        return Ok(access::found("/public/access-error/"));
    }
    let round = settings.integer("cur_round", 1).saturating_sub(1);
    let mut rows = sqlx::query_as::<_, (String, String, Option<String>, Option<String>)>(
        "SELECT government.name, opposition.name, chair.name, room.name
         FROM tab_round r JOIN tab_team government ON government.id = r.gov_team_id
         JOIN tab_team opposition ON opposition.id = r.opp_team_id
         LEFT JOIN tab_judge chair ON chair.id = r.chair_id
         LEFT JOIN tab_room room ON room.id = r.room_id
         WHERE r.round_number = ? AND r.victor = 0 ORDER BY r.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?;
    db.commit().await?;
    rows.sort_by(|left, right| {
        left.2
            .as_deref()
            .unwrap_or_default()
            .cmp(right.2.as_deref().unwrap_or_default())
    });
    let rows: Vec<_> = rows
        .into_iter()
        .map(|(government, opposition, chair, room)| Missing {
            government,
            opposition,
            chair,
            room,
        })
        .collect();
    Ok(html::render(
        "missing_ballots.html",
        context! {
            title => "Missing Ballots", nav => nav(actor, &settings), rows,
        },
        StatusCode::OK,
    ))
}

fn nav(actor: Actor, settings: &Settings) -> Navigation {
    let mut nav = Navigation::new(actor, settings.flag("motions_enabled", false));
    nav.teams_public = settings.flag("teams_public", false);
    nav.judges_public = settings.flag("judges_public", false);
    nav
}
