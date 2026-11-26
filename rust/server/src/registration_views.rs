use std::collections::{BTreeMap, BTreeSet, HashMap};

use axum::{
    Extension, Json,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use serde_json::{Value, json};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private, registration_directory as directory, registration_emails,
    registration_form::{Fields, MAX_TEAMS, Registration},
    registration_store::{self as store, Error},
    school_directory::{self, SchoolChoice},
    unavailable,
};

pub async fn form_script() -> Response {
    private((
        [(
            axum::http::header::CONTENT_TYPE,
            "text/javascript; charset=utf-8",
        )],
        include_str!("../templates/registration.js"),
    ))
}

fn not_found() -> Response {
    private((StatusCode::NOT_FOUND, "Registration not found."))
}

fn valid_code(code: &str) -> bool {
    !code.is_empty()
        && code.len() <= 255
        && code
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}

async fn lookup_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    message: Option<&str>,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let response = html::render(
        "code_lookup.html",
        context! {
            title => "Edit Registration", label => "Registration code", field => "registration_code",
            button => "Open Registration", csrf,
            message => message.or((!config.can_edit).then_some("Registration editing is currently disabled.")),
            has_error => message.is_some(), nav => Navigation::new(actor, true),
        },
        if message.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn search(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    lookup_page(&app, actor, &headers, None).await
}

pub async fn lookup(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
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
    } else if !valid_code(code) {
        "Please enter the registration code from your confirmation email."
    } else {
        match store::existing_code(&mut db, code).await {
            Ok(Some(_)) => return access::found(format!("/registration/{code}/")),
            Ok(None) => {
                "We couldn't find a registration for that code. Double-check and try again."
            }
            Err(_) => return unavailable(),
        }
    };
    drop(db);
    lookup_page(&app, actor, &headers, Some(problem)).await
}

pub async fn new_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    page(&app, actor, &headers, None, false, false).await
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
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    let registration = if let Some(code) = code {
        if !valid_code(code) {
            return not_found();
        }
        let id = match store::existing_code(&mut db, code).await {
            Ok(Some(id)) => id,
            Ok(None) => return not_found(),
            Err(_) => return unavailable(),
        };
        match store::entry(&mut db, id).await {
            Ok(Some(entry)) => entry.form,
            Ok(None) => return not_found(),
            Err(_) => return unavailable(),
        }
    } else {
        Registration::default()
    };
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
    drop(db);
    let Ok(schools) = school_directory::choices(&app.db).await else {
        return unavailable();
    };
    render(
        actor,
        headers,
        &registration,
        Portal {
            schools: &schools,
            config: &config,
            code,
            saved,
            delivery_warning,
            error: None,
            links,
        },
    )
}

struct Portal<'a> {
    schools: &'a [SchoolChoice],
    config: &'a store::Config,
    code: Option<&'a str>,
    saved: bool,
    delivery_warning: bool,
    error: Option<&'a str>,
    links: Vec<(String, String, String)>,
}

fn render(
    actor: Actor,
    headers: &HeaderMap,
    registration: &Registration,
    view: Portal<'_>,
) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let rounds: Vec<_> = (0..=view.config.last_round)
        .map(|round| {
            if round == 0 {
                (0, "availability_outround".into(), "Outrounds".into())
            } else {
                (
                    round,
                    format!("availability_round_{round}"),
                    format!("Round {round}"),
                )
            }
        })
        .collect();
    let can_submit = if view.code.is_some() {
        view.config.can_edit
    } else {
        view.config.can_create
    };
    let response = html::render(
        "registration.html",
        context! {
            title => "Tournament Registration", nav => Navigation::new(actor, true), csrf, registration,
            config => view.config, schools => view.schools, rounds, code => view.code,
            error => view.error, saved => view.saved, delivery_warning => view.delivery_warning, links => view.links, can_submit,
            private_email_enabled => std::env::var("BLACK_ROD_PRIVATE_API_TOKEN").is_ok_and(|token| !token.is_empty()),
            blank_team => crate::registration_form::Team::default(),
            blank_judge => crate::registration_form::Judge::default(), max_teams => MAX_TEAMS,
        },
        if view.error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
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
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    if let Some(code) = code {
        if !valid_code(code) {
            return not_found();
        }
        match store::existing_code(&mut db, code).await {
            Ok(Some(_)) => {}
            Ok(None) => return not_found(),
            Err(_) => return unavailable(),
        }
    }
    let Ok(config) = store::config(&mut db).await else {
        return unavailable();
    };
    drop(db);
    let Ok(schools) = school_directory::choices(&app.db).await else {
        return unavailable();
    };
    let parsed = Registration::from_form(&fields, config.last_round);
    let mut registration = parsed.unwrap_or_else(|| Registration {
        email: fields.get("email").into(),
        school: crate::registration_form::SchoolField {
            value: fields.get("school").into(),
            name: fields.get("school_name").into(),
        },
        ..Registration::default()
    });
    // Preserve the revision even when validation fails.
    registration.revision = fields.get("revision").into();
    let error = if let Err(problem) = registration.validate() {
        Error::Invalid(problem)
    } else if (code.is_some() && !config.can_edit) || (code.is_none() && !config.can_create) {
        Error::Closed
    } else {
        let private = resolve_private_emails(&registration).await;
        match store::save(
            &app.db,
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
    };
    let error = match error {
        Error::Invalid(message) => message,
        Error::Closed if code.is_some() => "Registration updates are currently disabled.",
        Error::Closed => "New registrations are currently closed.",
        Error::NotFound => return not_found(),
        Error::Database => return unavailable(),
    };
    render(
        actor,
        headers,
        &registration,
        Portal {
            schools: &schools,
            config: &config,
            code,
            saved: false,
            delivery_warning: false,
            error: Some(error),
            links: Vec::new(),
        },
    )
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
