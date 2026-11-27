use std::collections::HashMap;

use axum::{
    Extension, Form,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use mittab_tabulation::{
    ballots::{RoundFilter, ScoringPolicy},
    legacy_random::LegacyRandom,
    rankings::{Score, Stat},
};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    public_cache::{self, Page},
    settings::Settings,
    speakers,
    staff_pairing::lock_round,
    tournament_data::TournamentData,
    unavailable,
};

const CONTROL: &str = "/rankings/public-control/";
const PUBLIC_ERROR: &str = "/public/access-error/";
const RANKINGS: [(&str, &str, u32); 3] = [
    ("team", "Team Rankings", 1000),
    ("varsity", "Varsity Speakers", 10),
    ("novice", "Novice Speakers", 10),
];

#[derive(Clone, Serialize)]
struct Display {
    slug: String,
    label: String,
    kind: &'static str,
    visible: bool,
    speaks: bool,
    ranks: bool,
    maximum: u32,
    round: Option<u32>,
}

async fn configured(db: &mut MySqlConnection, total: i32) -> Result<Vec<Display>, sqlx::Error> {
    type Row = (String, bool, bool, bool, u32, Option<u32>);
    let existing: HashMap<_, _> = sqlx::query_as::<_, Row>(
        "SELECT slug, is_enabled, include_speaks, include_ranks, max_visible, round_number
         FROM tab_publicdisplaysetting WHERE display_type IN ('ranking','ballot')",
    )
    .fetch_all(db)
    .await?
    .into_iter()
    .map(|(slug, public, speaks, ranks, maximum, round)| {
        (slug, (public, speaks, ranks, maximum, round))
    })
    .collect();
    let mut choices: Vec<_> = RANKINGS
        .into_iter()
        .map(|(slug, label, limit)| {
            let (visible, speaks, ranks, maximum, round) = existing
                .get(slug)
                .copied()
                .unwrap_or((false, false, false, limit, None));
            Display {
                slug: slug.into(),
                label: label.into(),
                kind: "ranking",
                visible,
                speaks,
                ranks,
                maximum,
                round,
            }
        })
        .collect();
    for round in 1..=total {
        let slug = format!("ballot_round_{round}");
        let (visible, speaks, ranks, maximum, _) = existing
            .get(&slug)
            .copied()
            .unwrap_or((false, false, false, 0, None));
        choices.push(Display {
            slug,
            label: format!("Round {round} Ballots"),
            kind: "ballot",
            visible,
            speaks,
            ranks,
            maximum,
            round: Some(round as u32),
        });
    }
    Ok(choices)
}

pub async fn control(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, &["tot_rounds"]).await?;
        let display = configured(&mut db, settings.integer("tot_rounds", 0)).await?;
        let paired_rounds = sqlx::query_scalar::<_, i32>(
            "SELECT DISTINCT round_number FROM tab_round WHERE round_number > 0 ORDER BY round_number",
        ).fetch_all(&mut *db).await?;
        db.commit().await?;
        let Ok(csrf) = html::page_csrf(&headers) else { return Ok(unavailable()); };
        let (rankings, ballots) = display.split_at(RANKINGS.len());
        let response = html::render("ranking_control.html", context! {
            title => "Public Rankings Control", rankings, ballots, csrf, paired_rounds,
            nav => Navigation::new(actor, true),
        }, StatusCode::OK);
        Ok::<_, sqlx::Error>(html::form_response(response, &headers, &csrf))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

pub async fn update(
    State(app): State<App>,
    headers: HeaderMap,
    Form(form): Form<HashMap<String, String>>,
) -> Response {
    let value = |field: &str| form.get(field).map_or("", |value| value.trim());
    if !login::valid_csrf(&headers, value("csrfmiddlewaretoken")) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let settings = Settings::load(&mut db, &["tot_rounds"]).await?;
        let mut choices = configured(&mut db, settings.integer("tot_rounds", 0)).await?;
        for row in &mut choices {
            if row.kind == "ranking" {
                row.visible = !value(&format!("{}_public", row.slug)).is_empty();
                row.speaks = !value(&format!("{}_include_speaks", row.slug)).is_empty();
                row.maximum = value(&format!("{}_max_visible", row.slug)).parse::<i64>().ok()
                    .and_then(|value| u32::try_from(value.max(1)).ok()).unwrap_or(row.maximum.max(1));
                row.round = Some(value(&format!("{}_up_to_round", row.slug)).parse().unwrap_or(row.round.unwrap_or(0)));
            } else {
                let number = row.round.unwrap_or(0);
                row.visible = !value(&format!("round_{number}_visible")).is_empty();
                row.speaks = !value(&format!("round_{number}_include_speaks")).is_empty();
                row.ranks = !value(&format!("round_{number}_include_ranks")).is_empty();
            }
            sqlx::query(
                "INSERT INTO tab_publicdisplaysetting
                    (slug, label, display_type, is_enabled, include_speaks, include_ranks, max_visible, round_number)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?) AS incoming
                 ON DUPLICATE KEY UPDATE is_enabled = incoming.is_enabled, include_speaks = incoming.include_speaks,
                     include_ranks = incoming.include_ranks, max_visible = incoming.max_visible, round_number = incoming.round_number",
            ).bind(&row.slug).bind(&row.label).bind(row.kind)
                .bind(row.visible).bind(row.speaks).bind(row.ranks).bind(row.maximum).bind(row.round)
                .execute(&mut *db).await?;
        }
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(CONTROL))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Serialize)]
struct TeamRanking {
    place: usize,
    name: String,
    debaters: String,
    wins: i32,
    speaks: Option<String>,
    ranks: Option<String>,
}

pub fn policy(settings: &Settings) -> ScoringPolicy {
    ScoringPolicy {
        current_round: settings.integer("cur_round", 1),
        lenient_late_through: settings.integer("lenient_late", 0),
    }
}

fn score_pair(score: Score, speaks: Stat, ranks: Stat) -> (String, String) {
    (
        format!("{:.2}", score.get(speaks)),
        format!("{:.2}", score.get(ranks)),
    )
}

pub async fn teams(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::TeamRankings, async |db| {
        let display = configured(db, 0).await?;
        let config = &display[0];
        if !config.visible {
            return Ok(access::found(PUBLIC_ERROR));
        }
        let settings = Settings::load(
            db,
            &[
                "cur_round",
                "lenient_late",
                "debaters_public",
                "motions_enabled",
            ],
        )
        .await?;
        let tournament = TournamentData::load(db).await?;
        let mut metadata: HashMap<_, (String, Vec<String>)> = HashMap::new();
        for (id, team, debater) in sqlx::query_as::<_, (i32, String, Option<String>)>(
            "SELECT t.id, t.name, d.name FROM tab_team t
             LEFT JOIN tab_team_debaters m ON m.team_id = t.id AND ?
             LEFT JOIN tab_debater d ON d.id = m.debater_id
             WHERE t.ranking_public = TRUE ORDER BY d.name, d.id",
        )
        .bind(settings.flag("debaters_public", true))
        .fetch_all(&mut *db)
        .await?
        {
            let (_, debaters) = metadata
                .entry(i64::from(id))
                .or_insert_with(|| (team, Vec::new()));
            if let Some(debater) = debater {
                debaters.push(debater);
            }
        }

        let standings = tournament
            .scores(
                policy(&settings),
                RoundFilter {
                    exclude: None,
                    through: Some(i32::try_from(config.round.unwrap_or(0)).unwrap_or(i32::MAX)),
                },
            )
            .map_err(|message| sqlx::Error::Protocol(message.into()))?;
        let mut rows: Vec<_> = standings
            .into_iter()
            .filter_map(|standing| {
                let (name, debaters) = metadata.remove(&standing.id)?;
                let (speaks, ranks) = score_pair(standing.score, Stat::Speaks, Stat::Ranks);
                Some(TeamRanking {
                    place: 0,
                    name,
                    debaters: debaters.join(", "),
                    wins: standing.score.get(Stat::Wins) as i32,
                    speaks: config.speaks.then_some(speaks),
                    ranks: config.speaks.then_some(ranks),
                })
            })
            .collect();
        if config.speaks {
            for (index, row) in rows.iter_mut().enumerate() {
                row.place = index + 1;
            }
        } else {
            let mut rng = LegacyRandom::seeded(0xC0FFEE);
            let mut place = 1;
            for bracket in rows.chunk_by_mut(|a, b| a.wins == b.wins) {
                rng.shuffle(bracket);
                for row in bracket.iter_mut() {
                    row.place = place;
                }
                place += bracket.len();
            }
        }
        rows.truncate(config.maximum as usize);
        Ok::<_, sqlx::Error>(html::render(
            "public_team_rankings.html",
            context! {
                title => "Team Rankings", rows, show_scores => config.speaks,
                nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
            },
            StatusCode::OK,
        ))
    })
    .await
}

#[derive(Serialize)]
struct SpeakerRanking {
    place: usize,
    name: String,
    team: Option<String>,
    columns: Vec<(&'static str, String, String)>,
}

#[derive(Serialize)]
struct SpeakerSection {
    name: String,
    show_scores: bool,
    rows: Vec<SpeakerRanking>,
}

fn speaker_columns(score: Score, adjusted: bool) -> Vec<(&'static str, String, String)> {
    let mut groups = [
        ("Unadjusted", Stat::Speaks, Stat::Ranks),
        (
            "Single adjusted",
            Stat::SingleAdjustedSpeaks,
            Stat::SingleAdjustedRanks,
        ),
    ];
    if adjusted {
        groups.swap(0, 1);
    }
    groups
        .into_iter()
        .map(|(group, speaks, ranks)| {
            let (speaks, ranks) = score_pair(score, speaks, ranks);
            (group, speaks, ranks)
        })
        .collect()
}

pub async fn speaker_page(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::SpeakerRankings, async |db| {
        let display = configured(db, 0).await?;
        if !display[1..].iter().any(|config| config.visible) { return Ok(access::found(PUBLIC_ERROR)); }
        let mut keys = speakers::SETTINGS.to_vec();
        keys.push("motions_enabled");
        let settings = Settings::load(db, &keys).await?;
        let ordered = speakers::ordered_speaker_rows(db, &settings).await?;
        let names: HashMap<_, _> = sqlx::query_as::<_, (i32, String, Option<String>)>(
            "SELECT d.id, d.name, t.name FROM tab_debater d LEFT JOIN tab_team t
             ON t.id = (SELECT MIN(m.team_id) FROM tab_team_debaters m WHERE m.debater_id = d.id)",
        ).fetch_all(&mut *db).await?.into_iter().map(|(id, name, team)| (id, (name, team))).collect();
        let adjusted = settings.flag("speaker_rankings_use_single_adjusted", false);
        let sections: Vec<_> = display[1..].iter().enumerate().filter(|(_, config)| config.visible)
            .map(|(index, config)| SpeakerSection {
                name: config.label.clone(),
                show_scores: config.speaks,
                rows: ordered.iter().filter(|row| row.debater.novice_status == index as i32).take(config.maximum as usize)
                    .enumerate().map(|(index, row)| {
                    let (name, team) = &names[&row.debater.id];
                    SpeakerRanking { place: index + 1, name: name.clone(), team: team.clone(),
                        columns: if config.speaks { speaker_columns(row.score, adjusted) } else { Vec::new() } }
                }).collect(),
            }).collect();
        Ok::<_, sqlx::Error>(html::render("public_speaker_rankings.html", context! {
            title => "Speaker Rankings", sections, nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        }, StatusCode::OK))
    }).await
}

#[derive(Serialize)]
struct PublicBallot {
    gov: String,
    opp: String,
    result: &'static str,
    winner: String,
    speakers: Vec<BallotSpeaker>,
}

#[derive(Serialize)]
struct BallotSpeaker {
    side: &'static str,
    name: String,
    speaks: Option<String>,
    ranks: Option<String>,
}

#[derive(Serialize)]
struct RoundBallots {
    number: u32,
    speaks: bool,
    ranks: bool,
    rows: Vec<PublicBallot>,
}

async fn ballots_for(
    db: &mut MySqlConnection,
    config: &Display,
) -> Result<RoundBallots, sqlx::Error> {
    let number = config.round.unwrap_or(0);
    let rows = sqlx::query_as::<_, (i32, i32, String, i32, String, i32)>(
        "SELECT r.id, g.id, g.name, o.id, o.name, r.victor FROM tab_round r
         JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         WHERE r.round_number = ? AND r.victor IN (1,2,3,4) AND g.ranking_public AND o.ranking_public
         ORDER BY g.name, o.name, r.id",
    ).bind(number).fetch_all(&mut *db).await?;
    let mut scores: HashMap<i32, Vec<BallotSpeaker>> = HashMap::new();
    type Row = (i32, bool, String, Option<String>, Option<String>);
    for (id, govt, name, speaks, ranks) in sqlx::query_as::<_, Row>(
        "SELECT r.id, m.team_id = r.gov_team_id, d.name,
                IF(?, CAST(s.speaks AS CHAR), NULL), IF(?, CAST(s.ranks AS CHAR), NULL)
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         JOIN tab_team_debaters m ON m.team_id IN (g.id, o.id) JOIN tab_debater d ON d.id = m.debater_id
         LEFT JOIN tab_roundstats s ON s.id = (
             SELECT MAX(stat.id) FROM tab_roundstats stat WHERE stat.round_id = r.id AND stat.debater_id = d.id)
         WHERE r.round_number = ? AND r.victor IN (1,2,3,4) AND g.ranking_public AND o.ranking_public
         ORDER BY r.id, m.team_id = r.gov_team_id DESC, d.name, d.id",
    ).bind(config.speaks).bind(config.ranks).bind(number).fetch_all(db).await? {
        scores.entry(id).or_default().push(BallotSpeaker { side: if govt { "Gov" } else { "Opp" }, name, speaks, ranks });
    }
    let rows = rows
        .into_iter()
        .map(|(id, _, gov, _, opp, victor)| {
            let winner = if victor % 2 == 1 {
                gov.clone()
            } else {
                opp.clone()
            };
            PublicBallot {
                gov,
                opp,
                result: crate::board_snapshot::outcome(victor),
                winner,
                speakers: scores.remove(&id).unwrap_or_default(),
            }
        })
        .collect();
    Ok(RoundBallots {
        number,
        speaks: config.speaks,
        ranks: config.ranks,
        rows,
    })
}

pub async fn ballots(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::Ballots, async |db| {
        let settings = Settings::load(db, &["tot_rounds", "motions_enabled"]).await?;
        let display = configured(db, settings.integer("tot_rounds", 0)).await?;
        let mut rounds = Vec::new();
        for config in display.iter().rev().filter(|config| config.kind == "ballot" && config.visible) {
            rounds.push(ballots_for(db, config).await?);
        }
        if rounds.is_empty() { return Ok(access::found(PUBLIC_ERROR)); }
        Ok::<_, sqlx::Error>(html::render("public_ballots.html", context! {
            title => "Public Ballots", rounds, nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        }, StatusCode::OK))
    }).await
}
