/// APDA tournament points, including the zero-team case used by MIT-Tab.
pub fn tournament_value(team_count: usize) -> i32 {
    match team_count {
        0 => 0,
        1..=15 => 8,
        16..=79 => 10 + (team_count / 8) as i32,
        _ => 20,
    }
}

pub struct AwardPoints {
    pub toty: Vec<(&'static str, f64)>,
    pub soty: Vec<(i32, f64)>,
}

pub fn award_points(team_count: usize) -> AwardPoints {
    let value = f64::from(tournament_value(team_count));
    let toty = [
        ("Winner", value),
        ("Finals", value - 4.0),
        ("Semis", (value - 8.0) * 0.75),
        ("Quarters", (value - 12.0) * 0.5),
        ("Octos", (value - 18.0) * 0.75),
    ]
    .into_iter()
    .filter(|(_, points)| *points > 0.0)
    .collect();
    let soty = (0..8)
        .map(|index| (index + 1, value - f64::from(index) * 2.5))
        .filter(|(_, points)| *points > 0.0)
        .collect();
    AwardPoints { toty, soty }
}

/// Returns the expected sizes of the win/loss brackets after each round.
/// Columns are ordered chronologically. Each column starts with the 0-loss bracket.
pub fn expected_records(teams: usize, rounds: usize) -> Vec<Vec<usize>> {
    let mut brackets = vec![teams];
    let mut records = Vec::with_capacity(rounds);
    for round in 1..=rounds {
        let mut next = vec![0; round + 1];
        let mut give_extra_win = true;
        for (losses, count) in brackets.into_iter().enumerate() {
            let mut wins = count / 2;
            if count % 2 == 1 {
                wins += usize::from(give_extra_win);
                give_extra_win = !give_extra_win;
            }
            next[losses] += wins;
            next[losses + 1] += count - wins;
        }
        records.push(next.clone());
        brackets = next;
    }
    records
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_teams_are_lost_or_created_when_rounding_brackets() {
        for teams in 0..300 {
            for (round, brackets) in expected_records(teams, 12).iter().enumerate() {
                assert_eq!(brackets.len(), round + 2);
                assert_eq!(brackets.iter().sum::<usize>(), teams);
            }
        }
    }
}
