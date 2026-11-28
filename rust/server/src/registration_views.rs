use std::collections::{BTreeMap, BTreeSet, HashMap};

use axum::{
    Extension, Json,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::{Value as TemplateValue, context};
use serde_json::{Value, json};

use crate::{
    App, access, legacy_html,
    legacy_session::Actor,
    login::{self, TemplateCsrf},
    private, registration_directory as directory, registration_emails,
    registration_form::{Fields, MAX_TEAMS, Registration},
    registration_page,
    registration_store::{self as store, Error},
    school_directory::{self, SchoolChoice},
    staff_roster_notice::{self, Portal as NoticePortal},
    unavailable,
};

const LOOKUP: &str = "/registration/edit/";

fn not_found() -> Response {
    private((StatusCode::NOT_FOUND, "Registration not found."))
}

pub(crate) fn valid_code(code: &str) -> bool {
    !code.is_empty()
        && code.len() <= 255
        && code
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}

pub async fn search(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    let Ok(shell) = legacy_html::shell(&mut db, actor, LOOKUP).await else {
        return unavailable();
    };
    let Ok(csrf) = TemplateCsrf::new(&headers) else {
        return unavailable();
    };
    let mut response = staff_roster_notice::render_portal(
        &app,
        &headers,
        NoticePortal::Registration,
        LOOKUP,
        "registration/code_lookup.html",
        shell,
        context! { csrf_input => csrf.input(), can_modify_registration => config.can_edit },
    );
    csrf.set_cookie(&mut response, &headers);
    response
}

pub async fn lookup(State(app): State<App>, headers: HeaderMap, body: Bytes) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    let code = fields.get("registration_code");
    let problem = if !config.can_edit {
        "Registration editing is currently disabled."
    } else if code.is_empty() {
        "Please enter the registration code from your confirmation email."
    } else if !valid_code(code) {
        "We couldn't find a registration for that code. Double-check and try again."
    } else {
        match store::existing_code(&mut db, code).await {
            Ok(Some(_)) => return access::found(format!("/registration/{code}/")),
            Ok(None) => {
                "We couldn't find a registration for that code. Double-check and try again."
            }
            Err(_) => return unavailable(),
        }
    };
    staff_roster_notice::portal_redirect(
        &app,
        &headers,
        NoticePortal::Registration,
        LOOKUP,
        problem,
        true,
    )
}

pub async fn new_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    page(&app, actor, &headers, None, false, false, None).await
}

pub async fn edit_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(code): Path<String>,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    page(
        &app,
        actor,
        &headers,
        Some(&code),
        query.get("saved").is_some_and(|saved| saved == "1"),
        query
            .get("email")
            .is_some_and(|result| result == "unavailable"),
        None,
    )
    .await
}

async fn page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    code: Option<&str>,
    saved: bool,
    delivery_warning: bool,
    invalid: Option<(&Fields, &str)>,
) -> Response {
    let schools = if saved {
        Vec::new()
    } else {
        let Ok(schools) = school_directory::choices(&app.db).await else {
            return unavailable();
        };
        schools
    };
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let actor = match access::code_portal_actor(&mut db, app, actor, headers).await {
        Ok(actor) => actor,
        Err(error) => {
            drop(db);
            return error.response(app, headers).await;
        }
    };
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    let path = registration_path(code);
    let Ok(shell) = legacy_html::shell(&mut db, actor, &path).await else {
        return unavailable();
    };
    let (mut registration, persisted_code) = match load(&mut db, code).await {
        Ok(entry) => entry,
        Err(Error::NotFound) => {
            return legacy_html::render(
                "common/404.html",
                shell,
                context! {},
                StatusCode::NOT_FOUND,
            );
        }
        Err(_) => return unavailable(),
    };
    if registration.id.is_some() {
        registration.revision = store::revision(app, &registration);
    }
    let links = if saved {
        let Ok(links) = sqlx::query_as::<_, (String, String, String)>(
            "SELECT title, url, description FROM registration_registrationlink WHERE is_active = TRUE
             ORDER BY display_order, created_at, id",
        ).fetch_all(&mut *db).await else { return unavailable(); };
        links
            .into_iter()
            .filter(|(_, url, _)| {
                url::Url::parse(url).is_ok_and(|url| {
                    matches!(url.scheme(), "http" | "https") && url.host().is_some()
                })
            })
            .collect()
    } else {
        Vec::new()
    };
    if db.commit().await.is_err() {
        return unavailable();
    }
    let (posted, error) =
        invalid.map_or((None, None), |(fields, error)| (Some(fields), Some(error)));
    render(
        headers,
        shell,
        &registration,
        Portal {
            schools: &schools,
            config: &config,
            code: persisted_code.as_deref(),
            saved,
            delivery_warning,
            error,
            links,
            posted,
        },
    )
}

fn registration_path(code: Option<&str>) -> String {
    code.map_or_else(
        || "/registration/".into(),
        |code| format!("/registration/{code}/"),
    )
}

async fn load(
    db: &mut sqlx::MySqlConnection,
    code: Option<&str>,
) -> Result<(Registration, Option<String>), Error> {
    let Some(code) = code else {
        return Ok((Registration::default(), None));
    };
    if !valid_code(code) {
        return Err(Error::NotFound);
    }
    let id = store::existing_code(db, code)
        .await?
        .ok_or(Error::NotFound)?;
    let entry = store::entry(db, id).await?.ok_or(Error::NotFound)?;
    if !entry.code.eq_ignore_ascii_case(code) {
        return Err(Error::NotFound);
    }
    Ok((entry.form, Some(entry.code)))
}

struct Portal<'a> {
    schools: &'a [SchoolChoice],
    config: &'a store::Config,
    code: Option<&'a str>,
    saved: bool,
    delivery_warning: bool,
    error: Option<&'a str>,
    links: Vec<(String, String, String)>,
    posted: Option<&'a Fields>,
}

fn render(
    headers: &HeaderMap,
    shell: TemplateValue,
    registration: &Registration,
    view: Portal<'_>,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let (can_submit_registration, lock) = if view.code.is_some() {
        (
            view.config.can_edit,
            "Registration updates are currently disabled.",
        )
    } else {
        (
            view.config.can_create,
            "New registrations are currently closed.",
        )
    };
    let forms = if view.saved {
        TemplateValue::UNDEFINED
    } else {
        registration_page::context_for_forms(
            registration,
            view.schools,
            view.config.last_round,
            view.posted,
            view.error,
        )
    };
    let summary = view
        .code
        .map(|code| registration_page::summary(registration, code));
    let revision = view
        .posted
        .map_or(registration.revision.as_str(), |fields| {
            fields.get("revision")
        });
    let revision_input = registration_page::revision_input(view.code.is_some().then_some(revision));
    let post_registration_links: Vec<_> = view
        .links
        .into_iter()
        .map(|(title, url, description)| {
            context! {
                title, url, description,
            }
        })
        .collect();
    let messages = view.delivery_warning.then(|| {
        [context! {
            text => "Registration saved, but the confirmation email could not be sent.",
            tags => "warning", level => 30,
        }]
    });
    let mut response = legacy_html::render(
        "registration/portal.html",
        shell,
        context! {
            csrf_input => csrf.input(), revision_input,
            max_teams => MAX_TEAMS, can_submit_registration, registration_lock_message => (!can_submit_registration).then_some(lock),
            config => context! { crash_housing_enabled => view.config.housing },
            registration_saved => view.saved, summary, post_registration_links, messages,
            ..forms
        },
        if view.error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    csrf.set_cookie(&mut response, headers);
    response
}

pub async fn create(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, None, &body).await
}

pub async fn edit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(code): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, Some(&code), &body).await
}

async fn submit(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    code: Option<&str>,
    body: &[u8],
) -> Response {
    let fields = Fields::parse(body);
    if !login::valid_csrf(headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Ok(schools) = school_directory::choices(&app.db).await else {
        return unavailable();
    };
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let (stored, _) = match load(&mut db, code).await {
        Ok(entry) => entry,
        Err(Error::NotFound) => return not_found(),
        Err(_) => return unavailable(),
    };
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    if db.commit().await.is_err() {
        return unavailable();
    }
    let error = if let Some(mut registration) = Registration::from_form(&fields, config.last_round)
    {
        registration.preserve_private_contacts(&stored);
        if let Err(problem) = registration.validate() {
            Error::Invalid(problem)
        } else if (code.is_some() && !config.can_edit) || (code.is_none() && !config.can_create) {
            Error::Closed
        } else {
            let private = resolve_private_emails(&registration).await;
            match store::save(
                app,
                actor,
                headers,
                code,
                &registration,
                &school_directory::by_code(&schools),
                &private,
            )
            .await
            {
                Ok((id, code)) => {
                    let delivered =
                        registration_emails::send(&app.db, id, crate::email::origin(headers)).await;
                    let notice = if delivered { "" } else { "&email=unavailable" };
                    return access::found(format!("/registration/{code}/?saved=1{notice}"));
                }
                Err(error) => error,
            }
        }
    } else {
        Error::Invalid("Check the registration form and try again.")
    };
    let error = match error {
        Error::Invalid(message) => message,
        Error::Closed if code.is_some() => "Registration updates are currently disabled.",
        Error::Closed => "New registrations are currently closed.",
        Error::NotFound => return not_found(),
        Error::Access(response) => return *response,
        Error::Database => return unavailable(),
    };
    page(
        app,
        actor,
        headers,
        code,
        false,
        false,
        Some((&fields, error)),
    )
    .await
}

async fn resolve_private_emails(registration: &Registration) -> BTreeMap<(i32, i32), String> {
    let mut requests: BTreeMap<i32, BTreeSet<i32>> = BTreeMap::new();
    for member in registration.teams.iter().flat_map(|team| &team.members) {
        if !member.email.is_empty() || !member.use_private_email {
            continue;
        }
        if let Some((school, id)) = member
            .school
            .value
            .strip_prefix("apda:")
            .and_then(|school| school.parse::<i32>().ok())
            .zip(member.apda_id)
            .filter(|&(school, id)| school > 0 && id > 0)
        {
            requests.entry(school).or_default().insert(id);
        }
    }
    if !std::env::var("BLACK_ROD_PRIVATE_API_TOKEN").is_ok_and(|token| !token.is_empty()) {
        return BTreeMap::new();
    }
    // Limit outbound requests while looking up multiple affiliated schools.
    let mut jobs = tokio::task::JoinSet::new();
    let mut requests = requests.into_iter();
    let mut verified = BTreeSet::new();
    for _ in 0..4 {
        if let Some((school, ids)) = requests.next() {
            jobs.spawn(verify_school(school, ids));
        }
    }
    while let Some(result) = jobs.join_next().await {
        if let Ok(ids) = result {
            verified.extend(ids);
        }
        if let Some((school, ids)) = requests.next() {
            jobs.spawn(verify_school(school, ids));
        }
    }
    let ids = verified.iter().map(|&(_, id)| id).collect();
    let emails = directory::private_emails(&ids).await;
    verified
        .into_iter()
        .filter_map(|key| emails.get(&key.1).cloned().map(|email| (key, email)))
        .collect()
}

pub async fn debaters(Path(school): Path<i32>) -> Response {
    if school <= 0 {
        return private((
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Unknown school"})),
        ));
    }
    match directory::debaters(school).await {
        Ok(debaters) => private(Json(debaters)),
        Err(status) => private((
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"error": "Failed to fetch debaters"})),
        )),
    }
}

pub async fn email_status(State(app): State<App>, headers: HeaderMap, body: Bytes) -> Response {
    let token = headers
        .get("x-csrftoken")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    if !login::valid_csrf(&headers, token) {
        return login::csrf_error();
    }
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    drop(db);
    if !config.can_create {
        return private((
            StatusCode::FORBIDDEN,
            Json(json!({"error": "Registration is closed."})),
        ));
    }
    let Ok(payload) = serde_json::from_slice::<Value>(&body) else {
        return private((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Invalid JSON"})),
        ));
    };
    let Some((id, school)) = directory::positive_id(&payload["debater_id"])
        .zip(directory::positive_id(&payload["school_id"]))
    else {
        return private((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Invalid debater or school"})),
        ));
    };
    let available = directory::debaters(school)
        .await
        .ok()
        .is_some_and(|debaters| directory::includes(&debaters, id));
    let emails = if available {
        directory::private_emails(&BTreeSet::from([id])).await
    } else {
        BTreeMap::new()
    };
    let email = emails.get(&id);
    private((
        if available {
            StatusCode::OK
        } else {
            StatusCode::NOT_FOUND
        },
        Json(
            json!({"id": id, "has_email": email.is_some(), "masked_email": email.map_or_else(String::new, |email| directory::mask(email))}),
        ),
    ))
}

async fn verify_school(school: i32, ids: BTreeSet<i32>) -> Vec<(i32, i32)> {
    let Ok(debaters) = directory::debaters(school).await else {
        return Vec::new();
    };
    ids.into_iter()
        .filter(|&id| directory::includes(&debaters, id))
        .map(|id| (school, id))
        .collect()
}
