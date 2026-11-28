use std::cmp::Ordering;

#[derive(Debug, PartialEq, Eq)]
pub enum ByeCountError {
    NegativeMinimum,
    InsufficientTeams,
}

pub fn required_bye_count(team_count: usize, minimum: i32) -> Result<usize, ByeCountError> {
    let minimum = usize::try_from(minimum).map_err(|_| ByeCountError::NegativeMinimum)?;
    if minimum > team_count {
        return Err(ByeCountError::InsufficientTeams);
    }
    Ok(minimum + (team_count - minimum) % 2)
}

/// Returns eligible positions in the original first-round candidate order.
pub fn first_round_bye_candidates(seeds: &[i32], fair_bye: i32) -> Vec<usize> {
    let mut max_seed = match fair_bye {
        1 => 1, // Free seed
        2 => 0, // Unseeded
        _ => return (0..seeds.len()).collect(),
    };

    if let Some(lowest_seed) = seeds.iter().min() {
        max_seed = max_seed.max(*lowest_seed);
    }
    seeds
        .iter()
        .enumerate()
        .filter_map(|(index, seed)| (*seed <= max_seed).then_some(index))
        .collect()
}

#[derive(Clone, Copy, Debug)]
pub struct SideHistory {
    pub gov: u32,
    pub opp: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Government {
    FirstTeam,
    SecondTeam,
    CoinFlip,
}

pub fn government(first_team: SideHistory, second_team: SideHistory) -> Government {
    match first_team
        .gov
        .cmp(&second_team.gov)
        .then_with(|| second_team.opp.cmp(&first_team.opp))
    {
        Ordering::Less => Government::FirstTeam,
        Ordering::Greater => Government::SecondTeam,
        Ordering::Equal => Government::CoinFlip,
    }
}

#[derive(Clone, Copy, Debug)]
pub struct PairingTeam {
    pub school_id: Option<i64>,
    pub wins: u32,
    pub sides: SideHistory,
    pub hit_pull_up: bool,
}

#[derive(Clone, Copy, Debug)]
pub enum PowerPairingDistance {
    FirstRound {
        first_seed: i32,
        second_seed: i32,
        first_ideal_seed: i32,
        second_ideal_seed: i32,
    },
    LaterRound {
        first_position: u32,
        second_position: u32,
        first_ideal_position: u32,
        second_ideal_position: u32,
    },
}

#[derive(Clone, Copy, Debug)]
pub struct PairingWeights {
    pub power_pairing_multiple: i32,
    pub high_opp_penalty: i32,
    pub higher_opp_penalty: i32,
    pub high_gov_penalty: i32,
    pub same_school_penalty: i32,
    pub hit_pull_up_before: i32,
    pub hit_team_before: i32,
}

pub fn pairing_weight(
    first: PairingTeam,
    second: PairingTeam,
    distance: PowerPairingDistance,
    total_rounds: i32,
    rematch: bool,
    weights: PairingWeights,
) -> f64 {
    let distance = match distance {
        PowerPairingDistance::FirstRound {
            first_seed,
            second_seed,
            first_ideal_seed,
            second_ideal_seed,
        } => {
            u64::from(first_ideal_seed.abs_diff(second_seed))
                + u64::from(second_ideal_seed.abs_diff(first_seed))
        }
        PowerPairingDistance::LaterRound {
            first_position,
            second_position,
            first_ideal_position,
            second_ideal_position,
        } => {
            u64::from(first_ideal_position.abs_diff(second_position))
                + u64::from(second_ideal_position.abs_diff(first_position))
        }
    };
    let mut weight =
        (i128::from(weights.power_pairing_multiple) * i128::from(distance)) as f64 / 2.0;

    let half = i64::from(total_rounds.div_euclid(2)) + 1;
    let min_opp = i64::from(first.sides.opp.min(second.sides.opp));
    let min_gov = i64::from(first.sides.gov.min(second.sides.gov));
    if min_opp >= half {
        weight += f64::from(weights.high_opp_penalty);
    }
    if min_opp > half {
        weight += f64::from(weights.higher_opp_penalty);
    }
    if min_gov >= half {
        weight += f64::from(weights.high_gov_penalty);
    }
    if first.school_id == second.school_id {
        weight += f64::from(weights.same_school_penalty);
    }
    if (first.hit_pull_up && second.wins < first.wins)
        || (second.hit_pull_up && first.wins < second.wins)
    {
        weight += f64::from(weights.hit_pull_up_before);
    }
    if rematch {
        weight += f64::from(weights.hit_team_before);
    }
    weight
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bye_count_is_minimal_and_leaves_an_even_field() {
        for checked_in in 0..=100 {
            for minimum in 0..=checked_in {
                let count = required_bye_count(checked_in, minimum as i32).unwrap();
                assert!(count == minimum || count == minimum + 1);
                assert_eq!((checked_in - count) % 2, 0);
            }
        }
        assert_eq!(
            required_bye_count(2, -1),
            Err(ByeCountError::NegativeMinimum)
        );
        assert_eq!(
            required_bye_count(2, 3),
            Err(ByeCountError::InsufficientTeams)
        );
        assert_eq!(required_bye_count(usize::MAX, 0), Ok(1));
    }

    #[test]
    fn an_empty_bye_candidate_pool_stays_empty() {
        for policy in [0, 1, 2] {
            assert!(first_round_bye_candidates(&[], policy).is_empty());
        }
    }
}
