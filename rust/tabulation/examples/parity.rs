#[path = "parity/matching.rs"]
mod matching;
#[path = "parity/tournament.rs"]
mod tournament;

// Reads generated parity cases on stdin. This is a test driver for the Rust library.
use std::{
    fmt::Display,
    io::{self, BufRead, BufWriter, Write},
    str::{FromStr, SplitWhitespace},
};

use mittab_tabulation::{
    bracket::opening_matchups,
    pairing::{
        first_round_bye_candidates, government, pairing_weight, required_bye_count, ByeCountError,
        Government, PairingTeam, PairingWeights, PowerPairingDistance, SideHistory,
    },
    rankings::{Ranking, Score, Stat},
    scoring::totals,
};

fn read<T: FromStr>(fields: &mut SplitWhitespace<'_>) -> T {
    fields.next().unwrap().parse().ok().unwrap()
}

fn side_history(fields: &mut SplitWhitespace<'_>) -> SideHistory {
    SideHistory {
        gov: read(fields),
        opp: read(fields),
    }
}

fn team(fields: &mut SplitWhitespace<'_>) -> PairingTeam {
    let sides = side_history(fields);
    let school = fields.next().unwrap();
    PairingTeam {
        school_id: (school != "-").then(|| school.parse().unwrap()),
        wins: read(fields),
        sides,
        hit_pull_up: read::<u8>(fields) != 0,
    }
}

fn weight(fields: &mut SplitWhitespace<'_>) -> f64 {
    let first_round = read::<u8>(fields) == 1;
    let total_rounds = read(fields);
    let rematch = read::<u8>(fields) != 0;
    let first = team(fields);
    let second = team(fields);
    let distance = if first_round {
        PowerPairingDistance::FirstRound {
            first_seed: read(fields),
            second_seed: read(fields),
            first_ideal_seed: read(fields),
            second_ideal_seed: read(fields),
        }
    } else {
        PowerPairingDistance::LaterRound {
            first_position: read(fields),
            second_position: read(fields),
            first_ideal_position: read(fields),
            second_ideal_position: read(fields),
        }
    };
    let weights = PairingWeights {
        power_pairing_multiple: read(fields),
        high_opp_penalty: read(fields),
        higher_opp_penalty: read(fields),
        high_gov_penalty: read(fields),
        same_school_penalty: read(fields),
        hit_pull_up_before: read(fields),
        hit_team_before: read(fields),
    };
    pairing_weight(first, second, distance, total_rounds, rematch, weights)
}

const STATS: [Stat; 9] = [
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

fn standings(fields: &mut SplitWhitespace<'_>) -> String {
    let ranking = match read::<u8>(fields) {
        0 => Ranking::Team,
        1 => Ranking::Speaker {
            single_adjusted_first: false,
        },
        2 => Ranking::Speaker {
            single_adjusted_first: true,
        },
        mode => panic!("Unexpected standings mode {mode}"),
    };
    let count: usize = read(fields);
    let scores: Vec<_> = (0..count)
        .map(|_| {
            let values = STATS.map(|stat| (stat, read(fields)));
            Score::new(&values).unwrap()
        })
        .collect();
    let order = ranking.order(&scores);
    let tiebreakers = order.windows(2).map(|pair| {
        ranking
            .tiebreaker(&scores[pair[0]], &scores[pair[1]])
            .map_or_else(|| "-".to_owned(), |stat| (stat as usize).to_string())
    });
    format!("{}|{}", joined(order.iter()), joined(tiebreakers))
}

fn joined(values: impl IntoIterator<Item = impl Display>) -> String {
    values
        .into_iter()
        .map(|value| value.to_string())
        .collect::<Vec<_>>()
        .join(",")
}

fn result(line: &str) -> String {
    let mut fields = line.split_whitespace();
    let output = match fields.next().unwrap() {
        "bracket" => {
            let matchups = opening_matchups(read(&mut fields)).unwrap();
            joined(
                matchups
                    .iter()
                    .map(|(left, right)| format!("{left}:{right}")),
            )
        }
        "bye-count" => match required_bye_count(read(&mut fields), read(&mut fields)) {
            Ok(count) => count.to_string(),
            Err(ByeCountError::NegativeMinimum) => "negative".to_owned(),
            Err(ByeCountError::InsufficientTeams) => "insufficient".to_owned(),
        },
        "bye-candidates" => {
            let policy = read(&mut fields);
            let seeds: Vec<i32> = fields
                .by_ref()
                .map(|field| field.parse().unwrap())
                .collect();
            joined(first_round_bye_candidates(&seeds, policy))
        }
        "government" => match government(side_history(&mut fields), side_history(&mut fields)) {
            Government::FirstTeam => "0",
            Government::SecondTeam => "1",
            Government::CoinFlip => "coin",
        }
        .to_owned(),
        "weight" => weight(&mut fields).to_string(),
        "standings" => standings(&mut fields),
        "tournament" => tournament::run(&mut fields),
        "matching" => matching::run(&mut fields),
        "scores" => {
            let count = read::<usize>(&mut fields);
            let scores: Vec<Vec<f64>> = (0..count)
                .map(|_| {
                    (0..read::<usize>(&mut fields))
                        .map(|_| read(&mut fields))
                        .collect()
                })
                .collect();
            let rows: Vec<_> = scores.iter().map(Vec::as_slice).collect();
            let totals = totals(&rows).unwrap();
            joined([totals.raw, totals.single_adjusted, totals.double_adjusted])
        }
        operation => panic!("Unexpected operation {operation}"),
    };
    assert!(fields.next().is_none(), "Unexpected trailing fields");
    output
}

fn main() -> io::Result<()> {
    let mut output = BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        writeln!(output, "{}", result(&line?))?;
    }
    output.flush()
}
