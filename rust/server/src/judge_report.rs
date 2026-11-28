use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    num::NonZeroU64,
};

use axum::{
    Extension,
    extract::{RawQuery, State},
    http::{StatusCode, Uri},
    response::Response,
};
use bigdecimal::{BigDecimal, RoundingMode, ToPrimitive};
use minijinja::{Value, context};
use serde::{Serialize, Serializer};
use sqlx::MySqlConnection;

use crate::{
    App, legacy_html, legacy_session::Actor, registration_form::Fields, settings::Settings,
    unavailable,
};

const DEFAULT_MINIMUM: usize = 3;
const REPORT: &str = "tab/judge_speaker_points.html";

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    RawQuery(query): RawQuery,
    uri: Uri,
) -> Response {
    if !matches!(
        actor,
        Actor::User {
            board: false,
            superuser: true,
            ..
        }
    ) {
        let Ok(mut db) = app.db.acquire().await else {
            return unavailable();
        };
        return legacy_html::forbidden(&mut db, actor, uri.path())
            .await
            .unwrap_or_else(|_| unavailable());
    }
    let filter = Fields::parse(query.as_deref().unwrap_or_default().as_bytes());
    let entered = filter.all("minimum_rounds").last();
    let minimum = entered.and_then(|value| minimum_rounds(value));
    let invalid = entered.is_some() && minimum.is_none();
    let minimum = minimum.unwrap_or_else(|| DEFAULT_MINIMUM.to_string());
    let widget = number_input(entered.map_or(&minimum, String::as_str));
    let highlights: BTreeSet<_> = filter.all("judge").iter().map(String::as_str).collect();
    let result = async {
        let mut db = app.db.begin().await?;
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        let tournament = Settings::load(&mut db, &["tournament_name"])
            .await?
            .string("tournament_name", "New Tournament");
        let threshold = minimum.parse().unwrap_or(usize::MAX);
        let judges = metrics(&mut db, threshold, &highlights).await?;
        db.commit().await?;
        let mut alphabetic: Vec<_> = judges.iter().collect();
        alphabetic.sort_by_cached_key(|judge| legacy_html::casefold(&judge.name));
        let midpoint = judges.len().div_ceil(2);
        let panels = if judges.len() > 16 { vec![&judges[..midpoint], &judges[midpoint..]] } else { vec![judges.as_slice()] };
        Ok::<_, sqlx::Error>(legacy_html::render(REPORT, shell, context! {
            metrics => &judges,
            tournament,
            judges => alphabetic,
            panels,
            minimum_rounds => minimum,
            filters => context! {
                minimum_rounds => context! { id_for_label => "id_minimum_rounds", html => widget },
                errors => invalid,
            },
            plot => plot(&judges, 760),
            mobile_plot => plot(&judges, 360),
        }, StatusCode::OK))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

fn minimum_rounds(entered: &str) -> Option<String> {
    let entered = entered.trim();
    let whole = entered
        .rsplit_once('.')
        .filter(|(_, decimal)| decimal.bytes().all(|byte| byte == b'0'))
        .map_or(entered, |(whole, _)| whole.trim_end());
    let whole = whole.strip_prefix('+').unwrap_or(whole);
    let mut digits = String::new();
    for part in whole.split('_') {
        if part.is_empty() {
            return None;
        }
        for ch in part.chars() {
            digits.push(decimal_digit(ch)?);
        }
    }
    // CPython limits a decimal integer conversion to 4,300 digits.
    let value = digits.trim_start_matches('0');
    (!value.is_empty() && digits.len() <= 4300).then(|| value.into())
}

fn decimal_digit(ch: char) -> Option<char> {
    // The starting code points of each Unicode 13 decimal digit set in CPython 3.10.
    const ZEROES: &[u32] = &[
        0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66,
        0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90,
        0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0,
        0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0,
        0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x16a60, 0x16b50,
        0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e950, 0x1fbf0,
    ];
    let code = u32::from(ch);
    let index = ZEROES
        .partition_point(|&zero| zero <= code)
        .checked_sub(1)?;
    let distance = code - ZEROES[index];
    (distance < 10).then(|| char::from(b'0' + distance as u8))
}

fn number_input(value: &str) -> Value {
    let value_attr = if value.is_empty() {
        String::new()
    } else {
        format!(" value=\"{}\"", legacy_html::escape(value))
    };
    Value::from_safe_string(format!(
        "<input type=\"number\" name=\"minimum_rounds\"{value_attr} class=\"form-control\" min=\"1\" required id=\"id_minimum_rounds\">"
    ))
}

#[derive(Default)]
struct Total {
    points: i64,
    count: usize,
}

impl Total {
    fn add(&mut self, points: i64) {
        self.points += points;
        self.count += 1;
    }
}

#[derive(Default)]
struct Debate {
    raw: Total,
    gap_total: BigDecimal,
    gap_count: usize,
}

fn django_decimal(value: BigDecimal) -> BigDecimal {
    value.with_precision_round(NonZeroU64::new(28).unwrap(), RoundingMode::HalfEven)
}

fn mean_hundredths(total: BigDecimal, count: usize) -> i64 {
    let mean = django_decimal(total / BigDecimal::from(count as u64));
    mean.with_scale_round(2, RoundingMode::HalfEven)
        .into_bigint_and_scale()
        .0
        .to_i64()
        .expect("hundredths of a bounded ballot score")
}

#[derive(Serialize)]
struct Judge {
    id: i32,
    name: String,
    rounds: usize,
    mean: i64,
    generosity: Option<i64>,
    mean_rank: usize,
    generosity_rank: usize,
    mean_text: String,
    generosity_text: Option<String>,
    highlight: bool,
}

async fn metrics(
    db: &mut MySqlConnection,
    minimum: usize,
    highlights: &BTreeSet<&str>,
) -> Result<Vec<Judge>, sqlx::Error> {
    let scores = sqlx::query_as::<_, (i32, i32, i64)>(
        "SELECT s.round_id, s.debater_id, CAST(s.speaks * 10000 AS SIGNED)
         FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id WHERE r.victor IN (1, 2) ORDER BY s.id",
    ).fetch_all(&mut *db).await?;
    let mut debates: HashMap<i32, Debate> = HashMap::new();
    let mut all: HashMap<i32, Total> = HashMap::new();
    let mut within: HashMap<(i32, i32), Total> = HashMap::new();
    for &(round, debater, speaks) in &scores {
        debates.entry(round).or_default().raw.add(speaks);
        all.entry(debater).or_default().add(speaks);
        within.entry((round, debater)).or_default().add(speaks);
    }
    for (round, debater, speaks) in scores {
        let all = &all[&debater];
        let here = &within[&(round, debater)];
        let count = all.count - here.count;
        if count != 0 {
            // Python Decimal rounds division and subtraction at 28 significant
            // digits. statistics.mean then adds those rounded gaps exactly.
            let other = django_decimal(
                BigDecimal::new((all.points - here.points).into(), 4)
                    / BigDecimal::from(count as u64),
            );
            let debate = debates.get_mut(&round).expect("recorded debate");
            debate.gap_total += django_decimal(BigDecimal::new(speaks.into(), 4) - other);
            debate.gap_count += 1;
        }
    }

    let mut judges = BTreeMap::new();
    for (id, name, round) in sqlx::query_as::<_, (i32, String, i32)>(
        "SELECT j.id, j.name, a.round_id FROM tab_round_judges a
         JOIN tab_judge j ON j.id = a.judge_id ORDER BY a.round_id, j.id",
    )
    .fetch_all(db)
    .await?
    {
        if let Some(debate) = debates.get(&round) {
            let (_, rounds) = judges.entry(id).or_insert((name, Vec::new()));
            rounds.push(debate);
        }
    }
    let mut rows = Vec::new();
    for (id, (name, rounds)) in judges {
        if rounds.len() < minimum {
            continue;
        }
        let points: i64 = rounds.iter().map(|debate| debate.raw.points).sum();
        let scores: usize = rounds.iter().map(|debate| debate.raw.count).sum();
        let gaps: usize = rounds.iter().map(|debate| debate.gap_count).sum();
        let mean = mean_hundredths(BigDecimal::new(points.into(), 4), scores);
        let generosity = (gaps != 0).then(|| {
            let difference = rounds.iter().map(|debate| &debate.gap_total).sum();
            mean_hundredths(difference, gaps)
        });
        rows.push(Judge {
            id,
            name,
            rounds: rounds.len(),
            mean,
            generosity,
            mean_rank: 0,
            generosity_rank: 0,
            mean_text: format!("{:.2}", mean as f64 / 100.0),
            generosity_text: generosity.map(|points| format!("{:.2}", points as f64 / 100.0)),
            highlight: highlights.contains(id.to_string().as_str()),
        });
    }
    rows.sort_by_cached_key(|row| (-row.mean, legacy_html::casefold(&row.name), row.id));
    let mut means = HashMap::new();
    for (index, row) in rows.iter_mut().enumerate() {
        row.mean_rank = *means.entry(row.mean).or_insert(index + 1);
    }
    let mut adjusted: Vec<_> = rows
        .iter_mut()
        .filter(|row| row.generosity.is_some())
        .collect();
    adjusted.sort_by_key(|row| std::cmp::Reverse(row.generosity));
    let mut gaps = HashMap::new();
    for (index, row) in adjusted.into_iter().enumerate() {
        row.generosity_rank = *gaps.entry(row.generosity).or_insert(index + 1);
    }
    Ok(rows)
}

const LEFT: u32 = 56;
const TOP: f64 = 18.0;
const BOTTOM: f64 = 248.0;

struct Axis {
    start: f64,
    end: f64,
    low: i64,
    high: i64,
    step: f64,
}

#[derive(Serialize)]
struct Tick {
    #[serde(serialize_with = "svg_coordinate")]
    position: f64,
    text: String,
    zero: bool,
}

impl Axis {
    fn from_range(minimum: i64, maximum: i64, start: f64, end: f64) -> Self {
        let low = minimum as f64 / 100.0;
        let high = maximum as f64 / 100.0;
        let margin = ((high - low) * 0.1).max(0.04);
        let low = low - margin;
        let high = high + margin;
        let ideal = (high - low) / 4.0;
        let power = ideal.log10().floor() as i32;
        let unit = format!("1e{power}").parse::<f64>().expect("axis unit");
        let step = [1.0, 2.0, 5.0, 10.0]
            .into_iter()
            .map(|factor| factor * unit)
            .find(|&step| step >= ideal)
            .unwrap_or(unit * 10.0);
        Self {
            start,
            end,
            low: (low / step).floor() as i64,
            high: (high / step).ceil() as i64,
            step,
        }
    }

    fn coordinate(&self, value: f64) -> f64 {
        let fraction = (value / self.step - self.low as f64) / (self.high - self.low) as f64;
        let position = self.start + fraction * (self.end - self.start);
        format!("{position:.1}").parse().expect("axis coordinate")
    }

    fn ticks(&self) -> Vec<Tick> {
        (self.low..=self.high)
            .map(|tick| {
                let value = tick as f64 * self.step;
                let label = format!("{value:.2}");
                Tick {
                    position: self.coordinate(value),
                    text: label.trim_end_matches('0').trim_end_matches('.').into(),
                    zero: tick == 0,
                }
            })
            .collect()
    }
}

fn svg_coordinate<S: Serializer>(coordinate: &f64, serializer: S) -> Result<S::Ok, S::Error> {
    serializer.serialize_str(&format!("{coordinate:?}"))
}

#[derive(Clone, Copy, Serialize)]
struct Point<'a> {
    #[serde(serialize_with = "svg_coordinate")]
    x: f64,
    #[serde(serialize_with = "svg_coordinate")]
    y: f64,
    #[serde(flatten)]
    judge: &'a Judge,
}

#[derive(Serialize)]
struct Chart<'a> {
    width: u32,
    right: u32,
    field_width: u32,
    x_label: u32,
    points: Vec<Point<'a>>,
    labels: Vec<Label<'a>>,
    x_ticks: Vec<Tick>,
    y_ticks: Vec<Tick>,
}

#[derive(Serialize)]
struct Label<'a> {
    #[serde(serialize_with = "svg_coordinate")]
    x: f64,
    #[serde(serialize_with = "svg_coordinate")]
    y: f64,
    point: Point<'a>,
    anchor: &'static str,
    text: String,
}

#[derive(Clone, Copy)]
struct Bounds(f64, f64, f64, f64);

impl Bounds {
    fn intersects(self, other: Self) -> bool {
        self.0 < other.2 && self.1 < other.3 && self.2 > other.0 && self.3 > other.1
    }
}

fn labels<'a>(points: &[Point<'a>], outliers: &BTreeSet<i32>, right: u32) -> Vec<Label<'a>> {
    let left_edge = f64::from(LEFT);
    let right = f64::from(right);
    let mut occupied: Vec<_> = points
        .iter()
        .map(|point| Bounds(point.x - 7.0, point.y - 7.0, point.x + 7.0, point.y + 7.0))
        .collect();
    let mut candidates: Vec<_> = points
        .iter()
        .filter(|point| point.judge.highlight || outliers.contains(&point.judge.id))
        .collect();
    candidates.sort_by_key(|point| !point.judge.highlight);
    let mut labels = Vec::new();
    for point in candidates {
        let mut text: String = point.judge.name.chars().take(26).collect();
        if text.len() < point.judge.name.len() {
            text.pop();
            text.push('…');
        }
        let width = 8.0 * text.chars().count() as f64 + 6.0;
        let side = if point.x < (left_edge + right) / 2.0 {
            1.0
        } else {
            -1.0
        };
        let offsets = [0.0, -18.0, 18.0, -36.0, 36.0, -54.0, 54.0, -72.0, 72.0];
        let positions = [12.0, 32.0, 52.0].into_iter().flat_map(|distance| {
            offsets
                .into_iter()
                .flat_map(move |dy| [side, -side].map(|direction| (distance * direction, dy)))
        });
        for (dx, dy) in positions {
            let x = point.x + dx;
            let y = point.y + dy;
            let left = if dx.is_sign_positive() { x } else { x - width };
            let candidate = Bounds(left, y - 9.0, left + width, y + 9.0);
            if candidate.0 >= left_edge
                && candidate.1 >= TOP
                && candidate.2 <= right
                && candidate.3 <= BOTTOM
                && !occupied.iter().any(|other| other.intersects(candidate))
            {
                occupied.push(candidate);
                labels.push(Label {
                    x,
                    y,
                    point: *point,
                    anchor: if dx.is_sign_positive() {
                        "start"
                    } else {
                        "end"
                    },
                    text,
                });
                break;
            }
        }
    }
    labels
}

fn plot(rows: &[Judge], width: u32) -> Option<Chart<'_>> {
    let comparable: Vec<_> = rows
        .iter()
        .filter_map(|row| row.generosity.map(|gap| (row, gap)))
        .collect();
    let right = width - 18;
    let x = Axis::from_range(
        comparable.last()?.0.mean,
        comparable.first()?.0.mean,
        f64::from(LEFT),
        f64::from(right),
    );
    let y = Axis::from_range(
        comparable.iter().map(|(_, gap)| *gap).min()?.min(0),
        comparable.iter().map(|(_, gap)| *gap).max()?.max(0),
        BOTTOM,
        TOP,
    );
    let mut generosity = comparable.clone();
    generosity.sort_by_key(|(_, gap)| std::cmp::Reverse(*gap));
    let outliers = [&comparable, &generosity]
        .into_iter()
        .flat_map(|ordered| {
            ordered
                .iter()
                .take(3)
                .chain(ordered.iter().rev().take(3))
                .map(|(row, _)| row.id)
        })
        .collect();
    let mut points: Vec<_> = comparable
        .into_iter()
        .map(|(judge, gap)| Point {
            x: x.coordinate(judge.mean as f64 / 100.0),
            y: y.coordinate(gap as f64 / 100.0),
            judge,
        })
        .collect();
    points.sort_by_key(|point| point.judge.highlight);
    Some(Chart {
        width,
        right,
        field_width: right - LEFT,
        x_label: (LEFT + right) / 2,
        labels: labels(&points, &outliers, right),
        points,
        x_ticks: x.ticks(),
        y_ticks: y.ticks(),
    })
}
