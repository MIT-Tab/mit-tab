use axum::{
    extract::{MatchedPath, Request, State},
    http::{Method, StatusCode, header},
    middleware::Next,
    response::Response,
};

use sqlx::MySqlConnection;

use crate::{
    App,
    legacy_session::{Actor, actor},
    private,
    settings::Settings,
    unavailable,
};

pub async fn public_api(State(app): State<App>, request: Request, next: Next) -> Response {
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    match actor {
        Actor::User { board: true, .. } => {
            let Ok(open) = board_access_open(&app).await else {
                return unavailable();
            };
            if open {
                access_denied()
            } else {
                private((
                    StatusCode::FORBIDDEN,
                    "Board access opens near the end of inrounds.",
                ))
            }
        }
        Actor::User {
            staff: true,
            superuser: false,
            ..
        } => access_denied(),
        Actor::User {
            superuser: true, ..
        } => {
            let Ok(completed) = wizard_complete(&app).await else {
                return unavailable();
            };
            if completed {
                next.run(request).await
            } else {
                private((
                    StatusCode::FOUND,
                    [(header::LOCATION, "/setup/start-tournament/")],
                ))
            }
        }
        _ => next.run(request).await,
    }
}

pub async fn public_tournament(
    State(app): State<App>,
    mut request: Request,
    next: Next,
) -> Response {
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    if matches!(actor, Actor::User { board: true, .. }) {
        let Ok(open) = board_access_open(&app).await else {
            return unavailable();
        };
        if !open {
            return board_closed();
        }
        let read = matches!(request.method(), &Method::GET | &Method::HEAD);
        let allowed = matches!(
            request.uri().path(),
            "/" | "/public/"
                | "/favicon.ico"
                | "/tournament-logo/"
                | "/public/motions/"
                | "/public/pairings/"
                | "/public/missing-ballots/"
                | "/public/team-rankings/"
                | "/public/speaker-rankings/"
                | "/public/ballots/"
                | "/public/outrounds/0/"
                | "/public/outrounds/1/"
                | "/public/teams/"
                | "/public/judges/"
                | "/public/teams/expected-records/"
                | "/403/"
        );
        if !read || !allowed {
            return access_denied();
        }
    }
    request.extensions_mut().insert(actor);
    next.run(request).await
}

pub async fn staff_settings(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    match actor {
        Actor::Anonymous => {
            let mut target = "/public/login/?next=".to_string();
            target.push_str(request.uri().path());
            return found(target);
        }
        Actor::User { board: true, .. } => match board_access_open(&app).await {
            Ok(true) => return access_denied(),
            Ok(false) => return board_closed(),
            Err(_) => return unavailable(),
        },
        Actor::User {
            superuser: true, ..
        } if request.uri().path() != "/setup/start-tournament/" => {
            match wizard_complete(&app).await {
                Ok(true) => {}
                Ok(false) => return found("/setup/start-tournament/"),
                Err(_) => return unavailable(),
            }
        }
        Actor::User {
            superuser: true, ..
        } => {}
        Actor::User { staff: true, .. } => return access_denied(),
        Actor::User { id, .. } => match has_settings_permission(&app, id).await {
            Ok(true) => {}
            Ok(false) => return access_denied(),
            Err(_) => return unavailable(),
        },
    }
    request.extensions_mut().insert(actor);
    next.run(request).await
}

async fn has_settings_permission(app: &App, user: i32) -> Result<bool, sqlx::Error> {
    let allowed = sqlx::query_scalar::<_, i64>(
        "SELECT EXISTS(
            SELECT 1 FROM auth_permission p JOIN django_content_type ct ON ct.id = p.content_type_id
            WHERE ct.app_label = 'tab' AND p.codename = 'tab_settings.can_change'
              AND (
                  EXISTS(SELECT 1 FROM auth_user_user_permissions up
                         WHERE up.user_id = ? AND up.permission_id = p.id)
                  OR EXISTS(
                      SELECT 1 FROM auth_group_permissions gp
                      JOIN auth_user_groups ug ON ug.group_id = gp.group_id
                      WHERE ug.user_id = ? AND gp.permission_id = p.id
                  )
              )
        )",
    )
    .bind(user)
    .bind(user)
    .fetch_one(&app.db)
    .await?;
    Ok(allowed != 0)
}

pub fn found(target: impl AsRef<str>) -> Response {
    match axum::http::HeaderValue::from_str(target.as_ref()) {
        Ok(target) => private((StatusCode::FOUND, [(header::LOCATION, target)])),
        Err(_) => unavailable(),
    }
}

fn board_closed() -> Response {
    private((
        StatusCode::FORBIDDEN,
        "Board access opens near the end of inrounds.",
    ))
}

pub async fn board_restricted(app: &App) -> Response {
    match board_access_open(app).await {
        Ok(true) => access_denied(),
        Ok(false) => board_closed(),
        Err(_) => unavailable(),
    }
}

pub async fn wizard_complete(app: &App) -> Result<bool, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    let settings = Settings::load(&mut db, &["start_tournament_wizard_completed"]).await?;
    let completed = settings.string("start_tournament_wizard_completed", "1");
    Ok(matches!(
        completed.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    ))
}

pub fn access_denied() -> Response {
    private((StatusCode::FOUND, [(header::LOCATION, "/403/")]))
}

pub async fn board_access_open(app: &App) -> Result<bool, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    board_access_open_on(&mut db).await
}

pub async fn board_access_open_on(db: &mut MySqlConnection) -> Result<bool, sqlx::Error> {
    let total_rounds = sqlx::query_as::<_, (Option<i32>, Option<String>)>(
        "SELECT value, value_string FROM tab_tabsettings WHERE `key` = 'tot_rounds' ORDER BY id LIMIT 1",
    ).fetch_optional(&mut *db).await?
        .and_then(|(value, text)| match text {
            Some(text) => text.trim().parse().ok(),
            None => value,
        })
        .filter(|&rounds| rounds > 0);
    let Some(final_round) = total_rounds else {
        return Ok(false);
    };
    const MILESTONE: &str = "
        SELECT EXISTS(SELECT 1 FROM tab_outround)
           OR (COUNT(*) > 0 AND COUNT(CASE WHEN victor <> 0 THEN 1 END) * 5 >= COUNT(*) * 4)
        FROM tab_round WHERE round_number = ?
    ";
    let open = sqlx::query_scalar::<_, i64>(MILESTONE)
        .bind(final_round)
        .fetch_one(db)
        .await?;
    Ok(open != 0)
}

pub async fn board(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    if !matches!(actor, Actor::User { board: true, .. }) {
        return private((StatusCode::FORBIDDEN, "Board access is unavailable."));
    }
    match board_access_open(&app).await {
        Ok(true) => {}
        Ok(false) => return board_closed(),
        Err(_) => return unavailable(),
    }
    let route = request
        .extensions()
        .get::<MatchedPath>()
        .map(MatchedPath::as_str);
    let allowed = matches!(
        (request.method(), route),
        (
            &Method::GET | &Method::HEAD,
            Some(
                "/apda-board/"
                    | "/apda-board/inrounds/"
                    | "/apda-board/inrounds/{round}/"
                    | "/apda-board/outrounds/"
                    | "/apda-board/judges/"
                    | "/apda-board/schools/export/"
                    | "/apda-board/debaters/export/"
                    | "/apda-board/tab-cards/"
                    | "/apda-board/tab-card/{id}/"
                    | "/apda-board/school/{id}/"
                    | "/apda-board/debater/{id}/"
            )
        ) | (
            &Method::POST,
            Some(
                "/apda-board/outrounds/pair/"
                    | "/apda-board/outround/{id}/"
                    | "/apda-board/judges/"
                    | "/apda-board/schools/import/"
                    | "/apda-board/debaters/import/"
                    | "/apda-board/school/{id}/"
                    | "/apda-board/debater/{id}/"
            )
        )
    );
    if !allowed {
        return access_denied();
    }
    request.extensions_mut().insert(actor);
    private(next.run(request).await)
}

pub async fn checkins(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    match actor {
        Actor::Anonymous => return found(format!("/public/login/?next={}", request.uri().path())),
        Actor::User { board: true, .. } => match board_access_open(&app).await {
            Ok(true) => return access_denied(),
            Ok(false) => return board_closed(),
            Err(_) => return unavailable(),
        },
        Actor::User {
            superuser: true, ..
        } => match wizard_complete(&app).await {
            Ok(true) => {}
            Ok(false) => return found("/setup/start-tournament/"),
            Err(_) => return unavailable(),
        },
        Actor::User {
            id, staff: true, ..
        } => match crate::staff_roles::preset(&app.db, id).await {
            Ok(Some(preset)) if preset.checkins() => {}
            Ok(_) => return access_denied(),
            Err(_) => return unavailable(),
        },
        Actor::User { .. } => return access_denied(),
    }
    let allowed = matches!(
        (request.method(), request.uri().path()),
        (&Method::GET | &Method::HEAD, "/batch_checkin/") | (&Method::POST, "/bulk_check_in/")
    );
    if !allowed {
        return access_denied();
    }
    request.extensions_mut().insert(actor);
    next.run(request).await
}

/// The router that uses this middleware contains only roster data entry routes.
pub async fn data_entry(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    match actor {
        Actor::Anonymous => return found(format!("/public/login/?next={}", request.uri().path())),
        Actor::User { board: true, .. } => match board_access_open(&app).await {
            Ok(true) => return access_denied(),
            Ok(false) => return board_closed(),
            Err(_) => return unavailable(),
        },
        Actor::User {
            superuser: true, ..
        } => match wizard_complete(&app).await {
            Ok(true) => {}
            Ok(false) => return found("/setup/start-tournament/"),
            Err(_) => return unavailable(),
        },
        Actor::User {
            id, staff: true, ..
        } => match crate::staff_roles::preset(&app.db, id).await {
            Ok(Some(preset)) if preset.data_entry() => {}
            Ok(_) => return access_denied(),
            Err(_) => return unavailable(),
        },
        Actor::User { .. } => return access_denied(),
    }
    request.extensions_mut().insert(actor);
    next.run(request).await
}

pub async fn staff_or_board(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let route = request
        .extensions()
        .get::<MatchedPath>()
        .map(MatchedPath::as_str);
    if !matches!(request.method(), &Method::GET | &Method::HEAD)
        || !matches!(
            route,
            Some(
                "/all_tab_cards/"
                    | "/team/card/{id}/"
                    | "/team/card/{id}/pretty/"
                    | "/archive/black_rod_bundle/"
            )
        )
    {
        return access_denied();
    }
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    if matches!(actor, Actor::User { board: true, .. }) {
        match board_access_open(&app).await {
            Ok(true) => {
                request.extensions_mut().insert(actor);
                return private(next.run(request).await);
            }
            Ok(false) => return board_closed(),
            Err(_) => return unavailable(),
        }
    }
    staff_settings(State(app), request, next).await
}
