use std::collections::HashMap;

use axum::{Json, extract::State, http::StatusCode, response::Response};
use chrono::Utc;
use mittab_tabulation::{
    ballots::{Ballot, Outcome, Round, ScoringPolicy, TeamAttendance},
    rankings::{Score, Stat},
    standings::{Speaker, speaker_standings},
};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{App, private, publication, settings::Settings, unavailable};

pub const SETTINGS: &[&str] = &[
    "cur_round",
    "tot_rounds",
    "lenient_late",
    "speaker_rankings_use_single_adjusted",
    "var_teams_visible",
    "nov_teams_visible",
];

#[derive(Clone, Copy)]
pub struct SpeakerRow {
    pub id: i32,
    pub apda_id: Option<i32>,
    pub novice_status: i32,
    coin_flip: Option<i32>,
}

pub struct RankedSpeaker {
    pub debater: SpeakerRow,
    pub tied_forward: bool,
    pub tiebreaker: Option<Stat>,
    pub score: Score,
}

#[derive(Serialize)]
struct Award {
    apda_id: Option<i32>,
    tournament_id: i32,
}

enum Awards {
    Embargoed,
    Published(Vec<Award>),
}

impl Awards {
    fn response(self, key: &str) -> Response {
        match self {
            Self::Published(awards) => private(Json(serde_json::json!({key: awards}))),
            Self::Embargoed => private((
                StatusCode::LOCKED,
                Json(serde_json::json!({
                    "error": "Speaker results not published"
                })),
            )),
        }
    }
}

pub async fn varsity(State(app): State<App>) -> Response {
    match get_awards(&app, false).await {
        Ok(result) => result.response("varsity_speaker_awards"),
        Err(_) => unavailable(),
    }
}

pub async fn novice(State(app): State<App>) -> Response {
    match get_awards(&app, true).await {
        Ok(result) => result.response("novice_speaker_awards"),
        Err(_) => unavailable(),
    }
}

async fn get_awards(app: &App, novices_only: bool) -> Result<Awards, sqlx::Error> {
    let mut db = app.db.begin().await?;
    crate::staff_pairing::lock_round_if_configured(&mut db).await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let placements = publication::refresh_placements(&mut db, &settings).await?;
    let completed_at = publication::completion_time(&mut db, &settings).await?;
    if !publication::speakers_released(&placements, completed_at, Utc::now()) {
        db.commit().await?;
        return Ok(Awards::Embargoed);
    }
    let ordered = ordered_speaker_rows(&mut db, &settings).await?;
    db.commit().await?;
    let awards = ordered
        .into_iter()
        .map(|row| row.debater)
        .filter(|row| !novices_only || row.novice_status == 1)
        .take(10)
        .map(|row| Award {
            apda_id: row.apda_id,
            tournament_id: row.id,
        })
        .collect();
    Ok(Awards::Published(awards))
}

pub async fn ordered_speaker_rows(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<Vec<RankedSpeaker>, sqlx::Error> {
    let rows: Vec<_> = sqlx::query_as::<_, (i32, Option<i32>, i32, Option<i32>)>(
        "SELECT id, apda_id, novice_status, tiebreaker FROM tab_debater ORDER BY name",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, apda_id, novice_status, coin_flip)| SpeakerRow {
        id,
        apda_id,
        novice_status,
        coin_flip,
    })
    .collect();
    let teams: HashMap<i32, i32> = sqlx::query_as::<_, (i32, i32)>(
        "SELECT debater_id, MIN(team_id) FROM tab_team_debaters GROUP BY debater_id",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    let byes = attendance_rows(
        db,
        "SELECT bye_team_id, round_number FROM tab_bye ORDER BY id",
    )
    .await?;
    let no_shows = attendance_rows(
        db,
        "SELECT no_show_team_id, round_number FROM tab_noshow ORDER BY id",
    )
    .await?;
    let attendance: HashMap<_, _> = teams
        .values()
        .copied()
        .map(|id| {
            (
                id,
                TeamAttendance {
                    id: i64::from(id),
                    byes: byes.get(&id).map_or(&[], Vec::as_slice),
                    no_shows: no_shows.get(&id).map_or(&[], Vec::as_slice),
                },
            )
        })
        .collect();

    let rounds: HashMap<_, _> = sqlx::query_as::<_, (i32, i32, i32, i32, i32)>(
        "SELECT id, round_number, gov_team_id, opp_team_id, victor FROM tab_round",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, number, gov, opp, victor)| {
        let outcome = match victor {
            1 => Outcome::Government,
            2 => Outcome::Opposition,
            3 => Outcome::GovernmentByForfeit,
            4 => Outcome::OppositionByForfeit,
            5 => Outcome::BothLose,
            6 => Outcome::BothWin,
            _ => Outcome::Pending,
        };
        (
            id,
            Round {
                id: i64::from(id),
                number,
                government: i64::from(gov),
                opposition: i64::from(opp),
                outcome,
            },
        )
    })
    .collect();
    let ballot_rows = sqlx::query_as::<_, (i32, i32, String, String)>(
        "SELECT debater_id, round_id, CAST(speaks AS CHAR), CAST(ranks AS CHAR)
         FROM tab_roundstats ORDER BY id",
    )
    .fetch_all(db)
    .await?;
    let mut ballots: HashMap<_, Vec<_>> = HashMap::new();
    for (debater, round, speaks, ranks) in ballot_rows {
        let round = rounds
            .get(&round)
            .ok_or_else(|| sqlx::Error::Protocol("Ballot round is missing".into()))?;
        let speaks = speaks
            .parse::<f64>()
            .map_err(|_| sqlx::Error::Protocol("Invalid speaks".into()))?;
        let ranks = ranks
            .parse::<f64>()
            .map_err(|_| sqlx::Error::Protocol("Invalid ranks".into()))?;
        ballots.entry(debater).or_default().push(Ballot {
            round,
            speaks,
            ranks,
        });
    }

    let speakers: Vec<_> = rows
        .iter()
        .map(|row| Speaker {
            id: i64::from(row.id),
            team: teams.get(&row.id).and_then(|team| attendance.get(team)),
            ballots: ballots.get(&row.id).map_or(&[], Vec::as_slice),
            coin_flip: row.coin_flip,
        })
        .collect();
    let policy = ScoringPolicy {
        current_round: settings.integer("cur_round", 1),
        lenient_late_through: settings.integer("lenient_late", 0),
    };
    let standings = speaker_standings(
        &speakers,
        policy,
        settings.flag("speaker_rankings_use_single_adjusted", false),
    )
    .map_err(|_| sqlx::Error::Protocol("Invalid standings".into()))?;
    let by_id: HashMap<_, _> = rows
        .into_iter()
        .map(|row| (i64::from(row.id), row))
        .collect();
    let count = standings.len();
    Ok(standings
        .into_iter()
        .enumerate()
        .map(|(index, standing)| RankedSpeaker {
            debater: by_id[&standing.id],
            score: standing.score,
            tiebreaker: standing.tiebreaker_with_next,
            tied_forward: index + 1 < count && standing.tiebreaker_with_next.is_none(),
        })
        .collect())
}

async fn attendance_rows(
    db: &mut MySqlConnection,
    query: &'static str,
) -> Result<HashMap<i32, Vec<i32>>, sqlx::Error> {
    let mut by_team: HashMap<_, Vec<_>> = HashMap::new();
    for (team_id, number) in sqlx::query_as::<_, (i32, i32)>(query).fetch_all(db).await? {
        by_team.entry(team_id).or_default().push(number);
    }
    Ok(by_team)
}
