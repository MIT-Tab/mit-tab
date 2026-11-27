use std::collections::{HashMap, HashSet};

use axum::{
    Extension, Router,
    extract::State,
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
    routing::get,
};
use minijinja::{Value, context};
use mittab_tabulation::{ballots::RoundFilter, rankings::Stat};
use serde::Serialize;
use sqlx::MySqlConnection;
use unicode_normalization::UnicodeNormalization;

use crate::{
    App,
    html::{self, Navigation},
    legacy_html,
    legacy_session::Actor,
    public_cache::{self, Page},
    public_rankings::{django_hundredths, policy},
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
        .route("/team/ranking/", get(ranking_page))
        .route("/team/rank/", get(team_rankings))
        .route("/debater/ranking/", get(ranking_page))
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
                speaks: django_hundredths(standing.score.get(Stat::Speaks)),
                ranks: django_hundredths(standing.score.get(Stat::Ranks)),
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
                speaks: django_hundredths(row.score.get(Stat::Speaks)),
                ranks: django_hundredths(row.score.get(Stat::Ranks)),
                adjusted_speaks: django_hundredths(row.score.get(Stat::SingleAdjustedSpeaks)),
                adjusted_ranks: django_hundredths(row.score.get(Stat::SingleAdjustedRanks)),
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

struct RankingGroup {
    title: String,
    anchor: String,
    members: HashSet<i32>,
}

fn slugify(name: &str) -> String {
    let mut slug = String::new();
    let mut separator = false;
    for ch in name.nfkd().filter(char::is_ascii) {
        if ch == '-' || ch.is_ascii_whitespace() {
            separator = true;
        } else if ch == '_' || ch.is_ascii_alphanumeric() {
            if separator && !slug.is_empty() {
                slug.push('-');
            }
            separator = false;
            slug.push(ch.to_ascii_lowercase());
        }
    }
    slug.trim_matches(['-', '_']).into()
}

async fn groups(db: &mut MySqlConnection, team: bool) -> Result<Vec<RankingGroup>, sqlx::Error> {
    let mut groups: Vec<RankingGroup> = Vec::new();
    let mut last = None;
    let prefix = if team { "team" } else { "debater" };
    for (id, name, member) in group_memberships(db, team).await? {
        if last != Some(id) {
            groups.push(RankingGroup {
                title: format!("{name} Rankings"),
                anchor: format!("{prefix}-ranking-group-{}", slugify(&name)),
                members: HashSet::new(),
            });
            last = Some(id);
        }
        groups
            .last_mut()
            .expect("matching ranking group")
            .members
            .insert(member);
    }
    Ok(groups)
}

async fn ranking_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        db.commit().await?;
        let (template, title) = if uri.path() == "/team/ranking/" {
            ("tab/rank_teams.html", "Team Rankings")
        } else {
            ("tab/rank_debaters.html", "Debater Rankings")
        };
        Ok::<_, sqlx::Error>(staff_roster_notice::render(
            &app,
            &headers,
            uri.path(),
            template,
            shell,
            context! { title },
            StatusCode::OK,
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn team_rankings(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    rankings_response(
        &app,
        actor,
        Page::StaffTeamRankings,
        &headers,
        uri.path(),
        async |db, message| render_team_rankings(db, actor, message).await,
    )
    .await
}

async fn render_team_rankings(
    db: &mut MySqlConnection,
    actor: Actor,
    message: Option<&str>,
) -> Result<Response, sqlx::Error> {
    let settings = Settings::load(db, SETTINGS).await?;
    let rankings = teams(db, &settings).await?;
    let rows: Vec<_> = rankings
        .iter()
        .map(|row| {
            Value::from_serialize((
                context! { id => row.id, display_backend => &row.name },
                row.wins,
                &row.speaks,
                &row.ranks,
                row.tiebreaker,
            ))
        })
        .collect();
    let novice: Vec<_> = rankings
        .iter()
        .zip(&rows)
        .filter(|(rank, _)| rank.novice)
        .map(|(_, row)| row.clone())
        .collect();
    let team_ranking_groups: Vec<_> = groups(db, true)
        .await?
        .into_iter()
        .filter_map(|group| {
            let teams: Vec<_> = rankings
                .iter()
                .zip(&rows)
                .filter(|(rank, _)| group.members.contains(&(rank.id as i32)))
                .map(|(_, row)| row.clone())
                .collect();
            (!teams.is_empty())
                .then(|| context! { title => group.title, anchor => group.anchor, teams })
        })
        .collect();
    Ok(legacy_html::render(
        "tab/rank_teams_component.html",
        context! {},
        context! {
            user => context! { is_authenticated => actor != Actor::Anonymous },
            varsity => rows, novice, team_ranking_groups, ranking_notice => message,
        },
        StatusCode::OK,
    ))
}

async fn speaker_rankings(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    rankings_response(
        &app,
        actor,
        Page::StaffSpeakerRankings,
        &headers,
        uri.path(),
        async |db, message| render_speaker_rankings(db, message).await,
    )
    .await
}

async fn rankings_response(
    app: &App,
    actor: Actor,
    page: Page,
    headers: &HeaderMap,
    path: &str,
    load: impl for<'db, 'message> AsyncFnOnce(
        &'db mut MySqlConnection,
        Option<&'message str>,
    ) -> Result<Response, sqlx::Error>,
) -> Response {
    if let Some(message) = staff_roster_notice::message(app, headers, path) {
        // Render session-specific notices outside the shared rankings cache.
        let result = async {
            let mut db = app.db.begin().await?;
            let mut response = load(&mut db, Some(&message)).await?;
            db.commit().await?;
            if response.status().is_success() {
                staff_roster_notice::clear(&mut response, headers);
            }
            Ok::<_, sqlx::Error>(response)
        }
        .await;
        return result.unwrap_or_else(|_| unavailable());
    }
    public_cache::render(app, actor, page, async |db| load(db, None).await).await
}

async fn render_speaker_rankings(
    db: &mut MySqlConnection,
    message: Option<&str>,
) -> Result<Response, sqlx::Error> {
    let settings = Settings::load(db, SETTINGS).await?;
    let rankings = speaker_rows(db, &settings).await?;
    let adjusted_first = settings.flag("speaker_rankings_use_single_adjusted", false);
    let rows: Vec<_> = rankings.iter().map(|row| {
        let unadjusted = context! { label => "Unadjusted", speaks => &row.speaks, ranks => &row.ranks };
        let adjusted = context! { label => "Single adjusted", speaks => &row.adjusted_speaks, ranks => &row.adjusted_ranks };
        let score_columns = if adjusted_first { [adjusted, unadjusted] } else { [unadjusted, adjusted] };
        context! {
            debater => context! { id => row.id, name => &row.name },
            team => row.team_id.map(|id| context! { id, name => &row.team }),
            score_columns, tiebreaker => row.tiebreaker,
        }
    }).collect();
    let nov_debaters: Vec<_> = rankings
        .iter()
        .zip(&rows)
        .filter(|(rank, _)| rank.novice)
        .map(|(_, row)| row.clone())
        .collect();
    let debater_ranking_groups: Vec<_> = groups(db, false)
        .await?
        .into_iter()
        .filter_map(|group| {
            let debaters: Vec<_> = rankings
                .iter()
                .zip(&rows)
                .filter(|(rank, _)| group.members.contains(&rank.id))
                .map(|(_, row)| row.clone())
                .collect();
            (!debaters.is_empty())
                .then(|| context! { title => group.title, anchor => group.anchor, debaters })
        })
        .collect();
    Ok(legacy_html::render(
        "tab/rank_debaters_component.html",
        context! {},
        context! { debaters => rows, nov_debaters, debater_ranking_groups, ranking_notice => message },
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
