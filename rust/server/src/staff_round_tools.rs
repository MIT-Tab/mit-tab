use std::collections::BTreeMap;

use axum::{
    Extension, Json, Router,
    body::Bytes,
    extract::{MatchedPath, Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
    routing::get,
};
use minijinja::context;
use mittab_tabulation::rankings::Stat;
use serde::Serialize;
use serde_json::{Map, Value, json};
use sqlx::Row;

use crate::{
    App, access,
    board_snapshot::{self, Pairings, Snapshot},
    html::{self, Navigation},
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
            get(checkin_page).post(checkin),
        )
        .route("/pairings/view_rounds/", get(rounds))
        .route("/pairings/round_stats/", get(stats))
        .route("/round/{number}/stats/", get(team_stats))
        .route("/outround/{number}/stats/", get(team_stats))
        .route("/pairings/export_csv/", get(export_inround))
        .route(
            "/outround_pairings/export_csv/{division}/",
            get(export_outround),
        )
}

async fn checkin_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(settings) = Settings::load(&mut db, &["cur_round", "tot_rounds"]).await else {
        return unavailable();
    };
    let Ok(judges) =
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_judge ORDER BY name, id")
            .fetch_all(&mut *db)
            .await
    else {
        return unavailable();
    };
    let Ok(schools) =
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_school ORDER BY name, id")
            .fetch_all(&mut *db)
            .await
    else {
        return unavailable();
    };
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    let round = settings.integer("cur_round", 1);
    let total = settings.integer("tot_rounds", 5).clamp(0, 50);
    let response = html::render(
        "quick_judge_checkin.html",
        context! {
            title => "Quick Judge Check-In", csrf, nav => Navigation::new(actor, true),
            round, total, judges, schools,
        },
        StatusCode::OK,
    );
    html::form_response(response, &headers, &csrf)
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
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(settings) = Settings::load(&mut db, &["tot_rounds", "cur_round"]).await else {
        return unavailable();
    };
    let total = settings.integer("tot_rounds", 5).clamp(0, 50);
    html::render(
        "round_stats.html",
        context! {
            title => "Preliminary Rounds", nav => Navigation::new(actor, true),
            rounds => (1..=total).collect::<Vec<_>>(), current => settings.integer("cur_round", 1) - 1, index => true,
        },
        StatusCode::OK,
    )
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
    government: u64,
    opposition: u64,
    speaks: Vec<f64>,
}

impl Sample {
    fn add(&mut self, winner: i32) {
        self.government += u64::from(matches!(winner, 1 | 3));
        self.opposition += u64::from(matches!(winner, 2 | 4));
    }

    fn summary(mut self, label: String) -> Option<Summary> {
        let count = self.government + self.opposition;
        if count == 0 {
            return None;
        }
        let percent = self.government as f64 / count as f64 * 100.0;
        let speaks = self.speaks.len();
        self.speaks.sort_by(f64::total_cmp);
        let mean = (speaks > 0).then(|| self.speaks.iter().sum::<f64>() / speaks as f64);
        let median =
            (speaks > 0).then(|| (self.speaks[(speaks - 1) / 2] + self.speaks[speaks / 2]) / 2.0);
        let deviation = mean.filter(|_| speaks > 1).map(|mean| {
            (self
                .speaks
                .iter()
                .map(|score| (score - mean).powi(2))
                .sum::<f64>()
                / (speaks - 1) as f64)
                .sqrt()
        });
        let fmt =
            |value: Option<f64>| value.map_or_else(|| "—".into(), |value| format!("{value:.2}"));
        Some(Summary {
            label,
            government: self.government,
            opposition: self.opposition,
            percent: format!("{percent:.1}%"),
            mean: fmt(mean),
            median: fmt(median),
            deviation: fmt(deviation),
            minimum: fmt(self.speaks.first().copied()),
            maximum: fmt(self.speaks.last().copied()),
        })
    }
}

#[derive(Serialize)]
struct Summary {
    label: String,
    government: u64,
    opposition: u64,
    percent: String,
    mean: String,
    median: String,
    deviation: String,
    minimum: String,
    maximum: String,
}

async fn stats(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let mut overall = Sample::default();
        let mut prelim = BTreeMap::<i32, Sample>::new();
        for (round, winner) in sqlx::query_as::<_, (i32, i32)>("SELECT round_number, victor FROM tab_round")
            .fetch_all(&mut *db).await? {
            prelim.entry(round).or_default().add(winner);
            overall.add(winner);
        }
        for row in sqlx::query("SELECT r.round_number, CAST(s.speaks AS CHAR) AS speaks
                               FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id")
            .fetch_all(&mut *db).await? {
            let round: i32 = row.try_get("round_number")?;
            if let Ok(score) = row.try_get::<String, _>("speaks")?.parse::<f64>() {
                overall.speaks.push(score);
                prelim.entry(round).or_default().speaks.push(score);
            }
        }
        let mut out = BTreeMap::<(i32, i32), Sample>::new();
        for (division, stage, winner) in sqlx::query_as::<_, (i32, i32, i32)>("SELECT type_of_round, num_teams, victor FROM tab_outround")
            .fetch_all(&mut *db).await? {
            out.entry((division, stage)).or_default().add(winner);
        }
        let overall = overall.summary("All preliminary rounds".into());
        let prelim: Vec<_> = prelim.into_iter().filter_map(|(round, scores)| scores.summary(format!("Round {round}"))).collect();
        let out: Vec<_> = out.into_iter().filter_map(|((division, stage), scores)| {
            scores.summary(format!("{} · {stage} teams", if division == 0 { "Varsity" } else { "Novice" }))
        }).collect();
        db.commit().await?;
        Ok::<_, sqlx::Error>(html::render("round_stats.html", context! {
            title => "Round Statistics", nav => Navigation::new(actor, true), overall, prelim, out,
        }, StatusCode::OK))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}
