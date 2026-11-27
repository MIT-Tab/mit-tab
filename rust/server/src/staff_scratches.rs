use std::collections::BTreeSet;

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{MatchedPath, Path, Query, Request, State},
    http::{HeaderMap, Method, StatusCode},
    middleware::Next,
    response::Response,
    routing::get,
};
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::{self, Actor},
    login, private,
    registration_form::Fields,
    staff_pairing::lock_round,
    staff_roles::{self, Preset},
    staff_roster_data::{self, Kind},
    unavailable,
};

const ENTER: &str = "/enter_scratch/";
const LIST: &str = "/scratches/view/";
const MAX_BATCH: usize = 50;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    Submit,
    Review,
    Admin,
}

impl Scope {
    pub fn allows(self, path: &str, method: &Method) -> bool {
        let read = matches!(*method, Method::GET | Method::HEAD);
        match path {
            ENTER => read || *method == Method::POST,
            LIST => self != Self::Submit && read,
            "/judge/{id}/scratches/add/{count}/" | "/team/{id}/scratches/add/{count}/" => {
                self != Self::Submit && (read || *method == Method::POST)
            }
            "/judge/{id}/scratches/view/" | "/team/{id}/scratches/view/" => {
                self == Self::Admin || (self == Self::Review && read)
            }
            "/judge/{id}/scratches/delete/{scratch}/"
            | "/team/{id}/scratches/delete/{scratch}/" => {
                self == Self::Admin && (read || *method == Method::POST)
            }
            _ => false,
        }
    }
}

pub fn routes() -> Router<App> {
    let mut router = Router::new()
        .route(ENTER, get(enter).post(submit))
        .route(LIST, get(all));
    for label in ["team", "judge"] {
        router = router
            .route(
                &format!("/{label}/{{id}}/scratches/add/{{count}}/"),
                get(batch).post(add_batch),
            )
            .route(
                &format!("/{label}/{{id}}/scratches/view/"),
                get(scoped).post(update),
            )
            .route(
                &format!("/{label}/{{id}}/scratches/delete/{{scratch}}/"),
                get(delete_page).post(remove),
            );
    }
    router
}

pub async fn middleware(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let actor =
        match legacy_session::actor(&app.db, request.headers(), app.django_secret.as_deref()).await
        {
            Ok(actor) => actor,
            Err(error) => return error.response(),
        };
    let scope = match actor {
        Actor::Anonymous => {
            return access::found(format!("/public/login/?next={}", request.uri().path()));
        }
        Actor::User { board: true, .. } => return access::board_restricted(&app).await,
        Actor::User {
            superuser: true, ..
        } => match access::wizard_complete(&app).await {
            Ok(true) => Scope::Admin,
            Ok(false) => return access::found("/setup/start-tournament/"),
            Err(_) => return unavailable(),
        },
        Actor::User {
            id, staff: true, ..
        } => match staff_roles::preset(&app.db, id).await {
            Ok(Some(Preset::Scratches)) => Scope::Submit,
            Ok(Some(Preset::ScratchRead)) => Scope::Review,
            Ok(_) => return access::access_denied(),
            Err(_) => return unavailable(),
        },
        Actor::User { .. } => return access::access_denied(),
    };
    let path = request
        .extensions()
        .get::<MatchedPath>()
        .map(MatchedPath::as_str)
        .unwrap_or_default();
    if !scope.allows(path, request.method()) {
        return access::access_denied();
    }
    request.extensions_mut().insert(actor);
    request.extensions_mut().insert(scope);
    next.run(request).await
}

#[derive(Clone, Copy, Serialize, Default)]
struct Scratch {
    id: i32,
    team: i32,
    judge: i32,
    kind: i32,
}

#[derive(Clone, Copy)]
struct Subject {
    kind: Kind,
    id: i32,
}

impl Subject {
    fn from_path(path: &MatchedPath, id: i32) -> Option<Self> {
        let kind = Kind::parse(path.as_str())?;
        matches!(kind, Kind::Team | Kind::Judge).then_some(Self { kind, id })
    }

    fn url(self) -> String {
        format!("/{}/{}/scratches/view/", self.kind.model(), self.id)
    }

    fn seed(self) -> Scratch {
        if self.kind == Kind::Team {
            Scratch {
                team: self.id,
                ..Scratch::default()
            }
        } else {
            Scratch {
                judge: self.id,
                ..Scratch::default()
            }
        }
    }
}

type ScratchRow = (i32, i32, String, i32, String, i32, String);

async fn rows(
    db: &mut MySqlConnection,
    subject: Option<Subject>,
) -> Result<Vec<ScratchRow>, sqlx::Error> {
    let (field, id) = subject.map_or((None, None), |subject| {
        (Some(subject.kind), Some(subject.id))
    });
    let (team, judge) = (
        if field == Some(Kind::Team) { id } else { None },
        if field == Some(Kind::Judge) { id } else { None },
    );
    sqlx::query_as(
        "SELECT s.id, s.team_id, t.name, s.judge_id, j.name, s.scratch_type, COALESCE(u.username, '')
         FROM tab_scratch s JOIN tab_team t ON t.id = s.team_id JOIN tab_judge j ON j.id = s.judge_id
         LEFT JOIN auth_user u ON u.id = s.created_by_id
         WHERE (? IS NULL OR s.team_id = ?) AND (? IS NULL OR s.judge_id = ?)
         ORDER BY s.id",
    ).bind(team).bind(team).bind(judge).bind(judge).fetch_all(db).await
}

#[derive(Deserialize)]
pub struct QueryData {
    submitted: Option<u8>,
}

pub async fn enter(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(query): Query<QueryData>,
) -> Response {
    form_page(
        &app,
        actor,
        &headers,
        FormPage {
            title: "Add Scratch".into(),
            single: true,
            drafts: vec![Scratch::default()],
            error: None,
            submitted: query.submitted == Some(1),
        },
    )
    .await
}

struct FormPage<'a> {
    title: String,
    single: bool,
    drafts: Vec<Scratch>,
    error: Option<&'a str>,
    submitted: bool,
}

async fn form_page(app: &App, actor: Actor, headers: &HeaderMap, form: FormPage<'_>) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let Ok(teams) =
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_team ORDER BY name, id")
            .fetch_all(&app.db)
            .await
    else {
        return unavailable();
    };
    let Ok(judges) =
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_judge ORDER BY name, id")
            .fetch_all(&app.db)
            .await
    else {
        return unavailable();
    };
    let response = html::render(
        "scratches_form.html",
        context! {
            title => form.title, nav => Navigation::new(actor, true), csrf, teams, judges, drafts => form.drafts,
            single => form.single, error => form.error, submitted => form.submitted,
        },
        if form.error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn batch(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, count)): Path<(i32, usize)>,
    headers: HeaderMap,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    batch_page(&app, actor, &headers, subject, count, None).await
}

async fn batch_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    subject: Subject,
    count: usize,
    error: Option<&str>,
) -> Response {
    if !(1..=MAX_BATCH).contains(&count) {
        return private((
            StatusCode::BAD_REQUEST,
            "Choose between 1 and 50 scratches.",
        ));
    }
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let name = match staff_roster_data::name(&mut db, subject.kind, subject.id).await {
        Ok(Some(name)) => name,
        Ok(None) => return missing(),
        Err(_) => return unavailable(),
    };
    drop(db);
    form_page(
        app,
        actor,
        headers,
        FormPage {
            title: format!("Add Scratches · {name}"),
            single: false,
            drafts: vec![subject.seed(); count],
            error,
            submitted: false,
        },
    )
    .await
}

pub async fn submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(scope): Extension<Scope>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Some(user) = user_id(actor) else {
        return access::access_denied();
    };
    let input = read_input(&fields, "", 0, Scratch::default());
    let result = match input {
        Some(input) => apply(&app, user, &[input], None, scope == Scope::Submit).await,
        None => Err(Error::Invalid("Choose a team, judge, and scratch type.")),
    };
    match result {
        Ok(()) => access::found(format!("{ENTER}?submitted=1")),
        Err(Error::Invalid(error)) => {
            form_page(
                &app,
                actor,
                &headers,
                FormPage {
                    title: "Add Scratch".into(),
                    single: true,
                    drafts: vec![input.unwrap_or_default()],
                    error: Some(error),
                    submitted: false,
                },
            )
            .await
        }
        Err(_) => unavailable(),
    }
}

pub async fn add_batch(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, count)): Path<(i32, usize)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    if !(1..=MAX_BATCH).contains(&count) {
        return missing();
    }
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Some(user) = user_id(actor) else {
        return access::access_denied();
    };
    let batch: Option<Vec<_>> = (1..=count)
        .map(|index| read_input(&fields, &format!("{index}-"), 0, subject.seed()))
        .collect();
    let result = match batch {
        Some(batch) => apply(&app, user, &batch, Some(subject), false).await,
        None => Err(Error::Invalid(
            "Choose a team, judge, and scratch type for each entry.",
        )),
    };
    match result {
        Ok(()) => access::found(subject.url()),
        Err(Error::Invalid(error)) => {
            batch_page(&app, actor, &headers, subject, count, Some(error)).await
        }
        Err(_) => unavailable(),
    }
}

fn read_input(fields: &Fields, prefix: &str, id: i32, default: Scratch) -> Option<Scratch> {
    let number = |key| fields.get(&format!("{prefix}{key}")).parse::<i32>().ok();
    let team = number("team").unwrap_or(default.team);
    let judge = number("judge").unwrap_or(default.judge);
    let kind = number("scratch_type")?;
    (team > 0 && judge > 0 && matches!(kind, 0 | 1)).then_some(Scratch {
        id,
        team,
        judge,
        kind,
    })
}

fn user_id(actor: Actor) -> Option<i32> {
    match actor {
        Actor::User {
            id, board: false, ..
        } => Some(id),
        _ => None,
    }
}

enum Error {
    Invalid(&'static str),
    Database,
}

impl From<sqlx::Error> for Error {
    fn from(error: sqlx::Error) -> Self {
        if error
            .as_database_error()
            .is_some_and(|error| error.is_unique_violation())
        {
            Self::Invalid("This scratch already exists.")
        } else {
            Self::Database
        }
    }
}

async fn apply(
    app: &App,
    user: i32,
    inputs: &[Scratch],
    subject: Option<Subject>,
    blind: bool,
) -> Result<(), Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let scope: BTreeSet<i32> = if inputs.iter().any(|input| input.id != 0) {
        rows(&mut db, subject)
            .await?
            .iter()
            .map(|row| row.0)
            .collect()
    } else {
        BTreeSet::new()
    };
    let mut seen = BTreeSet::new();
    for input in inputs {
        let team = staff_roster_data::name(&mut db, Kind::Team, input.team).await?;
        let judge = staff_roster_data::name(&mut db, Kind::Judge, input.judge).await?;
        let (Some(team), Some(judge)) = (team, judge) else {
            return Err(Error::Invalid("Choose an existing team and judge."));
        };
        if input.id == 0 {
            let query = if blind {
                "INSERT INTO tab_scratch (team_id, judge_id, scratch_type, created_by_id, created_at)
                 VALUES (?, ?, ?, ?, UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE id = id"
            } else {
                "INSERT INTO tab_scratch (team_id, judge_id, scratch_type, created_by_id, created_at)
                 VALUES (?, ?, ?, ?, UTC_TIMESTAMP(6))"
            };
            let insert = sqlx::query(query)
                .bind(input.team)
                .bind(input.judge)
                .bind(input.kind)
                .bind(user)
                .execute(&mut *db)
                .await?;
            let id = insert.last_insert_id() as i32;
            if id > 0 {
                audit(&mut db, id, user, &team, &judge, input.kind, "create").await?;
            }
        } else {
            if !scope.contains(&input.id) || !seen.insert(input.id) {
                return Err(Error::Invalid(
                    "The scratch list changed. Reload and try again.",
                ));
            }
            let updated = sqlx::query(
                "UPDATE tab_scratch SET team_id = ?, judge_id = ?, scratch_type = ? WHERE id = ?",
            )
            .bind(input.team)
            .bind(input.judge)
            .bind(input.kind)
            .bind(input.id)
            .execute(&mut *db)
            .await?;
            if updated.rows_affected() > 0 {
                audit(&mut db, input.id, user, &team, &judge, input.kind, "edit").await?;
            }
        }
    }
    db.commit().await?;
    Ok(())
}

pub async fn audit(
    db: &mut MySqlConnection,
    id: i32,
    user: i32,
    team: &str,
    judge: &str,
    kind: i32,
    event: &'static str,
) -> Result<(), sqlx::Error> {
    let label = if kind == 0 { "Team" } else { "Tab" };
    let name = format!("{team} <={label}=> {judge}");
    sqlx::query(
        "INSERT INTO tab_auditevent (object_id, event_type, created_at, object_repr, changes, note, content_type_id, user_id)
         VALUES (?, ?, UTC_TIMESTAMP(6), ?, '{}', '',
            (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = 'scratch'), ?)",
    ).bind(id).bind(event).bind(name).bind(user).execute(db).await?;
    Ok(())
}

pub async fn all(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(scope): Extension<Scope>,
    headers: HeaderMap,
) -> Response {
    list(&app, actor, &headers, scope, None).await
}

pub async fn scoped(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(scope): Extension<Scope>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    list(&app, actor, &headers, scope, Some(subject)).await
}

async fn list(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    scope: Scope,
    subject: Option<Subject>,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let title = if let Some(subject) = subject {
        match staff_roster_data::name(&mut db, subject.kind, subject.id).await {
            Ok(Some(name)) => format!("Scratches · {name}"),
            Ok(None) => return missing(),
            Err(_) => return unavailable(),
        }
    } else {
        "Scratches".into()
    };
    let Ok(rows) = rows(&mut db, subject).await else {
        return unavailable();
    };
    drop(db);
    let edit = scope == Scope::Admin && subject.is_some() && !rows.is_empty();
    let (teams, judges) = if edit {
        let (teams, judges) = tokio::join!(
            sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_team ORDER BY name, id")
                .fetch_all(&app.db),
            sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_judge ORDER BY name, id")
                .fetch_all(&app.db)
        );
        let (Ok(teams), Ok(judges)) = (teams, judges) else {
            return unavailable();
        };
        (teams, judges)
    } else {
        (Vec::new(), Vec::new())
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let base = subject.map(|item| format!("/{}/{}/scratches/", item.kind.model(), item.id));
    let response = html::render(
        "scratches_list.html",
        context! {
            title, nav => Navigation::new(actor, true), csrf, rows, base, edit, teams, judges,
        },
        StatusCode::OK,
    );
    html::form_response(response, headers, &csrf)
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Some(user) = user_id(actor) else {
        return access::access_denied();
    };
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(rows) = rows(&mut db, Some(subject)).await else {
        return unavailable();
    };
    drop(db);
    let inputs: Option<Vec<_>> = rows
        .iter()
        .enumerate()
        .filter(|(index, _)| {
            !fields
                .get(&format!("{}-scratch_type", index + 1))
                .is_empty()
        })
        .map(|(index, row)| {
            let prefix = format!("{}-", index + 1);
            let id = fields.get(&format!("{prefix}id")).parse().unwrap_or(row.0);
            if id <= 0 {
                return None;
            }
            read_input(&fields, &prefix, id, Scratch::default())
        })
        .collect();
    match inputs {
        Some(inputs) if !inputs.is_empty() => {
            match apply(&app, user, &inputs, Some(subject), false).await {
                Ok(()) => access::found(subject.url()),
                Err(Error::Invalid(error)) => private((StatusCode::UNPROCESSABLE_ENTITY, error)),
                Err(Error::Database) => unavailable(),
            }
        }
        _ => private((
            StatusCode::UNPROCESSABLE_ENTITY,
            "Choose valid scratch entries.",
        )),
    }
}

pub async fn delete_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, scratch)): Path<(i32, i32)>,
    headers: HeaderMap,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(rows) = rows(&mut db, Some(subject)).await else {
        return unavailable();
    };
    let Some(row) = rows.into_iter().find(|row| row.0 == scratch) else {
        return missing();
    };
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    let response = html::render(
        "scratches_delete.html",
        context! {
            title => "Delete Scratch", nav => Navigation::new(actor, true), csrf, team => row.2, judge => row.4,
            link => subject.url(),
        },
        StatusCode::OK,
    );
    html::form_response(response, &headers, &csrf)
}

pub async fn remove(
    State(app): State<App>,
    path: MatchedPath,
    Path((id, scratch)): Path<(i32, i32)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let result = async {
        let mut tx = app.db.begin().await?;
        lock_round(&mut tx).await?;
        let sql = if subject.kind == Kind::Team {
            "DELETE FROM tab_scratch WHERE id = ? AND team_id = ?"
        } else {
            "DELETE FROM tab_scratch WHERE id = ? AND judge_id = ?"
        };
        let change = sqlx::query(sql)
            .bind(scratch)
            .bind(id)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        Ok::<_, sqlx::Error>(change.rows_affected())
    }
    .await;
    match result {
        Ok(1) => access::found(subject.url()),
        Ok(_) => missing(),
        Err(_) => unavailable(),
    }
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Scratch entry not found."))
}
