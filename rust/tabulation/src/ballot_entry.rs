use std::{cmp::Reverse, collections::BTreeMap};

pub const ROLES: [&str; 4] = ["pm", "mg", "lo", "mo"];
pub const ROLE_NAMES: [&str; 4] = [
    "Prime Minister",
    "Member of Government",
    "Leader of the Opposition",
    "Member of the Opposition",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Score {
    pub debater: i32,
    pub speaks: i32,
    pub rank: i32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Ballot {
    pub winner: i32,
    pub scores: [Score; 4],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SpeakPolicy {
    pub minimum: i32,
    pub maximum: i32,
    pub step: i32,
}

impl SpeakPolicy {
    pub fn valid(&self, speaks: i32) -> bool {
        (self.minimum..=self.maximum).contains(&speaks) && speaks % self.step == 0
    }
}

impl Ballot {
    pub fn validate(&self, policy: SpeakPolicy, eligible: [&[i32]; 2]) -> Result<(), &'static str> {
        if !(1..=6).contains(&self.winner) {
            return Err("Choose an outcome for this round.");
        }
        let mut ranks = [0_i32; 4];
        for (index, score) in self.scores.iter().enumerate() {
            if !eligible[index / 2].contains(&score.debater) {
                return Err("Select a debater listed for each position.");
            }
            if !policy.valid(score.speaks) {
                return Err("Speaker points must be within the configured range and use the configured increments.");
            }
            ranks[index] = score.rank;
        }
        ranks.sort_unstable();
        if ranks != [1, 2, 3, 4] {
            return Err("Ranks must be different.");
        }
        let mut ranked = self.scores;
        ranked.sort_by_key(|score| score.rank);
        if ranked
            .windows(2)
            .any(|scores| scores[0].speaks < scores[1].speaks)
        {
            return Err("Higher-ranked speakers must have equal or higher speaker points.");
        }
        let points = |scores: &[Score]| {
            (
                scores.iter().map(|score| score.speaks).sum::<i32>(),
                -scores.iter().map(|score| score.rank).sum::<i32>(),
            )
        };
        let government = points(&self.scores[..2]);
        let opposition = points(&self.scores[2..]);
        if (self.winner == 1 && opposition > government)
            || (self.winner == 2 && government > opposition)
        {
            return Err("The selected winner received fewer speaker points or lower ranks.");
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug)]
pub struct PanelScore {
    pub position: usize,
    pub debater: i32,
    pub speaks: f64,
    pub rank: usize,
}

#[derive(Debug)]
pub struct PanelResult {
    pub outcome: i32,
    pub scores: Vec<PanelScore>,
}

pub fn score_panel(
    ballots: &[Ballot],
    discard_minority: bool,
) -> Result<PanelResult, &'static str> {
    let Some(first) = ballots.first() else {
        return Err("Enter the panel's ballots.");
    };
    let mut votes = BTreeMap::<_, Vec<_>>::new();
    let mut vote_order = Vec::new();
    for ballot in ballots {
        if first
            .scores
            .iter()
            .zip(&ballot.scores)
            .any(|(a, b)| a.debater != b.debater)
        {
            return Err("Panel ballots must list the same debater in each position.");
        }
        votes
            .entry(ballot.winner)
            .or_insert_with(|| {
                vote_order.push(ballot.winner);
                Vec::new()
            })
            .push(ballot);
    }
    let (&outcome, majority) = votes
        .iter()
        .max_by_key(|&(winner, ballots)| (ballots.len(), *winner))
        .expect("nonempty panel");
    if majority.len() < 2 {
        return Err("At least two ballots must agree on the outcome.");
    }
    let chosen: Vec<_> = if discard_minority {
        majority.clone()
    } else {
        vote_order
            .iter()
            .flat_map(|winner| votes[winner].iter().copied())
            .collect()
    };
    let average = |position: usize, value: fn(&Score) -> f64| {
        chosen
            .iter()
            .map(|ballot| value(&ballot.scores[position]))
            .sum::<f64>()
            / chosen.len() as f64
    };
    let mut scores: Vec<_> = first
        .scores
        .iter()
        .enumerate()
        .map(|(position, score)| {
            let speaks = average(position, |score| f64::from(score.speaks) / 10_000.0);
            let prior_rank = average(position, |score| f64::from(score.rank));
            (
                position,
                score.debater,
                speaks,
                cents(speaks),
                cents(prior_rank),
            )
        })
        .collect();
    scores.sort_by_key(|&(_, _, _, speaks, rank)| (Reverse(speaks), rank));
    Ok(PanelResult {
        outcome,
        scores: scores
            .into_iter()
            .enumerate()
            .map(|(rank, (position, debater, speaks, _, _))| PanelScore {
                position,
                debater,
                speaks,
                rank: rank + 1,
            })
            .collect(),
    })
}

fn cents(number: f64) -> i64 {
    // The reference rounds the exact binary average to 0.01 before it sorts.
    let rounded = format!("{number:.2}");
    rounded
        .replace('.', "")
        .parse()
        .expect("finite panel score")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_ballots_must_agree_on_positions_and_multiple_judges_must_agree_on_outcome() {
        let result = Ballot {
            winner: 1,
            scores: std::array::from_fn(|index| Score {
                debater: index as i32 + 1,
                speaks: (32 - index as i32) * 10_000,
                rank: index as i32 + 1,
            }),
        };
        let policy = SpeakPolicy {
            minimum: 0,
            maximum: 50 * 10_000,
            step: 10_000,
        };
        assert!(result.validate(policy, [&[1, 2], &[3, 4]]).is_ok());
        let mut different = result.clone();
        different.scores.swap(0, 1);
        assert!(score_panel(&[result.clone(), different], false).is_err());
        assert!(score_panel(std::slice::from_ref(&result), true).is_err());
        let valid = score_panel(&[result.clone(), result], true).unwrap();
        assert_eq!(valid.outcome, 1);
        assert_eq!(valid.scores[0].rank, 1);
        assert_eq!(valid.scores[0].speaks, 32.0);
    }
}
