use std::{collections::HashMap, future::poll_fn};

use mittab_tabulation::{
    ballots::{
        Ballot, Outcome, Round, RoundFilter, ScoringPolicy, TeamAttendance, TeamId, speaker_points,
    },
    pairing::{PairingTeam, SideHistory},
    prelim::{Entrant, PairingPlan, Policy, plan_prelim},
    rankings::{Score, Stat},
    standings::{Speaker, Standing, Team, team_standings},
};
use sqlx::{Either, FromRow, MySqlConnection, Row};

pub struct TournamentData {
    pub teams: Vec<TeamRow>,
    pub rounds: Vec<Round>,
    byes: HashMap<TeamId, Vec<i32>>,
    no_shows: HashMap<TeamId, Vec<i32>>,
    speakers: Vec<SpeakerRow>,
    memberships: HashMap<TeamId, Vec<usize>>,
    ballots: HashMap<i32, Vec<BallotRow>>,
    history: HashMap<TeamId, TeamHistory>,
}

pub struct TeamRow {
    pub id: TeamId,
    pub seed: i32,
    pub checked_in: bool,
    school: i64,
    coin: Option<i32>,
}

struct SpeakerRow {
    id: i32,
    coin: Option<i32>,
    team: Option<TeamId>,
}

struct BallotRow {
    round: usize,
    speaks: f64,
    ranks: f64,
}

#[derive(Default)]
struct TeamHistory {
    government: u32,
    opposition: u32,
    pullups: usize,
    faced_pullup: bool,
    forfeit_wins: usize,
    opponents: Vec<TeamId>,
}

// Send the reads together over MySQL's text protocol. Callers provide the
// transaction, so results and publication settings share a snapshot.
const TOURNAMENT_QUERIES: &str = "
    SELECT id, seed, checked_in, school_id, tiebreaker FROM tab_team ORDER BY id;
    SELECT bye_team_id, round_number FROM tab_bye ORDER BY id;
    SELECT no_show_team_id, round_number FROM tab_noshow ORDER BY id;
    SELECT id, tiebreaker FROM tab_debater ORDER BY name;
    SELECT m.team_id, m.debater_id FROM tab_team_debaters m
        JOIN tab_debater d ON d.id = m.debater_id ORDER BY m.team_id, d.name;
    SELECT id, round_number, gov_team_id, opp_team_id, victor, pullup FROM tab_round ORDER BY id;
    SELECT gov_team_id, opp_team_id FROM tab_outround;
    SELECT debater_id, round_id, CAST(speaks AS CHAR), CAST(ranks AS CHAR)
        FROM tab_roundstats ORDER BY id;
";

type ScoreRow = (i32, i32, f64, f64);

fn parse_score(value: &str) -> Result<f64, sqlx::Error> {
    value
        .parse()
        .map_err(|_| sqlx::Error::Protocol("Invalid round statistic".into()))
}

#[derive(Default)]
struct TournamentRows {
    teams: Vec<TeamRow>,
    byes: HashMap<TeamId, Vec<i32>>,
    no_shows: HashMap<TeamId, Vec<i32>>,
    speakers: Vec<SpeakerRow>,
    memberships: Vec<(i32, i32)>,
    rounds: Vec<(i32, i32, i32, i32, i32, i32)>,
    outrounds: Vec<(i32, i32)>,
    scores: Vec<ScoreRow>,
}

impl TournamentRows {
    async fn load(db: &mut MySqlConnection) -> Result<Self, sqlx::Error> {
        let mut stream = sqlx::raw_sql(TOURNAMENT_QUERIES).fetch_many(db);
        let mut data = Self::default();
        let mut result_set = 0;
        while let Some(result) = poll_fn(|cx| stream.as_mut().poll_next(cx)).await {
            let Either::Right(row) = result? else {
                result_set += 1;
                continue;
            };
            match result_set {
                0 => {
                    let (id, seed, checked_in, school, coin): (i32, i32, bool, i32, Option<i32>) =
                        FromRow::from_row(&row)?;
                    data.teams.push(TeamRow {
                        id: i64::from(id),
                        seed,
                        checked_in,
                        school: i64::from(school),
                        coin,
                    });
                }
                1 | 2 => {
                    let (team, round): (i32, i32) = FromRow::from_row(&row)?;
                    let attendance = if result_set == 1 {
                        &mut data.byes
                    } else {
                        &mut data.no_shows
                    };
                    attendance.entry(i64::from(team)).or_default().push(round);
                }
                3 => {
                    let (id, coin) = FromRow::from_row(&row)?;
                    data.speakers.push(SpeakerRow {
                        id,
                        coin,
                        team: None,
                    });
                }
                4 => data.memberships.push(FromRow::from_row(&row)?),
                5 => data.rounds.push(FromRow::from_row(&row)?),
                6 => data.outrounds.push(FromRow::from_row(&row)?),
                7 => data.scores.push((
                    row.try_get(0)?,
                    row.try_get(1)?,
                    parse_score(row.try_get(2)?)?,
                    parse_score(row.try_get(3)?)?,
                )),
                _ => {
                    return Err(sqlx::Error::Protocol(
                        "Unexpected tournament result set".into(),
                    ));
                }
            }
        }
        if result_set != 8 {
            return Err(sqlx::Error::Protocol(
                "Incomplete tournament results".into(),
            ));
        }
        Ok(data)
    }
}

impl TournamentData {
    pub async fn load(db: &mut MySqlConnection) -> Result<Self, sqlx::Error> {
        let TournamentRows {
            teams,
            byes,
            no_shows,
            mut speakers,
            memberships: members,
            rounds: round_rows,
            outrounds,
            scores,
        } = TournamentRows::load(db).await?;
        let speaker_positions: HashMap<_, _> = speakers
            .iter()
            .enumerate()
            .map(|(position, speaker)| (speaker.id, position))
            .collect();
        let mut memberships: HashMap<_, Vec<_>> = HashMap::new();
        for (team, speaker) in members {
            let team = i64::from(team);
            let position = speaker_positions[&speaker];
            let primary_team = &mut speakers[position].team;
            // Django selects the lowest team ID when a debater has multiple teams.
            *primary_team = Some(primary_team.map_or(team, |other| other.min(team)));
            memberships.entry(team).or_default().push(position);
        }

        let mut rounds = Vec::new();
        let mut history: HashMap<TeamId, TeamHistory> = HashMap::new();
        let mut round_positions = HashMap::new();
        for (id, number, gov, opp, victor, pullup) in round_rows {
            let government = i64::from(gov);
            let opposition = i64::from(opp);
            {
                let government_history = history.entry(government).or_default();
                government_history.government += 1;
                government_history.pullups += usize::from(pullup == 1);
                government_history.faced_pullup |= pullup == 2;
                government_history.forfeit_wins += usize::from(matches!(victor, 3 | 6));
                government_history.opponents.push(opposition);
            }
            {
                let opposition_history = history.entry(opposition).or_default();
                opposition_history.opposition += 1;
                opposition_history.pullups += usize::from(pullup == 2);
                opposition_history.faced_pullup |= pullup == 1;
                opposition_history.forfeit_wins += usize::from(matches!(victor, 4 | 6));
                opposition_history.opponents.push(government);
            }
            let outcome = match victor {
                1 => Outcome::Government,
                2 => Outcome::Opposition,
                3 => Outcome::GovernmentByForfeit,
                4 => Outcome::OppositionByForfeit,
                5 => Outcome::BothLose,
                6 => Outcome::BothWin,
                _ => Outcome::Pending,
            };
            round_positions.insert(id, rounds.len());
            rounds.push(Round {
                id: i64::from(id),
                number,
                government,
                opposition,
                outcome,
            });
        }
        for (gov, opp) in outrounds {
            history.entry(i64::from(gov)).or_default().government += 1;
            history.entry(i64::from(opp)).or_default().opposition += 1;
        }

        let mut ballots: HashMap<_, Vec<_>> = HashMap::new();
        for (speaker, round, speaks, ranks) in scores {
            ballots.entry(speaker).or_default().push(BallotRow {
                round: round_positions[&round],
                speaks,
                ranks,
            });
        }
        Ok(Self {
            teams,
            rounds,
            byes,
            no_shows,
            speakers,
            memberships,
            ballots,
            history,
        })
    }

    pub fn scores(
        &self,
        policy: ScoringPolicy,
        filter: RoundFilter,
    ) -> Result<Vec<Standing>, &'static str> {
        let attendance: HashMap<_, _> = self
            .teams
            .iter()
            .map(|team| {
                (
                    team.id,
                    TeamAttendance {
                        id: team.id,
                        byes: self.byes.get(&team.id).map_or(&[], Vec::as_slice),
                        no_shows: self.no_shows.get(&team.id).map_or(&[], Vec::as_slice),
                    },
                )
            })
            .collect();
        let ballots: Vec<Vec<_>> = self
            .speakers
            .iter()
            .map(|speaker| self.speaker_ballots(speaker.id))
            .collect();
        let speakers: Vec<_> = self
            .speakers
            .iter()
            .zip(&ballots)
            .map(|(speaker, ballots)| Speaker {
                id: i64::from(speaker.id),
                team: speaker.team.and_then(|team| attendance.get(&team)),
                ballots,
                coin_flip: speaker.coin,
            })
            .collect();
        let members: Vec<Vec<_>> = self
            .teams
            .iter()
            .map(|team| {
                self.memberships
                    .get(&team.id)
                    .map_or(&[][..], Vec::as_slice)
                    .iter()
                    .map(|&index| &speakers[index])
                    .collect()
            })
            .collect();
        let teams: Vec<_> = self
            .teams
            .iter()
            .zip(&members)
            .map(|(team, speakers)| Team {
                attendance: &attendance[&team.id],
                speakers,
                coin_flip: team.coin,
            })
            .collect();
        team_standings(&teams, &self.rounds, policy, filter)
            .map_err(|_| "Could not calculate tournament standings.")
    }

    pub fn pairing_plan(
        &self,
        policy: &Policy,
        lenient_late: i32,
    ) -> Result<PairingPlan, &'static str> {
        let scores: HashMap<_, _> = self
            .scores(
                ScoringPolicy {
                    current_round: policy.current_round as i32,
                    lenient_late_through: lenient_late,
                },
                RoundFilter::default(),
            )?
            .into_iter()
            .map(|team| (team.id, team.score))
            .collect();
        let empty = TeamHistory::default();
        let entrants: Vec<_> = self
            .teams
            .iter()
            .filter(|team| team.checked_in)
            .map(|team| {
                let score = scores[&team.id];
                let history = self.history.get(&team.id).unwrap_or(&empty);
                let previous_byes = self.byes.get(&team.id).map_or(0, Vec::len);
                let lenient_no_shows = self.no_shows.get(&team.id).map_or(0, |rounds| {
                    rounds
                        .iter()
                        .filter(|&&round| round <= lenient_late)
                        .count()
                });
                Entrant {
                    id: team.id,
                    seed: team.seed,
                    previous_byes,
                    ranking: score,
                    has_been_pulled_up: history.pullups > 0,
                    average_score_rounds: previous_byes + lenient_no_shows + history.forfeit_wins,
                    opponents: &history.opponents,
                    team: PairingTeam {
                        school_id: Some(team.school),
                        wins: score.get(Stat::Wins) as u32,
                        sides: SideHistory {
                            gov: history.government,
                            opp: history.opposition,
                        },
                        hit_pull_up: history.faced_pullup,
                    },
                }
            })
            .collect();
        plan_prelim(&entrants, policy)
            .map_err(|_| "Could not pair the teams with the current results and settings.")
    }

    pub fn side_counts(&self, team: TeamId) -> (u32, u32) {
        self.history
            .get(&team)
            .map_or((0, 0), |history| (history.government, history.opposition))
    }

    fn speaker_ballots(&self, speaker: i32) -> Vec<Ballot<'_>> {
        self.ballots
            .get(&speaker)
            .map_or(&[][..], Vec::as_slice)
            .iter()
            .map(|ballot| Ballot {
                round: &self.rounds[ballot.round],
                speaks: ballot.speaks,
                ranks: ballot.ranks,
            })
            .collect()
    }

    pub fn team_details(
        &self,
        team: TeamId,
        policy: ScoringPolicy,
    ) -> Option<Vec<(&'static str, String)>> {
        let mut wins: HashMap<_, usize> = self
            .byes
            .iter()
            .map(|(&team, byes)| (team, byes.len()))
            .collect();
        for round in &self.rounds {
            let (gov, opp) = round.winning_sides();
            *wins.entry(round.government).or_default() += usize::from(gov);
            *wins.entry(round.opposition).or_default() += usize::from(opp);
        }
        let mut speaks = None;
        for &index in self.memberships.get(&team).into_iter().flatten() {
            let speaker = &self.speakers[index];
            let attendance = speaker.team.map(|id| TeamAttendance {
                id,
                byes: self.byes.get(&id).map_or(&[], Vec::as_slice),
                no_shows: self.no_shows.get(&id).map_or(&[], Vec::as_slice),
            });
            let points = speaker_points(
                &self.speaker_ballots(speaker.id),
                attendance.as_ref(),
                policy,
                RoundFilter::default(),
                false,
            )?;
            if !points.speaks.is_empty() {
                let total = points.speaks.iter().fold(0.0, |sum, point| sum + point);
                speaks = Some(speaks.unwrap_or(0.0) + total);
            }
        }
        let (gov, opp) = self.side_counts(team);
        let history = self.history.get(&team);
        let opponents = history.map_or(&[][..], |history| history.opponents.as_slice());
        let strength = if opponents.is_empty() {
            0.0
        } else {
            opponents
                .iter()
                .map(|id| wins.get(id).copied().unwrap_or(0))
                .sum::<usize>() as f64
                / opponents.len() as f64
        };
        Some(vec![
            ("Wins", wins.get(&team).copied().unwrap_or(0).to_string()),
            (
                "Total Speaks",
                speaks.map_or_else(|| "0".into(), |value| format!("{value:?}")),
            ),
            ("Govs", gov.to_string()),
            ("Opps", opp.to_string()),
            ("Avg. Opp Wins", format!("{strength:?}")),
            (
                "Been Pullup",
                history.map_or(0, |history| history.pullups).to_string(),
            ),
            (
                "Hit Pullup",
                if history.is_some_and(|history| history.faced_pullup) {
                    "True"
                } else {
                    "False"
                }
                .into(),
            ),
        ])
    }

    pub fn scores_by_team(
        &self,
        policy: ScoringPolicy,
    ) -> Result<HashMap<TeamId, Score>, &'static str> {
        Ok(self
            .scores(policy, RoundFilter::default())?
            .into_iter()
            .map(|row| (row.id, row.score))
            .collect())
    }
}
