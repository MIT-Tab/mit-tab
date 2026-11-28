use std::collections::{BTreeMap, BTreeSet};

use crate::bracket::opening_matchups;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Seed {
    pub team: i32,
    pub original: i32,
    pub effective: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SeedBasis {
    Original,
    Effective,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ChallengeError {
    InvalidField,
    UnavailableTeam,
    RepeatedTeam,
    MissingOpponent,
    InvalidSeeds,
}

pub struct ChallengeDraw {
    pub pairings: Vec<(i32, i32)>,
    pub seeds: BTreeMap<i32, i32>,
}

pub fn standard(seeds: &[Seed], size: usize) -> Result<Vec<(i32, i32)>, ChallengeError> {
    let slots = opening_matchups(size).ok_or(ChallengeError::InvalidField)?;
    let mut entries = BTreeMap::new();
    for seed in seeds {
        if seed.effective < 1
            || seed.effective as usize > size
            || entries.insert(seed.effective as usize, seed.team).is_some()
        {
            return Err(ChallengeError::InvalidSeeds);
        }
    }
    Ok(slots
        .into_iter()
        .filter_map(|(gov, opp)| entries.get(&gov).copied().zip(entries.get(&opp).copied()))
        .collect())
}

pub fn challenge(
    seeds: &[Seed],
    basis: SeedBasis,
    picks: &BTreeMap<i32, i32>,
) -> Result<ChallengeDraw, ChallengeError> {
    let size = seeds.len();
    if size < 4 || !size.is_power_of_two() || size > i32::MAX as usize {
        return Err(ChallengeError::InvalidField);
    }
    let active: BTreeSet<_> = seeds.iter().map(|seed| seed.team).collect();
    if active.len() != size {
        return Err(ChallengeError::InvalidSeeds);
    }
    let mut reserved = BTreeSet::new();
    let challenged: BTreeSet<_> = picks.values().copied().collect();
    for (&from, &to) in picks {
        if !active.contains(&from) || !active.contains(&to) {
            return Err(ChallengeError::UnavailableTeam);
        }
        if !reserved.insert(from) || !reserved.insert(to) {
            return Err(ChallengeError::RepeatedTeam);
        }
    }
    let mut teams = seeds.to_vec();
    teams.sort_by_key(|seed| {
        let order = if basis == SeedBasis::Original {
            seed.original
        } else {
            seed.effective
        };
        (order, seed.team)
    });
    let mut remaining: Vec<_> = teams.into_iter().map(|seed| seed.team).collect();
    let mut output = ChallengeDraw {
        pairings: Vec::with_capacity(size / 2),
        seeds: BTreeMap::new(),
    };
    while !remaining.is_empty() {
        let first = remaining
            .iter()
            .copied()
            .find(|team| !challenged.contains(team))
            .ok_or(ChallengeError::MissingOpponent)?;
        let second = if let Some(&pick) = picks.get(&first) {
            remaining.contains(&pick).then_some(pick)
        } else {
            remaining
                .iter()
                .rev()
                .copied()
                .find(|&team| team != first && !reserved.contains(&team))
        }
        .ok_or(ChallengeError::MissingOpponent)?;
        let high = output.pairings.len() as i32 + 1;
        let low = size as i32 - high + 1;
        output.seeds.insert(first, high);
        output.seeds.insert(second, low);
        output.pairings.push((first, second));
        remaining.retain(|&team| team != first && team != second);
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seeds(count: i32) -> Vec<Seed> {
        (1..=count)
            .map(|team| Seed {
                team,
                original: team,
                effective: team,
            })
            .collect()
    }

    #[test]
    fn standard_bracket_omits_the_matches_with_byes() {
        let matches = standard(&seeds(6), 8).unwrap();
        assert_eq!(matches, [(3, 6), (4, 5)]);
    }

    #[test]
    fn challenge_reserves_explicit_pairs_and_applies_new_effective_seeds() {
        let picks = BTreeMap::from([(2, 3)]);
        let chosen = challenge(&seeds(8), SeedBasis::Original, &picks).unwrap();
        assert_eq!(chosen.pairings, [(1, 8), (2, 3), (4, 7), (5, 6)]);
        assert_eq!((chosen.seeds[&2], chosen.seeds[&3]), (2, 7));
        for bad in [
            BTreeMap::from([(2, 2)]),
            BTreeMap::from([(2, 3), (3, 6)]),
            BTreeMap::from([(1, 3), (2, 3)]),
            BTreeMap::from([(1, 9)]),
        ] {
            assert!(challenge(&seeds(8), SeedBasis::Original, &bad).is_err());
        }
    }
}
