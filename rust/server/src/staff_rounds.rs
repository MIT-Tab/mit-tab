use axum::{
    Extension, Json, Router,
    body::Bytes,
    extract::{MatchedPath, Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::{get, post},
};
use minijinja::context;
use serde_json::{Value, json};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_form::Fields,
    staff_round_choices as choices,
    staff_round_store::{self as store, Adjustment, Debate, Error},
    unavailable,
};

pub fn routes() -> Router<App> {
    let mut router = Router::new();
    for round in ["round", "outround"] {
        let base = format!("/{round}/{{id}}/");
        router = router
            .route(&format!("{base}edit/"), get(editor).post(save))
            .route(&format!("{base}assign_room/{{target}}/"), post(room))
            .route(&format!("{base}assign_judge/{{target}}/"), post(judge))
            .route(
                &format!("{base}assign_judge/{{target}}/{{replace}}/"),
                post(replace),
            )
            .route(&format!("{base}remove_judge/{{target}}/"), post(remove))
            .route(&format!("{base}assign_chair/{{target}}/"), post(chair))
            .route(&format!("{base}switch_sides/"), post(sides))
            .route(&format!("{base}alternative_judges/"), get(alts))
            .route(
                &format!("{base}alternative_judges/{{current}}/"),
                get(alts_fixed),
            )
            .route(&format!("{base}alternative_rooms/"), get(alts))
            .route(
                &format!("{base}alternative_rooms/{{current}}/"),
                get(alts_fixed),
            )
            .route(
                &format!("{base}{{current}}/alternative_teams/{{side}}/"),
                get(alts_teams),
            );
    }
    router
        .route("/pairings/assign_team/{id}/{side}/{team}/", post(team))
        .route(
            "/outround/pairings/assign_team/{id}/{side}/{team}/",
            post(team),
        )
}

async fn editor(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    editor_page(
        &app,
        actor,
        Debate::route(path.as_str()),
        id,
        &headers,
        None,
    )
    .await
}

async fn editor_page(
    app: &App,
    actor: Actor,
    kind: Debate,
    id: i32,
    headers: &HeaderMap,
    error: Option<&str>,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let detail = match store::load(&mut db, kind, id).await {
        Ok(Some(detail)) => detail,
        Ok(None) => return error_response(Error::Missing),
        Err(_) => return unavailable(),
    };
    let Ok(options) = choices::all(&mut db, kind, &detail).await else {
        return unavailable();
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let revision = detail.snapshot.revision();
    let title = format!(
        "Edit {} {}",
        if kind == Debate::Outround {
            "Outround Ro"
        } else {
            "Round"
        },
        detail.stage
    );
    let edit = html::render(
        "staff_round_edit.html",
        context! {
            title, nav => Navigation::new(actor, true), csrf, revision, pairing => detail.snapshot,
            options, error, id, out => kind == Debate::Outround,
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(edit, headers, &csrf)
}

async fn save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let kind = Debate::route(path.as_str());
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Actor::User {
        id: user,
        board: false,
        ..
    } = actor
    else {
        return access::access_denied();
    };
    let target = fields
        .get("target")
        .parse::<i32>()
        .ok()
        .filter(|target| *target > 0);
    let option = match fields.get("action") {
        "government" => target.map(|team| Adjustment::Team {
            government: true,
            team,
        }),
        "opposition" => target.map(|team| Adjustment::Team {
            government: false,
            team,
        }),
        "room" => target.map(Adjustment::Room),
        "chair" => target.map(Adjustment::Chair),
        "remove" => target.map(Adjustment::Remove),
        "judge" => target.map(|judge| Adjustment::Assign {
            judge,
            replace: fields.get("replace").parse().ok(),
        }),
        "swap" => Some(Adjustment::SwapSides),
        _ => None,
    };
    let Some(option) = option else {
        return editor_page(
            &app,
            actor,
            kind,
            id,
            &headers,
            Some("Choose an assignment to save."),
        )
        .await;
    };
    match store::adjust(&app.db, user, kind, id, option, fields.get("revision")).await {
        Ok(_) => access::found(format!("/{}/{id}/edit/", kind.model())),
        Err(Error::Invalid(error)) => {
            editor_page(&app, actor, kind, id, &headers, Some(error)).await
        }
        Err(error) => error_response(error),
    }
}

fn error_response(error: Error) -> Response {
    match error {
        Error::Missing => json_response(
            StatusCode::NOT_FOUND,
            json!({"success": false, "error": "Pairing or roster entry not found."}),
        ),
        Error::Invalid(error) => json_response(
            StatusCode::UNPROCESSABLE_ENTITY,
            json!({"success": false, "error": error}),
        ),
        Error::Database => unavailable(),
    }
}

fn json_response(status: StatusCode, payload: Value) -> Response {
    private((status, Json(payload)))
}

async fn action(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    data: (Debate, i32, Adjustment),
    body: &[u8],
) -> Response {
    let fields = Fields::parse(body);
    let token = headers
        .get("x-csrftoken")
        .and_then(|token| token.to_str().ok())
        .unwrap_or_else(|| fields.get("csrfmiddlewaretoken"));
    if !login::valid_csrf(headers, token) {
        return login::csrf_error();
    }
    let Actor::User {
        id: user,
        board: false,
        ..
    } = actor
    else {
        return access::access_denied();
    };
    let (kind, id, operation) = data;
    match store::adjust(&app.db, user, kind, id, operation, fields.get("revision")).await {
        Ok(result) => json_response(StatusCode::OK, result),
        Err(error) => error_response(error),
    }
}

async fn team(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, side, team)): Path<(i32, String, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let government = match side.as_str() {
        "gov" => true,
        "opp" => false,
        _ => return error_response(Error::Invalid("Choose a side.")),
    };
    action(
        &app,
        actor,
        &headers,
        (
            Debate::route(path.as_str()),
            id,
            Adjustment::Team { government, team },
        ),
        &body,
    )
    .await
}

async fn room(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, room)): Path<(i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    action(
        &app,
        actor,
        &headers,
        (Debate::route(path.as_str()), id, Adjustment::Room(room)),
        &body,
    )
    .await
}

async fn judge(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, judge)): Path<(i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    action(
        &app,
        actor,
        &headers,
        (
            Debate::route(path.as_str()),
            id,
            Adjustment::Assign {
                judge,
                replace: None,
            },
        ),
        &body,
    )
    .await
}

async fn replace(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, judge, previous)): Path<(i32, i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    action(
        &app,
        actor,
        &headers,
        (
            Debate::route(path.as_str()),
            id,
            Adjustment::Assign {
                judge,
                replace: Some(previous),
            },
        ),
        &body,
    )
    .await
}

async fn remove(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, judge)): Path<(i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    action(
        &app,
        actor,
        &headers,
        (Debate::route(path.as_str()), id, Adjustment::Remove(judge)),
        &body,
    )
    .await
}

async fn chair(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, judge)): Path<(i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    action(
        &app,
        actor,
        &headers,
        (Debate::route(path.as_str()), id, Adjustment::Chair(judge)),
        &body,
    )
    .await
}

async fn sides(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    action(
        &app,
        actor,
        &headers,
        (Debate::route(path.as_str()), id, Adjustment::SwapSides),
        &body,
    )
    .await
}

async fn alts(State(app): State<App>, path: MatchedPath, Path(id): Path<i32>) -> Response {
    alternatives(&app, path.as_str(), id, None).await
}

async fn alts_fixed(
    State(app): State<App>,
    path: MatchedPath,
    Path((id, _)): Path<(i32, i32)>,
) -> Response {
    alternatives(&app, path.as_str(), id, None).await
}

async fn alts_teams(
    State(app): State<App>,
    path: MatchedPath,
    Path((id, _, side)): Path<(i32, i32, String)>,
) -> Response {
    let side = match side.as_str() {
        "gov" => true,
        "opp" => false,
        _ => return error_response(Error::Invalid("Choose a side.")),
    };
    alternatives(&app, path.as_str(), id, Some(side)).await
}

async fn alternatives(app: &App, path: &str, id: i32, side: Option<bool>) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let kind = Debate::route(path);
    let detail = match store::load(&mut db, kind, id).await {
        Ok(Some(detail)) => detail,
        Ok(None) => return error_response(Error::Missing),
        Err(_) => return unavailable(),
    };
    let options = if let Some(side) = side {
        choices::teams(&mut db, kind, &detail, side).await
    } else if path.contains("alternative_judges") {
        choices::judges(&mut db, kind, &detail).await
    } else {
        choices::rooms(&mut db, kind, &detail).await
    };
    match options {
        Ok(options) => html::render(
            "staff_round_choices.html",
            context! { options },
            StatusCode::OK,
        ),
        Err(_) => unavailable(),
    }
}
