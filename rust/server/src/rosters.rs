use std::collections::{HashMap, HashSet};

use axum::{
    Extension,
    extract::{Query, State},
    http::StatusCode,
    response::Response,
};
use chrono::{FixedOffset, NaiveDateTime};
use minijinja::context;
use mittab_tabulation::calculator::{award_points, expected_records, tournament_value};
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    public_cache::{self, Page},
    settings::Settings,
    unavailable,
};

const SETTINGS: &[&str] = &[
    "teams_public",
    "judges_public",
    "debaters_public",
    "motions_enabled",
    "tot_rounds",
];

#[derive(Serialize)]
struct Team {
    name: String,
    school: String,
    hybrid: Option<String>,
    debaters: String,
    checked_in: bool,
    division: &'static str,
    seed: String,
}

#[derive(Serialize)]
struct Judge {
    name: String,
    affiliations: String,
    availability: Vec<bool>,
}

#[derive(Serialize)]
struct Change {
    message: String,
    timestamp_est: String,
}

#[derive(Default)]
struct TeamMembers {
    names: Vec<String>,
    novice: bool,
    varsity: bool,
}

#[derive(Deserialize)]
pub struct TeamFilter {
    #[serde(default)]
    teams: String,
}

#[derive(Serialize)]
struct Record {
    label: String,
    count: usize,
}

pub async fn teams(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::Teams, async |db| {
        let settings = Settings::load(db, SETTINGS).await?;
        if !settings.flag("teams_public", false) {
            return Ok(access::found("/public/access-error/"));
        }
        let teams = team_rows(db, settings.flag("debaters_public", true)).await?;
        let changes = changes(db, "teams", settings.flag("debaters_public", true)).await?;
        let checked_in = teams.iter().filter(|team| team.checked_in).count();
        Ok(html::render(
            "teams.html",
            context! {
                title => "Team List", nav => navigation(actor, &settings),
                checked_in, checked_in_points => tournament_value(checked_in),
                total_points => tournament_value(teams.len()), teams, changes,
            },
            StatusCode::OK,
        ))
    })
    .await
}

pub async fn judges(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::Judges, async |db| {
        let settings = Settings::load(db, SETTINGS).await?;
        if !settings.flag("judges_public", false) {
            return Ok(access::found("/public/access-error/"));
        }
        let rounds: Vec<_> = (1..=settings.integer("tot_rounds", 5)).collect();
        let judges = judge_rows(db, &rounds).await?;
        let changes = changes(db, "judges", settings.flag("debaters_public", true)).await?;
        Ok(html::render(
            "judges.html",
            context! {
                title => "Judge List", nav => navigation(actor, &settings),
                judges, rounds, changes,
            },
            StatusCode::OK,
        ))
    })
    .await
}

pub async fn records(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Query(filter): Query<TeamFilter>,
) -> Response {
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let Ok(settings) = Settings::load(&mut db, SETTINGS).await else {
        return unavailable();
    };
    if !settings.flag("teams_public", false) {
        return access::found("/public/access-error/");
    }
    let checked_in = filter.teams == "checked-in";
    let count = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM tab_team WHERE ? = FALSE OR checked_in = TRUE",
    )
    .bind(checked_in)
    .fetch_one(&mut *db)
    .await;
    let Ok(team_count) = count else {
        return unavailable();
    };
    if db.commit().await.is_err() {
        return unavailable();
    }
    let num_rounds = settings.integer("tot_rounds", 5).max(0) as usize;
    let team_count = team_count as usize;
    let brackets = expected_records(team_count, num_rounds);
    let record_rows: Vec<_> = (0..=num_rounds)
        .map(|losses| {
            brackets
                .iter()
                .enumerate()
                .rev()
                .map(|(index, round)| {
                    round.get(losses).map(|&count| Record {
                        label: format!("{}-{losses}", index + 1 - losses),
                        count,
                    })
                })
                .collect::<Vec<_>>()
        })
        .collect();
    let awards = award_points(team_count);
    html::render(
        "expected_records.html",
        context! {
            title => "Expected Records", nav => navigation(actor, &settings),
            checked_in, team_count, num_rounds, value => tournament_value(team_count),
            round_numbers => (1..=num_rounds).rev().collect::<Vec<_>>(),
            table_width => num_rounds.saturating_mul(72),
            toty => awards.toty.into_iter().map(|(label, points)| (label, points.to_string())).collect::<Vec<_>>(),
            soty => awards.soty.into_iter().map(|(place, points)| (place, points.to_string())).collect::<Vec<_>>(),
            record_rows,
        },
        StatusCode::OK,
    )
}

fn navigation(actor: Actor, settings: &Settings) -> Navigation {
    let mut nav = Navigation::new(actor, settings.flag("motions_enabled", false));
    nav.teams_public = settings.flag("teams_public", false);
    nav.judges_public = settings.flag("judges_public", false);
    nav
}

async fn team_rows(
    db: &mut MySqlConnection,
    names_visible: bool,
) -> Result<Vec<Team>, sqlx::Error> {
    let member_rows = sqlx::query_as::<_, (i32, i32, Option<String>)>(
        "SELECT m.team_id, d.novice_status, IF(?, d.name, NULL)
         FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id
         ORDER BY d.name",
    )
    .bind(names_visible)
    .fetch_all(&mut *db)
    .await?;
    let mut membership: HashMap<_, TeamMembers> = HashMap::new();
    for (id, division, name) in member_rows {
        let members = membership.entry(id).or_default();
        members.record_division(division);
        if let Some(name) = name {
            members.names.push(name);
        }
    }

    let rows = sqlx::query_as::<_, (i32, String, String, Option<String>, bool, i32)>(
        "SELECT t.id, t.name, s.name, h.name, t.checked_in, t.seed
         FROM tab_team t JOIN tab_school s ON t.school_id = s.id
         LEFT JOIN tab_school h ON t.hybrid_school_id = h.id
         ORDER BY t.checked_in DESC, s.name, t.id",
    )
    .fetch_all(db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(id, name, school, hybrid, checked_in, seed)| {
            let members = membership.remove(&id).unwrap_or_default();
            let division = match (members.novice, members.varsity) {
                (true, true) => "Varsity / Novice",
                (true, false) => "Novice",
                (false, true) => "Varsity",
                _ => "—",
            };
            let seed = match seed {
                0 => "Unseeded".into(),
                1 => "Free Seed".into(),
                2 => "Half Seed".into(),
                3 => "Full Seed".into(),
                seed => seed.to_string(),
            };
            Team {
                name,
                school,
                hybrid,
                checked_in,
                seed,
                division,
                debaters: members.names.join(", "),
            }
        })
        .collect())
}

impl TeamMembers {
    fn record_division(&mut self, division: i32) {
        self.varsity |= division == 0;
        self.novice |= division == 1;
    }
}

async fn judge_rows(db: &mut MySqlConnection, rounds: &[i32]) -> Result<Vec<Judge>, sqlx::Error> {
    let mut schools: HashMap<_, Vec<_>> = HashMap::new();
    for (id, school) in sqlx::query_as::<_, (i32, String)>(
        "SELECT m.judge_id, s.name FROM tab_judge_schools m
         JOIN tab_school s ON s.id = m.school_id WHERE s.name <> '' ORDER BY s.name",
    )
    .fetch_all(&mut *db)
    .await?
    {
        schools.entry(id).or_default().push(school);
    }
    let expected: HashSet<_> = sqlx::query_as::<_, (i32, i32)>(
        "SELECT judge_id, round_number FROM tab_judgeexpectedcheckin",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    let rows = sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_judge ORDER BY name")
        .fetch_all(db)
        .await?;
    Ok(rows
        .into_iter()
        .map(|(id, name)| Judge {
            name,
            affiliations: schools.remove(&id).unwrap_or_default().join(", "),
            availability: rounds
                .iter()
                .map(|&round| expected.contains(&(id, round)))
                .collect(),
        })
        .collect())
}

async fn changes(
    db: &mut MySqlConnection,
    kind: &str,
    names_visible: bool,
) -> Result<Vec<Change>, sqlx::Error> {
    let rows = sqlx::query_as::<_, (String, NaiveDateTime)>(
        "SELECT message, created_at FROM tab_publicrosterchange
         WHERE list_kind = ? AND (? = TRUE OR involves_debaters = FALSE)
         ORDER BY created_at DESC, id DESC",
    )
    .bind(kind)
    .bind(names_visible)
    .fetch_all(db)
    .await?;
    let timezone = FixedOffset::west_opt(5 * 3600).unwrap();
    Ok(rows
        .into_iter()
        .map(|(message, created)| Change {
            message,
            timestamp_est: created
                .and_utc()
                .with_timezone(&timezone)
                .format("%B %-d, %Y, %-I:%M %p EST")
                .to_string(),
        })
        .collect())
}
