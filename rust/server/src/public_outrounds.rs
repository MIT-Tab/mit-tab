use std::collections::{BTreeMap, HashMap, HashSet};

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
    App, access, bracket_display,
    html::{self, Navigation},
    legacy_session::Actor,
    public_cache::{self, Page},
    public_pairings::{Team, paired_teams},
    settings::Settings,
};

const SETTINGS: &[&str] = &[
    "var_teams_visible",
    "nov_teams_visible",
    "cur_round",
    "debaters_public",
    "gov_opp_display",
    "sidelock",
    "choice",
    "outround_pairing_mode",
    "show_outs_bracket",
    "motions_enabled",
    "teams_public",
    "judges_public",
];

#[derive(Clone, Serialize)]
pub struct Judge {
    pub name: String,
    pub is_chair: bool,
}

#[derive(Serialize)]
pub struct Match<'a> {
    pub id: i32,
    pub size: i32,
    pub gov: &'a Team,
    pub opp: &'a Team,
    pub room: Option<String>,
    pub judges: Vec<Judge>,
    pub victor: i32,
    pub sidelock: bool,
    pub choice: i32,
}

struct Metadata {
    id: i32,
    size: i32,
    gov: i32,
    opp: i32,
    room: Option<String>,
    judges: Vec<Judge>,
    victor: i32,
    sidelock: bool,
    choice: i32,
}

impl Metadata {
    fn with_teams(self, teams: &HashMap<i32, Team>) -> Option<Match<'_>> {
        Some(Match {
            id: self.id,
            size: self.size,
            gov: teams.get(&self.gov)?,
            opp: teams.get(&self.opp)?,
            judges: self.judges,
            room: self.room,
            victor: self.victor,
            choice: self.choice,
            sidelock: self.sidelock,
        })
    }
}

async fn matches(
    db: &mut MySqlConnection,
    division: i32,
    visible: i32,
) -> Result<Vec<Metadata>, sqlx::Error> {
    let mut panels: HashMap<i32, Vec<Judge>> = HashMap::new();
    for (pairing, name, chair) in sqlx::query_as::<_, (i32, String, bool)>(
        "SELECT r.id, j.name, COALESCE(r.chair_id = j.id, FALSE)
         FROM tab_outround r JOIN tab_outround_judges x ON x.outround_id = r.id
         JOIN tab_judge j ON j.id = x.judge_id WHERE r.type_of_round = ? AND r.num_teams >= ?
         ORDER BY j.name, j.id",
    )
    .bind(division)
    .bind(visible)
    .fetch_all(&mut *db)
    .await?
    {
        panels.entry(pairing).or_default().push(Judge {
            name,
            is_chair: chair,
        });
    }
    type Row = (i32, i32, i32, i32, Option<String>, i32, bool, i32);
    Ok(sqlx::query_as::<_, Row>(
        "SELECT r.id, r.num_teams, r.gov_team_id, r.opp_team_id, m.name, r.victor, r.sidelock, r.choice
         FROM tab_outround r LEFT JOIN tab_room m ON m.id = r.room_id
         WHERE r.type_of_round = ? AND r.num_teams >= ? ORDER BY r.id",
    ).bind(division).bind(visible).fetch_all(db).await?.into_iter().map(
        |(id, size, gov, opp, room, victor, sidelock, choice)| Metadata {
            id, size, gov, opp, room, victor, sidelock, choice, judges: panels.remove(&id).unwrap_or_default(),
        }
    ).collect())
}

#[derive(Serialize)]
struct Stage<'a> {
    size: i32,
    matches: Vec<&'a Match<'a>>,
    advancing: Vec<&'a str>,
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(division): Path<i32>,
) -> Response {
    if !matches!(division, 0 | 1) {
        return crate::private((StatusCode::NOT_FOUND, "Division not found."));
    }
    public_cache::render(&app, actor, Page::Outrounds(division), async |db| {
        render(db, actor, division).await
    })
    .await
}

async fn render(
    db: &mut MySqlConnection,
    actor: Actor,
    division: i32,
) -> Result<Response, sqlx::Error> {
    let settings = Settings::load(db, SETTINGS).await?;
    let varsity = settings.integer("var_teams_visible", 256);
    let novice = settings.integer("nov_teams_visible", 256);
    let metadata = matches(db, division, if division == 0 { varsity } else { novice }).await?;
    if metadata.is_empty() {
        return Ok(access::found("/public/access-error/"));
    }
    let names = settings.flag("debaters_public", true);
    let teams = paired_teams(db, names).await?;
    let seeded: BTreeMap<_, _> = sqlx::query_as::<_, (i32, i32)>(
        "SELECT seed, team_id FROM tab_breakingteam WHERE type_of_team = ? ORDER BY id",
    )
    .bind(division)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .filter_map(|(seed, id)| teams.get(&id).map(|team| (seed, team)))
    .collect();
    let current = settings.integer("cur_round", 1).saturating_sub(1);
    let prelims = sqlx::query_scalar::<_, i32>(
        "SELECT DISTINCT round_number FROM tab_round WHERE round_number <= ? ORDER BY round_number",
    )
    .bind(current)
    .fetch_all(&mut *db)
    .await?;
    let (varsity, novice) = sqlx::query_as::<_, (bool, bool)>(
        "SELECT EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = 0 AND num_teams >= ?),
                EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = 1 AND num_teams >= ?)",
    )
    .bind(varsity)
    .bind(novice)
    .fetch_one(&mut *db)
    .await?;

    let matches: Vec<_> = metadata
        .into_iter()
        .filter_map(|row| row.with_teams(&teams))
        .collect();
    let mut by_stage: BTreeMap<_, Vec<_>> = BTreeMap::new();
    let mut eliminated = HashSet::new();
    for row in &matches {
        by_stage.entry(row.size).or_default().push(row);
        let loser = match row.victor {
            1 | 3 => Some(row.opp.id),
            2 | 4 => Some(row.gov.id),
            _ => None,
        };
        eliminated.extend(loser);
    }
    let stages: Vec<_> = by_stage
        .into_iter()
        .rev()
        .map(|(size, matches)| {
            let present: HashSet<_> = matches
                .iter()
                .flat_map(|row| [row.gov.id, row.opp.id])
                .collect();
            let advancing = seeded
                .values()
                .filter(|team| !present.contains(&team.id) && !eliminated.contains(&team.id))
                .map(|team| team.name.as_str())
                .collect();
            Stage {
                size,
                matches,
                advancing,
            }
        })
        .collect();
    let use_bracket = settings.flag("show_outs_bracket", true)
        && settings.integer("outround_pairing_mode", 0) != 1;
    let bracket = if use_bracket {
        bracket_display::data(&matches, &seeded).map(|payload| payload.to_string())
    } else {
        None
    };
    let mut nav = Navigation::new(actor, settings.flag("motions_enabled", true));
    nav.teams_public = settings.flag("teams_public", false);
    nav.judges_public = settings.flag("judges_public", false);
    let title = if division == 0 {
        "Varsity Outrounds Pairings"
    } else {
        "Novice Outrounds Pairings"
    };
    Ok(html::render(
        "public_outrounds.html",
        context! {
            title, stages, bracket, division, names, current, rounds => prelims, varsity, novice, nav,
            show_sides => settings.flag("gov_opp_display", false),
            sidelock => settings.flag("sidelock", false), choice => settings.flag("choice", false),
        },
        StatusCode::OK,
    ))
}
