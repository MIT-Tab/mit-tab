use std::collections::{HashMap, HashSet};

use crate::{
    legacy_random::LegacyRandom,
    matching::{maximum_matching, Edge, MatchError},
};

#[derive(Clone, Copy, Eq, PartialEq)]
pub enum RankingMode {
    Shared,
    Classic,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub enum WingMode {
    HelpChairs,
    Learning,
    Random,
}

pub struct InroundPolicy {
    pub ranking: RankingMode,
    pub assign_wings: bool,
    pub wings: WingMode,
    pub prioritize_bubbles: bool,
    pub allow_rejudges: bool,
    pub rejudge_penalty: i32,
    pub repeat_wing_penalty: i32,
}

#[derive(Default)]
pub struct Judge {
    pub id: i32,
    pub rank: i32,
    pub wing_only: bool,
    pub request_wing: bool,
    pub no_wings: bool,
    pub conflicts: HashSet<i32>,
    pub rejudges: HashMap<i32, i32>,
    pub chaired_by: HashMap<i32, i32>,
}

pub struct Debate {
    pub id: i32,
    pub teams: [i32; 2],
    pub strength: [f64; 3],
    pub bubble: bool,
}

#[derive(Debug, Eq, PartialEq)]
pub struct Panel {
    pub debate: i32,
    pub chair: i32,
    pub wings: Vec<i32>,
}

#[derive(Debug, Eq, PartialEq)]
pub enum JudgeError {
    IncompleteChairs,
    Matching(MatchError),
}

struct WingWeight {
    mode: WingMode,
    num_rounds: usize,
    chair_index: usize,
    relative_rank: usize,
    judge_index: usize,
}

fn weight(
    judge_score: i32,
    pairing: usize,
    ranking: RankingMode,
    wing: Option<WingWeight>,
    rng: &mut LegacyRandom,
) -> f64 {
    let pairing = pairing as i32;
    let mut effective = judge_score;
    let mut bonus = 0;
    if let Some(wing) = wing {
        effective = match wing.mode {
            WingMode::Learning => wing.relative_rank as i32,
            WingMode::HelpChairs => {
                bonus = wing.chair_index.saturating_sub(wing.judge_index) as i32 * 10;
                wing.num_rounds as i32 - wing.relative_rank as i32 - 1
            }
            WingMode::Random => {
                *rng = LegacyRandom::seeded((1000 * wing.judge_index) as u64 + pairing as u64);
                rng.below(wing.num_rounds) as i32
            }
        };
    }
    let base = match ranking {
        RankingMode::Classic => -f64::from((effective + pairing).abs()),
        RankingMode::Shared => -f64::from((effective - pairing).max(0)).powi(2),
    };
    base + f64::from(bonus)
}

fn scores(judges: &[&Judge], mode: RankingMode) -> Vec<i32> {
    let mut previous_rank = 0;
    let mut score = -1;
    judges
        .iter()
        .enumerate()
        .map(|(index, judge)| {
            if mode == RankingMode::Classic || judge.rank != previous_rank {
                score = index as i32;
                previous_rank = judge.rank;
            }
            score
        })
        .collect()
}

fn rejudges(judge: &Judge, debate: &Debate) -> i32 {
    debate
        .teams
        .iter()
        .map(|team| judge.rejudges.get(team).copied().unwrap_or(0))
        .sum()
}

fn eligible(judge: &Judge, debate: &Debate, allow_rejudges: bool) -> bool {
    (allow_rejudges || rejudges(judge, debate) == 0)
        && debate
            .teams
            .iter()
            .all(|team| !judge.conflicts.contains(team))
}

fn rejudge_weight(judge: &Judge, debate: &Debate, score: i32, policy: &InroundPolicy) -> f64 {
    if policy.allow_rejudges && !judge.wing_only && policy.rejudge_penalty > 0 {
        let rate = f64::from(policy.rejudge_penalty) * (1.0 + 0.1 * f64::from(score));
        rate * f64::from(rejudges(judge, debate))
    } else {
        0.0
    }
}

fn compare_strength(a: &Debate, b: &Debate) -> std::cmp::Ordering {
    a.strength
        .iter()
        .zip(b.strength)
        .find_map(|(a, b)| {
            let order = b.total_cmp(a);
            order.is_ne().then_some(order)
        })
        .unwrap_or(std::cmp::Ordering::Equal)
}

pub fn assign_inround(
    debates: &[Debate],
    judges: &[Judge],
    policy: &InroundPolicy,
) -> Result<Vec<Panel>, JudgeError> {
    let count = debates.len();
    if count == 0 {
        return Ok(Vec::new());
    }
    let mut rng = LegacyRandom::seeded(1337);
    let mut judges: Vec<_> = judges.iter().collect();
    rng.shuffle(&mut judges);
    judges.sort_by_key(|judge| std::cmp::Reverse(judge.rank));
    let chairs: Vec<_> = judges
        .iter()
        .copied()
        .filter(|judge| !judge.wing_only)
        .collect();

    let mut debates: Vec<_> = debates.iter().collect();
    debates.sort_by(|a, b| compare_strength(a, b));
    rng = LegacyRandom::seeded(1337);
    rng.shuffle(&mut debates);
    debates.sort_by(|a, b| {
        let bubble = if policy.prioritize_bubbles {
            b.bubble.cmp(&a.bubble)
        } else {
            std::cmp::Ordering::Equal
        };
        bubble.then_with(|| compare_strength(a, b))
    });

    let chair_scores = scores(&chairs, policy.ranking);
    let mut edges = Vec::new();
    for (index, &judge) in chairs.iter().enumerate() {
        let score = chair_scores[index];
        for (position, debate) in debates.iter().enumerate() {
            if eligible(judge, debate, policy.allow_rejudges) {
                let weight = weight(score, position, policy.ranking, None, &mut rng)
                    - rejudge_weight(judge, debate, score, policy);
                edges.push(Edge {
                    first: position,
                    second: count + index,
                    weight,
                });
            }
        }
    }
    let mates =
        maximum_matching(count + chairs.len(), &edges, true).map_err(JudgeError::Matching)?;
    let chair_indices: Vec<_> = mates[..count]
        .iter()
        .map(|chair| {
            chair
                .map(|chair| chair - count)
                .ok_or(JudgeError::IncompleteChairs)
        })
        .collect::<Result<_, _>>()?;
    let mut panels: Vec<_> = chair_indices
        .iter()
        .zip(&debates)
        .map(|(&chair, debate)| Panel {
            debate: debate.id,
            chair: chairs[chair].id,
            wings: Vec::new(),
        })
        .collect();

    if !policy.assign_wings || judges.len() <= count {
        return Ok(panels);
    }
    let mut assigned: HashSet<_> = panels.iter().map(|panel| panel.chair).collect();
    let max_per_room = (judges.len() / count + 1).min(3);
    for _ in 1..max_per_room {
        let wings: Vec<_> = judges
            .iter()
            .copied()
            .filter(|judge| !assigned.contains(&judge.id))
            .collect();
        let wing_scores = scores(&wings, policy.ranking);
        let mut wing_pool: Vec<_> = (0..wings.len()).collect();
        let mut pairing_indices: Vec<_> = (0..count).collect();
        if policy.wings == WingMode::Random {
            rng.shuffle(&mut wing_pool);
            rng.shuffle(&mut pairing_indices);
        }
        edges.clear();
        for (relative_rank, &index) in wing_pool.iter().enumerate() {
            let judge = wings[index];
            let score = wing_scores[index];
            for &position in &pairing_indices {
                let debate = debates[position];
                if !eligible(judge, debate, policy.allow_rejudges) {
                    continue;
                }
                let chair = chairs[chair_indices[position]];
                let preference = 100 * (i32::from(chair.request_wing) - i32::from(chair.no_wings));
                let repeats = judge.chaired_by.get(&chair.id).copied().unwrap_or(0);
                let repeat_penalty = policy.repeat_wing_penalty.max(0) * repeats;
                let wing = WingWeight {
                    mode: policy.wings,
                    num_rounds: count,
                    chair_index: chair_indices[position],
                    relative_rank,
                    judge_index: index,
                };
                let weight = weight(score, position, policy.ranking, Some(wing), &mut rng)
                    + f64::from(preference)
                    - rejudge_weight(judge, debate, score, policy)
                    - f64::from(repeat_penalty);
                edges.push(Edge {
                    first: position,
                    second: count + index,
                    weight,
                });
            }
        }
        if edges.is_empty() {
            break;
        }
        let mates =
            maximum_matching(count + wings.len(), &edges, true).map_err(JudgeError::Matching)?;
        for (position, wing) in mates[..count].iter().enumerate() {
            if let Some(wing) = wing {
                let id = wings[*wing - count].id;
                panels[position].wings.push(id);
                assigned.insert(id);
            }
        }
    }
    Ok(panels)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chair_and_wing_follow_conflicts_and_checkin_pool() {
        let debates = [
            Debate {
                id: 1,
                teams: [11, 12],
                strength: [10.0, 0.0, 0.0],
                bubble: false,
            },
            Debate {
                id: 2,
                teams: [21, 22],
                strength: [2.0, 0.0, 0.0],
                bubble: false,
            },
        ];
        let judges = [
            Judge {
                id: 1,
                rank: 700,
                conflicts: HashSet::from([12]),
                ..Judge::default()
            },
            Judge {
                id: 2,
                rank: 400,
                request_wing: true,
                ..Judge::default()
            },
            Judge {
                id: 3,
                rank: 800,
                wing_only: true,
                ..Judge::default()
            },
        ];
        let mut policy = InroundPolicy {
            ranking: RankingMode::Shared,
            assign_wings: true,
            wings: WingMode::HelpChairs,
            prioritize_bubbles: false,
            allow_rejudges: false,
            rejudge_penalty: 100,
            repeat_wing_penalty: 10,
        };
        let panels = assign_inround(&debates, &judges, &policy).unwrap();
        assert_eq!(
            panels[0],
            Panel {
                debate: 1,
                chair: 2,
                wings: vec![3]
            }
        );
        assert_eq!(
            panels[1],
            Panel {
                debate: 2,
                chair: 1,
                wings: vec![]
            }
        );
        policy.assign_wings = false;
        assert!(assign_inround(&debates, &judges, &policy)
            .unwrap()
            .iter()
            .all(|panel| panel.wings.is_empty()));
        assert_eq!(
            assign_inround(&debates, &judges[..1], &policy),
            Err(JudgeError::IncompleteChairs)
        );
    }
}
