use std::collections::BTreeMap;

pub type TeamId = i64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Pending,
    Government,
    Opposition,
    GovernmentByForfeit,
    OppositionByForfeit,
    BothLose,
    BothWin,
}

#[derive(Debug)]
pub struct Round {
    pub id: i64,
    pub number: i32,
    pub government: TeamId,
    pub opposition: TeamId,
    pub outcome: Outcome,
}

impl Round {
    pub fn winning_sides(&self) -> (bool, bool) {
        match self.outcome {
            Outcome::Government | Outcome::GovernmentByForfeit => (true, false),
            Outcome::Opposition | Outcome::OppositionByForfeit => (false, true),
            Outcome::BothWin => (true, true),
            Outcome::Pending | Outcome::BothLose => (false, false),
        }
    }

    fn forfeit(&self, team: Option<TeamId>) -> Option<bool> {
        let team = team?;
        let is_government = team == self.government;
        let is_opposition = team == self.opposition;
        if !is_government && !is_opposition {
            return None;
        }
        match self.outcome {
            Outcome::BothWin => Some(true),
            Outcome::GovernmentByForfeit => Some(is_government),
            Outcome::OppositionByForfeit => Some(is_opposition),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct RoundFilter {
    pub exclude: Option<i32>,
    pub through: Option<i32>,
}

impl RoundFilter {
    pub fn includes(self, number: i32) -> bool {
        self.exclude != Some(number) && self.through.is_none_or(|end| number <= end)
    }
}

#[derive(Debug)]
pub struct TeamAttendance<'a> {
    pub id: TeamId,
    pub byes: &'a [i32],
    pub no_shows: &'a [i32],
}

#[derive(Clone, Copy, Debug)]
pub struct Ballot<'a> {
    pub round: &'a Round,
    pub speaks: f64,
    pub ranks: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct ScoringPolicy {
    pub current_round: i32,
    pub lenient_late_through: i32,
}

#[derive(Debug, Default, PartialEq)]
pub struct SpeakerPoints {
    pub speaks: Vec<f64>,
    pub ranks: Vec<f64>,
}

pub fn speaker_points(
    ballots: &[Ballot<'_>],
    team: Option<&TeamAttendance<'_>>,
    policy: ScoringPolicy,
    filter: RoundFilter,
    average_ironmen: bool,
) -> Option<SpeakerPoints> {
    if ballots
        .iter()
        .any(|ballot| !ballot.speaks.is_finite() || !ballot.ranks.is_finite())
    {
        return None;
    }

    let mut by_round = BTreeMap::<i32, Vec<Ballot<'_>>>::new();
    for &ballot in ballots {
        by_round
            .entry(ballot.round.number)
            .or_default()
            .push(ballot);
    }

    // Historical standings use the same averages as current standings.
    let team_id = team.map(|team| team.id);
    let averages = real_ballot_averages(&by_round, team_id, policy.current_round);
    let mut points = SpeakerPoints::default();
    let end = filter
        .through
        .unwrap_or(policy.current_round.saturating_sub(1));
    for number in 1..=end {
        if let Some(entries) = by_round.get(&number).filter(|_| filter.includes(number)) {
            let first = entries[0].round;
            let forfeit = first.forfeit(team_id);
            append_metric(
                &mut points.ranks,
                entries.iter().map(|entry| entry.ranks),
                forfeit,
                averages.1,
                3.5,
                average_ironmen,
            );

            // Legacy imports can contain ballots for two distinct debates in
            // a round. Speaks use the highest entry, while ranks use all entries.
            let mut speaks = entries.clone();
            speaks.sort_by(|left, right| {
                right
                    .speaks
                    .partial_cmp(&left.speaks)
                    .expect("finite speaks")
            });
            let best = speaks[0].round;
            if speaks.iter().any(|entry| entry.round.id != best.id) {
                speaks.truncate(1);
            }
            append_metric(
                &mut points.speaks,
                speaks.iter().map(|entry| entry.speaks),
                best.forfeit(team_id),
                averages.0,
                0.0,
                average_ironmen,
            );
            continue;
        }

        match team {
            None => {
                points.speaks.push(0.0);
                points.ranks.push(0.0);
            }
            Some(team) => {
                let no_show = team.no_shows.contains(&number);
                if team.byes.contains(&number) || (no_show && number <= policy.lenient_late_through)
                {
                    points.speaks.push(averages.0);
                    points.ranks.push(averages.1);
                } else if no_show {
                    points.speaks.push(0.0);
                    points.ranks.push(3.5);
                }
            }
        }
    }

    Some(points)
}

fn real_ballot_averages(
    by_round: &BTreeMap<i32, Vec<Ballot<'_>>>,
    team: Option<TeamId>,
    current_round: i32,
) -> (f64, f64) {
    let rounds = by_round.iter().filter(|(number, entries)| {
        **number >= 1 && **number < current_round && entries[0].round.forfeit(team).is_none()
    });
    let mut speaks = 0.0;
    let mut ranks = 0.0;
    let mut count = 0;
    for (_, entries) in rounds {
        speaks += mean(entries.iter().map(|entry| entry.speaks));
        ranks += mean(entries.iter().map(|entry| entry.ranks));
        count += 1;
    }
    if count == 0 {
        (0.0, 0.0)
    } else {
        (speaks / f64::from(count), ranks / f64::from(count))
    }
}

fn mean(values: impl ExactSizeIterator<Item = f64>) -> f64 {
    let len = values.len() as f64;
    values.fold(0.0, |sum, score| sum + score) / len
}

fn append_metric(
    output: &mut Vec<f64>,
    values: impl ExactSizeIterator<Item = f64>,
    forfeit: Option<bool>,
    debater_average: f64,
    penalty: f64,
    average_ironmen: bool,
) {
    match forfeit {
        Some(true) => output.push(debater_average),
        Some(false) => output.push(penalty),
        None if average_ironmen => output.push(mean(values)),
        None => output.extend(values),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_forfeit_from_an_unrelated_debate_uses_the_actual_ballot() {
        let round = Round {
            id: 1,
            number: 1,
            government: 1,
            opposition: 2,
            outcome: Outcome::GovernmentByForfeit,
        };
        let attendance = TeamAttendance {
            id: 3,
            byes: &[],
            no_shows: &[],
        };
        let points = speaker_points(
            &[Ballot {
                round: &round,
                speaks: 27.0,
                ranks: 2.0,
            }],
            Some(&attendance),
            ScoringPolicy {
                current_round: 2,
                lenient_late_through: 0,
            },
            RoundFilter::default(),
            true,
        );
        assert_eq!(
            points,
            Some(SpeakerPoints {
                speaks: vec![27.0],
                ranks: vec![2.0]
            })
        );
    }
}
