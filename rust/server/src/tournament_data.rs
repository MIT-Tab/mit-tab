use std::collections::HashMap;

use mittab_tabulation::{
    ballots::{Ballot, Outcome, Round, RoundFilter, ScoringPolicy, TeamAttendance, TeamId},
    pairing::{PairingTeam, SideHistory},
    prelim::{Entrant, PairingPlan, Policy, plan_prelim},
    rankings::{Score, Stat},
    standings::{Speaker, Standing, Team, team_standings},
};
use sqlx::MySqlConnection;

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
    was_pulled: bool,
    faced_pullup: bool,
    forfeit_wins: usize,
    opponents: Vec<TeamId>,
}

impl TournamentData {
    pub async fn load(db: &mut MySqlConnection) -> Result<Self, sqlx::Error> {
        let teams = sqlx::query_as::<_, (i32, i32, bool, i32, Option<i32>)>(
            "SELECT id, seed, checked_in, school_id, tiebreaker FROM tab_team ORDER BY id",
        )
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(|(id, seed, checked_in, school, coin)| TeamRow {
            id: i64::from(id),
            seed,
            checked_in,
            school: i64::from(school),
            coin,
        })
        .collect();
        let byes = attendance(
            db,
            "SELECT bye_team_id, round_number FROM tab_bye ORDER BY id",
        )
        .await?;
        let no_shows = attendance(
            db,
            "SELECT no_show_team_id, round_number FROM tab_noshow ORDER BY id",
        )
        .await?;
        let primary: HashMap<_, _> = sqlx::query_as::<_, (i32, i32)>(
            "SELECT debater_id, MIN(team_id) FROM tab_team_debaters GROUP BY debater_id",
        )
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .collect();
        let speakers: Vec<_> = sqlx::query_as::<_, (i32, Option<i32>)>(
            "SELECT id, tiebreaker FROM tab_debater ORDER BY name",
        )
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(|(id, coin)| SpeakerRow {
            id,
            coin,
            team: primary.get(&id).copied().map(i64::from),
        })
        .collect();
        let speaker_positions: HashMap<_, _> = speakers
            .iter()
            .enumerate()
            .map(|(position, speaker)| (speaker.id, position))
            .collect();
        let mut memberships: HashMap<_, Vec<_>> = HashMap::new();
        for (team, speaker) in sqlx::query_as::<_, (i32, i32)>(
            "SELECT m.team_id, m.debater_id FROM tab_team_debaters m
             JOIN tab_debater d ON d.id = m.debater_id ORDER BY m.team_id, d.name",
        )
        .fetch_all(&mut *db)
        .await?
        {
            memberships
                .entry(i64::from(team))
                .or_default()
                .push(speaker_positions[&speaker]);
        }

        let mut rounds = Vec::new();
        let mut history: HashMap<TeamId, TeamHistory> = HashMap::new();
        let mut round_positions = HashMap::new();
        for (id, number, gov, opp, victor, pullup) in sqlx::query_as::<_, (i32, i32, i32, i32, i32, i32)>(
            "SELECT id, round_number, gov_team_id, opp_team_id, victor, pullup FROM tab_round ORDER BY id",
        ).fetch_all(&mut *db).await? {
            let government = i64::from(gov);
            let opposition = i64::from(opp);
            {
                let government_history = history.entry(government).or_default();
                government_history.government += 1;
                government_history.was_pulled |= pullup == 1;
                government_history.faced_pullup |= pullup == 2;
                government_history.forfeit_wins += usize::from(matches!(victor, 3 | 6));
                government_history.opponents.push(opposition);
            }
            {
                let opposition_history = history.entry(opposition).or_default();
                opposition_history.opposition += 1;
                opposition_history.was_pulled |= pullup == 2;
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
            rounds.push(Round { id: i64::from(id), number, government, opposition, outcome });
        }
        for (gov, opp) in
            sqlx::query_as::<_, (i32, i32)>("SELECT gov_team_id, opp_team_id FROM tab_outround")
                .fetch_all(&mut *db)
                .await?
        {
            history.entry(i64::from(gov)).or_default().government += 1;
            history.entry(i64::from(opp)).or_default().opposition += 1;
        }

        let mut ballots: HashMap<_, Vec<_>> = HashMap::new();
        for (speaker, round, speaks, ranks) in sqlx::query_as::<_, (i32, i32, String, String)>(
            "SELECT debater_id, round_id, CAST(speaks AS CHAR), CAST(ranks AS CHAR)
             FROM tab_roundstats ORDER BY id",
        )
        .fetch_all(db)
        .await?
        {
            let parse = |value: String| {
                value
                    .parse::<f64>()
                    .map_err(|_| sqlx::Error::Protocol("Invalid round statistic".into()))
            };
            ballots.entry(speaker).or_default().push(BallotRow {
                round: round_positions[&round],
                speaks: parse(speaks)?,
                ranks: parse(ranks)?,
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
            .map(|speaker| {
                self.ballots
                    .get(&speaker.id)
                    .map_or(&[][..], Vec::as_slice)
                    .iter()
                    .map(|ballot| Ballot {
                        round: &self.rounds[ballot.round],
                        speaks: ballot.speaks,
                        ranks: ballot.ranks,
                    })
                    .collect()
            })
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
                    has_been_pulled_up: history.was_pulled,
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

async fn attendance(
    db: &mut MySqlConnection,
    query: &'static str,
) -> Result<HashMap<TeamId, Vec<i32>>, sqlx::Error> {
    let mut results: HashMap<_, Vec<_>> = HashMap::new();
    for (team, round) in sqlx::query_as::<_, (i32, i32)>(query).fetch_all(db).await? {
        results.entry(i64::from(team)).or_default().push(round);
    }
    Ok(results)
}
