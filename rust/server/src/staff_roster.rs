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
use serde::Serialize;
use serde_json::Value;
use sqlx::{AssertSqlSafe, MySqlConnection};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_html,
    legacy_session::{self, Actor, SessionError},
    login, private,
    registration_form::Fields,
    settings::Settings,
    staff_pairing::lock_round,
    staff_rankings, staff_roles, staff_room,
    staff_roster_data::{self as data, Edit, Error, Kind},
    staff_roster_notice, staff_school, unavailable,
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

fn is_tab(actor: Actor) -> bool {
    matches!(
        actor,
        Actor::User {
            board: false,
            superuser: true,
            ..
        }
    )
}

pub fn toolbar() -> Vec<(String, String)> {
    Kind::ALL
        .into_iter()
        .map(|kind| {
            (
                format!("/view_{}s/", kind.model()),
                format!("{}s", kind.singular()),
            )
        })
        .collect()
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

async fn submitted_fields(app: &App, request: Request) -> Result<Fields, Box<Response>> {
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

fn form_return_path(uri: &Uri, headers: &HeaderMap, fields: Option<&Fields>) -> String {
    let query: BTreeMap<_, _> =
        url::form_urlencoded::parse(uri.query().unwrap_or_default().as_bytes()).collect();
    [
        fields.map(|fields| fields.get("return_to")),
        query.get("return_to").map(|target| target.as_ref()),
        query.get("next").map(|target| target.as_ref()),
        headers
            .get(header::REFERER)
            .and_then(|value| value.to_str().ok()),
        uri.path_and_query().map(|value| value.as_str()),
    ]
    .into_iter()
    .flatten()
    .find_map(|target| local_form_target(headers, target))
    .unwrap_or_else(|| "/".into())
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
    let Some(session) = legacy_session::cookie(headers, "sessionid") else {
        return Ok(access::access_denied());
    };
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    // Refresh authorization after waiting for the tournament write lock.
    let current =
        match legacy_session::actor_on(&mut db, session, app.django_secret.as_deref()).await {
            Ok(
                actor @ Actor::User {
                    id, board: false, ..
                },
            ) if id == user => actor,
            Err(SessionError::Database) => return Err(Error::Database),
            _ => return Ok(access::access_denied()),
        };
    let checkins = match current {
        Actor::User {
            superuser: true, ..
        } => true,
        Actor::User { staff: true, .. } => {
            let Some(preset) = staff_roles::preset_on(&mut *db, user)
                .await?
                .filter(|preset| preset.data_entry())
            else {
                return Ok(access::access_denied());
            };
            preset.checkins()
        }
        _ if access::legacy_entry_user(&mut *db, user).await? => false,
        _ => return Ok(access::access_denied()),
    };
    let result = data::save_on(
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
    .await;
    match result {
        Ok(saved) => {
            db.commit().await?;
            if matches!(kind, Kind::School | Kind::Room) {
                let name = fields.get("name");
                let (target, message) = if id.is_some() {
                    // The Django room edit also uses "School" in its success message.
                    (return_path, format!("School {name} updated successfully"))
                } else {
                    (
                        "/",
                        format!("{} {name} created successfully", kind.singular()),
                    )
                };
                Ok(staff_roster_notice::saved(app, headers, target, &message))
            } else {
                Ok(access::found(format!("/{}/{saved}/", kind.model())))
            }
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
    let legacy = match kind {
        Kind::School => {
            Some(staff_school::show(app, actor, headers, id, return_path, posted).await)
        }
        Kind::Room => Some(staff_room::show(app, actor, headers, id, return_path, posted).await),
        _ => None,
    };
    if let Some(legacy) = legacy {
        return match legacy {
            Ok(response) => response,
            Err(Error::Missing) => missing(),
            Err(_) => unavailable(),
        };
    }
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    match edit_page(app, actor, &csrf, kind, id, posted).await {
        Ok(response) => html::form_response(response, headers, &csrf),
        Err(Error::Missing) => missing(),
        Err(_) => unavailable(),
    }
}

type Values = BTreeMap<String, Vec<String>>;
type Choices = Vec<(String, String)>;

#[derive(Serialize)]
struct FormField {
    name: String,
    label: String,
    control: &'static str,
    values: Vec<String>,
    value: String,
    checked: bool,
    choices: Choices,
    required: bool,
    maximum: Option<usize>,
    step: Option<&'static str>,
}

impl FormField {
    fn new(
        values: &Values,
        name: impl Into<String>,
        label: impl Into<String>,
        control: &'static str,
    ) -> Self {
        let name = name.into();
        let values = values.get(&name).cloned().unwrap_or_default();
        let value = values.last().cloned().unwrap_or_default();
        let checked = ["1", "on", "true"].contains(&value.as_str());
        Self {
            name,
            label: label.into(),
            control,
            values,
            value,
            checked,
            choices: Vec::new(),
            required: false,
            maximum: None,
            step: None,
        }
    }

    fn choices(mut self, choices: Choices, required: bool) -> Self {
        self.choices = choices;
        self.required = required;
        self
    }

    fn input(mut self, required: bool, maximum: usize) -> Self {
        self.required = required;
        self.maximum = Some(maximum);
        self
    }
}

async fn edit_page(
    app: &App,
    actor: Actor,
    csrf: &str,
    kind: Kind,
    id: Option<i32>,
    posted: Option<(&Fields, &str)>,
) -> Result<Response, Error> {
    let mut db = app.db.acquire().await?;
    let checkins = staff_roles::can_check_in(&mut *db, actor).await?;
    let values = current(&mut db, kind, id, checkins).await?;
    let mut fields = form_fields(&mut db, kind, &values, id, checkins).await?;
    let checkin_fields = checkins && (kind == Kind::Team || id.is_some() && kind == Kind::Judge);
    if let Some((posted, _)) = posted {
        for field in &mut fields {
            if (field.name == "checked_in" || field.name.starts_with("checkin_"))
                && !posted.flag("mittab_checkins_present")
            {
                continue;
            }
            field.values = posted.all(&field.name).to_vec();
            field.value = posted.get(&field.name).into();
            field.checked = posted.flag(&field.name);
        }
    }
    let subject = kind.singular();
    let key = kind.model();
    let title = values
        .get("name")
        .and_then(|name| name.first())
        .map(|name| format!("{subject} · {name}"))
        .unwrap_or_else(|| format!("Create {subject}"));
    let error = posted.map(|(_, error)| error);
    Ok(html::render(
        "staff_roster_edit.html",
        context! {
            title, key, id, fields, nav => Navigation::new(actor, true), toolbar => toolbar(), csrf, error,
            checkin_fields, admin => is_tab(actor),
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    ))
}

const BASE: &[(&str, &str)] = &[
    ("needs_crash_housing", "Needs crash housing"),
    ("requests_wgm_housing", "Requests WGM housing"),
];
const JUDGE_FLAGS: &[(&str, &str)] = &[
    ("is_dino", "Dino"),
    ("wing_only", "Wing only"),
    ("request_wing", "Requests a wing"),
    ("no_wings", "Requests no wings"),
];

async fn form_fields(
    db: &mut MySqlConnection,
    kind: Kind,
    values: &Values,
    id: Option<i32>,
    tab: bool,
) -> Result<Vec<FormField>, sqlx::Error> {
    let field = |name, label, control| FormField::new(values, name, label, control);
    let mut output = vec![field("name", "Name", "text").input(true, kind.limit())];
    if kind == Kind::Debater {
        output.push(field("apda_id", "APDA ID", "number"));
    }
    if kind == Kind::Judge {
        let mut rank = field("rank", "Rank", "number");
        rank.required = true;
        rank.step = Some("0.01");
        output.push(rank);
    }
    if matches!(kind, Kind::Debater | Kind::Judge) {
        output.push(field("email", "Email", "email").input(false, 254));
        for &(name, label) in BASE {
            output.push(field(name, label, "checkbox"));
        }
    }
    if matches!(kind, Kind::Debater | Kind::Judge | Kind::Team) {
        let choices = options(db, "SELECT id, name FROM tab_school ORDER BY name, id").await?;
        match kind {
            Kind::Debater => {
                output.push(field("school", "School", "select").choices(choices, false))
            }
            Kind::Judge => output
                .push(field("schools", "School affiliations", "multi").choices(choices, false)),
            Kind::Team => {
                output.push(field("school", "School", "select").choices(choices.clone(), true));
                output.push(
                    field("hybrid_school", "Hybrid school", "select").choices(choices, false),
                );
            }
            Kind::School | Kind::Room => unreachable!(),
        }
    }
    if kind == Kind::Debater {
        output.push(field("novice_status", "Division", "select").choices(divisions(), true));
        output.push(field("qualified", "Qualified", "checkbox"));
    }
    if matches!(kind, Kind::Judge | Kind::Team) {
        let registrations = options(db,
            "SELECT r.id, CONCAT(s.name, ' — ', r.email) FROM registration_registration r JOIN tab_school s ON r.school_id = s.id ORDER BY s.name, r.id").await?;
        output.push(field("registration", "Registration", "select").choices(registrations, false));
    }
    if kind == Kind::Judge {
        output.push(field("ballot_code", "Ballot code", "text").input(false, 30));
        output.push(field(
            "reported_experience",
            "Reported years of experience",
            "number",
        ));
        for &(name, label) in JUDGE_FLAGS {
            output.push(field(name, label, "checkbox"));
        }
    }
    if kind == Kind::Team {
        output.push(
            field("seed", "Seed", "select").choices(
                ["Unseeded", "Free seed", "Half seed", "Full seed"]
                    .into_iter()
                    .enumerate()
                    .map(|(id, value)| (id.to_string(), value.into()))
                    .collect(),
                true,
            ),
        );
        output.push(
            field("break_preference", "Break preference", "select").choices(divisions(), true),
        );
        let debaters = options(db, "SELECT id, name FROM tab_debater ORDER BY name, id").await?;
        output.push(field("debaters", "Debaters", "multi").choices(debaters, false));
        output.push(field(
            "ranking_public",
            "Include in public rankings",
            "checkbox",
        ));
        if tab {
            output.push(field("checked_in", "Checked in", "checkbox"));
        }
    }
    if matches!(kind, Kind::Team | Kind::Judge) {
        output.push(
            field("required_room_tags", "Required room tags", "multi").choices(
                options(db, "SELECT id, tag FROM tab_roomtag ORDER BY tag, id").await?,
                false,
            ),
        );
    }
    if tab && id.is_some() && kind == Kind::Judge {
        for round in data::checkin_rounds(db).await? {
            let label = if round == 0 {
                "Checked in for outrounds".into()
            } else {
                format!("Checked in for round {round}")
            };
            output.push(FormField::new(
                values,
                format!("checkin_{}", round - 1),
                label,
                "checkbox",
            ));
        }
    }
    Ok(output)
}

fn divisions() -> Choices {
    vec![
        ("0".into(), "Varsity".into()),
        ("1".into(), "Novice".into()),
    ]
}

async fn options(
    db: &mut MySqlConnection,
    statement: &'static str,
) -> Result<Choices, sqlx::Error> {
    Ok(sqlx::query_as::<_, (i32, String)>(statement)
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(id, name)| (id.to_string(), name))
        .collect())
}

async fn current(
    db: &mut MySqlConnection,
    kind: Kind,
    id: Option<i32>,
    tab: bool,
) -> Result<Values, Error> {
    let Some(id) = id else {
        return Ok(["checked_in", "ranking_public"]
            .map(|key| (key.into(), vec!["1".into()]))
            .into_iter()
            .chain(
                ["novice_status", "seed", "break_preference"]
                    .map(|key| (key.into(), vec!["0".into()])),
            )
            .collect());
    };
    let fields = match kind {
        Kind::School | Kind::Room => unreachable!(),
        Kind::Debater => "'apda_id', apda_id, 'school', school_id, 'email', email, 'qualified', qualified,
            'needs_crash_housing', needs_crash_housing, 'requests_wgm_housing', requests_wgm_housing,
            'novice_status', novice_status",
        Kind::Judge => "'rank', CAST(`rank` AS CHAR), 'email', email, 'registration', registration_id,
            'ballot_code', ballot_code, 'reported_experience', reported_experience, 'wing_only', wing_only,
            'is_dino', is_dino, 'request_wing', request_wing, 'no_wings', no_wings,
            'needs_crash_housing', needs_crash_housing, 'requests_wgm_housing', requests_wgm_housing",
        Kind::Team => "'school', school_id, 'hybrid_school', hybrid_school_id, 'registration', registration_id,
            'seed', seed, 'break_preference', break_preference, 'ranking_public', ranking_public, 'checked_in', checked_in",
    };
    let sql = format!(
        "SELECT CAST(JSON_OBJECT('name', name, {fields}) AS CHAR) FROM {} WHERE id = ?",
        kind.table()
    );
    let text: Option<String> = sqlx::query_scalar(AssertSqlSafe(sql))
        .bind(id)
        .fetch_optional(&mut *db)
        .await?;
    let values = serde_json::from_str::<BTreeMap<String, Value>>(&text.ok_or(Error::Missing)?)
        .map_err(|_| Error::Database)?;
    let mut values: Values = values
        .into_iter()
        .filter_map(|(key, value)| match value {
            Value::Null => None,
            Value::String(value) => Some((key, vec![value])),
            Value::Number(_) | Value::Bool(_) => Some((key, vec![value.to_string()])),
            Value::Array(_) | Value::Object(_) => None,
        })
        .collect();
    let relations: &[(&str, &str)] = match kind {
        Kind::Team => &[
            (
                "required_room_tags",
                "SELECT roomtag_id FROM tab_team_required_room_tags WHERE team_id = ?",
            ),
            (
                "debaters",
                "SELECT debater_id FROM tab_team_debaters WHERE team_id = ?",
            ),
        ],
        Kind::Judge => &[
            (
                "required_room_tags",
                "SELECT roomtag_id FROM tab_judge_required_room_tags WHERE judge_id = ?",
            ),
            (
                "schools",
                "SELECT school_id FROM tab_judge_schools WHERE judge_id = ?",
            ),
        ],
        _ => &[],
    };
    for &(key, sql) in relations {
        let ids = sqlx::query_scalar::<_, i32>(sql)
            .bind(id)
            .fetch_all(&mut *db)
            .await?;
        values.insert(
            key.into(),
            ids.into_iter().map(|id| id.to_string()).collect(),
        );
    }
    if tab && kind == Kind::Judge {
        let rounds =
            sqlx::query_scalar::<_, i32>("SELECT round_number FROM tab_checkin WHERE judge_id = ?")
                .bind(id)
                .fetch_all(&mut *db)
                .await?;
        for round in rounds {
            values.insert(format!("checkin_{}", round - 1), vec!["1".into()]);
        }
    }
    Ok(values)
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
    match data::delete(&app.db, kind, id).await {
        Ok(()) => access::found(format!("/view_{}s/", kind.model())),
        Err(Error::Missing) => missing(),
        Err(Error::Invalid(error)) => {
            remove_page(&app, actor, &headers, kind, id, Some(error)).await
        }
        Err(Error::Database) => unavailable(),
    }
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Roster entry not found."))
}
