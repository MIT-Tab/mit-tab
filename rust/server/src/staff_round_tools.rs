use std::collections::BTreeMap;

use axum::{
    Extension, Json, Router,
    body::Bytes,
    extract::{MatchedPath, Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
    routing::get,
};
use bigdecimal::BigDecimal;
use minijinja::{Value as TemplateValue, context};
use mittab_tabulation::rankings::Stat;
use serde_json::{Map, Value, json};

use crate::{
    App, access,
    board_snapshot::{self, Pairings, Snapshot},
    legacy_html,
    legacy_session::Actor,
    login, private, public_rankings,
    registration_form::Fields,
    settings::Settings,
    spreadsheets::{self, Format},
    staff_pairing::lock_round,
    staff_roster_data::{self, Edit, Kind},
    tournament_data::TournamentData,
    unavailable,
};

pub fn routes() -> Router<App> {
    Router::new()
        .route(
            "/pairings/quick_judge_checkin/",
            get(checkin_requires_post).post(checkin),
        )
        .route("/pairings/view_rounds/", get(rounds))
        .route(STATS_PATH, get(stats))
        .route("/round/{number}/stats/", get(team_stats))
        .route("/outround/{number}/stats/", get(team_stats))
        .route("/pairings/export_csv/", get(export_inround))
        .route(
            "/outround_pairings/export_csv/{division}/",
            get(export_outround),
        )
}

async fn checkin_requires_post() -> Response {
    checkin_error(StatusCode::METHOD_NOT_ALLOWED, "POST required")
}

async fn checkin(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    let token = headers
        .get("x-csrftoken")
        .and_then(|token| token.to_str().ok())
        .unwrap_or_else(|| fields.get("csrfmiddlewaretoken"));
    if !login::valid_csrf(&headers, token) {
        return login::csrf_error();
    }
    let Actor::User {
        id, board: false, ..
    } = actor
    else {
        return access::access_denied();
    };
    let Ok(round) = fields.get("round_number").parse::<i32>() else {
        return checkin_error(StatusCode::BAD_REQUEST, "Invalid round");
    };
    match enter_checkin(&app, id, round, &fields).await {
        Ok((id, name, rank, created)) => {
            if fields.flag("redirect") {
                access::found("/pairings/status/")
            } else {
                private(Json(
                    json!({"success": true, "judge": {"id": id, "name": name, "rank": rank}, "created": created}),
                ))
            }
        }
        Err((status, error)) => checkin_error(status, error),
    }
}

fn checkin_error(status: StatusCode, reason: &str) -> Response {
    private((status, Json(json!({"success": false, "error": reason}))))
}

async fn enter_checkin(
    app: &App,
    user: i32,
    round: i32,
    fields: &Fields,
) -> Result<(i32, String, String, bool), (StatusCode, &'static str)> {
    const DATABASE: (StatusCode, &str) = (
        StatusCode::SERVICE_UNAVAILABLE,
        "Could not check in the judge.",
    );
    let invalid = |problem| (StatusCode::BAD_REQUEST, problem);
    let mut db = app.db.begin().await.map_err(|_| DATABASE)?;
    lock_round(&mut db).await.map_err(|_| DATABASE)?;
    let settings = Settings::load(&mut db, &["tot_rounds"])
        .await
        .map_err(|_| DATABASE)?;
    if !(0..=settings.integer("tot_rounds", 5)).contains(&round) {
        return Err(invalid("Invalid round"));
    }
    let existing = fields.get("judge_id");
    let created = existing.is_empty();
    let judge = if created {
        let name = fields.get("name");
        if name.is_empty() {
            return Err(invalid("Name is required"));
        }
        let mut draft = Fields::default();
        draft.push("name", name);
        let rank = fields.get("rank");
        let value = if rank.is_empty() {
            5.0
        } else {
            rank.parse::<f64>()
                .ok()
                .filter(|rank| rank.is_finite())
                .ok_or(invalid("Invalid rank"))?
        };
        draft.push("rank", format!("{value:.2}"));
        for school in fields
            .all("school_ids[]")
            .iter()
            .chain(fields.all("school_ids"))
        {
            draft.push("schools", school);
        }
        staff_roster_data::save_on(
            &mut db,
            Kind::Judge,
            Edit {
                id: None,
                user,
                checkins: false,
                public_log: true,
                fields: &draft,
            },
        )
        .await
        .map_err(|error| match error {
            staff_roster_data::Error::Invalid(error) => invalid(error),
            _ => DATABASE,
        })?
    } else {
        existing
            .parse::<i32>()
            .map_err(|_| (StatusCode::NOT_FOUND, "Judge not found"))?
    };
    let (name, rank) = sqlx::query_as::<_, (String, String)>(
        "SELECT name, CAST(`rank` AS CHAR) FROM tab_judge WHERE id = ?",
    )
    .bind(judge)
    .fetch_optional(&mut *db)
    .await
    .map_err(|_| DATABASE)?
    .ok_or((StatusCode::NOT_FOUND, "Judge not found"))?;
    sqlx::query("INSERT INTO tab_checkin (judge_id, round_number) VALUES (?, ?) ON DUPLICATE KEY UPDATE round_number = round_number")
        .bind(judge).bind(round).execute(&mut *db).await.map_err(|_| DATABASE)?;
    db.commit().await.map_err(|_| DATABASE)?;
    Ok((judge, name, rank, created))
}

async fn rounds(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    const PAGE: &str = "/pairings/view_rounds/";
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, &["tot_rounds"]).await?;
        let item_list: Vec<_> = (1..=settings.integer("tot_rounds", 0))
            .map(|number| (number, format!("Round {number}"), 0, ""))
            .collect();
        let shell = legacy_html::shell(&mut db, actor, PAGE).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(legacy_html::render(
            "common/list_data.html",
            shell,
            context! { item_type => "round", item_list },
            StatusCode::OK,
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn export_inround(State(app): State<App>) -> Response {
    match inround_csv(&app).await {
        Ok((round, rows)) => csv_download(
            &format!("round_{round}_pairings.csv"),
            &["Government", "Opposition", "Judge", "Room"],
            &rows,
        ),
        Err(_) => unavailable(),
    }
}

async fn inround_csv(app: &App) -> Result<(i32, Vec<Vec<String>>), sqlx::Error> {
    let mut db = app.db.begin().await?;
    let round = Settings::load(&mut db, &["cur_round"])
        .await?
        .integer("cur_round", 1)
        .saturating_sub(1);
    let pairings = board_snapshot::capture(&mut db, Pairings::Inround(round)).await?;
    let mut rows: Vec<_> = pairings.into_values().map(pairing_row).collect();
    rows.sort_by(|left, right| left[0].cmp(&right[0]));
    let byes: Vec<String> = sqlx::query_scalar(
        "SELECT t.name FROM tab_bye b JOIN tab_team t ON b.bye_team_id = t.id
         WHERE b.round_number = ? ORDER BY b.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?;
    let unpaired: Vec<String> = sqlx::query_scalar(
        "SELECT t.name FROM tab_team t WHERE t.checked_in
            AND NOT EXISTS(SELECT 1 FROM tab_bye b WHERE b.round_number = ? AND b.bye_team_id = t.id)
            AND NOT EXISTS(SELECT 1 FROM tab_round r WHERE r.round_number = ? AND t.id IN (r.gov_team_id, r.opp_team_id))
         ORDER BY t.id",
    ).bind(round).bind(round).fetch_all(&mut *db).await?;
    for (label, teams) in [("BYES", byes), ("UNPAIRED", unpaired)] {
        if teams.is_empty() {
            continue;
        }
        rows.push(vec![String::new(); 4]);
        rows.push(vec![
            label.into(),
            String::new(),
            String::new(),
            String::new(),
        ]);
        rows.extend(
            teams
                .into_iter()
                .map(|team| vec![team, String::new(), String::new(), String::new()]),
        );
    }
    db.commit().await?;
    Ok((round, rows))
}

async fn export_outround(State(app): State<App>, Path(division): Path<i32>) -> Response {
    if !matches!(division, 0 | 1) {
        return private((StatusCode::NOT_FOUND, "Division not found."));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let key = if division == 0 {
            "var_teams_visible"
        } else {
            "nov_teams_visible"
        };
        let visible = Settings::load(&mut db, &[key]).await?.integer(key, 256);
        let snapshots = board_snapshot::capture(&mut db, Pairings::Outround).await?;
        let mut rounds: Vec<_> = snapshots
            .into_values()
            .filter(|row| {
                row.division == Some(division) && row.num_teams.is_some_and(|size| size >= visible)
            })
            .collect();
        rounds.sort_by_key(|round| std::cmp::Reverse(round.num_teams));
        let display = if division == 0 { "V" } else { "N" };
        let mut output = Vec::new();
        for row in rounds {
            let label = format!("[{display}] Ro{}", row.num_teams.unwrap_or_default());
            let mut line = pairing_row(row);
            line.insert(0, label);
            output.push(line);
        }
        db.commit().await?;
        Ok::<_, sqlx::Error>(output)
    }
    .await;
    match result {
        Ok(rows) => csv_download(
            "outround_pairings.csv",
            &["Round", "Government", "Opposition", "Judge", "Room"],
            &rows,
        ),
        Err(_) => unavailable(),
    }
}

fn pairing_row(snapshot: Snapshot) -> Vec<String> {
    let judges = snapshot
        .judges
        .into_iter()
        .map(|judge| {
            if Some(judge.id) == snapshot.chair {
                format!("{} (chair)", judge.name)
            } else {
                judge.name
            }
        })
        .collect::<Vec<_>>()
        .join(", ");
    vec![
        snapshot.gov_team.map(|item| item.name).unwrap_or_default(),
        snapshot.opp_team.map(|item| item.name).unwrap_or_default(),
        judges,
        snapshot.room.map(|room| room.name).unwrap_or_default(),
    ]
}

pub fn csv_download(name: &str, headers: &[&str], rows: &[Vec<String>]) -> Response {
    let Ok(bytes) = spreadsheets::write(Format::Csv, headers, rows, false) else {
        return unavailable();
    };
    let Ok(filename) = HeaderValue::from_str(&format!("attachment; filename={name}")) else {
        return unavailable();
    };
    private((
        [
            (header::CONTENT_TYPE, HeaderValue::from_static("text/csv")),
            (header::CONTENT_DISPOSITION, filename),
        ],
        bytes,
    ))
}

async fn team_stats(
    State(app): State<App>,
    path: MatchedPath,
    Path(number): Path<i32>,
) -> Response {
    let out = path.as_str().starts_with("/outround/");
    match pairing_stats(&app, number, out).await {
        Ok(values) => private(Json(values)),
        Err(_) => unavailable(),
    }
}

async fn pairing_stats(app: &App, number: i32, out: bool) -> Result<Value, sqlx::Error> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, &["cur_round", "lenient_late"]).await?;
    let data = TournamentData::load(&mut db).await?;
    let scores = data
        .scores_by_team(public_rankings::policy(&settings))
        .map_err(|error| sqlx::Error::Protocol(error.into()))?;
    let pairings = if out {
        Pairings::Outround
    } else {
        Pairings::Inround(number)
    };
    let scope = board_snapshot::capture(&mut db, pairings).await?;
    let mut payload = Map::new();
    for debate in scope
        .into_values()
        .filter(|row| !out || row.num_teams == Some(number))
    {
        for team in [debate.gov_team, debate.opp_team].into_iter().flatten() {
            let key = team.id.to_string();
            if payload.contains_key(&key) {
                continue;
            }
            let Some(score) = scores.get(&i64::from(team.id)) else {
                continue;
            };
            let (govs, opps) = data.side_counts(i64::from(team.id));
            let seed = data
                .teams
                .iter()
                .find(|row| row.id == i64::from(team.id))
                .map_or(0, |row| row.seed);
            let seed = match seed {
                1 => "Free",
                2 => "Half",
                3 => "Full",
                _ => "Unseeded",
            };
            let names: Vec<String> = sqlx::query_scalar(
                "SELECT d.name FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id
                 WHERE m.team_id = ? ORDER BY d.name, d.id",
            )
            .bind(team.id)
            .fetch_all(&mut *db)
            .await?;
            let mut line = json!({"seed": seed, "wins": score.get(Stat::Wins) as i32, "total_speaks": score.get(Stat::Speaks),
                "govs": govs, "opps": opps, "debaters": names.join(", ")});
            if let Some((seed, effective)) = sqlx::query_as::<_, (i32, i32)>(
                "SELECT seed, effective_seed FROM tab_breakingteam WHERE team_id = ?",
            )
            .bind(team.id)
            .fetch_optional(&mut *db)
            .await?
            {
                line["outround_seed"] = json!(seed);
                line["effective_outround_seed"] = json!(effective);
            }
            payload.insert(key, line);
        }
    }
    db.commit().await?;
    Ok(Value::Object(payload))
}

#[derive(Default)]
struct Sample {
    gov_wins: u64,
    opp_wins: u64,
    speaks: Vec<f64>,
}

impl Sample {
    fn add(&mut self, winner: i32) {
        self.gov_wins += u64::from(matches!(winner, 1 | 3));
        self.opp_wins += u64::from(matches!(winner, 2 | 4));
    }

    fn summary(mut self) -> Option<TemplateValue> {
        let count = self.gov_wins + self.opp_wins;
        if count == 0 {
            return None;
        }

        let gov_pct = self.gov_wins as f64 / count as f64 * 100.0;
        let opp_pct = self.opp_wins as f64 / count as f64 * 100.0;
        let len = self.speaks.len();
        self.speaks.sort_by(f64::total_cmp);
        let median_speak =
            (len > 0).then(|| (self.speaks[(len - 1) / 2] + self.speaks[len / 2]) / 2.0);
        let (mean_speak, stddev_speak) = speak_moments(&self.speaks);
        Some(context! {
            gov_wins => self.gov_wins,
            opp_wins => self.opp_wins,
            gov_pct, opp_pct, median_speak, mean_speak, stddev_speak,
            highest_speak => self.speaks.last(),
            lowest_speak => self.speaks.first(),
        })
    }
}

fn speak_moments(speaks: &[f64]) -> (Option<f64>, Option<f64>) {
    if speaks.is_empty() {
        return (None, None);
    }
    // Python's statistics module sums the exact values of the binary floats. Preserve
    // those values through the mean and sample variance before rounding back to f64.
    let mut sum = BigDecimal::from(0);
    let mut sum_squared = BigDecimal::from(0);
    for &score in speaks {
        let score = BigDecimal::try_from(score).expect("stored speaker score is finite");
        sum_squared += score.square();
        sum += score;
    }
    let count = BigDecimal::from(speaks.len() as u64);
    let as_float = |value: BigDecimal| value.to_string().parse::<f64>().expect("finite statistic");
    let mean = as_float(&sum / &count);
    let deviation = (speaks.len() > 1).then(|| {
        let variance = (&count * sum_squared - sum.square()) / (&count * (&count - 1_i32));
        // Python 3.10 rounds the variance to a float before taking its square root.
        as_float(variance).sqrt()
    });
    (Some(mean), deviation)
}

const STATS_PATH: &str = "/pairings/round_stats/";

async fn stats(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let mut overall = Sample::default();
        let mut prelim = BTreeMap::<i32, Sample>::new();
        let rounds = sqlx::query_as::<_, (i32, i32)>("SELECT round_number, victor FROM tab_round")
            .fetch_all(&mut *db)
            .await?;
        for (round, winner) in rounds {
            prelim.entry(round).or_default().add(winner);
            overall.add(winner);
        }
        let scores = sqlx::query_as::<_, (i32, String)>(
            "SELECT r.round_number, CAST(s.speaks AS CHAR)
             FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id WHERE s.speaks IS NOT NULL",
        )
        .fetch_all(&mut *db)
        .await?;
        for (round, score) in scores {
            let score = score.parse::<f64>().expect("stored decimal speaker score");
            overall.speaks.push(score);
            prelim.entry(round).or_default().speaks.push(score);
        }
        let mut out = BTreeMap::<(i32, i32), Sample>::new();
        for (division, stage, winner) in sqlx::query_as::<_, (i32, i32, i32)>(
            "SELECT type_of_round, num_teams, victor FROM tab_outround",
        )
        .fetch_all(&mut *db)
        .await?
        {
            out.entry((division, stage)).or_default().add(winner);
        }
        let shell = legacy_html::shell(&mut db, actor, STATS_PATH).await?;
        db.commit().await?;

        let tournament_stats = overall.summary();
        let prelim_stats: Vec<_> = prelim
            .into_iter()
            .filter_map(|(round_number, sample)| {
                sample.summary().map(|stats| context! { round_number, ..stats })
            })
            .collect();
        let outround_stats: Vec<_> = out
            .into_iter()
            .filter_map(|((division, stage), sample)| {
                let division = if division == 0 { "Varsity" } else { "Novice" };
                let name = format!("{division} - {stage} Teams");
                sample.summary().map(|stats| context! { name, ..stats })
            })
            .collect();
        Ok::<_, sqlx::Error>(legacy_html::render(
            "tab/round_stats.html",
            shell,
            context! { title => "Round Statistics", tournament_stats, prelim_stats, outround_stats },
            StatusCode::OK,
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}
