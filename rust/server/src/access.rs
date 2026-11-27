use axum::{
    extract::{MatchedPath, Request, State},
    http::{HeaderMap, Method, StatusCode, header},
    middleware::Next,
    response::Response,
};

use sqlx::MySqlConnection;

use crate::{
    App,
    admin_model_forms::AdminFormProof,
    legacy_session::{self, Actor, SessionError, actor, cookie},
    login, private,
    settings::Settings,
    unavailable,
};

pub async fn public_api(State(app): State<App>, request: Request, next: Next) -> Response {
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(error) => return error.response(),
    };
    match actor {
        Actor::User { board: true, .. } => {
            let Ok(open) = board_access_open(&app).await else {
                return unavailable();
            };
            if open {
                board_denied(&app, request.headers())
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
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(SessionError::Reauthenticate) if request.uri().path() == "/public/login/" => {
            Actor::Anonymous
        }
        Err(error) => return error.response(),
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
                | "/dynamic-media/jsi18n/"
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
                | "/404/"
                | "/500/"
        );
        if !read || !allowed {
            return board_denied(&app, request.headers());
        }
        request.extensions_mut().insert(actor);
        let response = next.run(request).await;
        return if response.status().is_server_error() {
            private((
                StatusCode::INTERNAL_SERVER_ERROR,
                "Unable to complete this request.",
            ))
        } else {
            private(response)
        };
    }
    request.extensions_mut().insert(actor);
    next.run(request).await
}

pub async fn staff_settings(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(error) => return error.response(),
    };
    match actor {
        Actor::Anonymous => {
            let mut target = "/public/login/?next=".to_string();
            target.push_str(request.uri().path());
            return found(target);
        }
        Actor::User { board: true, .. } => match board_access_open(&app).await {
            Ok(true) => return board_denied(&app, request.headers()),
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
        // The report renders its own 403 for authenticated non-staff accounts.
        Actor::User { .. } if request.uri().path() == "/judges/speaker-points/" => {}
        Actor::User { id, .. } => {
            let allowed = if entry_ballot_route(&request) {
                match legacy_entry_user(&app.db, id).await {
                    Ok(true) => Ok(true),
                    Ok(false) => has_settings_permission(&app, id).await,
                    Err(error) => Err(error),
                }
            } else {
                has_settings_permission(&app, id).await
            };
            match allowed {
                Ok(true) => {}
                Ok(false) => return access_denied(),
                Err(_) => return unavailable(),
            }
        }
    }
    request.extensions_mut().insert(actor);
    next.run(request).await
}

pub async fn legacy_entry_user<'db>(
    db: impl sqlx::MySqlExecutor<'db>,
    user: i32,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM auth_user WHERE id = ?
         AND CAST(username AS BINARY) = CAST('entry' AS BINARY)
         AND is_active = TRUE AND is_staff = FALSE AND is_superuser = FALSE)",
    )
    .bind(user)
    .fetch_one(db)
    .await
}

fn entry_ballot_route(request: &Request) -> bool {
    match request
        .extensions()
        .get::<MatchedPath>()
        .map(MatchedPath::as_str)
    {
        Some("/pairings/status/" | "/round/{round}/") => {
            matches!(request.method(), &Method::GET | &Method::HEAD)
        }
        Some("/round/{round}/result/" | "/round/{round}/result/{count}/") => matches!(
            request.method(),
            &Method::GET | &Method::HEAD | &Method::POST
        ),
        _ => false,
    }
}

fn entry_roster_route(request: &Request) -> bool {
    match request
        .extensions()
        .get::<MatchedPath>()
        .map(MatchedPath::as_str)
    {
        Some(
            "/view_teams/" | "/view_debaters/" | "/view_schools/" | "/view_judges/"
            | "/view_rooms/" | "/team/{id}" | "/judge/{id}" | "/room/{id}" | "/debater/{id}"
            | "/school/{id}",
        ) => matches!(request.method(), &Method::GET | &Method::HEAD),
        Some(
            "/enter_team/" | "/team/{id}/" | "/enter_debater/" | "/debater/{id}/"
            | "/enter_school/" | "/school/{id}/" | "/enter_judge/" | "/judge/{id}/"
            | "/enter_room/" | "/room/{id}/",
        ) => matches!(
            request.method(),
            &Method::GET | &Method::HEAD | &Method::POST
        ),
        _ => false,
    }
}

async fn has_settings_permission(app: &App, user: i32) -> Result<bool, sqlx::Error> {
    user_permission(&app.db, user, "tab_settings.can_change").await
}

pub(super) async fn user_permission<'db>(
    db: impl sqlx::MySqlExecutor<'db>,
    user: i32,
    code: &'static str,
) -> Result<bool, sqlx::Error> {
    let allowed = sqlx::query_scalar::<_, i64>(
        "SELECT EXISTS(
            SELECT 1 FROM auth_permission p JOIN django_content_type ct ON ct.id = p.content_type_id
            WHERE ct.app_label = 'tab' AND p.codename = ?
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
    .bind(code)
    .bind(user)
    .bind(user)
    .fetch_one(db)
    .await?;
    Ok(allowed != 0)
}

pub async fn allowed_permission(
    app: &App,
    actor: Actor,
    code: &'static str,
) -> Result<bool, sqlx::Error> {
    match actor {
        Actor::User {
            board: false,
            superuser: true,
            ..
        } => Ok(true),
        Actor::User {
            board: false, id, ..
        } => user_permission(&app.db, id, code).await,
        _ => Ok(false),
    }
}

pub async fn administration(State(app): State<App>, mut request: Request, next: Next) -> Response {
    match admin_actor(&app, request.headers(), request.uri().path()).await {
        Ok(actor) => {
            request.extensions_mut().insert(actor);
            next.run(request).await
        }
        Err(response) => *response,
    }
}

pub async fn admin_actor(
    app: &App,
    headers: &axum::http::HeaderMap,
    path: &str,
) -> Result<Actor, Box<Response>> {
    let actor = actor(&app.db, headers, app.django_secret.as_deref())
        .await
        .map_err(|error| Box::new(error.response()))?;
    let denied = match actor {
        Actor::Anonymous => {
            let path = if path == "/admin/login/" {
                "/admin/"
            } else {
                path
            };
            found(format!("/public/login/?next={path}"))
        }
        Actor::User { board: true, .. } => match board_access_open(app).await {
            Ok(true) => board_denied(app, headers),
            Ok(false) => board_closed(),
            Err(_) => unavailable(),
        },
        Actor::User {
            staff: true,
            superuser: true,
            ..
        } => match wizard_complete(app).await {
            Ok(true) => return Ok(actor),
            Ok(false) => found("/setup/start-tournament/"),
            Err(_) => unavailable(),
        },
        _ => access_denied(),
    };
    Err(Box::new(denied))
}

pub async fn roster_log(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(error) => return error.response(),
    };
    match actor {
        Actor::Anonymous => return found(format!("/public/login/?next={}", request.uri().path())),
        Actor::User { board: true, .. } => {
            return match board_access_open(&app).await {
                Ok(true) => board_denied(&app, request.headers()),
                Ok(false) => board_closed(),
                Err(_) => unavailable(),
            };
        }
        _ => {}
    }
    match allowed_permission(&app, actor, "change_publicrosterchange").await {
        Ok(true) => {}
        Ok(false) => return access_denied(),
        Err(_) => return unavailable(),
    }
    request.extensions_mut().insert(actor);
    next.run(request).await
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

pub async fn board_restricted(app: &App, headers: &HeaderMap) -> Response {
    match board_access_open(app).await {
        Ok(true) => board_denied(app, headers),
        Ok(false) => board_closed(),
        Err(_) => unavailable(),
    }
}

pub enum PortalAccessError {
    Board,
    Session(SessionError),
    Denied,
    Database,
}

impl PortalAccessError {
    pub async fn response(self, app: &App, headers: &HeaderMap) -> Response {
        match self {
            Self::Board => board_restricted(app, headers).await,
            Self::Session(error) => error.response(),
            Self::Denied => access_denied(),
            Self::Database => unavailable(),
        }
    }
}

pub async fn code_portal_actor(
    db: &mut MySqlConnection,
    app: &App,
    original: Actor,
    headers: &HeaderMap,
) -> Result<Actor, PortalAccessError> {
    let current = match cookie(headers, "sessionid") {
        Some(key) => legacy_session::actor_on(db, key, app.django_secret.as_deref())
            .await
            .map_err(PortalAccessError::Session)?,
        None => Actor::Anonymous,
    };
    match (original, current) {
        (_, Actor::User { board: true, .. }) => Err(PortalAccessError::Board),
        (Actor::Anonymous, _) => Ok(current),
        (Actor::User { id, .. }, Actor::User { id: current_id, .. }) if id == current_id => {
            Ok(current)
        }
        _ => Err(PortalAccessError::Denied),
    }
}

pub(super) async fn settings_actor(
    db: &mut MySqlConnection,
    app: &App,
    original: Actor,
    headers: &HeaderMap,
) -> Result<Actor, PortalAccessError> {
    let actor = code_portal_actor(db, app, original, headers).await?;
    match actor {
        Actor::User {
            superuser: true, ..
        } => Ok(actor),
        Actor::User {
            id, staff: false, ..
        } if user_permission(db, id, "tab_settings.can_change")
            .await
            .map_err(|_| PortalAccessError::Database)? =>
        {
            Ok(actor)
        }
        _ => Err(PortalAccessError::Denied),
    }
}

pub(super) async fn data_entry_actor(
    db: &mut MySqlConnection,
    app: &App,
    original: Actor,
    headers: &HeaderMap,
) -> Result<Actor, PortalAccessError> {
    let actor = code_portal_actor(db, app, original, headers).await?;
    match actor {
        Actor::User {
            superuser: true, ..
        } => Ok(actor),
        Actor::User {
            id, staff: true, ..
        } if crate::staff_roles::preset_on(db, id)
            .await
            .map_err(|_| PortalAccessError::Database)?
            .is_some_and(crate::staff_roles::Preset::data_entry) =>
        {
            Ok(actor)
        }
        _ => Err(PortalAccessError::Denied),
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

pub const BOARD_DENIAL_COOKIE: &str = "mittab_board_denial";
pub const BOARD_DENIAL_TEXT: &str = "APDA Board access is limited to APDA tools and public pages.";

fn board_denial_proof(headers: &HeaderMap) -> Option<AdminFormProof> {
    AdminFormProof::for_target(headers, "board access denial", "/403/", ())
}

fn board_denied(app: &App, headers: &HeaderMap) -> Response {
    let mut response = access_denied();
    if let Some(proof) = board_denial_proof(headers) {
        login::set_cookie(
            &mut response,
            BOARD_DENIAL_COOKIE,
            &proof.token(app),
            60,
            login::secure_request(headers),
            true,
        );
    }
    response
}

pub fn board_denial_notice(app: &App, headers: &HeaderMap) -> bool {
    cookie(headers, BOARD_DENIAL_COOKIE)
        .zip(board_denial_proof(headers))
        .is_some_and(|(token, proof)| proof.verified(app, token))
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
    const MILESTONE: &str = "
        SELECT EXISTS(SELECT 1 FROM tab_outround)
           OR (COUNT(*) > 0 AND COUNT(CASE WHEN victor <> 0 THEN 1 END) * 5 >= COUNT(*) * 4)
        FROM tab_round WHERE round_number = ?
    ";
    let open = sqlx::query_scalar::<_, i64>(MILESTONE)
        .bind(total_rounds)
        .fetch_one(db)
        .await?;
    Ok(open != 0)
}

pub async fn board(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(error) => return error.response(),
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
        return board_denied(&app, request.headers());
    }
    request.extensions_mut().insert(actor);
    private(next.run(request).await)
}

pub async fn checkins(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(error) => return error.response(),
    };
    match actor {
        Actor::Anonymous => return found(format!("/public/login/?next={}", request.uri().path())),
        Actor::User { board: true, .. } => match board_access_open(&app).await {
            Ok(true) => return board_denied(&app, request.headers()),
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
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(error) => return error.response(),
    };
    match actor {
        Actor::Anonymous => return found(format!("/public/login/?next={}", request.uri().path())),
        Actor::User { board: true, .. } => match board_access_open(&app).await {
            Ok(true) => return board_denied(&app, request.headers()),
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
        Actor::User { id, .. } if entry_roster_route(&request) => {
            match legacy_entry_user(&app.db, id).await {
                Ok(true) => {}
                Ok(false) => return access_denied(),
                Err(_) => return unavailable(),
            }
        }
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
    let allowed = matches!(request.method(), &Method::GET | &Method::HEAD)
        && matches!(
            route,
            Some(
                "/all_tab_cards/"
                    | "/team/card/{id}/"
                    | "/team/card/{id}/pretty/"
                    | "/archive/black_rod_bundle/"
                    | "/forum_post"
            )
        );
    let actor = match actor(&app.db, request.headers(), app.django_secret.as_deref()).await {
        Ok(actor) => actor,
        Err(error) => return error.response(),
    };
    if matches!(actor, Actor::User { board: true, .. }) {
        match board_access_open(&app).await {
            Ok(true) if allowed => {
                request.extensions_mut().insert(actor);
                return private(next.run(request).await);
            }
            Ok(true) => return board_denied(&app, request.headers()),
            Ok(false) => return board_closed(),
            Err(_) => return unavailable(),
        }
    }
    if !allowed {
        return access_denied();
    }
    staff_settings(State(app), request, next).await
}
