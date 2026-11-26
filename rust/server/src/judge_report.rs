use std::collections::{BTreeMap, BTreeSet, HashMap};

use axum::{
    Extension,
    extract::{RawQuery, State},
    http::StatusCode,
    response::Response,
};
use minijinja::context;
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App,
    html::{self, Navigation},
    legacy_session::Actor,
    private,
    registration_form::Fields,
    unavailable,
};

const DEFAULT_MINIMUM: usize = 3;

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    RawQuery(query): RawQuery,
) -> Response {
    if !matches!(
        actor,
        Actor::User {
            board: false,
            superuser: true,
            ..
        }
    ) {
        return private((
            StatusCode::FORBIDDEN,
            "You cannot view judge speaker points.",
        ));
    }
    let filter = Fields::parse(query.as_deref().unwrap_or_default().as_bytes());
    let entered = filter.get("minimum_rounds");
    let minimum = entered.parse::<usize>().ok().filter(|&value| value >= 1);
    let invalid = !entered.is_empty() && minimum.is_none();
    let minimum = minimum.unwrap_or(DEFAULT_MINIMUM);
    let highlights: BTreeSet<_> = filter
        .all("judge")
        .iter()
        .filter_map(|value| value.parse().ok())
        .collect();
    let result = async {
        let mut db = app.db.begin().await?;
        let judges = metrics(&mut db, minimum, &highlights).await?;
        db.commit().await?;
        let mut alphabetic: Vec<_> = judges.iter().collect();
        alphabetic.sort_by_cached_key(|judge| (judge.name.to_lowercase(), judge.id));
        let midpoint = judges.len().div_ceil(2);
        let panels = if judges.len() > 16 { vec![&judges[..midpoint], &judges[midpoint..]] } else { vec![judges.as_slice()] };
        let chart = plot(&judges, 760);
        let narrow = plot(&judges, 360);
        Ok::<_, sqlx::Error>(html::render("judge_report.html", context! {
            title => "Judge Speaker Points", nav => Navigation::new(actor, true), panels, minimum,
            alphabetic, chart, narrow, invalid,
        }, StatusCode::OK))
    }.await;
    result.unwrap_or_else(|_| unavailable())
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
    gaps: Vec<f64>,
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
    highlights: &BTreeSet<i32>,
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
            let other = (all.points - here.points) as f64 / count as f64;
            debates
                .entry(round)
                .or_default()
                .gaps
                .push(speaks as f64 - other);
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
        let gaps: usize = rounds.iter().map(|debate| debate.gaps.len()).sum();
        let mean = (points as f64 / (100.0 * scores as f64)).round_ties_even() as i64;
        let generosity = (gaps != 0).then(|| {
            let difference: f64 = rounds.iter().flat_map(|debate| &debate.gaps).sum();
            (difference / (100.0 * gaps as f64)).round_ties_even() as i64
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
            generosity_text: generosity.map(|points| {
                let sign = if points > 0 { "+" } else { "" };
                format!("{sign}{:.2}", points as f64 / 100.0)
            }),
            highlight: highlights.contains(&id),
        });
    }
    rows.sort_by_cached_key(|row| (-row.mean, row.name.to_lowercase(), row.id));
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

const LEFT: f64 = 54.0;
const TOP: f64 = 18.0;
const BOTTOM: f64 = 238.0;

struct Axis {
    start: f64,
    end: f64,
    low: i64,
    high: i64,
    step: f64,
}

#[derive(Serialize)]
struct Tick {
    position: f64,
    text: String,
    zero: bool,
}

impl Axis {
    fn from_range(minimum: i64, maximum: i64, start: f64, end: f64) -> Self {
        let margin = ((maximum - minimum) as f64 * 0.1).max(4.0);
        let low = minimum as f64 - margin;
        let high = maximum as f64 + margin;
        let ideal = (high - low) / 4.0;
        let unit = 10_f64.powf(ideal.log10().floor());
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
        ((self.start + fraction * (self.end - self.start)) * 10.0).round() / 10.0
    }

    fn ticks(&self) -> Vec<Tick> {
        (self.low..=self.high)
            .map(|tick| {
                let value = tick as f64 * self.step;
                let label = format!("{:.2}", value / 100.0);
                Tick {
                    position: self.coordinate(value),
                    text: label.trim_end_matches('0').trim_end_matches('.').into(),
                    zero: tick == 0,
                }
            })
            .collect()
    }
}

#[derive(Serialize)]
struct Point<'a> {
    x: f64,
    y: f64,
    judge: &'a Judge,
}

#[derive(Serialize)]
struct Chart<'a> {
    width: u32,
    right: f64,
    midpoint: f64,
    points: Vec<Point<'a>>,
    labels: Vec<Label>,
    x_ticks: Vec<Tick>,
    y_ticks: Vec<Tick>,
}

#[derive(Serialize)]
struct Label {
    x: f64,
    y: f64,
    origin_x: f64,
    origin_y: f64,
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

fn labels(points: &[Point<'_>], outliers: &BTreeSet<i32>, right: f64) -> Vec<Label> {
    let mut occupied: Vec<_> = points
        .iter()
        .map(|point| Bounds(point.x - 6.0, point.y - 6.0, point.x + 6.0, point.y + 6.0))
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
        let width = 7.5 * text.chars().count() as f64 + 4.0;
        let side = if point.x < (LEFT + right) / 2.0 {
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
            let candidate = Bounds(left, y - 8.0, left + width, y + 8.0);
            if candidate.0 >= LEFT
                && candidate.1 >= TOP
                && candidate.2 <= right
                && candidate.3 <= BOTTOM
                && !occupied.iter().any(|other| other.intersects(candidate))
            {
                occupied.push(candidate);
                labels.push(Label {
                    x,
                    y,
                    origin_x: point.x,
                    origin_y: point.y,
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
    let right = f64::from(width) - 18.0;
    let x = Axis::from_range(
        comparable.last()?.0.mean,
        comparable.first()?.0.mean,
        LEFT,
        right,
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
            x: x.coordinate(judge.mean as f64),
            y: y.coordinate(gap as f64),
            judge,
        })
        .collect();
    points.sort_by_key(|point| point.judge.highlight);
    Some(Chart {
        width,
        right,
        midpoint: (LEFT + right) / 2.0,
        labels: labels(&points, &outliers, right),
        points,
        x_ticks: x.ticks(),
        y_ticks: y.ticks(),
    })
}
