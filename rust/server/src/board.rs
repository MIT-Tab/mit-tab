use std::collections::HashMap;

use axum::{
    Extension, Form,
    extract::{Path, Query, State, rejection::FormRejection},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::context;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    board_snapshot::{self, Histories, History, Pairings, Snapshot},
    html::{self, Navigation},
    legacy_session::Actor,
    login, private, unavailable,
};

pub enum BoardError {
    Closed,
    Conflict(&'static str),
    Missing,
    Storage,
}

impl From<sqlx::Error> for BoardError {
    fn from(_: sqlx::Error) -> Self {
        Self::Storage
    }
}

pub fn respond_error(actor: Actor, error: BoardError, back: &'static str) -> Response {
    match error {
        BoardError::Storage => unavailable(),
        BoardError::Missing => private((StatusCode::NOT_FOUND, "Item not found.")),
        BoardError::Closed => private((StatusCode::FORBIDDEN, "Board access is unavailable.")),
        BoardError::Conflict(message) => html::render(
            "error.html",
            context! {
                title => "Change unavailable", heading => message, back, nav => Navigation::new(actor, true),
            },
            StatusCode::CONFLICT,
        ),
    }
}

pub fn csrf_valid(headers: &HeaderMap, token: &str) -> bool {
    login::valid_csrf(headers, token)
}

pub fn csrf_error() -> Response {
    private((StatusCode::FORBIDDEN, "CSRF verification failed."))
}

pub async fn lock_board(db: &mut MySqlConnection, actor: Actor) -> Result<i32, BoardError> {
    let Actor::User {
        id, board: true, ..
    } = actor
    else {
        return Err(BoardError::Closed);
    };
    sqlx::query("SELECT id FROM tab_tabsettings WHERE `key` IN ('tot_rounds','cur_round') ORDER BY id FOR UPDATE")
        .fetch_all(&mut *db).await?;
    let member = sqlx::query_scalar::<_, i64>(
        "SELECT EXISTS(SELECT 1 FROM auth_user u
              JOIN auth_user_groups ug ON ug.user_id = u.id JOIN auth_group g ON g.id = ug.group_id
              WHERE u.id = ? AND u.is_active = TRUE AND g.name = 'APDA Board')",
    )
    .bind(id)
    .fetch_one(&mut *db)
    .await?;
    if member == 0 || !access::board_access_open_on(db).await? {
        return Err(BoardError::Closed);
    }
    Ok(id)
}

#[derive(Serialize)]
struct MissingCounts {
    schools: i64,
    debaters: i64,
}

pub async fn home(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    let Ok((schools, debaters)) = sqlx::query_as::<_, (i64, i64)>(
        "SELECT (SELECT COUNT(*) FROM tab_school WHERE apda_id IS NULL OR apda_id = -1),
                (SELECT COUNT(*) FROM tab_debater WHERE apda_id IS NULL OR apda_id = -1)",
    )
    .fetch_one(&app.db)
    .await
    else {
        return unavailable();
    };
    html::render(
        "board_home.html",
        context! {
            title => "APDA Board",
            missing => MissingCounts { schools, debaters },
            nav => Navigation::new(actor, true),
        },
        StatusCode::OK,
    )
}

#[derive(Deserialize)]
pub struct InroundFilter {
    manual: Option<String>,
}

#[derive(Serialize)]
struct Display<'a> {
    id: i32,
    data: &'a Snapshot,
    outcome: &'static str,
    history: Option<&'a Vec<History>>,
}

fn display<'a>(id: i32, snapshot: &'a Snapshot, history: &'a Histories) -> Display<'a> {
    Display {
        id,
        data: snapshot,
        outcome: board_snapshot::outcome(snapshot.victor.unwrap_or(0)),
        history: history.retained.get(&id),
    }
}

pub async fn inrounds(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Query(filter): Query<InroundFilter>,
) -> Response {
    inround_page(&app, actor, None, filter).await
}

pub async fn inround(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(round): Path<i32>,
    Query(filter): Query<InroundFilter>,
) -> Response {
    inround_page(&app, actor, Some(round), filter).await
}

async fn inround_page(
    app: &App,
    actor: Actor,
    selected: Option<i32>,
    filter: InroundFilter,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let history = board_snapshot::histories(&mut db, "round").await?;
        let manual = filter.manual.as_deref() == Some("1");
        let numbers = if manual {
            let mut numbers: Vec<_> = sqlx::query_as::<_, (i32, i32)>(
                "SELECT id, round_number FROM tab_round ORDER BY round_number, id",
            )
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .filter_map(|(id, number)| history.retained.contains_key(&id).then_some(number))
            .collect();
            numbers.extend(
                history
                    .removed
                    .values()
                    .filter_map(|snapshot| snapshot.round_number),
            );
            numbers.sort_unstable();
            numbers.dedup();
            numbers
        } else {
            sqlx::query_scalar::<_, i32>(
                "SELECT DISTINCT round_number FROM tab_round ORDER BY round_number",
            )
            .fetch_all(&mut *db)
            .await?
        };
        let selected = selected
            .filter(|round| !manual || numbers.contains(round))
            .or_else(|| numbers.last().copied());
        let rows = if let Some(round) = selected {
            board_snapshot::capture(&mut db, Pairings::Inround(round)).await?
        } else {
            Default::default()
        };
        db.commit().await?;
        let removed: Vec<_> = history
            .removed
            .iter()
            .filter(|(id, snapshot)| {
                selected.is_some() && snapshot.round_number == selected && !rows.contains_key(id)
            })
            .map(|(&id, snapshot)| display(id, snapshot, &history))
            .collect();
        let rows: Vec<_> = rows
            .iter()
            .filter(|(id, _)| !manual || history.retained.contains_key(id))
            .map(|(&id, snapshot)| display(id, snapshot, &history))
            .collect();
        Ok::<_, sqlx::Error>(html::render(
            "board_inrounds.html",
            context! {
                title => "Inrounds", round => selected, rows, removed, numbers, manual,
                nav => Navigation::new(actor, true),
            },
            StatusCode::OK,
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Serialize)]
struct Judge<'a> {
    id: i32,
    name: String,
    is_dino: bool,
    history: Option<&'a Vec<DinoChange>>,
}

#[derive(Serialize)]
struct DinoChange {
    actor: String,
    time: String,
    before: bool,
    after: bool,
}

const JUDGES: &str = "/apda-board/judges/";

pub async fn judges(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let mut history: HashMap<i32, Vec<DinoChange>> = HashMap::new();
        for (id, actor, time, before, after) in
            sqlx::query_as::<_, (i32, String, NaiveDateTime, String, String)>(
                "SELECT CAST(a.object_id AS SIGNED), COALESCE(u.username, 'Unknown'), a.created_at,
                    JSON_UNQUOTE(JSON_EXTRACT(a.changes, '$.is_dino.before')),
                    JSON_UNQUOTE(JSON_EXTRACT(a.changes, '$.is_dino.after'))
             FROM tab_auditevent a JOIN django_content_type t ON t.id = a.content_type_id
             LEFT JOIN auth_user u ON u.id = a.user_id
             WHERE t.app_label = 'tab' AND t.model = 'judge'
                 AND JSON_TYPE(JSON_EXTRACT(a.changes, '$.is_dino.before')) = 'BOOLEAN'
                 AND JSON_TYPE(JSON_EXTRACT(a.changes, '$.is_dino.after')) = 'BOOLEAN'
             ORDER BY a.created_at DESC, a.id DESC",
            )
            .fetch_all(&mut *db)
            .await?
        {
            history.entry(id).or_default().push(DinoChange {
                actor,
                time: time.format("%b %d %H:%M UTC").to_string(),
                before: before == "true",
                after: after == "true",
            });
        }
        let judges: Vec<_> = sqlx::query_as::<_, (i32, String, bool)>(
            "SELECT id, name, is_dino FROM tab_judge ORDER BY name, id",
        )
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(|(id, name, is_dino)| Judge {
            id,
            name,
            is_dino,
            history: history.get(&id),
        })
        .collect();
        db.commit().await?;
        let Ok(csrf) = html::page_csrf(&headers) else {
            return Ok(unavailable());
        };
        let response = html::render(
            "board_judges.html",
            context! {
                title => "Judges", judges, csrf, nav => Navigation::new(actor, true),
            },
            StatusCode::OK,
        );
        Ok::<_, sqlx::Error>(html::form_response(response, &headers, &csrf))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JudgeForm {
    csrfmiddlewaretoken: String,
    judge: i32,
    is_dino: Option<String>,
}

pub async fn update_judge(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    form: Result<Form<JudgeForm>, FormRejection>,
) -> Response {
    let Ok(Form(form)) = form else {
        return crate::board::invalid_form();
    };
    if !csrf_valid(&headers, &form.csrfmiddlewaretoken) {
        return csrf_error();
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let user = lock_board(&mut db, actor).await?;
        let Some((name, before)) = sqlx::query_as::<_, (String, bool)>(
            "SELECT name, is_dino FROM tab_judge WHERE id = ? FOR UPDATE",
        )
        .bind(form.judge)
        .fetch_optional(&mut *db)
        .await?
        else {
            return Err(BoardError::Missing);
        };
        let after = form.is_dino.as_deref() == Some("on");
        if before != after {
            sqlx::query("UPDATE tab_judge SET is_dino = ? WHERE id = ?")
                .bind(after)
                .bind(form.judge)
                .execute(&mut *db)
                .await?;
            board_snapshot::record(
                &mut db,
                "judge",
                form.judge,
                user,
                name,
                json!({"is_dino": {"before": before, "after": after}}),
                false,
            )
            .await?;
        }
        db.commit().await?;
        Ok(())
    }
    .await;
    match result {
        Ok(()) => access::found(JUDGES),
        Err(error) => respond_error(actor, error, JUDGES),
    }
}

pub fn invalid_form() -> Response {
    private((StatusCode::UNPROCESSABLE_ENTITY, "Invalid form submission."))
}
