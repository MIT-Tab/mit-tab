use std::collections::{BTreeMap, HashMap};

use mittab_tabulation::bracket::opening_matchups;
use serde::Serialize;
use serde_json::Serializer;

use crate::{
    legacy_html::DjangoJson,
    public_outrounds::{Judge, Match},
    public_pairings::Team,
};

// Preserve the key order and spelling of the Python payload in the inline script.
#[derive(Serialize)]
struct Bracket<'a> {
    stages: [Stage; 1],
    groups: [Group; 1],
    rounds: Vec<Round>,
    participants: Vec<Participant>,
    matches: Vec<BracketMatch>,
    #[serde(rename = "matchGames")]
    match_games: [(); 0],
    match_metadata: BTreeMap<usize, Metadata<'a>>,
}

#[derive(Serialize)]
struct Stage {
    id: usize,
    tournament_id: usize,
    name: &'static str,
    #[serde(rename = "type")]
    kind: &'static str,
    number: usize,
    settings: StageSettings,
}

#[derive(Serialize)]
struct StageSettings {
    size: usize,
}

#[derive(Serialize)]
struct Group {
    id: usize,
    stage_id: usize,
    number: usize,
}

#[derive(Serialize)]
struct Round {
    id: usize,
    number: usize,
    stage_id: usize,
    group_id: usize,
}

#[derive(Serialize)]
struct Participant {
    id: usize,
    tournament_id: usize,
    name: String,
    team_name: String,
    debaters_names: String,
}

#[derive(Serialize)]
struct BracketMatch {
    id: usize,
    number: usize,
    stage_id: usize,
    group_id: usize,
    round_id: usize,
    child_count: usize,
    status: i32,
    opponent1: Option<Opponent>,
    opponent2: Option<Opponent>,
}

#[derive(Serialize)]
struct Opponent {
    id: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<&'static str>,
}

#[derive(Serialize)]
struct Metadata<'a> {
    id: i32,
    room: &'a str,
    judges: &'a [Judge],
    gov_team: Appearance,
    opp_team: Appearance,
}

#[derive(Serialize)]
struct Appearance {
    display: String,
    debaters: String,
    seed: Option<usize>,
    name: String,
    debaters_plain: String,
}

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

pub fn data(matches: &[Match<'_>], seeded: &BTreeMap<i32, &Team>) -> Option<String> {
    let size = (matches
        .iter()
        .filter(|row| row.size >= 2)
        .map(|row| row.size)
        .max()? as usize)
        .checked_next_power_of_two()?;
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
                participants.push(Participant {
                    id,
                    tournament_id: 1,
                    name: name.clone(),
                    team_name: name,
                    debaters_names: debaters,
                });
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

    let round_rows = (1..=rounds.len())
        .map(|number| Round {
            id: number,
            number,
            stage_id: 1,
            group_id: 1,
        })
        .collect();
    let mut match_rows = Vec::new();
    let mut metadata = BTreeMap::new();
    for (stage, nodes) in rounds.iter().enumerate() {
        for (position, node) in nodes.iter().enumerate() {
            let id = match_rows.len() + 1;
            let opponent = |index: usize| {
                let slot = &node.slots[index];
                if slot.bye {
                    return None;
                }
                let participant = slot
                    .team
                    .and_then(|team| participant_id.get(&team).copied());
                if participant.is_none() && stage == 0 {
                    return None;
                }
                Some(Opponent {
                    id: participant,
                    result: participant.and_then(|_| {
                        node.winner
                            .map(|winner| if winner == index { "win" } else { "loss" })
                    }),
                })
            };
            match_rows.push(BracketMatch {
                id,
                number: position + 1,
                stage_id: 1,
                group_id: 1,
                round_id: stage + 1,
                child_count: 0,
                status: node.status(),
                opponent1: opponent(0),
                opponent2: opponent(1),
            });
            if let Some(row) = node.matchup {
                let appearance = |slot: &Slot| match slot.team.and_then(|team| by_team.get(&team)) {
                    Some((seed, team)) => Appearance {
                        display: format!("[{seed}] {}", team.name),
                        debaters: format!("[{seed}] {}", team.debaters),
                        seed: Some(*seed as usize),
                        name: team.name.clone(),
                        debaters_plain: team.debaters.clone(),
                    },
                    None => {
                        let mut seeds = slot.seeds.clone();
                        seeds.sort_unstable();
                        let label = seeds
                            .iter()
                            .map(|seed| format!("Seed {seed}"))
                            .collect::<Vec<_>>()
                            .join(" / ");
                        Appearance {
                            display: label.clone(),
                            debaters: String::new(),
                            seed: seeds.first().copied(),
                            name: label,
                            debaters_plain: String::new(),
                        }
                    }
                };
                metadata.insert(
                    id,
                    Metadata {
                        id: row.id,
                        room: row.room.as_deref().unwrap_or("TBD"),
                        judges: &row.judges,
                        gov_team: appearance(&node.slots[0]),
                        opp_team: appearance(&node.slots[1]),
                    },
                );
            }
        }
    }
    let bracket = Bracket {
        stages: [Stage {
            id: 1,
            tournament_id: 1,
            name: "Elimination Bracket",
            kind: "single_elimination",
            number: 1,
            settings: StageSettings { size },
        }],
        groups: [Group {
            id: 1,
            stage_id: 1,
            number: 1,
        }],
        rounds: round_rows,
        participants,
        matches: match_rows,
        match_games: [],
        match_metadata: metadata,
    };
    let mut payload = Vec::new();
    bracket
        .serialize(&mut Serializer::with_formatter(
            &mut payload,
            DjangoJson { ascii_only: true },
        ))
        .ok()?;
    String::from_utf8(payload).ok()
}
