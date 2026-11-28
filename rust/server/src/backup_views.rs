use std::collections::BTreeSet;

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, Multipart, Path, Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
    routing::get,
};
use chrono::{DateTime, FixedOffset, Utc};
use chrono_tz::America::New_York;
use minijinja::context;
use serde::{Deserialize, Serialize};

use crate::{
    App, access, backup_dump,
    backup_restore::{self, RestoreError},
    backups::{BackupError, MAX_BACKUP_BYTES, SUFFIX, valid_key},
    html::{self, Navigation},
    legacy_html,
    legacy_session::Actor,
    login, private,
    registration_form::Fields,
    settings::Settings,
    unavailable,
};

const LIST: &str = "/pairing/view_backups/";
const TYPES: &[&str] = &[
    "Manual",
    "Initial",
    "Before New Tournament",
    "Before Judge Assign",
    "Before Pairing",
    "Before Room Assign",
    "Before the Break",
    "Upload",
    "Other",
];

pub fn routes() -> Router<App> {
    Router::new()
        .route(LIST, get(list))
        .route("/pairing/backup/", get(back).post(create))
        .route(
            "/upload_backup/",
            get(back)
                .post(upload)
                .layer(DefaultBodyLimit::max(MAX_BACKUP_BYTES + 256 * 1024)),
        )
        .route("/backup/{key}/", get(detail))
        .route("/backup/{key}", get(detail))
        .route("/backup/download/{key}/", get(download))
        .route("/backup/restore/{key}/", get(restore_page).post(restore))
}

fn key(value: &str) -> Option<&str> {
    let value = value.strip_suffix(SUFFIX).unwrap_or(value);
    valid_key(value).then_some(value)
}

#[derive(Serialize)]
struct Backup {
    url_key: String,
    name: String,
    category: &'static str,
    round: String,
    key: String,
    legacy_time: String,
    time: String,
    scratches: &'static str,
}

impl Backup {
    fn new(key: String) -> Self {
        let mut metadata = key.rsplitn(5, '_');
        let (flag, time, round, kind, name) = (
            metadata.next(),
            metadata.next(),
            metadata.next(),
            metadata.next(),
            metadata.next(),
        );
        let seconds = time.and_then(|time| time.parse::<i64>().ok());
        let category = kind
            .and_then(|kind| kind.parse::<usize>().ok())
            .and_then(|kind| TYPES.get(kind).copied())
            .unwrap_or("Unknown");
        let time = seconds
            .filter(|&time| time > 0)
            .and_then(|time| DateTime::from_timestamp(time, 0))
            .map(|stamp| {
                stamp
                    .with_timezone(&New_York)
                    .format("%b %e, %Y %I:%M %p %Z")
                    .to_string()
            })
            .unwrap_or_default();
        let scratches = match flag {
            Some("1") => "Yes",
            Some("0") => "No",
            _ => "Unknown",
        };
        Self {
            name: name.unwrap_or(&key).into(),
            category,
            round: round.unwrap_or_default().into(),
            legacy_time: legacy_time(seconds),
            time,
            scratches,
            url_key: url_component(&key),
            key: if key.contains(['?', '#', '%']) {
                url_component(&key)
            } else {
                key
            },
        }
    }
}

fn legacy_time(seconds: Option<i64>) -> String {
    let east = FixedOffset::west_opt(5 * 3600).expect("fixed EST offset");
    let Some(stamp) = seconds.and_then(|time| DateTime::from_timestamp(time, 0)) else {
        return "Unknown".into();
    };
    let stamp = stamp.with_timezone(&east);
    let today = Utc::now().with_timezone(&east).date_naive();
    let format = match (today - stamp.date_naive()).num_days() {
        0 => "Today at %I:%M %p",
        1 => "Yesterday at %I:%M %p",
        _ => "%b %d at %I:%M %p",
    };
    stamp.format(format).to_string()
}

#[derive(Default, Deserialize, Serialize)]
struct ListOptions {
    saved: Option<u8>,
    #[serde(rename = "type", default)]
    category: String,
    #[serde(default)]
    round: String,
    #[serde(default)]
    scratches: String,
}

async fn list(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(options): Query<ListOptions>,
) -> Response {
    show_list(&app, actor, &headers, &options, None).await
}

async fn show_list(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    options: &ListOptions,
    error: Option<&str>,
) -> Response {
    let Ok(csrf) = login::TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let keys = match app.backups.list().await {
        Ok(keys) => keys,
        Err(issue) => return backup_error(issue),
    };
    let mut items: Vec<_> = keys.into_iter().map(Backup::new).collect();
    items.sort_by(|first, second| first.round.cmp(&second.round));
    let categories: BTreeSet<_> = items
        .iter()
        .map(|entry| entry.category)
        .filter(|category| *category != "Unknown")
        .collect();
    let mut rounds: Vec<_> = items
        .iter()
        .map(|entry| entry.round.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    rounds.sort_by_key(|value| {
        (
            value.parse::<u32>().unwrap_or(u32::MAX),
            value.to_lowercase(),
        )
    });
    items.retain(|entry| {
        (options.category.is_empty() || entry.category == options.category)
            && (options.round.is_empty() || entry.round == options.round)
            && (options.scratches.is_empty() || entry.scratches == options.scratches)
    });
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(shell) = legacy_html::shell(&mut db, actor, LIST).await else {
        return unavailable();
    };
    let messages = if let Some(text) = error {
        vec![context! { text, tags => "error", level => 40 }]
    } else if options.saved == Some(1) {
        vec![context! { text => "Backup saved.", tags => "success", level => 25 }]
    } else {
        Vec::new()
    };
    let mut response = legacy_html::render(
        "tab/backup_list.html",
        shell,
        context! {
            title => "Backup List",
            csrf_input => csrf.input(),
            widgets => backup_widgets(),
            headers => ["Name", "Type", "Round", "Timestamp", "Scratches"],
            filters => [
                context! { id => "type", label => "Type", options => categories },
                context! { id => "round", label => "Round", options => rounds },
                context! { id => "scratches", label => "Scratches", options => ["No", "Unknown", "Yes"] },
            ],
            backups => items.iter().map(|item| (
                &item.key, &item.name, item.category, &item.round, &item.legacy_time, item.scratches,
            )).collect::<Vec<_>>(),
            messages,
            DEFAULT_MESSAGE_LEVELS => context! { ERROR => 40 },
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    csrf.set_cookie(&mut response, headers);
    response
}

fn backup_widgets() -> minijinja::Value {
    context! {
        create_form => context! {
            backup_name => context! {
                field => context! {label_tag => minijinja::Value::from_safe_string(r#"<label for="id_backup_name">Backup name:</label>"#.into())},
                input => r#"<input type="text" name="backup_name" placeholder="Enter backup name" pattern="[^_]*" maxlength="255" class="form-control form-control-sm" required id="id_backup_name">"#,
            },
            include_scratches => context! {
                checkbox => true,
                field => context! {label_tag => minijinja::Value::from_safe_string(r#"<label for="id_include_scratches">Include scratches:</label>"#.into())},
                input => r#"<input type="checkbox" name="include_scratches" class="form-check-input" id="id_include_scratches" checked>"#,
            },
        },
        upload_form => context! {
            file => context! {
                field => context! {label_tag => minijinja::Value::from_safe_string(r#"<label for="id_file">Your Backup File:</label>"#.into())},
                input => r#"<input type="file" name="file" class="form-control-file form-control-sm" required id="id_file">"#,
            },
        },
    }
}

async fn back() -> Response {
    access::found(LIST)
}

async fn round(app: &App) -> Result<String, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    let settings = Settings::load(&mut db, &["cur_round"]).await?;
    Ok(settings.integer("cur_round", 1).to_string())
}

async fn create(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let name = fields.get("backup_name");
    if !valid_key(name) || name.contains('_') {
        return show_list(
            &app,
            actor,
            &headers,
            &ListOptions::default(),
            Some("Enter a backup name without underscores or path separators."),
        )
        .await;
    }
    let Ok(round) = round(&app).await else {
        return unavailable();
    };
    match app
        .backups
        .create(name, 0, &round, fields.flag("include_scratches"))
        .await
    {
        Ok(_) => access::found(format!("{LIST}?saved=1")),
        Err(BackupError::InvalidName) => {
            show_list(
                &app,
                actor,
                &headers,
                &ListOptions::default(),
                Some("The backup name is too long for storage."),
            )
            .await
        }
        Err(issue) => backup_error(issue),
    }
}

async fn upload(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    mut form: Multipart,
) -> Response {
    let mut csrf = String::new();
    let mut bytes = None;
    let mut name = String::new();
    loop {
        let field = match form.next_field().await {
            Ok(Some(field)) => field,
            Ok(None) => break,
            Err(_) => return backup_error(BackupError::TooLarge),
        };
        match field.name() {
            Some("csrfmiddlewaretoken") => {
                let Ok(value) = field.text().await else {
                    return login::csrf_error();
                };
                csrf = value;
            }
            Some("file") if bytes.is_none() => {
                name = field.file_name().unwrap_or_default().to_owned();
                if !name.to_ascii_lowercase().ends_with(".sql")
                    || !valid_key(&name)
                    || name.len() > 160
                {
                    return show_list(
                        &app,
                        actor,
                        &headers,
                        &ListOptions::default(),
                        Some("Choose a SQL backup file with a valid filename."),
                    )
                    .await;
                }
                let Ok(content) = field.bytes().await else {
                    return backup_error(BackupError::TooLarge);
                };
                bytes = Some(content);
            }
            _ => return private((StatusCode::BAD_REQUEST, "Invalid backup upload.")),
        }
    }
    if !login::valid_csrf(&headers, &csrf) {
        return login::csrf_error();
    }
    let Some(bytes) = bytes else {
        return show_list(
            &app,
            actor,
            &headers,
            &ListOptions::default(),
            Some("Choose a SQL backup file."),
        )
        .await;
    };
    if bytes.len() > MAX_BACKUP_BYTES {
        return backup_error(BackupError::TooLarge);
    }
    let check = tokio::task::spawn_blocking(move || {
        backup_dump::parse(&bytes)?;
        Ok::<_, &'static str>(bytes)
    })
    .await;
    let bytes = match check {
        Ok(Ok(bytes)) => bytes,
        Ok(Err(message)) => {
            return show_list(
                &app,
                actor,
                &headers,
                &ListOptions::default(),
                Some(message),
            )
            .await;
        }
        Err(_) => return unavailable(),
    };
    let Ok(round) = round(&app).await else {
        return unavailable();
    };
    let name = name
        .strip_suffix(SUFFIX)
        .or_else(|| name.strip_suffix(".sql"))
        .unwrap_or(&name);
    match app.backups.upload(name, &round, bytes.to_vec()).await {
        Ok(_) => access::found(format!("{LIST}?saved=1")),
        Err(issue) => backup_error(issue),
    }
}

async fn detail(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(name): Path<String>,
) -> Response {
    show_detail(&app, actor, &headers, &name, false, None).await
}

async fn restore_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(name): Path<String>,
) -> Response {
    show_detail(&app, actor, &headers, &name, true, None).await
}

async fn show_detail(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    name: &str,
    restore: bool,
    error: Option<&str>,
) -> Response {
    let Some(name) = key(name) else {
        return backup_error(BackupError::InvalidName);
    };
    let available = match app.backups.list().await {
        Ok(available) => available,
        Err(issue) => return backup_error(issue),
    };
    if !available.iter().any(|key| key == name) {
        return backup_error(BackupError::Missing);
    }
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let item = Backup::new(name.into());
    let result = html::render(
        "backup_detail.html",
        context! {
            title => if restore { "Restore Backup" } else { "Backup Details" }, nav => Navigation::new(actor, true),
            csrf, item, restore, error,
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(result, headers, &csrf)
}

async fn restore(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(name): Path<String>,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Some(name) = key(&name) else {
        return backup_error(BackupError::InvalidName);
    };
    if !fields.flag("confirm") {
        return show_detail(
            &app,
            actor,
            &headers,
            name,
            true,
            Some("Confirm that this backup will replace tournament data."),
        )
        .await;
    }
    match backup_restore::restore(&app, name).await {
        Ok(()) => {
            let mut response = access::found("/public/login/");
            login::set_cookie(
                &mut response,
                "sessionid",
                "",
                0,
                login::secure_request(&headers),
                true,
            );
            response
        }
        Err(RestoreError::Invalid(message)) => {
            show_detail(&app, actor, &headers, name, true, Some(message)).await
        }
        Err(RestoreError::Backup(issue)) => backup_error(issue),
        Err(RestoreError::Database) => unavailable(),
    }
}

async fn download(State(app): State<App>, Path(name): Path<String>) -> Response {
    let Some(name) = key(&name) else {
        return backup_error(BackupError::InvalidName);
    };
    match app.backups.content(name).await {
        Ok(raw) => {
            let filename = format!("{name}{SUFFIX}");
            let safe = url_component(&filename);
            let header = HeaderValue::from_str(&format!("attachment; filename*=UTF-8''{safe}"))
                .expect("percent-encoded backup name");
            private((
                [
                    (
                        header::CONTENT_TYPE,
                        HeaderValue::from_static("application/sql"),
                    ),
                    (header::CONTENT_DISPOSITION, header),
                    (
                        header::X_CONTENT_TYPE_OPTIONS,
                        HeaderValue::from_static("nosniff"),
                    ),
                ],
                raw,
            ))
        }
        Err(issue) => backup_error(issue),
    }
}

fn url_component(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes())
        .collect::<String>()
        .replace('+', "%20")
}

fn backup_error(issue: BackupError) -> Response {
    match issue {
        BackupError::InvalidName | BackupError::Missing => {
            private((StatusCode::NOT_FOUND, "Backup not found."))
        }
        BackupError::TooLarge => private((
            StatusCode::PAYLOAD_TOO_LARGE,
            "Backup exceeds the supported size.",
        )),
        BackupError::Storage => unavailable(),
    }
}
