use std::collections::BTreeMap;

use axum::{
    Extension, Router,
    extract::{DefaultBodyLimit, Multipart, Path, Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
    routing::get,
};
use minijinja::context;
use serde::{Deserialize, Serialize};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private, roster_spreadsheet_data as data,
    roster_spreadsheet_layout::Roster,
    spreadsheets::{self, Format},
    staff_roster, unavailable,
};

const PATH: &str = "/import_data/";
const MAX_FILE_BYTES: usize = 12 * 1024 * 1024;

pub fn routes() -> Router<App> {
    Router::new()
        .route(
            PATH,
            get(page)
                .post(upload)
                .layer(DefaultBodyLimit::max(MAX_FILE_BYTES * 4 + 256 * 1024)),
        )
        .route("/export_data/{filename}", get(download))
}

fn full_tab(actor: Actor) -> bool {
    matches!(
        actor,
        Actor::User {
            board: false,
            superuser: true,
            ..
        }
    )
}

#[derive(Deserialize)]
struct Feedback {
    imported: Option<usize>,
}

async fn page(
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(feedback): Query<Feedback>,
) -> Response {
    render(actor, &headers, feedback.imported, None, &[])
}

#[derive(Serialize)]
struct ImportResult {
    label: &'static str,
    rows: Option<usize>,
    errors: Vec<String>,
}

fn render(
    actor: Actor,
    headers: &HeaderMap,
    imported: Option<usize>,
    error: Option<&str>,
    results: &[ImportResult],
) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let types: Vec<_> = Roster::ALL
        .into_iter()
        .filter(|&kind| kind != Roster::Scratches || full_tab(actor))
        .map(|kind| (kind.key(), kind.label(), kind.upload()))
        .collect();
    let invalid = error.is_some() || results.iter().any(|item| !item.errors.is_empty());
    let response = html::render(
        "roster_spreadsheets.html",
        context! {
            title => "Import and Export Roster", nav => Navigation::new(actor, true), csrf, error, types,
            imported, results, toolbar => staff_roster::toolbar(),
        },
        if invalid {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

async fn read_upload(
    mut parts: Multipart,
    allow_scratches: bool,
) -> Result<(String, BTreeMap<Roster, Uploaded>), &'static str> {
    let mut csrf = None;
    let mut files = BTreeMap::new();
    while let Some(field) = parts
        .next_field()
        .await
        .map_err(|_| "The upload is invalid or too large.")?
    {
        if field.name() == Some("csrfmiddlewaretoken") {
            if csrf.is_some() {
                return Err("The upload contains more than one form token.");
            }
            csrf = Some(
                field
                    .text()
                    .await
                    .map_err(|_| "The form token could not be read.")?,
            );
            continue;
        }
        let Some(kind) = Roster::ALL
            .into_iter()
            .find(|kind| field.name() == Some(kind.upload()))
        else {
            return Err("The upload contains an unknown field.");
        };
        if kind == Roster::Scratches && !allow_scratches {
            return Err("This account cannot import scratches.");
        }
        let Some(name) = field.file_name().filter(|name| !name.is_empty()) else {
            continue;
        };
        if files.contains_key(&kind) {
            return Err("Upload one spreadsheet per roster type.");
        }
        let format = name
            .rsplit_once('.')
            .and_then(|(_, extension)| Format::parse(extension))
            .ok_or("Choose CSV, XLS, or XLSX spreadsheets.")?;
        let contents = field
            .bytes()
            .await
            .map_err(|_| "The spreadsheet is too large.")?;
        if contents.len() > MAX_FILE_BYTES {
            return Err("Each spreadsheet must be 12 MB or smaller.");
        }
        files.insert(
            kind,
            Uploaded {
                format,
                bytes: contents.to_vec(),
            },
        );
    }
    Ok((csrf.unwrap_or_default(), files))
}

struct Uploaded {
    format: Format,
    bytes: Vec<u8>,
}

async fn upload(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    parts: Multipart,
) -> Response {
    let Actor::User {
        id, board: false, ..
    } = actor
    else {
        return access::access_denied();
    };
    let (token, files) = match read_upload(parts, full_tab(actor)).await {
        Ok(data) => data,
        Err(message) => return render(actor, &headers, None, Some(message), &[]),
    };
    if !login::valid_csrf(&headers, &token) {
        return login::csrf_error();
    }
    if files.is_empty() {
        return render(
            actor,
            &headers,
            None,
            Some("Choose at least one spreadsheet."),
            &[],
        );
    }
    let mut results = Vec::new();
    let mut total = 0;
    for (kind, Uploaded { format, bytes }) in files {
        let (rows, errors) = match data::import(&app.db, kind, format, bytes, id).await {
            Ok(count) => {
                total += count;
                (Some(count), Vec::new())
            }
            Err(errors) => (None, errors),
        };
        results.push(ImportResult {
            label: kind.label(),
            rows,
            errors,
        });
    }
    if results.iter().all(|result| result.errors.is_empty()) {
        access::found(format!("{PATH}?imported={total}"))
    } else {
        render(actor, &headers, None, None, &results)
    }
}

async fn download(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(filename): Path<String>,
) -> Response {
    let legal = filename
        .rsplit_once('.')
        .and_then(|(name, extension)| Roster::parse(name).zip(Format::parse(extension)));
    let Some((kind, format)) = legal else {
        return private((
            StatusCode::BAD_REQUEST,
            "Choose teams, judges, rooms, or scratches and CSV, XLS, or XLSX.",
        ));
    };
    if kind == Roster::Scratches && !full_tab(actor) {
        return access::access_denied();
    }
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let Ok(sheet) = data::export(&mut db, kind).await else {
        return unavailable();
    };
    if db.commit().await.is_err() {
        return unavailable();
    }
    let bytes = tokio::task::spawn_blocking(move || {
        let headers: Vec<_> = sheet.headers.iter().map(String::as_str).collect();
        spreadsheets::write(format, &headers, &sheet.rows, true)
    })
    .await;
    let bytes = match bytes {
        Ok(Ok(bytes)) => bytes,
        Ok(Err(message)) => return private((StatusCode::BAD_REQUEST, message)),
        Err(_) => return unavailable(),
    };
    let disposition = HeaderValue::from_str(&format!(
        "attachment; filename=\"{}.{}\"",
        kind.key(),
        format.suffix()
    ))
    .expect("static roster filename");
    private((
        [
            (
                header::CONTENT_TYPE,
                HeaderValue::from_static(format.content_type()),
            ),
            (header::CONTENT_DISPOSITION, disposition),
        ],
        bytes,
    ))
}
