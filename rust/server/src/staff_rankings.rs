use std::collections::{HashMap, HashSet};

use axum::{
    Extension, Router,
    extract::State,
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
    routing::get,
};
use minijinja::context;
use mittab_tabulation::{ballots::RoundFilter, rankings::Stat};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App,
    html::{self, Navigation},
    legacy_session::Actor,
    public_cache::{self, Page},
    public_rankings::policy,
    settings::Settings,
    speakers,
    staff_elimination::forum_results,
    staff_elimination_store::break_sizes,
    staff_roster_notice,
    tournament_data::TournamentData,
    unavailable,
};

const SETTINGS: &[&str] = &[
    "cur_round",
    "lenient_late",
    "debaters_public",
    "speaker_rankings_use_single_adjusted",
    "var_teams_to_break",
    "nov_teams_to_break",
    "motions_enabled",
];

pub fn routes() -> Router<App> {
    Router::new()
        .route("/team/ranking/", get(team_rankings))
        .route("/team/rank/", get(team_rankings))
        .route("/debater/ranking/", get(speaker_rankings))
        .route("/debater/rank/", get(speaker_rankings))
}

#[derive(Serialize)]
struct TeamRanking {
    id: i64,
    name: String,
    members: String,
    novice: bool,
    wins: i32,
    speaks: String,
    ranks: String,
    tiebreaker: &'static str,
}

#[derive(Serialize)]
struct SpeakerRanking {
    id: i32,
    name: String,
    team_id: Option<i32>,
    team: String,
    novice: bool,
    speaks: String,
    ranks: String,
    adjusted_speaks: String,
    adjusted_ranks: String,
    tiebreaker: &'static str,
}

fn tiebreaker(stat: Option<Stat>, has_next: bool) -> &'static str {
    match stat {
        Some(Stat::Wins) => "Wins",
        Some(Stat::Speaks) => "Speaks",
        Some(Stat::Ranks) => "Ranks",
        Some(Stat::SingleAdjustedSpeaks) => "Single adjusted speaks",
        Some(Stat::SingleAdjustedRanks) => "Single adjusted ranks",
        Some(Stat::DoubleAdjustedSpeaks) => "Double adjusted speaks",
        Some(Stat::DoubleAdjustedRanks) => "Double adjusted ranks",
        Some(Stat::OppStrength) => "Opp strength",
        Some(Stat::CoinFlip) => "Coin flip",
        None if has_next => "Tie not broken",
        None => "N/A",
    }
}

async fn teams(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<Vec<TeamRanking>, sqlx::Error> {
    let standings = TournamentData::load(db)
        .await?
        .scores(
            policy(settings),
            RoundFilter {
                exclude: None,
                through: None,
            },
        )
        .map_err(|message| sqlx::Error::Protocol(message.into()))?;
    let mut names: HashMap<_, _> =
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_team")
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .map(|(id, name)| (i64::from(id), name))
            .collect();
    let mut memberships: HashMap<i64, (Vec<String>, bool)> = HashMap::new();
    let show_names = settings.flag("debaters_public", true);
    for (id, name, novice) in sqlx::query_as::<_, (i32, String, i32)>(
        "SELECT m.team_id, d.name, d.novice_status FROM tab_team_debaters m JOIN tab_debater d ON m.debater_id = d.id
         ORDER BY d.name, d.id",
    ).fetch_all(db).await? {
        let entry = memberships.entry(i64::from(id)).or_insert_with(|| (Vec::new(), true));
        entry.1 &= novice == 1;
        if show_names {
            entry.0.push(name);
        }
    }
    let count = standings.len();
    Ok(standings
        .into_iter()
        .enumerate()
        .map(|(index, standing)| {
            let (members, novice) = memberships
                .remove(&standing.id)
                .unwrap_or_else(|| (Vec::new(), true));
            TeamRanking {
                id: standing.id,
                name: names.remove(&standing.id).unwrap_or_default(),
                members: members.join(", "),
                novice,
                wins: standing.score.get(Stat::Wins) as i32,
                speaks: format!("{:.2}", standing.score.get(Stat::Speaks)),
                ranks: format!("{:.2}", standing.score.get(Stat::Ranks)),
                tiebreaker: tiebreaker(standing.tiebreaker_with_next, index + 1 < count),
            }
        })
        .collect())
}

async fn speaker_rows(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<Vec<SpeakerRanking>, sqlx::Error> {
    type Metadata = (i32, String, Option<i32>, Option<String>);
    let mut names: HashMap<_, _> = sqlx::query_as::<_, Metadata>(
        "SELECT d.id, d.name, t.id, t.name FROM tab_debater d LEFT JOIN tab_team t
         ON t.id = (SELECT MIN(m.team_id) FROM tab_team_debaters m WHERE m.debater_id = d.id)",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, name, team_id, team)| (id, (name, team_id, team)))
    .collect();
    let rows = speakers::ordered_speaker_rows(db, settings).await?;
    let count = rows.len();
    Ok(rows
        .into_iter()
        .enumerate()
        .map(|(index, row)| {
            let (name, team_id, team) = names.remove(&row.debater.id).unwrap_or_default();
            SpeakerRanking {
                id: row.debater.id,
                name,
                team_id,
                team: team.unwrap_or_default(),
                novice: row.debater.novice_status == 1,
                speaks: format!("{:.2}", row.score.get(Stat::Speaks)),
                ranks: format!("{:.2}", row.score.get(Stat::Ranks)),
                adjusted_speaks: format!("{:.2}", row.score.get(Stat::SingleAdjustedSpeaks)),
                adjusted_ranks: format!("{:.2}", row.score.get(Stat::SingleAdjustedRanks)),
                tiebreaker: tiebreaker(row.tiebreaker, index + 1 < count),
            }
        })
        .collect())
}

pub async fn group_memberships(
    db: &mut MySqlConnection,
    team: bool,
) -> Result<Vec<(i32, String, i32)>, sqlx::Error> {
    let query = if team {
        "SELECT g.id, g.name, m.team_id FROM tab_rankinggroup g JOIN tab_rankinggroup_teams m ON m.rankinggroup_id = g.id ORDER BY g.name, g.id"
    } else {
        "SELECT g.id, g.name, m.debater_id FROM tab_rankinggroup g JOIN tab_rankinggroup_debaters m ON m.rankinggroup_id = g.id ORDER BY g.name, g.id"
    };
    sqlx::query_as(query).fetch_all(db).await
}

async fn groups(
    db: &mut MySqlConnection,
    team: bool,
) -> Result<Vec<(String, HashSet<i32>)>, sqlx::Error> {
    let mut groups: Vec<(String, HashSet<i32>)> = Vec::new();
    let mut last = None;
    for (id, name, member) in group_memberships(db, team).await? {
        if last != Some(id) {
            groups.push((format!("{name} Rankings"), HashSet::new()));
            last = Some(id);
        }
        groups
            .last_mut()
            .expect("matching ranking group")
            .1
            .insert(member);
    }
    Ok(groups)
}

async fn team_rankings(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::StaffTeamRankings, async |db| {
        let settings = Settings::load(db, SETTINGS).await?;
        let rankings = teams(db, &settings).await?;
        let memberships = groups(db, true).await?;
        let novice: Vec<_> = rankings.iter().filter(|row| row.novice).collect();
        let groups: Vec<_> = memberships.into_iter().map(|(title, members)| {
            (title, rankings.iter().filter(|row| members.contains(&(row.id as i32))).collect::<Vec<_>>())
        }).collect();
        Ok::<_, sqlx::Error>(html::render("staff_rankings.html", context! {
            title => "Team Rankings", nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
            varsity => rankings, novice, groups, team => true,
        }, StatusCode::OK))
    }).await
}

async fn speaker_rankings(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    if let Some(message) = staff_roster_notice::message(&app, &headers, uri.path()) {
        // Render session-specific notices outside the shared rankings cache.
        let result = async {
            let mut db = app.db.begin().await?;
            let mut response = render_speaker_rankings(&mut db, actor, Some(&message)).await?;
            db.commit().await?;
            if response.status().is_success() {
                staff_roster_notice::clear(&mut response, &headers);
            }
            Ok::<_, sqlx::Error>(response)
        }
        .await;
        return result.unwrap_or_else(|_| unavailable());
    }
    public_cache::render(&app, actor, Page::StaffSpeakerRankings, async |db| {
        render_speaker_rankings(db, actor, None).await
    })
    .await
}

async fn render_speaker_rankings(
    db: &mut MySqlConnection,
    actor: Actor,
    message: Option<&str>,
) -> Result<Response, sqlx::Error> {
    let settings = Settings::load(db, SETTINGS).await?;
    let rankings = speaker_rows(db, &settings).await?;
    let memberships = groups(db, false).await?;
    let novice: Vec<_> = rankings.iter().filter(|row| row.novice).collect();
    let groups: Vec<_> = memberships
        .into_iter()
        .map(|(title, members)| {
            (
                title,
                rankings
                    .iter()
                    .filter(|row| members.contains(&row.id))
                    .collect::<Vec<_>>(),
            )
        })
        .collect();
    Ok(html::render(
        "staff_rankings.html",
        context! {
            title => "Speaker Rankings", nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
            varsity => rankings, novice, groups, team => false, message,
            adjusted_first => settings.flag("speaker_rankings_use_single_adjusted", false),
        },
        StatusCode::OK,
    ))
}

pub async fn forum(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, SETTINGS).await?;
        let teams = teams(&mut db, &settings).await?;
        let speakers = speaker_rows(&mut db, &settings).await?;
        let dinos = sqlx::query_scalar::<_, String>("SELECT name FROM tab_judge WHERE is_dino = TRUE ORDER BY name, id")
            .fetch_all(&mut *db).await?;
        let (team_count, novice_count) = sqlx::query_as::<_, (i64, i64)>(
            "WITH eligible AS (
                SELECT team FROM (
                    SELECT gov_team_id AS team FROM tab_round UNION ALL SELECT opp_team_id FROM tab_round
                ) appearances GROUP BY team HAVING COUNT(*) >= 3
             )
             SELECT (SELECT COUNT(*) FROM eligible),
                    (SELECT COUNT(*) FROM eligible e JOIN tab_team_debaters m ON e.team = m.team_id
                     JOIN tab_debater d ON d.id = m.debater_id WHERE d.novice_status = 1)",
        ).fetch_one(&mut *db).await?;
        let (varsity_results, varsity_champ) = forum_results(&mut db, 0).await?;
        let (novice_results, novice_champ) = forum_results(&mut db, 1).await?;
        db.commit().await?;
        let [varsity_size, novice_size] = break_sizes(&settings);
        let varsity_teams = &teams[..teams.len().min(varsity_size.max(0) as usize)];
        let crossover = varsity_teams.iter().filter(|row| row.novice).count();
        let novice_teams: Vec<_> = teams.iter().filter(|row| row.novice).take(novice_size.max(0) as usize + crossover).collect();
        let varsity_speakers = &speakers[..speakers.len().min(10)];
        let novice_speakers: Vec<_> = speakers.iter().filter(|row| row.novice).take(10).collect();
        Ok::<_, sqlx::Error>(html::render("forum_preview.html", context! {
            title => "Tournament Results", nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
            dinos, team_count, novice_count, varsity_results, novice_results, varsity_champ, novice_champ,
            varsity_teams, novice_teams, varsity_speakers, novice_speakers,
        }, StatusCode::OK))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}
