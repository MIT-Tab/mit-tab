use std::str::SplitWhitespace;

use mittab_tabulation::{
    legacy_random::LegacyRandom,
    pairing::PairingWeights,
    prelim::{plan_prelim, Entrant, Policy},
    rankings::{Score, Stat},
};

use super::{joined, read, team};

pub fn random(fields: &mut SplitWhitespace<'_>) -> String {
    let seed = read(fields);
    let mut rng = LegacyRandom::seeded(seed);
    joined(
        fields
            .by_ref()
            .map(|bound| rng.below(bound.parse().unwrap())),
    )
}

pub fn run(fields: &mut SplitWhitespace<'_>) -> String {
    let current_round = read(fields);
    let total_rounds = read(fields);
    let minimum_byes = read(fields);
    let fair_bye = read(fields);
    let random_second_round = read::<u8>(fields) != 0;
    let weights = PairingWeights {
        power_pairing_multiple: -1,
        high_opp_penalty: 0,
        higher_opp_penalty: -10,
        high_gov_penalty: -100,
        same_school_penalty: -1000,
        hit_pull_up_before: -10000,
        hit_team_before: -100000,
    };
    let policy = Policy {
        current_round,
        total_rounds,
        minimum_byes,
        fair_bye,
        random_second_round,
        weights,
    };
    let count = read::<usize>(fields);
    let mut owned = Vec::with_capacity(count);
    for _ in 0..count {
        let id = read(fields);
        let seed = read(fields);
        let pairing_team = team(fields);
        let previous_byes = read(fields);
        let average = read(fields);
        let pulled_up = read::<u8>(fields) != 0;
        let score = Score::new(&[
            (Stat::Wins, f64::from(pairing_team.wins)),
            (Stat::Speaks, read(fields)),
            (Stat::CoinFlip, read(fields)),
        ])
        .unwrap();
        let opponents: Vec<i64> = (0..read::<usize>(fields)).map(|_| read(fields)).collect();
        owned.push((
            id,
            seed,
            pairing_team,
            previous_byes,
            average,
            pulled_up,
            score,
            opponents,
        ));
    }
    let entrants: Vec<_> = owned
        .iter()
        .map(
            |&(
                id,
                seed,
                team,
                previous_byes,
                average_score_rounds,
                has_been_pulled_up,
                ranking,
                ref opponents,
            )| Entrant {
                id,
                seed,
                team,
                ranking,
                previous_byes,
                average_score_rounds,
                has_been_pulled_up,
                opponents,
            },
        )
        .collect();
    match plan_prelim(&entrants, &policy) {
        Err(_) => "error".into(),
        Ok(plan) => format!(
            "{}|{}",
            joined(plan.byes),
            joined(plan.debates.iter().map(|debate| {
                let pulled_up = debate
                    .pulled_up
                    .map_or_else(|| "-".into(), |team| team.to_string());
                format!("{}:{}:{pulled_up}", debate.government, debate.opposition)
            }))
        ),
    }
}
