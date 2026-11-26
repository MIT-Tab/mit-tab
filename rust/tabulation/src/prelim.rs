use std::collections::HashSet;

use crate::{
    ballots::TeamId,
    legacy_random::LegacyRandom,
    matching::{maximum_matching, Edge, MatchError},
    pairing::{
        first_round_bye_candidates, government, pairing_weight, required_bye_count, ByeCountError,
        Government, PairingTeam, PairingWeights, PowerPairingDistance,
    },
    rankings::{Ranking, Score},
};

pub struct Entrant<'a> {
    pub id: TeamId,
    pub seed: i32,
    pub team: PairingTeam,
    pub ranking: Score,
    pub previous_byes: usize,
    pub average_score_rounds: usize,
    pub has_been_pulled_up: bool,
    pub opponents: &'a [TeamId],
}

pub struct Policy {
    pub current_round: u32,
    pub total_rounds: i32,
    pub minimum_byes: i32,
    pub fair_bye: i32,
    pub random_second_round: bool,
    pub weights: PairingWeights,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Debate {
    pub government: TeamId,
    pub opposition: TeamId,
    pub pulled_up: Option<TeamId>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct PairingPlan {
    pub debates: Vec<Debate>,
    pub byes: Vec<TeamId>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum PairingError {
    InvalidRound,
    DuplicateTeam(TeamId),
    InvalidRecord(TeamId),
    InvalidByeCount(ByeCountError),
    UnpairableBracket,
    Matching(MatchError),
}

pub fn plan_prelim(entrants: &[Entrant<'_>], policy: &Policy) -> Result<PairingPlan, PairingError> {
    if policy.current_round == 0 {
        return Err(PairingError::InvalidRound);
    }
    let mut seen = HashSet::with_capacity(entrants.len());
    for entrant in entrants {
        if !seen.insert(entrant.id) {
            return Err(PairingError::DuplicateTeam(entrant.id));
        }
        if policy.current_round > 1 && entrant.team.wins >= policy.current_round {
            return Err(PairingError::InvalidRecord(entrant.id));
        }
    }
    let bye_count = required_bye_count(entrants.len(), policy.minimum_byes)
        .map_err(PairingError::InvalidByeCount)?;
    let mut rng = LegacyRandom::seeded(0xBEEF);
    let mut remaining: Vec<_> = (0..entrants.len()).collect();
    let mut byes = Vec::new();
    if policy.minimum_byes > 0 {
        assign_byes(
            entrants,
            policy,
            bye_count,
            &mut remaining,
            &mut byes,
            &mut rng,
        );
    }
    let mut pullups = HashSet::new();
    let mut pairs = Vec::new();
    if policy.current_round == 1 {
        if remaining.len() % 2 == 1 {
            let eligible = first_round_candidates(entrants, &remaining, policy.fair_bye);
            let team = eligible[rng.below(eligible.len())];
            byes.push(team);
            remaining.retain(|&index| index != team);
        }
        rng.shuffle(&mut remaining);
        remaining.sort_by_key(|&index| std::cmp::Reverse(entrants[index].seed));
        pairs = pair_bracket(entrants, &remaining, policy, &mut rng)?;
        rng.shuffle(&mut pairs);
        pairs.sort_by_key(|&(first, second)| {
            std::cmp::Reverse(entrants[first].seed.max(entrants[second].seed))
        });
    } else {
        let brackets = even_brackets(
            entrants,
            remaining,
            policy,
            &mut byes,
            &mut pullups,
            &mut rng,
        )?;
        for bracket in brackets {
            pairs.extend(pair_bracket(entrants, &bracket, policy, &mut rng)?);
        }
        let mut sorted: Vec<_> = (0..entrants.len()).collect();
        rank(entrants, &mut sorted);
        let mut position = vec![0; entrants.len()];
        for (index, team) in sorted.into_iter().enumerate() {
            position[team] = index;
        }
        pairs.sort_by_key(|&(first, second)| position[first].min(position[second]));
    }
    Ok(PairingPlan {
        byes: byes.into_iter().map(|team| entrants[team].id).collect(),
        debates: pairs
            .into_iter()
            .map(|(gov, opp)| Debate {
                government: entrants[gov].id,
                opposition: entrants[opp].id,
                pulled_up: [gov, opp]
                    .into_iter()
                    .find(|team| pullups.contains(team))
                    .map(|team| entrants[team].id),
            })
            .collect(),
    })
}

fn first_round_candidates(
    entrants: &[Entrant<'_>],
    candidates: &[usize],
    policy: i32,
) -> Vec<usize> {
    let seeds: Vec<_> = candidates.iter().map(|&team| entrants[team].seed).collect();
    first_round_bye_candidates(&seeds, policy)
        .into_iter()
        .map(|index| candidates[index])
        .collect()
}

fn rank(entrants: &[Entrant<'_>], teams: &mut [usize]) {
    teams
        .sort_by(|&a, &b| Ranking::Team.compare_scores(&entrants[a].ranking, &entrants[b].ranking));
}

fn assign_byes(
    entrants: &[Entrant<'_>],
    policy: &Policy,
    count: usize,
    remaining: &mut Vec<usize>,
    byes: &mut Vec<usize>,
    rng: &mut LegacyRandom,
) {
    let mut ordered = remaining.clone();
    if policy.current_round > 1 {
        rank(entrants, &mut ordered);
        ordered.sort_by_key(|&team| std::cmp::Reverse(entrants[team].team.wins));
    }
    for _ in 0..count {
        let fewest = ordered
            .iter()
            .map(|&team| entrants[team].previous_byes)
            .min()
            .unwrap();
        let candidates: Vec<_> = ordered
            .iter()
            .copied()
            .filter(|&team| entrants[team].previous_byes == fewest)
            .collect();
        let chosen = if policy.current_round == 1 {
            let eligible = first_round_candidates(entrants, &candidates, policy.fair_bye);
            eligible[rng.below(eligible.len())]
        } else {
            *candidates.last().unwrap()
        };
        byes.push(chosen);
        ordered.retain(|&team| team != chosen);
    }
    remaining.retain(|team| !byes.contains(team));
}

fn even_brackets(
    entrants: &[Entrant<'_>],
    mut remaining: Vec<usize>,
    policy: &Policy,
    byes: &mut Vec<usize>,
    pullups: &mut HashSet<usize>,
    rng: &mut LegacyRandom,
) -> Result<Vec<Vec<usize>>, PairingError> {
    let original_order = remaining.clone();
    rng.shuffle(&mut remaining);
    let completed = policy.current_round as usize - 1;
    let mut brackets = vec![Vec::new(); completed + 1];
    let mut middle = HashSet::new();
    let mut middle_order = Vec::new();
    for index in remaining {
        let team = &entrants[index];
        if team.average_score_rounds == completed {
            middle.insert(index);
            middle_order.push(index);
        } else {
            brackets[team.team.wins as usize].push(index);
        }
    }
    for bucket in &mut brackets {
        rank(entrants, bucket);
    }
    for index in middle_order {
        let bracket = &mut brackets[entrants[index].team.wins as usize];
        bracket.insert(bracket.len() / 2, index);
    }

    for bucket in (0..brackets.len()).rev() {
        if brackets[bucket].len() % 2 == 0 {
            continue;
        }
        if policy.minimum_byes == 0 && bucket == 0 {
            byes.push(brackets[0].pop().unwrap());
        } else if policy.minimum_byes == 0 && bucket == 1 && brackets[0].is_empty() {
            let index = brackets[1]
                .iter()
                .rposition(|&team| entrants[team].previous_byes == 0)
                .ok_or(PairingError::UnpairableBracket)?;
            byes.push(brackets[1].remove(index));
        } else {
            let donor = if policy.minimum_byes > 0 {
                (0..bucket).rev().find(|&lower| !brackets[lower].is_empty())
            } else {
                bucket.checked_sub(1)
            }
            .ok_or(PairingError::UnpairableBracket)?;
            let best_donor = brackets[donor]
                .iter()
                .rposition(|&team| !entrants[team].has_been_pulled_up)
                .or_else(|| brackets[donor].len().checked_sub(1))
                .ok_or(PairingError::UnpairableBracket)?;
            let pulled = brackets[donor].remove(best_donor);
            pullups.insert(pulled);
            let recipients = &mut brackets[bucket];
            recipients.push(pulled);
            let reinsert: Vec<_> = original_order
                .iter()
                .copied()
                .filter(|team| {
                    middle.contains(team)
                        && entrants[*team].team.wins as usize == bucket
                        && recipients.contains(team)
                })
                .collect();
            recipients.retain(|team| !reinsert.contains(team));
            rank(entrants, recipients);
            for team in reinsert {
                recipients.insert(recipients.len() / 2, team);
            }
        }
    }
    Ok(brackets)
}

fn pair_bracket(
    entrants: &[Entrant<'_>],
    bracket: &[usize],
    policy: &Policy,
    rng: &mut LegacyRandom,
) -> Result<Vec<(usize, usize)>, PairingError> {
    let random = policy.current_round == 2 && policy.random_second_round;
    let mut edges = Vec::new();
    let size = bracket.len();
    for (first, &first_team) in bracket.iter().enumerate() {
        for (second, &second_team) in bracket[..first].iter().enumerate() {
            let a = &entrants[first_team];
            let b = &entrants[second_team];
            let rematch = a.opponents.contains(&b.id);
            let weight = if random {
                if rematch {
                    continue;
                }
                rng.below((1 << 16) + 1) as f64
            } else {
                let first_ideal = size - first - 1;
                let second_ideal = size - second - 1;
                let distance = if policy.current_round == 1 {
                    PowerPairingDistance::FirstRound {
                        first_seed: a.seed,
                        second_seed: b.seed,
                        first_ideal_seed: entrants[bracket[first_ideal]].seed,
                        second_ideal_seed: entrants[bracket[second_ideal]].seed,
                    }
                } else {
                    PowerPairingDistance::LaterRound {
                        first_position: first as u32,
                        second_position: second as u32,
                        first_ideal_position: first_ideal as u32,
                        second_ideal_position: second_ideal as u32,
                    }
                };
                pairing_weight(
                    a.team,
                    b.team,
                    distance,
                    policy.total_rounds,
                    rematch,
                    policy.weights,
                )
            };
            edges.push(Edge {
                first,
                second,
                weight,
            });
        }
    }
    let matched = maximum_matching(size, &edges, true).map_err(PairingError::Matching)?;
    let mut pairs = Vec::new();
    for (first, second) in matched.into_iter().enumerate() {
        let second = second.ok_or(PairingError::UnpairableBracket)?;
        if first >= second {
            continue;
        }
        let (mut a, mut b) = if random {
            (bracket[first], bracket[second])
        } else {
            (bracket[second], bracket[first])
        };
        if second_in_set_first(entrants[a].id, entrants[b].id) {
            std::mem::swap(&mut a, &mut b);
        }
        match government(entrants[a].team.sides, entrants[b].team.sides) {
            Government::FirstTeam => {}
            Government::SecondTeam => std::mem::swap(&mut a, &mut b),
            Government::CoinFlip => {
                if rng.below(2) == 1 {
                    std::mem::swap(&mut a, &mut b);
                }
            }
        }
        pairs.push((a, b));
    }
    Ok(pairs)
}

/// A two-element CPython set orders by its occupied slots. Team model hashes
/// are primary-key hashes. The old pairing code used this order before flipping
/// a coin, so preserve it when side counts tie.
fn second_in_set_first(first: TeamId, second: TeamId) -> bool {
    let hash = |id| if id == -1 { -2_i64 } else { id } as u64;
    let first_slot = hash(first) & 7;
    let mut perturb = hash(second);
    let mut second_slot = perturb & 7;
    while second_slot == first_slot {
        perturb >>= 5;
        second_slot = (second_slot * 5 + 1 + perturb) & 7;
    }
    second_slot < first_slot
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{pairing::SideHistory, rankings::Stat};

    #[test]
    fn a_middle_team_already_pulled_up_is_not_removed_from_its_old_bracket_again() {
        let entrants: Vec<_> = [
            (1, 3, 0, false),
            (2, 2, 3, false),
            (3, 2, 0, true),
            (4, 1, 0, false),
            (5, 1, 0, false),
            (6, 0, 0, false),
        ]
        .into_iter()
        .map(|(id, wins, average, pulled_up)| Entrant {
            id,
            seed: 0,
            previous_byes: 0,
            average_score_rounds: average,
            has_been_pulled_up: pulled_up,
            opponents: &[],
            team: PairingTeam {
                school_id: None,
                wins,
                sides: SideHistory { gov: 0, opp: 0 },
                hit_pull_up: false,
            },
            ranking: Score::new(&[(Stat::Wins, f64::from(wins)), (Stat::CoinFlip, id as f64)])
                .unwrap(),
        })
        .collect();
        let plan = plan_prelim(
            &entrants,
            &Policy {
                current_round: 4,
                total_rounds: 5,
                minimum_byes: 0,
                fair_bye: 1,
                random_second_round: false,
                weights: PairingWeights {
                    power_pairing_multiple: -1,
                    high_opp_penalty: 0,
                    higher_opp_penalty: -10,
                    high_gov_penalty: -100,
                    same_school_penalty: -1000,
                    hit_pull_up_before: -10000,
                    hit_team_before: -100000,
                },
            },
        )
        .unwrap();
        assert_eq!(plan.debates.len(), 3);
        assert!(plan.byes.is_empty());
        assert_eq!(plan.debates[0].pulled_up, Some(2));
        let paired: HashSet<_> = plan
            .debates
            .iter()
            .flat_map(|row| [row.government, row.opposition])
            .collect();
        assert_eq!(paired.len(), entrants.len());
    }
}
