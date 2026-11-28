use std::cmp::Ordering;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(usize)]
pub enum Stat {
    Wins,
    Speaks,
    Ranks,
    SingleAdjustedSpeaks,
    SingleAdjustedRanks,
    DoubleAdjustedSpeaks,
    DoubleAdjustedRanks,
    OppStrength,
    CoinFlip,
}

const TEAM_PRIORITY: [Stat; 9] = [
    Stat::Wins,
    Stat::Speaks,
    Stat::Ranks,
    Stat::SingleAdjustedSpeaks,
    Stat::SingleAdjustedRanks,
    Stat::DoubleAdjustedSpeaks,
    Stat::DoubleAdjustedRanks,
    Stat::OppStrength,
    Stat::CoinFlip,
];

const SPEAKER_PRIORITY: [Stat; 7] = [
    Stat::Speaks,
    Stat::Ranks,
    Stat::SingleAdjustedSpeaks,
    Stat::SingleAdjustedRanks,
    Stat::DoubleAdjustedSpeaks,
    Stat::DoubleAdjustedRanks,
    Stat::CoinFlip,
];

#[derive(Clone, Copy, Debug)]
pub enum Ranking {
    Team,
    Speaker { single_adjusted_first: bool },
}

#[derive(Clone, Copy, Debug)]
pub struct Score {
    values: [f64; TEAM_PRIORITY.len()],
}

impl Score {
    pub fn new(values: &[(Stat, f64)]) -> Option<Self> {
        let mut stats = [0.0; TEAM_PRIORITY.len()];
        for &(stat, value) in values {
            if !value.is_finite() {
                return None;
            }
            stats[stat as usize] = value;
        }
        Some(Self { values: stats })
    }

    pub fn get(&self, stat: Stat) -> f64 {
        self.values[stat as usize]
    }
}

impl Ranking {
    fn compare(self, first: &Score, second: &Score) -> (Ordering, Option<Stat>) {
        let mut adjusted_priority = SPEAKER_PRIORITY;
        let priority: &[Stat] = match self {
            Self::Team => &TEAM_PRIORITY,
            Self::Speaker {
                single_adjusted_first: false,
            } => &SPEAKER_PRIORITY,
            Self::Speaker {
                single_adjusted_first: true,
            } => {
                adjusted_priority.swap(0, 2);
                &adjusted_priority
            }
        };

        for &stat in priority {
            // Scores contain only finite values. Partial comparison treats -0.0 as 0.0.
            let ordering = first.values[stat as usize]
                .partial_cmp(&second.values[stat as usize])
                .expect("validated finite scores");
            if !ordering.is_eq() {
                let descending = matches!(
                    stat,
                    Stat::Wins
                        | Stat::Speaks
                        | Stat::SingleAdjustedSpeaks
                        | Stat::DoubleAdjustedSpeaks
                        | Stat::OppStrength
                );
                return (
                    if descending {
                        ordering.reverse()
                    } else {
                        ordering
                    },
                    Some(stat),
                );
            }
        }
        (Ordering::Equal, None)
    }

    /// Returns input positions in standings order, preserving ties in input order.
    pub fn order(self, scores: &[Score]) -> Vec<usize> {
        let mut positions: Vec<_> = (0..scores.len()).collect();
        positions.sort_by(|&first, &second| self.compare(&scores[first], &scores[second]).0);
        positions
    }

    pub fn compare_scores(self, first: &Score, second: &Score) -> Ordering {
        self.compare(first, second).0
    }

    pub fn tiebreaker(self, first: &Score, second: &Score) -> Option<Stat> {
        self.compare(first, second).1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_non_finite_scores() {
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(Score::new(&[(Stat::Speaks, value)]).is_none());
        }
    }

    #[test]
    fn treats_both_signed_zeros_and_missing_values_as_ties() {
        let scores = [
            Score::new(&[(Stat::Speaks, -0.0)]).unwrap(),
            Score::new(&[(Stat::Speaks, 0.0)]).unwrap(),
            Score::new(&[]).unwrap(),
        ];
        assert_eq!(Ranking::Team.order(&scores), [0, 1, 2]);
        assert_eq!(Ranking::Team.tiebreaker(&scores[0], &scores[1]), None);
    }
}
