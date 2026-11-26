#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Totals {
    pub raw: f64,
    pub single_adjusted: f64,
    pub double_adjusted: f64,
}

/// Summarizes speaks or ranks after the caller has resolved byes, forfeits, and
/// ironman scoring. Pass one slice per debater, in tournament order.
pub fn totals(scores_by_debater: &[&[f64]]) -> Option<Totals> {
    let mut pooled_scores: Vec<_> = scores_by_debater
        .iter()
        .flat_map(|scores| scores.iter().copied())
        .collect();
    if pooled_scores.iter().any(|score| !score.is_finite()) {
        return None;
    }

    // Django sums each debater's ballot scores before adding the team totals.
    // Flattening that sum could change the standings for fractional scores.
    let raw = sum(scores_by_debater
        .iter()
        .map(|scores| sum(scores.iter().copied())));
    pooled_scores.sort_by(|first, second| first.partial_cmp(second).expect("finite scores"));
    let adjusted = |drop| {
        let scores = pooled_scores
            .get(drop..pooled_scores.len().saturating_sub(drop))
            .unwrap_or(&[]);
        sum(scores.iter().copied())
    };
    Some(Totals {
        raw,
        single_adjusted: adjusted(1),
        double_adjusted: adjusted(2),
    })
}

fn sum(values: impl Iterator<Item = f64>) -> f64 {
    values.fold(0.0, |sum, value| sum + value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn score_adjustments_remove_both_ends_of_the_pooled_scores() {
        assert_eq!(
            totals(&[&[32.0, 25.0, 32.0], &[19.0, 23.0]]),
            Some(Totals {
                raw: 131.0,
                single_adjusted: 80.0,
                double_adjusted: 25.0,
            }),
        );
    }

    #[test]
    fn rejects_non_finite_scores() {
        assert_eq!(totals(&[&[1.0], &[f64::NAN]]), None);
    }
}
