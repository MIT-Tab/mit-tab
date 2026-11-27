use std::collections::{BTreeMap, BTreeSet};

use crate::spreadsheets::{MAX_CELLS, Rows, normalized_header};

const SCRATCH_COLUMNS: &str = "Use one row per scratch with columns \"Team name\", \"Judge name\", and \"Scratch type\" (team or tab).";

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Roster {
    Teams,
    Judges,
    Rooms,
    Scratches,
}

impl Roster {
    pub const ALL: [Self; 4] = [Self::Teams, Self::Judges, Self::Rooms, Self::Scratches];

    pub fn parse(kind: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|roster| roster.key() == kind)
    }

    pub fn key(self) -> &'static str {
        match self {
            Self::Teams => "teams",
            Self::Judges => "judges",
            Self::Rooms => "rooms",
            Self::Scratches => "scratches",
        }
    }

    pub fn upload(self) -> &'static str {
        match self {
            Self::Teams => "team_file",
            Self::Judges => "judge_file",
            Self::Rooms => "room_file",
            Self::Scratches => "scratch_file",
        }
    }

    pub fn headers(self) -> &'static [&'static str] {
        match self {
            Self::Teams => &[
                "Team name",
                "School",
                "Hybrid school",
                "Seed",
                "Debater 1 name",
                "Debater 1 status",
                "Debater 1 email",
                "Debater 1 APDA ID",
                "Debater 2 name",
                "Debater 2 status",
                "Debater 2 email",
                "Debater 2 APDA ID",
            ],
            Self::Judges => &["Judge name", "Judge email", "Calibration"],
            Self::Rooms => &["Room name", "Rank"],
            Self::Scratches => &["Team name", "Judge name", "Scratch type"],
        }
    }

    fn required(self) -> &'static [usize] {
        match self {
            Self::Teams => &[0, 1, 4, 8],
            Self::Judges => &[0, 2],
            Self::Rooms => &[0, 1],
            Self::Scratches => &[0, 1, 2],
        }
    }

    fn aliases(self) -> BTreeMap<String, usize> {
        let mut aliases = BTreeMap::new();
        for (index, &label) in self.headers().iter().enumerate() {
            let alternate = match label {
                "Team name" => &["team", "name"][..],
                "School" => &["school name", "primary school"],
                "Hybrid school" => &["hybrid", "hybrid school name"],
                "Seed" => &["team seed", "seeding"],
                "Judge name" => &["judge", "name"],
                "Judge email" => &["email", "email address"],
                "Calibration" => &["rank", "judge rank", "judge calibration", "rating"],
                "Room name" => &["room", "name"],
                "Rank" => &["room rank", "calibration", "rating"],
                "Scratch type" => &["type", "scratch"],
                _ => &[],
            };
            aliases.extend(
                std::iter::once(label)
                    .chain(alternate.iter().copied())
                    .map(|name| (normalized_header(name), index)),
            );
        }
        if self == Self::Teams {
            for (i, offset) in [(1, 4), (2, 8)] {
                for (extra, names) in [
                    (
                        0,
                        vec![
                            format!("debater {i}"),
                            format!("speaker {i}"),
                            format!("speaker {i} name"),
                        ],
                    ),
                    (
                        1,
                        vec![
                            format!("debater {i} N?"),
                            format!("D{i} Varsity/Novice Status"),
                            format!("debater {i} novice status"),
                            format!("novice {i}"),
                            format!("debater {i} novice"),
                            format!("speaker {i} status"),
                        ],
                    ),
                    (2, vec![format!("email {i}"), format!("speaker {i} email")]),
                    (3, vec![format!("apda id {i}"), format!("debater {i} id")]),
                ] {
                    aliases.extend(
                        names
                            .iter()
                            .map(|name| (normalized_header(name), offset + extra)),
                    );
                }
            }
        }
        aliases
    }
}

pub struct Layout {
    pub rows: Rows,
    pub named_headers: bool,
}

pub fn finite(value: &str) -> Option<f64> {
    value.parse::<f64>().ok().filter(|value| value.is_finite())
}

pub fn seed(value: &str) -> Option<i32> {
    match value.to_ascii_lowercase().as_str() {
        "" | "unseeded" | "un" | "none" => Some(0),
        "free" | "free seed" => Some(1),
        "half" | "half seed" => Some(2),
        "full" | "full seed" => Some(3),
        _ => None,
    }
}

pub fn novice(value: &str) -> Option<i32> {
    match value.to_ascii_lowercase().as_str() {
        "" | "v" | "var" | "varsity" => Some(0),
        "n" | "nov" | "novice" => Some(1),
        _ => None,
    }
}

pub fn scratch(value: &str) -> Option<i32> {
    match value.to_ascii_lowercase().as_str() {
        "team" | "team scratch" => Some(0),
        "tab" | "tab scratch" => Some(1),
        _ => None,
    }
}

pub fn expected(value: &str) -> Option<bool> {
    match value.to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "y" | "expected" | "in" | "x" | "check" | "checked" => Some(true),
        "" | "0" | "false" | "no" | "n" | "out" => Some(false),
        _ => None,
    }
}

fn matches(roster: Roster, index: usize, value: &str) -> bool {
    if value.is_empty() {
        return !roster.required().contains(&index);
    }
    let label = roster.headers()[index];
    if label.contains("email") {
        return value.contains('@');
    }
    if matches!(label, "Rank" | "Calibration") || label.contains("APDA ID") {
        return finite(value).is_some();
    }
    if label == "Seed" {
        return seed(value).is_some();
    }
    if label.contains("status") {
        return novice(value).is_some();
    }
    if label == "Scratch type" {
        return scratch(value).is_some();
    }
    !value.contains('@') && finite(value).is_none()
}

fn at(row: &[String], index: usize) -> &str {
    row.get(index).map(String::as_str).unwrap_or_default()
}

fn named_round(header: &str) -> Option<usize> {
    let header = header.strip_prefix("expected").unwrap_or(header);
    let digits = header
        .strip_prefix("round")
        .or_else(|| header.strip_prefix('r'))?;
    let digits = digits.strip_suffix("expected").unwrap_or(digits);
    if digits.bytes().all(|byte| byte.is_ascii_digit()) {
        digits.parse().ok()
    } else {
        None
    }
}

fn judge_extras(
    headers: &[String],
    data: &Rows,
    mapping: &BTreeMap<usize, usize>,
    count: usize,
) -> Result<Vec<Option<usize>>, String> {
    let default = mapping.iter().all(|(target, source)| target == source);
    let remaining: Vec<_> = (0..headers.len())
        .filter(|index| !mapping.values().any(|source| source == index))
        .collect();
    let mut rounds = BTreeMap::new();
    for &index in &remaining {
        if let Some(number) = named_round(&normalized_header(&headers[index])) {
            if !(1..=count).contains(&number) {
                return Err(format!(
                    "Column \"{}\" exceeds the configured total of {count} rounds.",
                    headers[index]
                ));
            }
            if rounds.insert(number, index).is_some() {
                return Err(format!(
                    "Duplicate expected-round column for round {number}."
                ));
            }
        }
    }
    if rounds.is_empty() && default && count > 0 {
        let candidates: Vec<_> = remaining
            .iter()
            .copied()
            .filter(|&index| index >= 3)
            .rev()
            .take(count)
            .collect();
        if candidates.len() == count
            && data.iter().all(|(_, row)| {
                candidates
                    .iter()
                    .all(|&index| expected(at(row, index)).is_some())
            })
        {
            rounds.extend(
                candidates
                    .into_iter()
                    .rev()
                    .enumerate()
                    .map(|(index, source)| (index + 1, source)),
            );
        }
    }
    let schools = remaining.into_iter().filter(|&index| {
        let label = normalized_header(&headers[index]);
        !rounds.values().any(|&source| source == index)
            && ((default && index >= 3)
                || ["school", "affiliation", "affiliatedschool"]
                    .iter()
                    .any(|prefix| label.starts_with(prefix)))
    });
    Ok(schools
        .map(Some)
        .chain((1..=count).map(|round| rounds.get(&round).copied()))
        .collect())
}

pub fn configure(roster: Roster, mut rows: Rows, round_count: usize) -> Result<Layout, String> {
    let Some((_, first)) = rows.first() else {
        return Err("The spreadsheet is empty.".into());
    };
    let first = first.clone();
    let width = roster.headers().len();
    if roster == Roster::Scratches {
        let first_labels: Vec<_> = first
            .iter()
            .map(|header| normalized_header(header))
            .collect();
        if first_labels.iter().any(|label| label == "teamname")
            && first_labels.iter().any(|label| {
                label.strip_prefix("disc").is_some_and(|rest| {
                    !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_digit())
                })
            })
        {
            return Err(format!(
                "This looks like a scratch form response sheet. {SCRATCH_COLUMNS}"
            ));
        }
    }
    let default = |rows: &Rows| {
        rows.iter()
            .all(|(_, row)| (0..width).all(|index| matches(roster, index, at(row, index))))
    };
    let sequential = || {
        (0..width)
            .map(|index| (index, index))
            .collect::<BTreeMap<_, _>>()
    };
    if default(&rows) {
        let extra = rows
            .iter()
            .map(|(_, row)| row.len())
            .max()
            .unwrap_or_default();
        return project(
            rows,
            width,
            sequential(),
            (width..extra).map(Some).collect(),
            false,
        );
    }

    let aliases = roster.aliases();
    let mut mapping = BTreeMap::new();
    for (index, label) in first.iter().enumerate() {
        if let Some(&target) = aliases.get(&normalized_header(label))
            && mapping.insert(target, index).is_some()
        {
            return Err(format!(
                "Duplicate column \"{}\" in header row.",
                roster.headers()[target]
            ));
        }
    }
    let typed = rows.len() > 1
        && mapping.iter().any(|(&target, &source)| {
            !matches(roster, target, at(&first, source))
                && rows[1..]
                    .iter()
                    .all(|(_, row)| source < row.len() && matches(roster, target, at(row, source)))
        });
    let has_header = mapping.len() >= 2 || typed;
    if has_header {
        rows.remove(0);
    }
    if rows.is_empty() {
        return Err("The spreadsheet has headers but no data rows.".into());
    }
    let extras = if roster == Roster::Judges && has_header {
        judge_extras(&first, &rows, &mapping, round_count)?
    } else {
        Vec::new()
    };
    let default_header = mapping.iter().all(|(target, source)| target == source);
    if (!has_header || default_header) && default(&rows) {
        let extras = if roster == Roster::Judges && has_header {
            extras
        } else {
            (width..first.len()).map(Some).collect()
        };
        return project(rows, width, sequential(), extras, has_header);
    }
    if !has_header {
        mapping.clear();
    }
    let mut used: BTreeSet<_> = mapping
        .values()
        .copied()
        .chain(extras.iter().copied().flatten())
        .collect();
    let columns = rows
        .iter()
        .map(|(_, row)| row.len())
        .max()
        .unwrap_or_default();
    for target in 0..width {
        if mapping.contains_key(&target) {
            continue;
        }
        let candidates: Vec<_> = (0..columns)
            .filter(|source| {
                !used.contains(source)
                    && rows.iter().any(|(_, row)| !at(row, *source).is_empty())
                    && rows.iter().all(|(_, row)| {
                        at(row, *source).is_empty() || matches(roster, target, at(row, *source))
                    })
            })
            .take(2)
            .collect();
        if let [source] = candidates.as_slice() {
            mapping.insert(target, *source);
            used.insert(*source);
        }
    }
    let missing: Vec<_> = roster
        .required()
        .iter()
        .filter(|id| !mapping.contains_key(id))
        .map(|&index| roster.headers()[index])
        .collect();
    if !missing.is_empty() {
        if !has_header
            && rows.iter().all(|(_, row)| {
                row.len() >= width
                    && matches(roster, 0, &row[0])
                    && (0..width)
                        .filter(|&index| matches(roster, index, &row[index]))
                        .count()
                        >= width - 1
            })
        {
            return project(
                rows,
                width,
                sequential(),
                (width..first.len()).map(Some).collect(),
                false,
            );
        }
        let names = missing
            .into_iter()
            .map(|label| format!("\"{label}\""))
            .collect::<Vec<_>>()
            .join(", ");
        let mut error = format!(
            "Couldn't find an unambiguous column for {names}. Use the default column order or add the named headers from an export."
        );
        if roster == Roster::Scratches {
            error.push_str(" For scratches, ");
            error.push_str(&SCRATCH_COLUMNS.replacen("Use", "use", 1));
        }
        return Err(error);
    }
    project(rows, width, mapping, extras, has_header)
}

fn project(
    rows: Rows,
    width: usize,
    mapping: BTreeMap<usize, usize>,
    extras: Vec<Option<usize>>,
    named_headers: bool,
) -> Result<Layout, String> {
    let columns: Vec<_> = (0..width)
        .map(|target| mapping.get(&target).copied())
        .chain(extras)
        .collect();
    if rows.len().saturating_mul(columns.len()) > MAX_CELLS {
        return Err("The spreadsheet exceeds the row or column limit.".into());
    }
    let rows = rows
        .into_iter()
        .map(|(number, row)| {
            (
                number,
                columns
                    .iter()
                    .map(|index| index.map_or("", |index| at(&row, index)).into())
                    .collect(),
            )
        })
        .collect();
    Ok(Layout {
        rows,
        named_headers,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rows(values: &[&[&str]]) -> Rows {
        values
            .iter()
            .enumerate()
            .map(|(index, row)| (index + 1, row.iter().map(|value| (*value).into()).collect()))
            .collect()
    }

    #[test]
    fn judge_headers_reorder_school_and_expected_columns() {
        let sheet = rows(&[
            &[
                "Round 3 expected",
                "Affiliation 1",
                "Judge calibration",
                "Judge name",
            ],
            &["yes", "School 1", "5", "Jamie"],
        ]);
        let configured = configure(Roster::Judges, sheet.clone(), 3).unwrap();
        assert!(configured.named_headers);
        assert_eq!(
            configured.rows[0].1,
            ["Jamie", "", "5", "School 1", "", "", "yes"]
        );
        assert!(configure(Roster::Judges, sheet, 2).is_err());
    }

    #[test]
    fn header_alias_as_a_room_name_stays_in_headerless_data() {
        let configured = configure(Roster::Rooms, rows(&[&["Room", "5"]]), 0).unwrap();
        assert!(!configured.named_headers);
        assert_eq!(configured.rows[0].1, ["Room", "5"]);
    }
}
