use std::collections::{BTreeSet, HashSet};

use axum::{
    Extension, Json,
    body::Bytes,
    extract::{Query, State},
    http::{HeaderMap, StatusCode, header},
    response::Response,
};
use minijinja::context;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    checkin_data::{self as data, ChangeError, Entity},
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    settings::Settings,
    staff_pairing::lock_round,
    unavailable,
};

const PAGE: &str = "/batch_checkin/";
const SETTINGS: &[&str] = &["tot_rounds", "motions_enabled"];

#[derive(Deserialize)]
pub struct Filters {
    history: Option<String>,
    page: Option<u32>,
    before: Option<String>,
}

#[derive(Serialize)]
struct Mark {
    round: Option<i32>,
    status: bool,
    expected: bool,
}

#[derive(Serialize)]
struct Row {
    id: i32,
    name: String,
    secondary: String,
    marks: Vec<Mark>,
}

#[derive(Serialize)]
struct Grid {
    kind: &'static str,
    title: &'static str,
    fragment: &'static str,
    rows: Vec<Row>,
    totals: Vec<usize>,
    team: bool,
    expected: bool,
    undo: Option<Undo>,
}

#[derive(Serialize)]
struct Undo {
    id: String,
    count: usize,
}

impl From<data::Change> for Undo {
    fn from(change: data::Change) -> Self {
        Self {
            id: data::printable(&change.token),
            count: change.cells.len(),
        }
    }
}

async fn rows(
    db: &mut MySqlConnection,
    entity: Entity,
    rounds: &[i32],
) -> Result<Grid, sqlx::Error> {
    let current = data::checked(db, entity).await?;
    let projected = if entity == Entity::Judge {
        data::checked(db, Entity::Expected).await?
    } else {
        BTreeSet::new()
    };
    let query = match entity {
        Entity::Team => "SELECT t.id, t.name, s.name FROM tab_team t
                          JOIN tab_school s ON s.id = t.school_id ORDER BY t.name, t.id",
        Entity::Judge | Entity::Expected => "SELECT j.id, j.name,
           COALESCE((SELECT GROUP_CONCAT(s.name ORDER BY s.name SEPARATOR ', ')
             FROM tab_judge_schools x JOIN tab_school s ON s.id = x.school_id WHERE x.judge_id = j.id), '')
           FROM tab_judge j ORDER BY j.name, j.id",
        Entity::Room => "SELECT id, name, '' FROM tab_room ORDER BY name, id",
    };
    let round_keys: Vec<_> = if entity == Entity::Team {
        vec![None]
    } else {
        rounds.iter().map(|&round| Some(round)).collect()
    };
    let mut totals = vec![0; round_keys.len()];
    let rows = sqlx::query_as::<_, (i32, String, String)>(query)
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(|(id, name, secondary)| {
            let marks = round_keys
                .iter()
                .enumerate()
                .map(|(index, &round)| {
                    let status = current.contains(&(id, round));
                    totals[index] += usize::from(status);
                    Mark {
                        round,
                        status,
                        expected: projected.contains(&(id, round)),
                    }
                })
                .collect();
            Row {
                id,
                name,
                secondary,
                marks,
            }
        })
        .collect();
    Ok(Grid {
        kind: entity.key(),
        title: entity.title(),
        fragment: entity.fragment(),
        rows,
        totals,
        team: entity == Entity::Team,
        expected: entity == Entity::Expected,
        undo: data::latest(db, entity).await?.map(Undo::from),
    })
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(filters): Query<Filters>,
) -> Response {
    if let Some(kind) = &filters.history {
        let Some(entity) = Entity::parse(kind) else {
            return private((StatusCode::BAD_REQUEST, "Unknown check-in category."));
        };
        return history(&app, actor, &headers, entity, filters, false).await;
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, SETTINGS).await?;
        let rounds: Vec<_> = (1..=settings.integer("tot_rounds", 0)).chain(std::iter::once(0)).collect();
        let mut grids = Vec::new();
        for entity in Entity::ALL {
            grids.push(rows(&mut db, entity, &rounds).await?);
        }
        db.commit().await?;
        let Ok(csrf) = html::page_csrf(&headers) else { return Ok(unavailable()); };
        let response = html::render("checkin.html", context! {
            title => "Batch Check-In", csrf, grids, rounds, nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        }, StatusCode::OK);
        Ok::<_, sqlx::Error>(html::form_response(response, &headers, &csrf))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Serialize)]
struct HistoryRow {
    token: String,
    checked: bool,
    undone: bool,
    count: usize,
    time: String,
    rounds: Vec<i32>,
}

async fn history(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    entity: Entity,
    filters: Filters,
    mut conflict: bool,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let requested = filters.page.unwrap_or(1).max(1);
        let (rows, pages) = data::history(&mut db, entity, requested).await?;
        let current = requested.min(pages);
        let mut preview = None;
        if let Some(token) = &filters.before {
            match data::restore(&mut db, entity, token, None).await {
                Ok(result) => preview = Some(result),
                Err(ChangeError::Conflict) => conflict = true,
                Err(ChangeError::Storage) => return Ok(unavailable()),
            }
        }
        let rows: Vec<_> = rows.into_iter().map(|change| HistoryRow {
            token: data::printable(&change.token),
            checked: change.checked,
            undone: change.undone,
            count: change.cells.len(),
            time: change.created.format("%b %d, %Y at %H:%M UTC").to_string(),
            rounds: change.cells.iter().filter_map(|(_, round)| *round).collect::<BTreeSet<_>>().into_iter().collect(),
        }).collect();
        db.commit().await?;
        let Ok(csrf) = html::page_csrf(headers) else { return Ok(unavailable()); };
        let code = if conflict { StatusCode::CONFLICT } else { StatusCode::OK };
        let response = html::render("checkin_history.html", context! {
            title => format!("{} Check-In History", entity.title()),
            csrf, rows, current, pages, preview, target => filters.before, conflict,
            kind => entity.key(), fragment => entity.fragment(), expected => entity == Entity::Expected,
            team => entity == Entity::Team, nav => Navigation::new(actor, true),
        }, code);
        Ok::<_, sqlx::Error>(html::form_response(response, headers, &csrf))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if !headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.split(';').next() == Some("application/x-www-form-urlencoded"))
    {
        return private((
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "Submit a check-in form.",
        ));
    }
    let form: Vec<_> = url::form_urlencoded::parse(&body).collect();
    let value = |field: &str| {
        form.iter()
            .rev()
            .find(|(key, _)| key == field)
            .map_or("", |(_, value)| value.as_ref())
    };
    if !login::valid_csrf(&headers, value("csrfmiddlewaretoken")) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let action = value("action");
    let Some(entity) = Entity::parse(value("entity_type")) else {
        return answer(false, StatusCode::BAD_REQUEST);
    };
    let result: Result<Response, ChangeError> = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        if action == "undo" || action == "restore" {
            let target = value("change_id");
            let head = if action == "undo" {
                target
            } else {
                value("head")
            };
            data::restore(&mut db, entity, target, Some(head)).await?;
            db.commit().await?;
            return Ok(if action == "restore" || value("return_to") == "checkin" {
                access::found(format!("{PAGE}#{}", entity.fragment()))
            } else {
                answer(true, StatusCode::OK)
            });
        }
        if !matches!(action, "check_in" | "check_out") {
            return Ok(answer(false, StatusCode::BAD_REQUEST));
        }
        let ids: HashSet<_> = form
            .iter()
            .filter(|(key, _)| key == "entity_ids[]")
            .filter_map(|(_, raw)| raw.parse::<i32>().ok().filter(|&id| id > 0))
            .collect();
        let rounds = if entity == Entity::Team {
            BTreeSet::from([None])
        } else {
            let settings = Settings::load(&mut db, SETTINGS).await?;
            let last = settings.integer("tot_rounds", 0);
            form.iter()
                .filter(|(key, _)| key == "rounds[]")
                .filter_map(|(_, raw)| raw.parse::<i32>().ok())
                .filter(|round| (0..=last).contains(round))
                .map(Some)
                .collect()
        };
        if ids.len().saturating_mul(rounds.len()) > 100_000 {
            return Ok(answer(false, StatusCode::BAD_REQUEST));
        }
        let change = if !ids.is_empty() && !rounds.is_empty() {
            data::apply(&mut db, entity, &ids, &rounds, action == "check_in")
                .await?
                .map(Undo::from)
        } else {
            None
        };
        db.commit().await?;
        if value("return_to") == "checkin" {
            return Ok(access::found(format!("{PAGE}#{}", entity.fragment())));
        }
        Ok(if !value("include_undo").is_empty() {
            private(Json(json!({"success": true, "undo": change})))
        } else {
            answer(true, StatusCode::OK)
        })
    }
    .await;
    match result {
        Ok(response) => response,
        Err(ChangeError::Storage) => unavailable(),
        Err(ChangeError::Conflict) if action == "restore" => {
            history(
                &app,
                actor,
                &headers,
                entity,
                Filters {
                    history: None,
                    page: None,
                    before: None,
                },
                true,
            )
            .await
        }
        Err(ChangeError::Conflict) => answer(false, StatusCode::CONFLICT),
    }
}

fn answer(success: bool, code: StatusCode) -> Response {
    private((code, Json(json!({"success": success}))))
}
