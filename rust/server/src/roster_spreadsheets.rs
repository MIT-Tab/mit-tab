use std::collections::{BTreeMap, btree_map::Entry};

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, FromRequest, Multipart, Path, Request, State},
    http::{HeaderMap, HeaderValue, StatusCode, Uri, header},
    response::{IntoResponse, Response},
    routing::get,
};
use minijinja::{Value, context};

use crate::{
    App, access, legacy_html,
    legacy_session::Actor,
    login::{self, TemplateCsrf},
    private,
    registration_form::Fields,
    roster_spreadsheet_data as data,
    roster_spreadsheet_layout::Roster,
    spreadsheets::{self, Format},
    staff_roster::{form_return_path, form_return_target},
    staff_roster_notice, unavailable,
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
        .route("/export_data/{filename}", get(download).head(download_head))
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

async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    render(
        &app,
        actor,
        &headers,
        &uri,
        &Upload::default(),
        &BTreeMap::new(),
    )
    .await
}

type Results = BTreeMap<Roster, Vec<String>>;

#[derive(Default)]
struct Upload {
    fields: Fields,
    files: BTreeMap<Roster, Uploaded>,
    errors: BTreeMap<Roster, &'static str>,
    non_field_error: Option<&'static str>,
}

struct Uploaded {
    format: Option<Format>,
    bytes: Vec<u8>,
}

fn file_field(kind: Roster, error: Option<&str>) -> Value {
    let name = kind.upload();
    let label = match kind {
        Roster::Teams => "Teams Data File",
        Roster::Judges => "Judge Data File",
        Roster::Rooms => "Room Data File",
        Roster::Scratches => "Scratch Data File",
    };
    let help_id = format!("id_{name}_helptext");
    let error_id = if error.is_some() {
        format!("id_{name}_error")
    } else {
        String::new()
    };
    let (invalid, accessibility, description) = if error.is_some() {
        (
            " is-invalid",
            r#" aria-invalid="true""#,
            format!("{help_id} {error_id}"),
        )
    } else {
        ("", "", help_id.clone())
    };
    context! {
        input => format!(r#"<input type="file" name="{name}" accept=".csv,.xls,.xlsx" class="form-control-file{invalid}"{accessibility} aria-describedby="{description}" id="id_{name}">"#),
        label_tag => Value::from_safe_string(format!(r#"<label for="id_{name}">{label}:</label>"#)),
        is_file => true,
        errors => error.map(|message| [message]),
        error_id, help_id, help_text => "CSV (UTF-8), Excel XLS, or Excel XLSX.",
    }
}

async fn render(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    uri: &Uri,
    upload: &Upload,
    results: &Results,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let actor = match access::data_entry_actor(&mut db, app, actor, headers).await {
        Ok(actor) => actor,
        Err(error) => {
            drop(db);
            return error.response(app, headers).await;
        }
    };
    let Ok(shell) = legacy_html::shell(&mut db, actor, PATH).await else {
        return unavailable();
    };
    if db.commit().await.is_err() {
        return unavailable();
    }
    let scratches = full_tab(actor);
    let visible_fields: Vec<_> = Roster::ALL
        .into_iter()
        .filter(|&kind| kind != Roster::Scratches || scratches)
        .map(|kind| file_field(kind, upload.errors.get(&kind).copied()))
        .collect();
    let info = |kind| {
        let errors = results.get(&kind);
        context! { uploaded => errors.is_some(), errors }
    };
    let non_field_errors = upload.non_field_error.map(|message| {
        Value::from_safe_string(format!(
            r#"<ul class="errorlist nonfield"><li>{}</li></ul>"#,
            legacy_html::escape(message)
        ))
    });
    let mut response = staff_roster_notice::render(
        app,
        headers,
        PATH,
        "common/data_upload.html",
        shell,
        context! {
            title => "Upload Input Files",
            form => context! { visible_fields, non_field_errors },
            csrf_input => csrf.input(),
            return_path => form_return_path(uri, headers, Some(&upload.fields)),
            team_info => info(Roster::Teams), judge_info => info(Roster::Judges),
            room_info => info(Roster::Rooms),
            scratch_info => scratches.then(|| info(Roster::Scratches)),
            can_export_scratches => scratches,
            export_kinds => ["teams", "judges", "rooms"], export_formats => ["xlsx", "xls", "csv"],
        },
        StatusCode::OK,
    );
    csrf.set_cookie(&mut response, headers);
    response
}

async fn read_upload(
    app: &App,
    request: Request,
    allow_scratches: bool,
) -> Result<Upload, Box<Response>> {
    let multipart = request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.starts_with("multipart/form-data"));
    if !multipart {
        let body = Bytes::from_request(request, app)
            .await
            .map_err(|error| Box::new(private(error.into_response())))?;
        return Ok(Upload {
            fields: Fields::parse(&body),
            ..Upload::default()
        });
    }
    let mut parts = Multipart::from_request(request, app)
        .await
        .map_err(|error| Box::new(private(error.into_response())))?;
    let mut upload = Upload::default();
    while let Some(field) = parts
        .next_field()
        .await
        .map_err(|error| Box::new(private(error.into_response())))?
    {
        if let Some(name @ ("csrfmiddlewaretoken" | "return_to")) = field.name()
            && field.file_name().is_none()
        {
            let name = name.to_owned();
            let value = field
                .text()
                .await
                .map_err(|error| Box::new(private(error.into_response())))?;
            upload.fields.push(name, value);
            continue;
        }
        let Some(kind) = Roster::ALL
            .into_iter()
            .find(|kind| field.name() == Some(kind.upload()))
        else {
            continue;
        };
        if kind == Roster::Scratches && !allow_scratches {
            continue;
        }
        let Some(name) = field.file_name().filter(|name| !name.is_empty()) else {
            continue;
        };
        let format = name
            .rsplit_once('.')
            .filter(|(stem, _)| stem.chars().any(|character| character != '.'))
            .and_then(|(_, extension)| Format::parse(extension));
        let bytes = field
            .bytes()
            .await
            .map_err(|error| Box::new(private(error.into_response())))?;
        if bytes.is_empty() {
            upload.errors.insert(kind, "The submitted file is empty.");
        } else if bytes.len() > MAX_FILE_BYTES {
            upload
                .errors
                .insert(kind, "Each spreadsheet must be 12 MB or smaller.");
        } else if let Entry::Vacant(entry) = upload.files.entry(kind) {
            entry.insert(Uploaded {
                format,
                bytes: bytes.to_vec(),
            });
        } else {
            upload
                .errors
                .insert(kind, "Upload one spreadsheet per roster type.");
        }
    }
    Ok(upload)
}

async fn upload(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    request: Request,
) -> Response {
    let mut upload = match read_upload(&app, request, full_tab(actor)).await {
        Ok(data) => data,
        Err(response) => return *response,
    };
    if !login::valid_csrf(&headers, upload.fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    if upload.files.is_empty() {
        upload.non_field_error = Some("Choose at least one spreadsheet to import.");
    }
    let mut results = Results::new();
    if upload.errors.is_empty() && upload.non_field_error.is_none() {
        for (kind, Uploaded { format, bytes }) in std::mem::take(&mut upload.files) {
            let errors = match format {
                Some(format) => {
                    match data::import(&app, actor, &headers, kind, format, bytes).await {
                        Ok(()) => Vec::new(),
                        Err(data::ImportError::Validation(errors)) => errors,
                        Err(data::ImportError::Access(error)) => {
                            return error.response(&app, &headers).await;
                        }
                    }
                }
                None => vec!["Unsupported file format. Choose a .csv, .xls, or .xlsx file.".into()],
            };
            results.insert(kind, errors);
        }
        if results.values().all(Vec::is_empty) {
            let target = form_return_target(&uri, &headers, Some(&upload.fields))
                .unwrap_or_else(|| "/".into());
            return staff_roster_notice::saved(
                &app,
                &headers,
                &target,
                "Data imported successfully",
            );
        }
    }
    render(&app, actor, &headers, &uri, &upload, &results).await
}

async fn download(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(filename): Path<String>,
) -> Response {
    let Some((name, extension)) = filename
        .rsplit_once('.')
        .filter(|(name, ext)| !name.is_empty() && !ext.is_empty())
    else {
        return crate::administration::not_found(State(app), Extension(actor)).await;
    };
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let actor = match access::data_entry_actor(&mut db, &app, actor, &headers).await {
        Ok(actor) => actor,
        Err(error) => {
            drop(db);
            return error.response(&app, &headers).await;
        }
    };
    if name == "scratches" && !full_tab(actor) {
        return private((
            StatusCode::FORBIDDEN,
            "Scratch exports require permission to view scratches.",
        ));
    }
    let Some((kind, format)) = Roster::parse(name)
        .zip(Format::parse(extension).filter(|format| format.suffix() == extension))
    else {
        return private((
            StatusCode::BAD_REQUEST,
            "Choose teams, judges, rooms, or scratches and csv, xls, or xlsx.",
        ));
    };
    let Ok(sheet) = data::export(&mut db, kind).await else {
        return unavailable();
    };
    if db.commit().await.is_err() {
        return unavailable();
    }
    let bytes = tokio::task::spawn_blocking(move || {
        let headers: Vec<_> = sheet.headers.iter().map(String::as_str).collect();
        spreadsheets::write_roster(format, &headers, &sheet.rows)
    })
    .await;
    let bytes = match bytes {
        Ok(Ok(bytes)) => bytes,
        Ok(Err(_)) => {
            return private((
                StatusCode::BAD_REQUEST,
                "Could not export spreadsheet. Check cell text and spreadsheet format limits, or try another download format.",
            ));
        }
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

async fn download_head(Extension(actor): Extension<Actor>) -> Response {
    if full_tab(actor) {
        private((StatusCode::METHOD_NOT_ALLOWED, [(header::ALLOW, "GET")]))
    } else {
        access::access_denied()
    }
}
