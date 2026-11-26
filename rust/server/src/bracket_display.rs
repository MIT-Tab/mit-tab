use std::collections::{BTreeMap, HashMap};

use mittab_tabulation::bracket::opening_matchups;
use serde_json::{Value, json};

use crate::{public_outrounds::Match, public_pairings::Team};

struct Slot {
    seeds: Vec<usize>,
    team: Option<i32>,
    bye: bool,
}

impl Slot {
    fn new(seeds: Vec<usize>) -> Self {
        Self {
            seeds,
            team: None,
            bye: false,
        }
    }
}

struct Node<'a> {
    slots: [Slot; 2],
    winner: Option<usize>,
    matchup: Option<&'a Match<'a>>,
}

impl Node<'_> {
    fn new(first: Slot, second: Slot) -> Self {
        Self {
            slots: [first, second],
            winner: None,
            matchup: None,
        }
    }

    fn seeds(&self) -> Vec<usize> {
        self.slots
            .iter()
            .flat_map(|slot| slot.seeds.iter().copied())
            .collect()
    }

    fn status(&self) -> i32 {
        let [first, second] = &self.slots;
        if first.bye || second.bye || (first.team.is_none() && second.team.is_none()) {
            0
        } else if first.team.is_none() || second.team.is_none() {
            1
        } else if self.winner.is_some() {
            4
        } else {
            2
        }
    }
}

pub fn data(matches: &[Match<'_>], seeded: &BTreeMap<i32, &Team>) -> Option<Value> {
    let size =
        (matches.iter().map(|row| row.size).max()?.max(2) as usize).checked_next_power_of_two()?;
    if size > 8192 {
        return None;
    }
    let first: Vec<_> = opening_matchups(size)?
        .into_iter()
        .map(|(a, b)| Node::new(Slot::new(vec![a]), Slot::new(vec![b])))
        .collect();
    let mut rounds = vec![first];
    while let Some(previous) = rounds.last().filter(|round| round.len() > 1) {
        let round = previous
            .as_chunks::<2>()
            .0
            .iter()
            .map(|[left, right]| Node::new(Slot::new(left.seeds()), Slot::new(right.seeds())))
            .collect();
        rounds.push(round);
    }

    let by_team: HashMap<_, _> = seeded
        .range(1..=size as i32)
        .map(|(&seed, team)| (team.id, (seed, *team)))
        .collect();
    let mut participants = Vec::new();
    let mut participant_id = HashMap::new();
    for node in &mut rounds[0] {
        for slot in &mut node.slots {
            if let Some(team) = seeded.get(&(slot.seeds[0] as i32)) {
                let seed = slot.seeds[0];
                let id = participants.len() + 1;
                let name = format!("[{seed}] {}", team.name);
                let debaters = format!("[{seed}] {}", team.debaters);
                participants.push(json!({"id": id, "tournament_id": 1, "name": name,
                    "team_name": name, "debaters_names": debaters}));
                participant_id.insert(team.id, id);
                slot.team = Some(team.id);
            } else {
                slot.bye = true;
            }
        }
    }

    for row in matches {
        if row.size < 2 {
            continue;
        }
        let Some(index) = rounds.len().checked_sub((row.size as u32).ilog2() as usize) else {
            continue;
        };
        let Some(stage) = rounds.get_mut(index) else {
            continue;
        };
        let (Some(&(a, _)), Some(&(b, _))) = (by_team.get(&row.gov.id), by_team.get(&row.opp.id))
        else {
            continue;
        };
        for node in stage {
            let [first, second] = &node.slots;
            let ab = first.seeds.contains(&(a as usize)) && second.seeds.contains(&(b as usize));
            let ba = first.seeds.contains(&(b as usize)) && second.seeds.contains(&(a as usize));
            if !ab && !ba {
                continue;
            }
            let teams = if ab {
                [row.gov.id, row.opp.id]
            } else {
                [row.opp.id, row.gov.id]
            };
            for (slot, team) in node.slots.iter_mut().zip(teams) {
                slot.team = Some(team);
                slot.bye = false;
            }
            node.winner = match row.victor {
                1 | 3 => Some(usize::from(!ab)),
                2 | 4 => Some(usize::from(ab)),
                _ => None,
            };
            node.matchup = Some(row);
            break;
        }
    }

    for stage in 0..rounds.len() {
        for index in 0..rounds[stage].len() {
            let node = &mut rounds[stage][index];
            if node.winner.is_none() {
                let [a, b] = &node.slots;
                node.winner = if a.team.is_some() && b.bye {
                    Some(0)
                } else if b.team.is_some() && a.bye {
                    Some(1)
                } else {
                    None
                };
            }
            if let Some(team) = node.winner.and_then(|winner| node.slots[winner].team)
                && let Some(parent) = rounds.get_mut(stage + 1)
            {
                let slot = &mut parent[index / 2].slots[index % 2];
                slot.team = Some(team);
                slot.bye = false;
            }
        }
    }

    let round_rows: Vec<_> = (1..=rounds.len())
        .map(|number| {
            json!({
                "id": number, "number": number, "stage_id": 1, "group_id": 1,
            })
        })
        .collect();
    let mut match_rows = Vec::new();
    let mut metadata = BTreeMap::new();
    for (stage, nodes) in rounds.iter().enumerate() {
        for (position, node) in nodes.iter().enumerate() {
            let id = match_rows.len() + 1;
            let opponents: Vec<_> = node.slots.iter().enumerate().map(|(index, slot)| {
                if slot.bye { return Value::Null; }
                let Some(participant) = slot.team.and_then(|id| participant_id.get(&id)) else {
                    return if stage == 0 { Value::Null } else { json!({"id": null}) };
                };
                if let Some(winner) = node.winner {
                    json!({"id": participant, "result": if winner == index { "win" } else { "loss" }})
                } else { json!({"id": participant}) }
            }).collect();
            match_rows.push(json!({
                "id": id, "number": position + 1, "stage_id": 1, "group_id": 1, "round_id": stage + 1,
                "child_count": 0, "status": node.status(), "opponent1": opponents[0], "opponent2": opponents[1],
            }));
            if let Some(row) = node.matchup {
                let appearance = |slot: &Slot| match slot.team.and_then(|team| by_team.get(&team)) {
                    Some((seed, team)) => json!({"display": format!("[{seed}] {}", team.name),
                        "debaters": format!("[{seed}] {}", team.debaters), "seed": seed,
                        "name": team.name, "debaters_plain": team.debaters}),
                    None => {
                        let mut seeds = slot.seeds.clone();
                        seeds.sort_unstable();
                        let label = seeds
                            .iter()
                            .map(|seed| format!("Seed {seed}"))
                            .collect::<Vec<_>>()
                            .join(" / ");
                        json!({"display": label, "debaters": "", "seed": seeds.first(),
                               "name": label, "debaters_plain": ""})
                    }
                };
                metadata.insert(id, json!({"id": row.id, "room": row.room.as_deref().unwrap_or("TBD"),
                    "judges": row.judges, "gov_team": appearance(&node.slots[0]), "opp_team": appearance(&node.slots[1])}));
            }
        }
    }
    Some(json!({
        "stages": [{"id": 1, "tournament_id": 1, "name": "Elimination Bracket",
                    "type": "single_elimination", "number": 1, "settings": {"size": size}}],
        "groups": [{"id": 1, "stage_id": 1, "number": 1}],
        "rounds": round_rows, "participants": participants,
        "matches": match_rows, "matchGames": [], "match_metadata": metadata,
    }))
}
