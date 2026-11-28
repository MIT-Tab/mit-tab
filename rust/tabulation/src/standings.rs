use std::collections::HashMap;

use crate::{
    ballots::{speaker_points, Ballot, Round, RoundFilter, ScoringPolicy, TeamAttendance, TeamId},
    rankings::{Ranking, Score, Stat},
    scoring::{totals, Totals},
};

#[derive(Debug)]
pub struct Speaker<'a> {
    pub id: i64,
    /// The team Django's prefetched team selection would return for this speaker.
    pub team: Option<&'a TeamAttendance<'a>>,
    pub ballots: &'a [Ballot<'a>],
    pub coin_flip: Option<i32>,
}

#[derive(Debug)]
pub struct Team<'a> {
    pub attendance: &'a TeamAttendance<'a>,
    pub speakers: &'a [&'a Speaker<'a>],
    pub coin_flip: Option<i32>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum StandingsError {
    DuplicateTeam(TeamId),
    MissingTeam(TeamId),
    InvalidScore,
}

#[derive(Debug)]
pub struct Standing {
    pub id: i64,
    pub score: Score,
    pub tiebreaker_with_next: Option<Stat>,
}

pub fn speaker_standings(
    speakers: &[Speaker<'_>],
    policy: ScoringPolicy,
    single_adjusted_first: bool,
) -> Result<Vec<Standing>, StandingsError> {
    let mut scores = Vec::with_capacity(speakers.len());
    for speaker in speakers {
        let points = speaker_points(
            speaker.ballots,
            speaker.team,
            policy,
            RoundFilter::default(),
            true,
        )
        .ok_or(StandingsError::InvalidScore)?;
        scores.push(score(
            &[&points.speaks],
            &[&points.ranks],
            &[(
                Stat::CoinFlip,
                f64::from(speaker.coin_flip.unwrap_or_default()),
            )],
        )?);
    }
    Ok(rank(
        Ranking::Speaker {
            single_adjusted_first,
        },
        &scores,
        speakers.iter().map(|speaker| speaker.id),
    ))
}

/// Calculates team standings from preliminary ballots and rounds.
/// Input order resolves exact ties, including ties in the coin-flip field.
pub fn team_standings(
    teams: &[Team<'_>],
    rounds: &[Round],
    policy: ScoringPolicy,
    filter: RoundFilter,
) -> Result<Vec<Standing>, StandingsError> {
    let mut position_by_id = HashMap::with_capacity(teams.len());
    for (position, team) in teams.iter().enumerate() {
        if position_by_id
            .insert(team.attendance.id, position)
            .is_some()
        {
            return Err(StandingsError::DuplicateTeam(team.attendance.id));
        }
    }

    let mut opponents = vec![Vec::new(); teams.len()];
    let mut wins: Vec<_> = teams
        .iter()
        .map(|team| {
            team.attendance
                .byes
                .iter()
                .filter(|&&number| filter.includes(number))
                .count()
        })
        .collect();

    for round in rounds.iter().filter(|round| filter.includes(round.number)) {
        let government = *position_by_id
            .get(&round.government)
            .ok_or(StandingsError::MissingTeam(round.government))?;
        let opposition = *position_by_id
            .get(&round.opposition)
            .ok_or(StandingsError::MissingTeam(round.opposition))?;
        // Team identity and the two roles usually coincide one-to-one, but
        // legacy self-pairings can occur and count once per role in Django.
        let (government_won, opposition_won) = round.winning_sides();
        wins[government] += usize::from(government_won);
        wins[opposition] += usize::from(opposition_won);
        opponents[government].push(opposition);
        opponents[opposition].push(government);
    }

    let mut scores = Vec::with_capacity(teams.len());
    for (position, team) in teams.iter().enumerate() {
        let mut speaks = Vec::with_capacity(team.speakers.len());
        let mut ranks = Vec::with_capacity(team.speakers.len());
        for speaker in team.speakers {
            let points = speaker_points(speaker.ballots, speaker.team, policy, filter, false)
                .ok_or(StandingsError::InvalidScore)?;
            speaks.push(points.speaks);
            ranks.push(points.ranks);
        }
        let opponent_wins: usize = opponents[position]
            .iter()
            .map(|&opponent| wins[opponent])
            .sum();
        let opp_strength = if opponents[position].is_empty() {
            0.0
        } else {
            opponent_wins as f64 / opponents[position].len() as f64
        };
        let speaks: Vec<_> = speaks.iter().map(Vec::as_slice).collect();
        let ranks: Vec<_> = ranks.iter().map(Vec::as_slice).collect();
        scores.push(score(
            &speaks,
            &ranks,
            &[
                (Stat::Wins, wins[position] as f64),
                (Stat::OppStrength, opp_strength),
                (
                    Stat::CoinFlip,
                    f64::from(team.coin_flip.unwrap_or_default()),
                ),
            ],
        )?);
    }
    Ok(rank(
        Ranking::Team,
        &scores,
        teams.iter().map(|team| team.attendance.id),
    ))
}

fn score(
    speaks: &[&[f64]],
    ranks: &[&[f64]],
    stats: &[(Stat, f64)],
) -> Result<Score, StandingsError> {
    let Totals {
        raw: speaks,
        single_adjusted: single_speaks,
        double_adjusted: double_speaks,
    } = totals(speaks).ok_or(StandingsError::InvalidScore)?;
    let Totals {
        raw: ranks,
        single_adjusted: single_ranks,
        double_adjusted: double_ranks,
    } = totals(ranks).ok_or(StandingsError::InvalidScore)?;
    let mut values = vec![
        (Stat::Speaks, speaks),
        (Stat::Ranks, ranks),
        (Stat::SingleAdjustedSpeaks, single_speaks),
        (Stat::SingleAdjustedRanks, single_ranks),
        (Stat::DoubleAdjustedSpeaks, double_speaks),
        (Stat::DoubleAdjustedRanks, double_ranks),
    ];
    values.extend_from_slice(stats);
    Score::new(&values).ok_or(StandingsError::InvalidScore)
}

fn rank(ranking: Ranking, scores: &[Score], ids: impl Iterator<Item = i64>) -> Vec<Standing> {
    let ids: Vec<_> = ids.collect();
    let order = ranking.order(scores);
    order
        .iter()
        .enumerate()
        .map(|(index, &position)| Standing {
            id: ids[position],
            score: scores[position],
            tiebreaker_with_next: order
                .get(index + 1)
                .and_then(|&next| ranking.tiebreaker(&scores[position], &scores[next])),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ballots::Outcome;

    #[test]
    fn repeated_team_ids_cannot_overwrite_results() {
        let attendance = TeamAttendance {
            id: 5,
            byes: &[],
            no_shows: &[],
        };
        let team = || Team {
            attendance: &attendance,
            speakers: &[],
            coin_flip: None,
        };
        let policy = ScoringPolicy {
            current_round: 1,
            lenient_late_through: 0,
        };
        assert_eq!(
            team_standings(&[team(), team()], &[], policy, RoundFilter::default()).unwrap_err(),
            StandingsError::DuplicateTeam(5),
        );
    }

    #[test]
    fn a_round_with_an_unknown_opponent_cannot_be_counted() {
        let attendance = TeamAttendance {
            id: 5,
            byes: &[],
            no_shows: &[],
        };
        let team = Team {
            attendance: &attendance,
            speakers: &[],
            coin_flip: None,
        };
        let rounds = [Round {
            id: 1,
            number: 1,
            government: 5,
            opposition: 6,
            outcome: Outcome::Government,
        }];
        let policy = ScoringPolicy {
            current_round: 2,
            lenient_late_through: 0,
        };
        assert_eq!(
            team_standings(&[team], &rounds, policy, RoundFilter::default()).unwrap_err(),
            StandingsError::MissingTeam(6),
        );
    }
}
