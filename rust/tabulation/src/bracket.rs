/// Returns the opening matchups of a complete elimination bracket in display order.
/// Seeds start at one.
pub fn opening_matchups(team_count: usize) -> Option<Vec<(usize, usize)>> {
    if team_count < 2 || !team_count.is_power_of_two() {
        return None;
    }

    let mut matchups = Vec::new();
    matchups.try_reserve_exact(team_count / 2).ok()?;
    place_seed(1, 2, team_count, &mut matchups);
    Some(matchups)
}

fn place_seed(
    seed: usize,
    round_size: usize,
    bracket_size: usize,
    matchups: &mut Vec<(usize, usize)>,
) {
    let opposite = round_size + 1 - seed;
    if round_size == bracket_size {
        matchups.push((seed, opposite));
    } else {
        // The two-seed's subtree is ordered differently in the existing bracket.
        let (left, right) = if seed == 2 {
            (seed, opposite)
        } else {
            (opposite, seed)
        };
        place_seed(left, round_size * 2, bracket_size, matchups);
        place_seed(right, round_size * 2, bracket_size, matchups);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_invalid_bracket_sizes() {
        for size in [0, 1, 3, 6, 12, 127, usize::MAX] {
            assert_eq!(opening_matchups(size), None);
        }
        assert_eq!(opening_matchups(usize::MAX / 2 + 1), None);
    }

    #[test]
    fn every_seed_appears_once_and_matches_its_complement() {
        for exponent in 1..=14 {
            let size = 1 << exponent;
            let matchups = opening_matchups(size).unwrap();
            assert_eq!(matchups.len(), size / 2);
            let mut seeds = vec![false; size + 1];
            for (left, right) in matchups {
                assert_eq!(left + right, size + 1);
                assert!(!seeds[left] && !seeds[right]);
                seeds[left] = true;
                seeds[right] = true;
            }
            assert!(seeds[1..].iter().all(|placed| *placed));
        }
    }
}
