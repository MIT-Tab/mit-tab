use std::str::SplitWhitespace;

use mittab_tabulation::{
    ballots::{Ballot, Outcome, Round, RoundFilter, ScoringPolicy, TeamAttendance},
    standings::{speaker_standings, team_standings, Speaker, Team},
};

use super::{joined, read, STATS};

fn read_optional(fields: &mut SplitWhitespace<'_>) -> Option<i32> {
    let field = fields.next().unwrap();
    (field != "-").then(|| field.parse().unwrap())
}

fn read_outcome(fields: &mut SplitWhitespace<'_>) -> Outcome {
    match read::<i32>(fields) {
        0 => Outcome::Pending,
        1 => Outcome::Government,
        2 => Outcome::Opposition,
        3 => Outcome::GovernmentByForfeit,
        4 => Outcome::OppositionByForfeit,
        5 => Outcome::BothLose,
        6 => Outcome::BothWin,
        result => panic!("unexpected debate outcome: {result}"),
    }
}

pub fn run(fields: &mut SplitWhitespace<'_>) -> String {
    let mode = read::<u8>(fields);
    let policy = ScoringPolicy {
        current_round: read(fields),
        lenient_late_through: read(fields),
    };
    let filter = RoundFilter {
        exclude: read_optional(fields),
        through: read_optional(fields),
    };
    let count = read::<usize>(fields);
    let rounds: Vec<_> = (0..count)
        .map(|_| Round {
            id: read(fields),
            number: read(fields),
            government: read(fields),
            opposition: read(fields),
            outcome: read_outcome(fields),
        })
        .collect();
    let count = read::<usize>(fields);
    let teams: Vec<_> = (0..count)
        .map(|_| {
            let id: i64 = read(fields);
            let coin_flip = read_optional(fields);
            let byes: Vec<i32> = (0..read::<usize>(fields)).map(|_| read(fields)).collect();
            let no_shows: Vec<i32> = (0..read::<usize>(fields)).map(|_| read(fields)).collect();
            let speakers: Vec<usize> = (0..read::<usize>(fields)).map(|_| read(fields)).collect();
            (id, coin_flip, byes, no_shows, speakers)
        })
        .collect();
    let attendance: Vec<_> = teams
        .iter()
        .map(|(id, _, byes, no_shows, _)| TeamAttendance {
            id: *id,
            byes,
            no_shows,
        })
        .collect();

    let count = read::<usize>(fields);
    let speakers: Vec<_> = (0..count)
        .map(|_| {
            let id: i64 = read(fields);
            let coin_flip = read_optional(fields);
            let team_index = read_optional(fields);
            let ballots: Vec<_> = (0..read::<usize>(fields))
                .map(|_| Ballot {
                    round: &rounds[read::<usize>(fields)],
                    speaks: read(fields),
                    ranks: read(fields),
                })
                .collect();
            (id, coin_flip, team_index, ballots)
        })
        .collect();
    let speakers: Vec<_> = speakers
        .iter()
        .map(|(id, coin_flip, team, ballots)| Speaker {
            id: *id,
            coin_flip: *coin_flip,
            team: team.map(|index| &attendance[index as usize]),
            ballots,
        })
        .collect();
    let standings = if mode == 0 {
        let team_speakers: Vec<Vec<_>> = teams
            .iter()
            .map(|(_, _, _, _, indexes)| indexes.iter().map(|&index| &speakers[index]).collect())
            .collect();
        let teams: Vec<_> = teams
            .iter()
            .enumerate()
            .map(|(index, (_, coin_flip, _, _, _))| Team {
                coin_flip: *coin_flip,
                attendance: &attendance[index],
                speakers: &team_speakers[index],
            })
            .collect();
        team_standings(&teams, &rounds, policy, filter)
    } else {
        speaker_standings(&speakers, policy, mode == 2)
    }
    .unwrap();

    joined(standings.iter().map(|row| {
        let tiebreak = row
            .tiebreaker_with_next
            .map_or_else(|| "-".to_owned(), |stat| (stat as usize).to_string());
        let stats: Vec<_> = STATS
            .iter()
            .map(|&stat| format!("{:016x}", row.score.get(stat).to_bits()))
            .collect();
        format!("{}:{}:{}", row.id, tiebreak, stats.join(":"))
    }))
}
