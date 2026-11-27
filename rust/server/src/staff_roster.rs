use std::collections::{BTreeMap, HashSet};

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{FromRequest, MatchedPath, Multipart, Path, Request, State},
    http::{HeaderMap, StatusCode, Uri, header},
    response::{IntoResponse, Response},
    routing::get,
};
use minijinja::{Value as TemplateValue, context};
use sqlx::MySqlConnection;

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_html,
    legacy_session::{self, Actor, SessionError},
    login, private,
    registration_form::Fields,
    settings::Settings,
    staff_debater, staff_judge,
    staff_pairing::lock_round,
    staff_rankings, staff_roles, staff_room,
    staff_roster_data::{self as data, Edit, Error, Kind},
    staff_roster_notice, staff_school, staff_team, unavailable,
};

pub fn routes() -> Router<App> {
    let mut router = Router::new();
    for kind in Kind::ALL {
        let name = kind.model();
        router = router
            .route(&format!("/view_{name}s/"), get(list))
            .route(&format!("/enter_{name}/"), get(enter).post(create))
            .route(&format!("/{name}/{{id}}"), get(roster_redirect))
            .route(&format!("/{name}/{{id}}/"), get(view).post(update))
            .route(
                &format!("/{name}/{{id}}/delete/"),
                get(confirm_delete).post(delete),
            );
    }
    router
}

async fn roster_redirect(uri: Uri, Path(_id): Path<i32>) -> Response {
    let mut location = format!("{}/", uri.path());
    if let Some(query) = uri.query() {
        location.push('?');
        location.push_str(query);
    }
    access::found(location)
}

pub async fn dashboard(app: &App, actor: Actor, headers: &HeaderMap) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let school_rows = sqlx::query_as::<_, (i32, String, bool)>(
            "SELECT id, name, apda_id IS NULL OR apda_id = -1 FROM tab_school ORDER BY name",
        )
        .fetch_all(&mut *db)
        .await?;
        let debater_rows = sqlx::query_as::<_, (i32, String, bool)>(
            "SELECT id, name, apda_id IS NULL OR apda_id = -1 FROM tab_debater ORDER BY name",
        )
        .fetch_all(&mut *db)
        .await?;
        let judge_list = sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_judge ORDER BY name")
            .fetch_all(&mut *db)
            .await?;
        let team_list = sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_team ORDER BY name, id")
            .fetch_all(&mut *db)
            .await?;
        let room_list = sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_room ORDER BY name")
            .fetch_all(&mut *db)
            .await?;
        let shell = legacy_html::shell(&mut db, actor, "/").await?;
        db.commit().await?;
        let schools_missing_apda_ids: Vec<_> = school_rows.iter().filter_map(|(id, _, missing)| missing.then_some(*id)).collect();
        let debaters_missing_apda_ids: Vec<_> = debater_rows.iter().filter_map(|(id, _, missing)| missing.then_some(*id)).collect();
        let school_list: Vec<_> = school_rows.into_iter().map(|(id, name, _)| (id, name)).collect();
        let debater_list: Vec<_> = debater_rows.into_iter().map(|(id, name, _)| (id, name)).collect();
        Ok::<_, sqlx::Error>(staff_roster_notice::render(
            app, headers, "/",
            "common/index.html",
            shell,
            context! {
                number_schools => school_list.len(), school_list,
                number_judges => judge_list.len(), judge_list,
                number_teams => team_list.len(), team_list,
                number_debaters => debater_list.len(), debater_list,
                number_rooms => room_list.len(), room_list,
                schools_missing_apda_count => schools_missing_apda_ids.len(), schools_missing_apda_ids,
                debaters_missing_apda_count => debaters_missing_apda_ids.len(), debaters_missing_apda_ids,
            },
            StatusCode::OK,
        ))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

pub async fn list(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    headers: HeaderMap,
) -> Response {
    let Some(kind) = Kind::parse(path.as_str()) else {
        return missing();
    };
    match listing(&app, kind, actor, &headers).await {
        Ok(response) => response,
        Err(_) => unavailable(),
    }
}

async fn listing(
    app: &App,
    kind: Kind,
    actor: Actor,
    headers: &HeaderMap,
) -> Result<Response, sqlx::Error> {
    let mut db = app.db.begin().await?;
    let item_type = kind.model();
    let path = format!("/view_{item_type}s/");
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    let checkins = shell
        .get_attr("can_checkins")
        .is_ok_and(|value| value.is_true());
    let mut filters = Vec::new();
    let mut symbol_text = Vec::new();
    let item_list: Vec<TemplateValue> = match kind {
        Kind::School => {
            sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_school ORDER BY name")
                .fetch_all(&mut *db)
                .await?
                .into_iter()
                .map(|(id, name)| TemplateValue::from_serialize((id, name, 0, "")))
                .collect()
        }
        Kind::Room => {
            filters.push(vec![
                (128, "Room has rank of 0"),
                (256, "Room has rank > 0"),
            ]);
            symbol_text.push(("*", "Room has rank of 0"));
            sqlx::query_as::<_, (i32, String, bool)>(
                "SELECT id, name, `rank` = 0 FROM tab_room ORDER BY name",
            )
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .map(|(id, name, zero_rank)| {
                let (flag, symbol) = if zero_rank { (128, "*") } else { (256, "") };
                TemplateValue::from_serialize((id, name, flag, symbol))
            })
            .collect()
        }
        Kind::Debater => {
            let mut groups = ranking_badges(&mut db, false).await?;
            sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_debater ORDER BY name")
                .fetch_all(&mut *db)
                .await?
                .into_iter()
                .map(|(id, name)| {
                    TemplateValue::from_serialize((
                        id,
                        name,
                        0,
                        "",
                        groups.remove(&id).unwrap_or_default(),
                    ))
                })
                .collect()
        }
        Kind::Team => {
            if checkins {
                filters.push(vec![(1, "Team Checked In"), (2, "Team NOT Checked In")]);
                symbol_text.push(("*", "Team NOT Checked In"));
            }
            let mut groups = ranking_badges(&mut db, true).await?;
            sqlx::query_as::<_, (i32, String, bool)>(
                "SELECT id, name, checked_in FROM tab_team ORDER BY id",
            )
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .map(|(id, name, checked_in)| {
                let (flag, symbol) = match (checkins, checked_in) {
                    (true, true) => (1, ""),
                    (true, false) => (2, "*"),
                    (false, _) => (0, ""),
                };
                TemplateValue::from_serialize((
                    id,
                    name,
                    flag,
                    symbol,
                    groups.remove(&id).unwrap_or_default(),
                ))
            })
            .collect()
        }
        Kind::Judge => {
            if checkins {
                filters.push(vec![
                    (4, "Judge Checked In, Current Round"),
                    (8, "Judge NOT Checked In, Current Round"),
                    (512, "Judge Checked In, Next Round"),
                    (1024, "Judge NOT Checked In, Next Round"),
                ]);
            }
            filters.push(vec![
                (16, "Low Ranked Judge"),
                (32, "Mid Ranked Judge"),
                (64, "High Ranked Judge"),
            ]);
            judge_list(&mut db, checkins).await?
        }
    };
    db.commit().await?;
    Ok(staff_roster_notice::render(
        app,
        headers,
        &path,
        "common/list_data.html",
        shell,
        context! {
            title => format!("Viewing All {}s", kind.singular()), item_type, item_list, filters, symbol_text,
        },
        StatusCode::OK,
    ))
}

async fn ranking_badges(
    db: &mut MySqlConnection,
    teams: bool,
) -> Result<BTreeMap<i32, Vec<String>>, sqlx::Error> {
    let mut groups = BTreeMap::<_, Vec<_>>::new();
    for (_, name, id) in staff_rankings::group_memberships(db, teams).await? {
        groups.entry(id).or_default().push(name);
    }
    Ok(groups)
}

async fn judge_list(
    db: &mut MySqlConnection,
    checkins: bool,
) -> Result<Vec<TemplateValue>, sqlx::Error> {
    let mut checked_in = HashSet::new();
    let mut next = 1;
    if checkins {
        next = Settings::load(db, &["cur_round"])
            .await?
            .integer("cur_round", 1);
        checked_in.extend(
            sqlx::query_as::<_, (i32, i32)>(
                "SELECT judge_id, round_number FROM tab_checkin WHERE round_number IN (?, ?)",
            )
            .bind(next.saturating_sub(1))
            .bind(next)
            .fetch_all(&mut *db)
            .await?,
        );
    }
    let judges = sqlx::query_as::<_, (i32, String, Option<String>, String, i32, bool, bool, bool)>(
        "SELECT id, name, ballot_code, CAST(`rank` AS CHAR),
            CASE WHEN `rank` < 3 THEN 16 WHEN `rank` < 5 THEN 32 ELSE 64 END,
            wing_only, request_wing, no_wings FROM tab_judge ORDER BY `rank` DESC, name",
    )
    .fetch_all(db)
    .await?;
    Ok(judges
        .into_iter()
        .map(
            |(id, name, code, rank, mut flags, wing_only, request_wing, no_wings)| {
                if checkins {
                    flags |= if checked_in.contains(&(id, next.saturating_sub(1))) {
                        4
                    } else {
                        8
                    };
                    flags |= if checked_in.contains(&(id, next)) {
                        512
                    } else {
                        1024
                    };
                }
                let code = format!("({})", code.as_deref().unwrap_or("None"));
                TemplateValue::from_serialize((
                    id,
                    name,
                    flags,
                    code,
                    rank,
                    wing_only,
                    request_wing,
                    no_wings,
                ))
            },
        )
        .collect())
}

pub async fn enter(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    show(
        &app,
        actor,
        &headers,
        Kind::parse(path.as_str()),
        None,
        &form_return_path(&uri, &headers, None),
        None,
    )
    .await
}

pub async fn view(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    show(
        &app,
        actor,
        &headers,
        Kind::parse(path.as_str()),
        Some(id),
        &form_return_path(&uri, &headers, None),
        None,
    )
    .await
}

pub async fn create(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    request: Request,
) -> Response {
    edit(&app, actor, Kind::parse(path.as_str()), None, request).await
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    request: Request,
) -> Response {
    edit(&app, actor, Kind::parse(path.as_str()), Some(id), request).await
}

async fn edit(
    app: &App,
    actor: Actor,
    kind: Option<Kind>,
    id: Option<i32>,
    request: Request,
) -> Response {
    let Some(kind) = kind else {
        return missing();
    };
    let Actor::User { id: user, .. } = actor else {
        return access::access_denied();
    };
    let headers = request.headers().clone();
    let uri = request.uri().clone();
    let fields = match submitted_fields(app, request).await {
        Ok(fields) => fields,
        Err(response) => return *response,
    };
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let return_path = form_return_path(&uri, &headers, Some(&fields));
    match save_edit(app, &headers, kind, id, user, &fields, &return_path).await {
        Ok(response) => response,
        Err(Error::Missing) => missing(),
        Err(_) => unavailable(),
    }
}

pub(super) async fn submitted_fields(app: &App, request: Request) -> Result<Fields, Box<Response>> {
    let multipart = request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .is_some_and(|value| value.trim().eq_ignore_ascii_case("multipart/form-data"));
    if !multipart {
        let bytes = Bytes::from_request(request, app)
            .await
            .map_err(|error| Box::new(error.into_response()))?;
        return Ok(Fields::parse(&bytes));
    }
    let mut parts = Multipart::from_request(request, app)
        .await
        .map_err(|error| Box::new(error.into_response()))?;
    let mut fields = Fields::default();
    while let Some(part) = parts
        .next_field()
        .await
        .map_err(|error| Box::new(error.into_response()))?
    {
        let Some(name) = part
            .name()
            .filter(|_| part.file_name().is_none())
            .map(str::to_owned)
        else {
            return Err(Box::new(private((
                StatusCode::BAD_REQUEST,
                "Expected a named form field.",
            ))));
        };
        fields.push(
            name,
            part.text()
                .await
                .map_err(|error| Box::new(error.into_response()))?,
        );
    }
    Ok(fields)
}

pub(super) fn form_return_path(uri: &Uri, headers: &HeaderMap, fields: Option<&Fields>) -> String {
    form_return_target(uri, headers, fields)
        .or_else(|| {
            uri.path_and_query()
                .and_then(|target| local_form_target(headers, target.as_str()))
        })
        .unwrap_or_else(|| "/".into())
}

pub(super) fn form_return_target(
    uri: &Uri,
    headers: &HeaderMap,
    fields: Option<&Fields>,
) -> Option<String> {
    let query: BTreeMap<_, _> =
        url::form_urlencoded::parse(uri.query().unwrap_or_default().as_bytes()).collect();
    [
        fields.map(|fields| fields.get("return_to")),
        query.get("return_to").map(|target| target.as_ref()),
        query.get("next").map(|target| target.as_ref()),
        headers
            .get(header::REFERER)
            .and_then(|value| value.to_str().ok()),
    ]
    .into_iter()
    .flatten()
    .find_map(|target| local_form_target(headers, target))
}

fn local_form_target(headers: &HeaderMap, target: &str) -> Option<String> {
    let path = login::local_next(target);
    if !path.is_empty() {
        return Some(path.into());
    }
    let uri = target.parse::<Uri>().ok()?;
    let host = headers.get(header::HOST)?.to_str().ok()?;
    if uri.authority()?.as_str() != host
        || !(uri.scheme_str() == Some("https")
            || uri.scheme_str() == Some("http") && !login::secure_request(headers))
    {
        return None;
    }
    let path = login::local_next(uri.path_and_query().map_or("/", |value| value.as_str()));
    (!path.is_empty()).then(|| path.into())
}

async fn save_edit(
    app: &App,
    headers: &HeaderMap,
    kind: Kind,
    id: Option<i32>,
    user: i32,
    fields: &Fields,
    return_path: &str,
) -> Result<Response, Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let Some((current, checkins)) = roster_writer(app, &mut db, headers, user, true).await? else {
        return Ok(access::access_denied());
    };
    let initial_scratches = kind == Kind::Team
        && id.is_none()
        && matches!(
            current,
            Actor::User {
                superuser: true,
                ..
            }
        );
    let scratch_count = if kind == Kind::Team {
        staff_team::validate_post(fields, initial_scratches)
    } else {
        Ok(0)
    };
    let result = async {
        let count = scratch_count?;
        if let Some(id) = id.filter(|_| kind == Kind::Judge) {
            staff_judge::verify_edit(app, &mut db, headers, id, fields).await?;
        }
        let saved = data::save_on(
            &mut db,
            kind,
            Edit {
                id,
                user,
                checkins: checkins && fields.flag("mittab_checkins_present"),
                public_log: true,
                fields,
            },
        )
        .await?;
        Ok((saved, count))
    }
    .await;
    match result {
        Ok((saved, scratch_count)) => {
            db.commit().await?;
            if scratch_count > 0 {
                return Ok(access::found(format!(
                    "/team/{saved}/scratches/add/{scratch_count}"
                )));
            }
            let name = fields.get("name");
            let (target, message) = if id.is_some() {
                // The Django room edit uses "School" in its success message.
                let subject = if kind == Kind::Room {
                    "School"
                } else {
                    kind.singular()
                };
                (
                    return_path,
                    format!("{subject} {name} updated successfully"),
                )
            } else {
                (
                    "/",
                    format!("{} {name} created successfully", kind.singular()),
                )
            };
            Ok(staff_roster_notice::saved(app, headers, target, &message))
        }
        Err(Error::Invalid(error)) => {
            db.rollback().await?;
            Ok(show(
                app,
                current,
                headers,
                Some(kind),
                id,
                return_path,
                Some((fields, error)),
            )
            .await)
        }
        Err(error) => Err(error),
    }
}

async fn roster_writer(
    app: &App,
    db: &mut MySqlConnection,
    headers: &HeaderMap,
    user: i32,
    allow_entry: bool,
) -> Result<Option<(Actor, bool)>, Error> {
    // The caller holds the tournament write lock. Check the session and role again.
    let Some(session) = legacy_session::cookie(headers, "sessionid") else {
        return Ok(None);
    };
    let actor = match legacy_session::actor_on(db, session, app.django_secret.as_deref()).await {
        Ok(
            actor @ Actor::User {
                id, board: false, ..
            },
        ) if id == user => actor,
        Err(SessionError::Database) => return Err(Error::Database),
        _ => return Ok(None),
    };
    let checkins = match actor {
        Actor::User {
            superuser: true, ..
        } => Some(true),
        Actor::User { staff: true, .. } => staff_roles::preset_on(&mut *db, user)
            .await?
            .filter(|preset| preset.data_entry())
            .map(|preset| preset.checkins()),
        _ if allow_entry && access::legacy_entry_user(&mut *db, user).await? => Some(false),
        _ => None,
    };
    Ok(checkins.map(|checkins| (actor, checkins)))
}

async fn show(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: Option<Kind>,
    id: Option<i32>,
    return_path: &str,
    posted: Option<(&Fields, &str)>,
) -> Response {
    let Some(kind) = kind else {
        return missing();
    };
    let result = match kind {
        Kind::School => staff_school::show(app, actor, headers, id, return_path, posted).await,
        Kind::Room => staff_room::show(app, actor, headers, id, return_path, posted).await,
        Kind::Debater => staff_debater::show(app, actor, headers, id, return_path, posted).await,
        Kind::Team => staff_team::show(app, actor, headers, id, return_path, posted).await,
        Kind::Judge => staff_judge::show(app, actor, headers, id, return_path, posted).await,
    };
    match result {
        Ok(response) => response,
        Err(Error::Missing) => missing(),
        Err(_) => unavailable(),
    }
}

pub async fn confirm_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    let Some(kind) = Kind::parse(path.as_str()) else {
        return missing();
    };
    remove_page(&app, actor, &headers, kind, id, None).await
}

async fn remove_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: Kind,
    id: i32,
    error: Option<&str>,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let name = match data::name(&mut db, kind, id).await {
        Ok(Some(name)) => name,
        Ok(None) => return missing(),
        Err(_) => return unavailable(),
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let response = html::render(
        "staff_roster_delete.html",
        context! {
            title => format!("Delete {}", kind.singular()), key => kind.model(), id, name, error, csrf,
            nav => Navigation::new(actor, true),
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(kind) = Kind::parse(path.as_str()) else {
        return missing();
    };
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Actor::User { id: user, .. } = actor else {
        return access::access_denied();
    };
    match delete_entry(&app, &headers, kind, id, user).await {
        Ok(response) => response,
        Err(Error::Missing) => missing(),
        Err(_) => unavailable(),
    }
}

async fn delete_entry(
    app: &App,
    headers: &HeaderMap,
    kind: Kind,
    id: i32,
    user: i32,
) -> Result<Response, Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let Some((actor, _)) = roster_writer(app, &mut db, headers, user, false).await? else {
        return Ok(access::access_denied());
    };
    match data::delete_on(&mut db, kind, id).await {
        Ok(()) => {
            db.commit().await?;
            if matches!(kind, Kind::Debater | Kind::Team | Kind::Judge) {
                Ok(staff_roster_notice::saved(
                    app,
                    headers,
                    "/",
                    &format!("{} deleted successfully", kind.singular()),
                ))
            } else {
                Ok(access::found(format!("/view_{}s/", kind.model())))
            }
        }
        Err(Error::Invalid(error)) => {
            db.rollback().await?;
            Ok(remove_page(app, actor, headers, kind, id, Some(error)).await)
        }
        Err(error) => Err(error),
    }
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Roster entry not found."))
}
