use axum::{
    Extension, Router,
    body::Bytes,
    extract::{MatchedPath, Path, Request, State},
    http::{HeaderMap, Method, StatusCode, Uri},
    middleware::Next,
    response::Response,
    routing::get,
};
use chrono::NaiveDateTime;
use minijinja::{Value, context};
use ring::hmac;
use serde::Serialize;
use serde_json::json;
use sqlx::{MySqlConnection, QueryBuilder};

use crate::{
    App, access,
    admin_html::django_datetime,
    html::{self, Navigation},
    legacy_html,
    legacy_session::{self, Actor, SessionError},
    login::{self, TemplateCsrf},
    private,
    registration_form::Fields,
    staff_pairing::lock_round,
    staff_roles::{self, Preset},
    staff_roster::{form_return_path, submitted_fields},
    staff_roster_data::{self, Kind},
    staff_roster_fields::{Choices, REQUIRED, audit_histories, select},
    staff_roster_notice, unavailable,
};

const ENTER: &str = "/enter_scratch/";
const LIST: &str = "/scratches/view/";
pub const MAX_BATCH: usize = 50;
const TYPES: [&str; 2] = ["Discretionary Scratch", "Tab Scratch"];

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
            "/judge/{id}/scratches/add/{count}" | "/team/{id}/scratches/add/{count}" => {
                self != Self::Submit && read
            }
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
            "/judge/{id}/scratches/delete/{scratch}" | "/team/{id}/scratches/delete/{scratch}" => {
                self == Self::Admin && read
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
                &format!("/{label}/{{id}}/scratches/add/{{count}}"),
                get(add_slash),
            )
            .route(
                &format!("/{label}/{{id}}/scratches/delete/{{scratch}}"),
                get(add_slash),
            )
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

async fn add_slash(uri: Uri) -> Response {
    let mut location = format!("{}/", uri.path());
    if let Some(query) = uri.query() {
        location.push('?');
        location.push_str(query);
    }
    let mut response = access::found(location);
    *response.status_mut() = StatusCode::MOVED_PERMANENTLY;
    response
}

pub async fn middleware(State(app): State<App>, mut request: Request, next: Next) -> Response {
    let actor =
        match legacy_session::actor(&app.db, request.headers(), app.django_secret.as_deref()).await
        {
            Ok(actor) => actor,
            Err(error) => return error.response(),
        };
    match actor {
        Actor::Anonymous => {
            return access::found(format!("/public/login/?next={}", request.uri().path()));
        }
        Actor::User { board: true, .. } => {
            return access::board_restricted(&app, request.headers()).await;
        }
        Actor::User {
            superuser: true, ..
        } => match access::wizard_complete(&app).await {
            Ok(true) => {}
            Ok(false) => return access::found("/setup/start-tournament/"),
            Err(_) => return unavailable(),
        },
        Actor::User { .. } => {}
    }
    let scope = match scratch_scope(&app.db, actor).await {
        Ok(Some(scope)) => scope,
        Ok(None) => return access::access_denied(),
        Err(_) => return unavailable(),
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

async fn scratch_scope<'db>(
    db: impl sqlx::MySqlExecutor<'db>,
    actor: Actor,
) -> Result<Option<Scope>, sqlx::Error> {
    match actor {
        Actor::User {
            superuser: true,
            board: false,
            ..
        } => Ok(Some(Scope::Admin)),
        Actor::User {
            id,
            staff: true,
            board: false,
            ..
        } => Ok(match staff_roles::preset_on(db, id).await? {
            Some(Preset::Scratches) => Some(Scope::Submit),
            Some(Preset::ScratchRead) => Some(Scope::Review),
            _ => None,
        }),
        _ => Ok(None),
    }
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

    fn base(self) -> String {
        format!("/{}/{}/scratches", self.kind.model(), self.id)
    }

    fn seed(self) -> Scratch {
        match self.kind {
            Kind::Team => Scratch {
                team: self.id,
                ..Scratch::default()
            },
            _ => Scratch {
                judge: self.id,
                ..Scratch::default()
            },
        }
    }
}

#[derive(Clone, Copy)]
enum Page {
    Entry,
    Batch(Subject, usize),
    Scoped(Subject),
    List,
}

struct ScratchRow {
    scratch: Scratch,
    team: String,
    judge: String,
    creator: String,
    created: Option<NaiveDateTime>,
}

async fn rows(
    db: &mut MySqlConnection,
    subject: Option<Subject>,
    locked: bool,
) -> Result<Vec<ScratchRow>, sqlx::Error> {
    let mut query = QueryBuilder::new(
        "SELECT s.id, s.team_id, t.name, s.judge_id, j.name, s.scratch_type,
                COALESCE((SELECT username FROM auth_user WHERE id = s.created_by_id), 'Unknown'), s.created_at
         FROM tab_scratch s JOIN tab_team t ON t.id = s.team_id JOIN tab_judge j ON j.id = s.judge_id",
    );
    if let Some(subject) = subject {
        let field = if subject.kind == Kind::Team {
            "s.team_id"
        } else {
            "s.judge_id"
        };
        query
            .push(" WHERE ")
            .push(field)
            .push(" = ")
            .push_bind(subject.id);
    }
    query.push(" ORDER BY s.id");
    if locked {
        query.push(" FOR UPDATE");
    }
    let result = query
        .build_query_as::<(
            i32,
            i32,
            String,
            i32,
            String,
            i32,
            String,
            Option<NaiveDateTime>,
        )>()
        .fetch_all(db)
        .await?;
    Ok(result
        .into_iter()
        .map(
            |(id, team_id, team, judge_id, judge, kind, creator, created)| ScratchRow {
                scratch: Scratch {
                    id,
                    team: team_id,
                    judge: judge_id,
                    kind,
                },
                team,
                judge,
                creator,
                created,
            },
        )
        .collect())
}

struct ChoicesByKind {
    teams: Choices,
    judges: Choices,
    kinds: Choices,
}

impl ChoicesByKind {
    async fn load(db: &mut MySqlConnection) -> Result<Self, sqlx::Error> {
        Ok(Self {
            teams: names(db, "SELECT id, name FROM tab_team").await?,
            judges: names(db, "SELECT id, name FROM tab_judge").await?,
            kinds: TYPES
                .into_iter()
                .enumerate()
                .map(|(value, label)| (value.to_string(), label.into()))
                .collect(),
        })
    }
}

async fn names(db: &mut MySqlConnection, query: &'static str) -> Result<Choices, sqlx::Error> {
    let mut rows = sqlx::query_as::<_, (i32, String)>(query)
        .fetch_all(db)
        .await?;
    rows.sort_by_cached_key(|(id, name)| (legacy_html::casefold(name), *id));
    Ok(rows
        .into_iter()
        .map(|(id, name)| (id.to_string(), name))
        .collect())
}

fn field_name(index: usize, name: &str) -> String {
    if index == 0 {
        name.into()
    } else {
        format!("{index}-{name}")
    }
}

fn form(
    choices: &ChoicesByKind,
    index: usize,
    draft: Scratch,
    posted: Option<&Fields>,
    error: Option<&str>,
) -> Value {
    let selected = |value: i32| {
        if value > 0 {
            value.to_string()
        } else {
            String::new()
        }
    };
    let values = [
        (
            "scratch_type",
            "Scratch type:",
            &choices.kinds,
            draft.kind.to_string(),
        ),
        ("team", "Team:", &choices.teams, selected(draft.team)),
        ("judge", "Judge:", &choices.judges, selected(draft.judge)),
    ];
    let visible_fields: Vec<_> = values
        .into_iter()
        .map(|(name, label, options, initial)| {
            let name = field_name(index, name);
            let entry = posted.map(|fields| fields.all(&name).last().map_or("", String::as_str));
            let invalid = entry.and_then(|value| {
                if value.is_empty() {
                    Some(REQUIRED.into())
                } else {
                    (!options.iter().any(|(id, _)| id == value)).then(|| {
                        format!(
                            "Select a valid choice. {value} is not one of the available choices."
                        )
                    })
                }
            });
            select(
                &name,
                label,
                options,
                entry.unwrap_or(&initial),
                false,
                false,
                invalid,
            )
        })
        .collect();
    let prefix = index.to_string();
    let non_field_errors = error.map(|text| {
        Value::from_safe_string(format!(
            r#"<ul class="errorlist nonfield"><li>{}</li></ul>"#,
            legacy_html::escape(text),
        ))
    });
    context! { prefix, visible_fields, non_field_errors }
}

fn token_data(
    headers: &HeaderMap,
    purpose: &str,
    subject: Subject,
    entries: &[Scratch],
) -> Option<Vec<u8>> {
    let session = legacy_session::cookie(headers, "sessionid")?;
    let mut bytes = format!(
        "mittab scratch revision v1\0{session}\0{purpose}\0{}\0{}\0",
        subject.kind.model(),
        subject.id
    )
    .into_bytes();
    bytes.extend(serde_json::to_vec(entries).ok()?);
    Some(bytes)
}

fn revision(
    app: &App,
    headers: &HeaderMap,
    purpose: &str,
    subject: Subject,
    entries: &[Scratch],
) -> Option<String> {
    token_data(headers, purpose, subject, entries)
        .map(|data| hex::encode(hmac::sign(&app.admin_notice_key, &data).as_ref()))
}

fn verify_revision(
    app: &App,
    headers: &HeaderMap,
    proof: &str,
    purpose: &str,
    subject: Subject,
    entries: &[Scratch],
) -> bool {
    proof.len() == 64
        && hex::decode(proof)
            .ok()
            .zip(token_data(headers, purpose, subject, entries))
            .is_some_and(|(proof, data)| hmac::verify(&app.admin_notice_key, &data, &proof).is_ok())
}

pub async fn enter(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    show(&app, actor, &headers, uri, Page::Entry, None, None).await
}

pub async fn all(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    show(&app, actor, &headers, uri, Page::List, None, None).await
}

pub async fn batch(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path((id, count)): Path<(i32, usize)>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    if !(1..=MAX_BATCH).contains(&count) {
        return private((
            StatusCode::BAD_REQUEST,
            "Choose between 1 and 50 scratches.",
        ));
    }
    show(
        &app,
        actor,
        &headers,
        uri,
        Page::Batch(subject, count),
        None,
        None,
    )
    .await
}

pub async fn scoped(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    path: MatchedPath,
    Path(id): Path<i32>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    show(
        &app,
        actor,
        &headers,
        uri,
        Page::Scoped(subject),
        None,
        None,
    )
    .await
}

async fn show(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    uri: Uri,
    page: Page,
    posted: Option<&Fields>,
    error: Option<Error>,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        let admin = matches!(actor, Actor::User { superuser: true, board: false, .. });
        if matches!(page, Page::List) {
            let item_list: Vec<_> = rows(&mut db, None, false).await?.into_iter().map(|row| {
                (row.scratch.team, scratch_name(&row.team, &row.judge, row.scratch.kind), 0, "")
            }).collect();
            db.commit().await?;
            return Ok(staff_roster_notice::render(app, headers, LIST, "common/list_data.html", shell, context! {
                title => "Viewing All Scratches for Teams", item_type => "team", item_list, scoped_scratch_links => !admin,
            }, StatusCode::OK));
        }

        let csrf = TemplateCsrf::new(headers).map_err(|_| Error::Database)?;
        let return_path = form_return_path(&uri, headers, posted);
        let choices = ChoicesByKind::load(&mut db).await?;
        let mut status = match error {
            Some(Error::Stale) => StatusCode::CONFLICT,
            Some(Error::Duplicate) => StatusCode::UNPROCESSABLE_ENTITY,
            Some(Error::Invalid) if matches!(page, Page::Entry) => StatusCode::UNPROCESSABLE_ENTITY,
            _ => StatusCode::OK,
        };
        let mut message = match error {
            Some(Error::Stale) => Some("The scratches changed. Review the current entries before saving again."),
            Some(Error::Duplicate) => Some("This scratch already exists."),
            _ => None,
        };
        let (template, context) = match page {
            Page::Entry => (
                "common/data_entry.html",
                context! { title => "Adding Scratch", form => form(&choices, 0, Scratch::default(), posted, message),
                           csrf_input => csrf.input(), return_path },
            ),
            Page::Batch(subject, count) => {
                let name = subject_name(&mut db, subject).await?;
                let forms: Vec<_> = (1..=count).map(|index| {
                    (form(&choices, index, subject.seed(), posted, None), None::<String>, None::<Value>)
                }).collect();
                (
                    "common/data_entry_multiple.html",
                    context! { title => format!("Adding Scratch(es) for {name}"), forms,
                               data_type => "Scratch", csrf_input => csrf.input(), return_path, scratch_error => message },
                )
            }
            Page::Scoped(subject) => {
                let name = subject_name(&mut db, subject).await?;
                let rows = rows(&mut db, Some(subject), false).await?;
                let ids: Vec<_> = rows.iter().map(|row| row.scratch.id).collect();
                let mut histories = audit_histories(&mut db, "scratch", &ids).await?;
                let base = subject.base();
                let entries: Vec<_> = rows.iter().map(|row| row.scratch).collect();
                let posted = posted.filter(|fields| {
                    if verify_revision(app, headers, fields.get("mittab_scratch_revision"), "edit", subject, &entries) {
                        true
                    } else {
                        status = StatusCode::CONFLICT;
                        message = Some("The scratches changed. Review the current entries before saving again.");
                        false
                    }
                });
                let scratch_revision = admin.then(|| revision(app, headers, "edit", subject, &entries)).flatten();
                let forms: Vec<_> = rows.into_iter().enumerate().map(|(index, row)| {
                    let id = row.scratch.id;
                    let delete = admin.then(|| format!("{base}/delete/{id}"));
                    let metadata = context! {
                        created_by => row.creator, created_at => row.created.map(django_datetime),
                        audit_events => histories.remove(&id).unwrap_or_default(),
                    };
                    (form(&choices, index + 1, row.scratch, posted, None), delete, metadata)
                }).collect();
                (
                    "common/data_entry_multiple.html",
                    context! {
                        title => format!("Viewing Scratch Information for {name}"), forms,
                        data_type => "Scratch", csrf_input => csrf.input(), return_path, scratch_revision,
                        links => [(format!("{base}/add/1/"), "Add Scratch")], read_only => !admin, scratch_error => message,
                    },
                )
            }
            Page::List => unreachable!(),
        };
        db.commit().await?;
        let mut response = staff_roster_notice::render(app, headers, uri.path(), template, shell, context, status);
        csrf.set_cookie(&mut response, headers);
        Ok::<_, Error>(response)
    }.await;
    match result {
        Ok(response) => response,
        Err(Error::Missing) => missing(),
        Err(_) => unavailable(),
    }
}

async fn subject_name(db: &mut MySqlConnection, subject: Subject) -> Result<String, Error> {
    staff_roster_data::name(db, subject.kind, subject.id)
        .await?
        .ok_or(Error::Missing)
}

pub async fn submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(scope): Extension<Scope>,
    request: Request,
) -> Response {
    save(&app, actor, scope, ENTER, Page::Entry, request).await
}

pub async fn add_batch(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(scope): Extension<Scope>,
    path: MatchedPath,
    Path((id, count)): Path<(i32, usize)>,
    request: Request,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    if !(1..=MAX_BATCH).contains(&count) {
        return missing();
    }
    save(
        &app,
        actor,
        scope,
        path.as_str(),
        Page::Batch(subject, count),
        request,
    )
    .await
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(scope): Extension<Scope>,
    path: MatchedPath,
    Path(id): Path<i32>,
    request: Request,
) -> Response {
    let Some(subject) = Subject::from_path(&path, id) else {
        return missing();
    };
    save(
        &app,
        actor,
        scope,
        path.as_str(),
        Page::Scoped(subject),
        request,
    )
    .await
}

async fn save(
    app: &App,
    actor: Actor,
    scope: Scope,
    route: &str,
    page: Page,
    request: Request,
) -> Response {
    let Some(user) = user_id(actor) else {
        return access::access_denied();
    };
    let uri = request.uri().clone();
    let headers = request.headers().clone();
    let fields = match submitted_fields(app, request).await {
        Ok(fields) => fields,
        Err(response) => return *response,
    };
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    match apply(
        app,
        &headers,
        user,
        route,
        page,
        &fields,
        scope == Scope::Submit,
    )
    .await
    {
        Ok(true) => staff_roster_notice::saved(app, &headers, ENTER, "Scratch submitted."),
        Ok(false) => {
            let target = form_return_path(&uri, &headers, Some(&fields));
            let message = match page {
                Page::Entry => "Scratch created successfully",
                Page::Scoped(subject) if subject.kind == Kind::Team => {
                    "Scratches successfully modified"
                }
                _ => "Scratches created successfully",
            };
            staff_roster_notice::saved(app, &headers, &target, message)
        }
        Err(Error::Stale) => show(app, actor, &headers, uri, page, None, Some(Error::Stale)).await,
        Err(error @ (Error::Invalid | Error::Duplicate)) => {
            show(app, actor, &headers, uri, page, Some(&fields), Some(error)).await
        }
        Err(Error::Forbidden) => access::access_denied(),
        Err(Error::Missing) => missing(),
        Err(Error::Database) => unavailable(),
    }
}

fn read_input(fields: &Fields, index: usize, id: i32) -> Result<Scratch, Error> {
    let number = |name| {
        let value = fields
            .all(&field_name(index, name))
            .last()
            .map_or("", String::as_str);
        value
            .parse::<i32>()
            .ok()
            .filter(|parsed| parsed.to_string() == value)
            .ok_or(Error::Invalid)
    };
    let input = Scratch {
        id,
        team: number("team")?,
        judge: number("judge")?,
        kind: number("scratch_type")?,
    };
    if input.team <= 0 || input.judge <= 0 || !matches!(input.kind, 0 | 1) {
        Err(Error::Invalid)
    } else {
        Ok(input)
    }
}

fn user_id(actor: Actor) -> Option<i32> {
    match actor {
        Actor::User {
            id, board: false, ..
        } => Some(id),
        _ => None,
    }
}

#[derive(Clone, Copy)]
enum Error {
    Invalid,
    Duplicate,
    Stale,
    Missing,
    Database,
    Forbidden,
}

impl From<sqlx::Error> for Error {
    fn from(error: sqlx::Error) -> Self {
        if error
            .as_database_error()
            .is_some_and(|error| error.is_unique_violation())
        {
            Self::Duplicate
        } else {
            Self::Database
        }
    }
}

async fn apply(
    app: &App,
    headers: &HeaderMap,
    user: i32,
    path: &str,
    page: Page,
    fields: &Fields,
    blind: bool,
) -> Result<bool, Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let current = write_scope(&mut db, app, headers, user, path).await?;
    let blind = blind || current == Scope::Submit;
    let (first, previous) = match page {
        Page::Entry => (0, vec![Scratch::default()]),
        Page::Batch(subject, count) => {
            subject_name(&mut db, subject).await?;
            (1, vec![Scratch::default(); count])
        }
        Page::Scoped(subject) => {
            let scratch: Vec<_> = rows(&mut db, Some(subject), true)
                .await?
                .into_iter()
                .map(|row| row.scratch)
                .collect();
            if !verify_revision(
                app,
                headers,
                fields.get("mittab_scratch_revision"),
                "edit",
                subject,
                &scratch,
            ) {
                return Err(Error::Stale);
            }
            (1, scratch)
        }
        Page::List => return Err(Error::Forbidden),
    };
    for (offset, before) in previous.into_iter().enumerate() {
        let input = read_input(fields, first + offset, before.id)?;
        save_row(&mut db, user, input, before, blind).await?;
    }
    db.commit().await?;
    Ok(blind)
}

async fn save_row(
    db: &mut MySqlConnection,
    user: i32,
    input: Scratch,
    before: Scratch,
    blind: bool,
) -> Result<(), Error> {
    let team = staff_roster_data::name(db, Kind::Team, input.team)
        .await?
        .ok_or(Error::Invalid)?;
    let judge = staff_roster_data::name(db, Kind::Judge, input.judge)
        .await?
        .ok_or(Error::Invalid)?;
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
            audit(db, id, user, &team, &judge, input.kind, "create").await?;
        }
    } else {
        sqlx::query(
            "UPDATE tab_scratch SET team_id = ?, judge_id = ?, scratch_type = ? WHERE id = ?",
        )
        .bind(input.team)
        .bind(input.judge)
        .bind(input.kind)
        .bind(input.id)
        .execute(&mut *db)
        .await?;
        // Django's ChoiceField compares its string input against the integer model value.
        let mut changed = vec!["scratch_type"];
        if before.team != input.team {
            changed.push("team");
        }
        if before.judge != input.judge {
            changed.push("judge");
        }
        record_audit(
            db,
            input.id,
            user,
            (&team, &judge, input.kind),
            "edit",
            json!({ "fields": changed }),
        )
        .await?;
    }
    Ok(())
}

async fn write_scope(
    db: &mut MySqlConnection,
    app: &App,
    headers: &HeaderMap,
    user: i32,
    path: &str,
) -> Result<Scope, Error> {
    let session = legacy_session::cookie(headers, "sessionid").ok_or(Error::Forbidden)?;
    let actor = match legacy_session::actor_on(db, session, app.django_secret.as_deref()).await {
        Ok(actor) if user_id(actor) == Some(user) => actor,
        Err(SessionError::Database) => return Err(Error::Database),
        _ => return Err(Error::Forbidden),
    };
    scratch_scope(db, actor)
        .await?
        .filter(|scope| scope.allows(path, &Method::POST))
        .ok_or(Error::Forbidden)
}

fn scratch_name(team: &str, judge: &str, kind: i32) -> String {
    let label = if kind == 0 { "Team" } else { "Tab" };
    format!("{team} <={label}=> {judge}")
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
    record_audit(db, id, user, (team, judge, kind), event, json!({})).await
}

async fn record_audit(
    db: &mut MySqlConnection,
    id: i32,
    user: i32,
    (team, judge, kind): (&str, &str, i32),
    event: &'static str,
    changes: serde_json::Value,
) -> Result<(), sqlx::Error> {
    let name = scratch_name(team, judge, kind);
    sqlx::query(
        "INSERT INTO tab_auditevent (object_id, event_type, created_at, object_repr, changes, note, content_type_id, user_id)
         VALUES (?, ?, UTC_TIMESTAMP(6), ?, ?, '',
            (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = 'scratch'), ?)",
    ).bind(id).bind(event).bind(name).bind(changes.to_string()).bind(user).execute(db).await?;
    Ok(())
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
    confirm_delete(&app, actor, &headers, subject, scratch, false).await
}

async fn confirm_delete(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    subject: Subject,
    id: i32,
    stale: bool,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(rows) = rows(&mut db, Some(subject), false).await else {
        return unavailable();
    };
    let Some(row) = rows.into_iter().find(|row| row.scratch.id == id) else {
        return missing();
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let proof = revision(app, headers, "delete", subject, &[row.scratch]);
    let response = html::render(
        "scratches_delete.html",
        context! {
            title => "Delete Scratch", nav => Navigation::new(actor, true), csrf, team => row.team, judge => row.judge,
            scratch_type => TYPES.get(row.scratch.kind as usize).copied().unwrap_or("Scratch"),
            link => format!("{}/view/", subject.base()), stale, proof,
        },
        if stale {
            StatusCode::CONFLICT
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn remove(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
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
    let Some(user) = user_id(actor) else {
        return access::access_denied();
    };
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        write_scope(&mut db, &app, &headers, user, path.as_str()).await?;
        let rows = rows(&mut db, Some(subject), true).await?;
        let row = rows
            .iter()
            .find(|row| row.scratch.id == scratch)
            .ok_or(Error::Missing)?;
        if !verify_revision(
            &app,
            &headers,
            fields.get("mittab_scratch_revision"),
            "delete",
            subject,
            &[row.scratch],
        ) {
            return Err(Error::Stale);
        }
        sqlx::query("DELETE FROM tab_scratch WHERE id = ?")
            .bind(scratch)
            .execute(&mut *db)
            .await?;
        db.commit().await?;
        Ok(())
    }
    .await;
    match result {
        Ok(()) => staff_roster_notice::saved(&app, &headers, "/", "Scratch deleted successfully"),
        Err(Error::Stale) => confirm_delete(&app, actor, &headers, subject, scratch, true).await,
        Err(Error::Missing) => missing(),
        Err(Error::Forbidden) => access::access_denied(),
        Err(_) => unavailable(),
    }
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Scratch entry not found."))
}
