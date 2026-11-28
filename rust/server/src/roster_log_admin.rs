use axum::{
    Extension, Router,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use chrono::{FixedOffset, NaiveDateTime, TimeZone, Utc};
use minijinja::context;
use serde::{Deserialize, Serialize};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_form::Fields,
    roster_log, unavailable,
};

const LIST: &str = "/setup/public-change-log/";
const PAGE_SIZE: u64 = 30;

pub fn routes() -> Router<App> {
    Router::new()
        .route(LIST, get(list))
        .route("/setup/public-change-log/{id}/", get(edit).post(update))
}

#[derive(Deserialize)]
struct Filter {
    kind: Option<String>,
    page: Option<String>,
}

type Row = (i32, String, String, bool, NaiveDateTime);

#[derive(Serialize)]
struct Entry {
    id: i32,
    kind: String,
    message: String,
    involves_debaters: bool,
    local_time: String,
    timestamp: String,
}

impl From<Row> for Entry {
    fn from((id, kind, message, involves_debaters, created_at): Row) -> Self {
        let local = created_at.and_utc().with_timezone(&est());
        Self {
            id,
            kind,
            message,
            involves_debaters,
            local_time: local.format("%Y-%m-%dT%H:%M").to_string(),
            timestamp: local.format("%b %e, %Y %H:%M").to_string(),
        }
    }
}

fn est() -> FixedOffset {
    FixedOffset::west_opt(5 * 3600).expect("valid fixed EST offset")
}

async fn list(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Query(filter): Query<Filter>,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let logging = roster_log::enabled(&mut db).await?;
        let status = sqlx::query_as::<_, (NaiveDateTime, Option<NaiveDateTime>)>(
            "SELECT first_published_at, started_at FROM tab_publicrosterlogstate WHERE id = 1",
        ).fetch_optional(&mut *db).await?;
        let kind = filter.kind.as_deref().filter(|kind| matches!(*kind, "teams" | "judges"));
        let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM tab_publicrosterchange WHERE (? IS NULL OR list_kind = ?)")
            .bind(kind).bind(kind).fetch_one(&mut *db).await?;
        let pages = (count.max(0) as u64).div_ceil(PAGE_SIZE).max(1);
        let page = filter.page.and_then(|value| value.parse::<i64>().ok()).map_or(1, |value| {
            if value < 1 { pages } else { (value as u64).min(pages) }
        });
        let entries: Vec<_> = sqlx::query_as::<_, Row>(
            "SELECT id, list_kind, message, involves_debaters, created_at FROM tab_publicrosterchange
             WHERE (? IS NULL OR list_kind = ?) ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        ).bind(kind).bind(kind).bind(PAGE_SIZE).bind((page - 1) * PAGE_SIZE)
            .fetch_all(&mut *db).await?.into_iter().map(Entry::from).collect();
        db.commit().await?;
        Ok::<_, sqlx::Error>(html::render("roster_log_admin.html", context! {
            title => "Public Change Log", nav => Navigation::new(actor, true), entries, kind,
            page, pages, previous => (page > 1).then_some(page - 1),
            next => (page < pages).then_some(page + 1),
            logging, published => status.is_some(), started => status.and_then(|(_, started)| started).is_some(),
        }, StatusCode::OK))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

async fn edit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    let row = sqlx::query_as::<_, Row>(
        "SELECT id, list_kind, message, involves_debaters, created_at FROM tab_publicrosterchange WHERE id = ?",
    ).bind(id).fetch_optional(&app.db).await;
    let row = match row {
        Ok(Some(row)) => Entry::from(row),
        Ok(None) => return missing(),
        Err(_) => return unavailable(),
    };
    let may_delete =
        match access::allowed_permission(&app, actor, "delete_publicrosterchange").await {
            Ok(allowed) => allowed,
            Err(_) => return unavailable(),
        };
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    let response = html::render(
        "roster_log_admin.html",
        context! {
            title => "Edit Public Change", nav => Navigation::new(actor, true), row, csrf, may_delete,
        },
        StatusCode::OK,
    );
    html::form_response(response, &headers, &csrf)
}

async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let changed = if fields.get("action") == "delete" {
        match access::allowed_permission(&app, actor, "delete_publicrosterchange").await {
            Ok(true) => {}
            Ok(false) => {
                return private((
                    StatusCode::FORBIDDEN,
                    "You cannot delete public change log entries.",
                ));
            }
            Err(_) => return unavailable(),
        };
        sqlx::query("DELETE FROM tab_publicrosterchange WHERE id = ?")
            .bind(id)
            .execute(&app.db)
            .await
    } else {
        let kind = fields.get("list_kind");
        let message = fields.get("message").trim();
        let stamp = fields.get("occurred_at");
        let when = NaiveDateTime::parse_from_str(stamp, "%Y-%m-%dT%H:%M")
            .or_else(|_| NaiveDateTime::parse_from_str(stamp, "%Y-%m-%dT%H:%M:%S"))
            .ok()
            .and_then(|value| est().from_local_datetime(&value).single())
            .map(|value| value.with_timezone(&Utc).naive_utc());
        if !matches!(kind, "teams" | "judges") || message.is_empty() || when.is_none() {
            return private((
                StatusCode::UNPROCESSABLE_ENTITY,
                "Choose a public list and enter a message and valid date.",
            ));
        }
        let hidden = fields.flag("involves_debaters");
        // A valid repeat submission also succeeds when MySQL reports zero changed rows.
        let mut db = match app.db.begin().await {
            Ok(db) => db,
            Err(_) => return unavailable(),
        };
        match sqlx::query_scalar::<_, i32>(
            "SELECT id FROM tab_publicrosterchange WHERE id = ? FOR UPDATE",
        )
        .bind(id)
        .fetch_optional(&mut *db)
        .await
        {
            Ok(Some(_)) => {}
            Ok(None) => return missing(),
            Err(_) => return unavailable(),
        }
        let result = sqlx::query(
            "UPDATE tab_publicrosterchange SET list_kind = ?, message = ?, involves_debaters = ?, created_at = ?
             WHERE id = ?",
        ).bind(kind).bind(message).bind(hidden).bind(when).bind(id).execute(&mut *db).await;
        return if result.is_ok() && db.commit().await.is_ok() {
            access::found(LIST)
        } else {
            unavailable()
        };
    };
    match changed {
        Ok(result) if result.rows_affected() == 1 => access::found(LIST),
        Ok(_) => missing(),
        Err(_) => unavailable(),
    }
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Public change log entry not found."))
}
