use std::collections::BTreeMap;

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{MatchedPath, Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use minijinja::context;
use serde::Serialize;
use serde_json::Value;
use sqlx::{AssertSqlSafe, MySqlConnection};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_form::Fields,
    settings::Settings,
    staff_roster_data::{self as data, Edit, Error, Kind},
    unavailable,
};

pub fn routes() -> Router<App> {
    let mut router = Router::new();
    for kind in ["team", "judge", "room"] {
        router = router.route(&format!("/{kind}/{{id}}"), get(roster_redirect));
    }
    for kind in Kind::ALL {
        let name = kind.model();
        router = router
            .route(&format!("/view_{name}s/"), get(list))
            .route(&format!("/enter_{name}/"), get(enter).post(create))
            .route(&format!("/{name}/{{id}}/"), get(view).post(update))
            .route(
                &format!("/{name}/{{id}}/delete/"),
                get(confirm_delete).post(delete),
            );
    }
    router
}

async fn roster_redirect(path: MatchedPath, Path(id): Path<i32>) -> Response {
    let Some(kind) = Kind::parse(path.as_str()) else {
        return missing();
    };
    access::found(format!("/{}/{id}/", kind.model()))
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

pub async fn list(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
) -> Response {
    let Some(kind) = Kind::parse(path.as_str()) else {
        return missing();
    };
    match listing(&app, kind, actor).await {
        Ok(response) => response,
        Err(_) => unavailable(),
    }
}

async fn listing(app: &App, kind: Kind, actor: Actor) -> Result<Response, sqlx::Error> {
    let (table, key) = (kind.table(), kind.model());
    let subject = kind.singular();
    let statement = match kind {
        Kind::School => "SELECT s.id, s.name, COALESCE(CAST(s.apda_id AS CHAR), '') FROM tab_school s ORDER BY s.name, s.id",
        Kind::Room => "SELECT r.id, r.name, CAST(r.`rank` AS CHAR) FROM tab_room r ORDER BY r.name, r.id",
        Kind::Debater => "SELECT d.id, d.name, COALESCE(s.name, '') FROM tab_debater d LEFT JOIN tab_school s ON s.id = d.school_id ORDER BY d.name, d.id",
        Kind::Judge =>
            "SELECT j.id, j.name, COALESCE(GROUP_CONCAT(s.name ORDER BY s.name SEPARATOR ', '), '')
             FROM tab_judge j LEFT JOIN tab_judge_schools js ON js.judge_id = j.id LEFT JOIN tab_school s ON s.id = js.school_id
             GROUP BY j.id ORDER BY j.name, j.id",
        Kind::Team => "SELECT t.id, t.name, s.name FROM tab_team t JOIN tab_school s ON s.id = t.school_id ORDER BY t.name, t.id",
    };
    let rows = sqlx::query_as::<_, (i32, String, String)>(statement)
        .fetch_all(&app.db)
        .await?;
    let column = match kind {
        Kind::School => "APDA ID",
        Kind::Room => "Rank",
        Kind::Judge => "Affiliations",
        Kind::Team | Kind::Debater => "School",
    };
    Ok(html::render(
        "staff_roster_list.html",
        context! {
            title => format!("{subject}s"), subject, key, table, rows, column, toolbar => toolbar(),
            nav => Navigation::new(actor, true), admin => is_tab(actor),
        },
        StatusCode::OK,
    ))
}

pub async fn enter(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    headers: HeaderMap,
) -> Response {
    show(
        &app,
        actor,
        &headers,
        Kind::parse(path.as_str()),
        None,
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
) -> Response {
    show(
        &app,
        actor,
        &headers,
        Kind::parse(path.as_str()),
        Some(id),
        None,
    )
    .await
}

pub async fn create(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    edit(
        &app,
        actor,
        &headers,
        Kind::parse(path.as_str()),
        None,
        &body,
    )
    .await
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    edit(
        &app,
        actor,
        &headers,
        Kind::parse(path.as_str()),
        Some(id),
        &body,
    )
    .await
}

async fn edit(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: Option<Kind>,
    id: Option<i32>,
    body: &[u8],
) -> Response {
    let Some(kind) = kind else {
        return missing();
    };
    let Actor::User { id: user, .. } = actor else {
        return access::access_denied();
    };
    let fields = Fields::parse(body);
    if !login::valid_csrf(headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    match data::save(
        &app.db,
        kind,
        Edit {
            id,
            user,
            checkins: is_tab(actor),
            public_log: true,
            fields: &fields,
        },
    )
    .await
    {
        Ok(id) => access::found(format!("/{}/{id}/", kind.model())),
        Err(Error::Missing) => missing(),
        Err(Error::Invalid(error)) => {
            show(app, actor, headers, Some(kind), id, Some((&fields, error))).await
        }
        Err(Error::Database) => unavailable(),
    }
}

async fn show(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: Option<Kind>,
    id: Option<i32>,
    posted: Option<(&Fields, &str)>,
) -> Response {
    let Some(kind) = kind else {
        return missing();
    };
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
    let values = current(&mut db, kind, id, is_tab(actor)).await?;
    let mut fields = form_fields(&mut db, kind, &values, id, is_tab(actor)).await?;
    if let Some((posted, _)) = posted {
        for field in &mut fields {
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
    let links = if kind == Kind::School {
        if let Some(id) = id {
            school_links(&mut db, id).await?
        } else {
            Vec::new()
        }
    } else {
        Vec::new()
    };
    let error = posted.map(|(_, error)| error);
    Ok(html::render(
        "staff_roster_edit.html",
        context! {
            title, key, id, fields, links, nav => Navigation::new(actor, true), toolbar => toolbar(), csrf, error, admin => is_tab(actor),
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
    if matches!(kind, Kind::School | Kind::Debater) {
        output.push(field("apda_id", "APDA ID", "number"));
    }
    if matches!(kind, Kind::Room | Kind::Judge) {
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
    if matches!(kind, Kind::Room | Kind::Team | Kind::Judge) {
        let (key, label) = if kind == Kind::Room {
            ("tags", "Room tags")
        } else {
            ("required_room_tags", "Required room tags")
        };
        output.push(field(key, label, "multi").choices(
            options(db, "SELECT id, tag FROM tab_roomtag ORDER BY tag, id").await?,
            false,
        ));
    }
    if tab && id.is_some() && matches!(kind, Kind::Room | Kind::Judge) {
        let settings = Settings::load(db, &["tot_rounds"]).await?;
        for round in 0..=settings.integer("tot_rounds", 6).clamp(0, 50) {
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
        Kind::School => "'apda_id', apda_id",
        Kind::Room => "'rank', CAST(`rank` AS CHAR)",
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
        Kind::Room => &[(
            "tags",
            "SELECT roomtag_id FROM tab_room_tags WHERE room_id = ?",
        )],
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
    if tab {
        let checkins = match kind {
            Kind::Room => Some("SELECT round_number FROM tab_roomcheckin WHERE room_id = ?"),
            Kind::Judge => Some("SELECT round_number FROM tab_checkin WHERE judge_id = ?"),
            _ => None,
        };
        if let Some(checkins) = checkins {
            let rounds = sqlx::query_scalar::<_, i32>(checkins)
                .bind(id)
                .fetch_all(&mut *db)
                .await?;
            for round in rounds {
                values.insert(format!("checkin_{}", round - 1), vec!["1".into()]);
            }
        }
    }
    Ok(values)
}

async fn school_links(
    db: &mut MySqlConnection,
    id: i32,
) -> Result<Vec<(String, String, i32, String)>, sqlx::Error> {
    sqlx::query_as(
        "SELECT 'Teams' category, 'team' kind, id, name FROM tab_team WHERE school_id = ?
         UNION ALL SELECT 'Hybrid teams', 'team', id, name FROM tab_team WHERE hybrid_school_id = ?
         UNION ALL SELECT 'Affiliated judges', 'judge', j.id, j.name FROM tab_judge j JOIN tab_judge_schools s ON s.judge_id = j.id WHERE s.school_id = ?
         ORDER BY category, name, id",
    ).bind(id).bind(id).bind(id).fetch_all(db).await
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
