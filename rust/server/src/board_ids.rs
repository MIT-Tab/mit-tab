use axum::{
    Extension, Form,
    extract::{Path, State, rejection::FormRejection},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access,
    board::{self, BoardError},
    html::{self, Navigation},
    legacy_session::Actor,
    unavailable,
};

#[derive(Clone, Copy)]
pub enum Kind {
    School,
    Debater,
}

impl Kind {
    fn label(self) -> &'static str {
        match self {
            Self::School => "School",
            Self::Debater => "Debater",
        }
    }

    fn key(self) -> &'static str {
        match self {
            Self::School => "school",
            Self::Debater => "debater",
        }
    }
}

#[derive(Serialize)]
pub struct Row {
    pub id: i32,
    pub name: String,
    pub apda_id: Option<i32>,
}

impl Row {
    pub fn missing(&self) -> bool {
        self.apda_id.is_none_or(|id| id == -1)
    }
}

pub async fn list(db: &mut MySqlConnection, kind: Kind) -> Result<Vec<Row>, sqlx::Error> {
    let statement = match kind {
        Kind::School => "SELECT id, name, apda_id FROM tab_school ORDER BY name, id",
        Kind::Debater => "SELECT id, name, apda_id FROM tab_debater ORDER BY name, id",
    };
    Ok(sqlx::query_as::<_, (i32, String, Option<i32>)>(statement)
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(id, name, apda_id)| Row { id, name, apda_id })
        .collect())
}

#[derive(Serialize)]
struct Affiliation {
    name: String,
    school: String,
    hybrid: Option<String>,
}

pub async fn school(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(id): Path<i32>,
) -> Response {
    detail(&app, actor, &headers, id, Kind::School).await
}

pub async fn debater(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(id): Path<i32>,
) -> Response {
    detail(&app, actor, &headers, id, Kind::Debater).await
}

async fn detail(app: &App, actor: Actor, headers: &HeaderMap, id: i32, kind: Kind) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let sql = match kind {
            Kind::School => "SELECT name, apda_id FROM tab_school WHERE id = ?",
            Kind::Debater => "SELECT name, apda_id FROM tab_debater WHERE id = ?",
        };
        let Some((name, apda_id)) = sqlx::query_as::<_, (String, Option<i32>)>(sql)
            .bind(id).fetch_optional(&mut *db).await? else { return Err(BoardError::Missing); };
        let queries = match kind {
            Kind::School => "SELECT t.name, s.name, h.name FROM tab_team t
                             JOIN tab_school s ON s.id = t.school_id LEFT JOIN tab_school h ON h.id = t.hybrid_school_id
                             WHERE ? IN (t.school_id, t.hybrid_school_id) ORDER BY t.name, t.id",
            Kind::Debater => "SELECT t.name, s.name, h.name FROM tab_team_debaters x
                              JOIN tab_team t ON t.id = x.team_id JOIN tab_school s ON s.id = t.school_id
                              LEFT JOIN tab_school h ON h.id = t.hybrid_school_id
                              WHERE x.debater_id = ? ORDER BY t.name, t.id",
        };
        let teams: Vec<_> = sqlx::query_as::<_, (String, String, Option<String>)>(queries)
            .bind(id).fetch_all(&mut *db).await?.into_iter().map(|(name, school, hybrid)| Affiliation { name, school, hybrid }).collect();
        db.commit().await?;
        let Ok(csrf) = html::page_csrf(headers) else { return Ok(unavailable()); };
        let response = html::render("board_apda_id.html", context! {
            title => format!("{} APDA ID", kind.label()), csrf, name, apda_id, teams, nav => Navigation::new(actor, true),
        }, StatusCode::OK);
        Ok(html::form_response(response, headers, &csrf))
    }.await;
    match result {
        Ok(response) => response,
        Err(error) => board::respond_error(actor, error, "/apda-board/"),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Edit {
    csrfmiddlewaretoken: String,
    apda_id: String,
}

pub async fn school_save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(id): Path<i32>,
    form: Result<Form<Edit>, FormRejection>,
) -> Response {
    save(&app, actor, &headers, id, Kind::School, form).await
}

pub async fn debater_save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(id): Path<i32>,
    form: Result<Form<Edit>, FormRejection>,
) -> Response {
    save(&app, actor, &headers, id, Kind::Debater, form).await
}

async fn save(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: i32,
    kind: Kind,
    form: Result<Form<Edit>, FormRejection>,
) -> Response {
    let Ok(Form(form)) = form else {
        return board::invalid_form();
    };
    if !board::csrf_valid(headers, &form.csrfmiddlewaretoken) {
        return board::csrf_error();
    }
    let value = form.apda_id.trim();
    let number = if value.is_empty() {
        None
    } else if let Ok(number) = value.parse::<i32>() {
        Some(number)
    } else {
        return board::invalid_form();
    };
    let result = async {
        let mut db = app.db.begin().await?;
        board::lock_board(&mut db, actor).await?;
        let existing = match kind {
            Kind::School => "SELECT id FROM tab_school WHERE id = ? FOR UPDATE",
            Kind::Debater => "SELECT id FROM tab_debater WHERE id = ? FOR UPDATE",
        };
        if sqlx::query_scalar::<_, i32>(existing)
            .bind(id)
            .fetch_optional(&mut *db)
            .await?
            .is_none()
        {
            return Err(BoardError::Missing);
        }
        let sql = match kind {
            Kind::School => "UPDATE tab_school SET apda_id = ? WHERE id = ?",
            Kind::Debater => "UPDATE tab_debater SET apda_id = ? WHERE id = ?",
        };
        sqlx::query(sql)
            .bind(number)
            .bind(id)
            .execute(&mut *db)
            .await?;
        db.commit().await?;
        Ok(access::found(format!("/apda-board/{}/{id}/", kind.key())))
    }
    .await;
    match result {
        Ok(response) => response,
        Err(error) => board::respond_error(actor, error, "/apda-board/"),
    }
}

#[derive(Deserialize)]
pub struct ExportOptions {
    format: Option<String>,
}

pub async fn export_schools(
    State(app): State<App>,
    axum::extract::Query(options): axum::extract::Query<ExportOptions>,
) -> Response {
    export(&app, Kind::School, options).await
}

pub async fn export_debaters(
    State(app): State<App>,
    axum::extract::Query(options): axum::extract::Query<ExportOptions>,
) -> Response {
    export(&app, Kind::Debater, options).await
}

async fn export(app: &App, kind: Kind, options: ExportOptions) -> Response {
    use crate::spreadsheets::{self, Format};
    use axum::http::{HeaderValue, header};
    let Some(format) = Format::parse(options.format.as_deref().unwrap_or("csv")) else {
        return crate::private((StatusCode::BAD_REQUEST, "Choose CSV, XLS, or XLSX."));
    };
    let result = async {
        let mut db = app.db.acquire().await?;
        let rows: Vec<_> = list(&mut db, kind)
            .await?
            .into_iter()
            .map(|row| {
                vec![
                    row.name,
                    row.apda_id.map_or_else(String::new, |id| id.to_string()),
                ]
            })
            .collect();
        Ok::<_, sqlx::Error>(rows)
    }
    .await;
    let Ok(rows) = result else {
        return unavailable();
    };
    let body = tokio::task::spawn_blocking(move || {
        spreadsheets::write(format, &[kind.key(), "id"], &rows, false)
    })
    .await;
    let Ok(Ok(body)) = body else {
        return crate::private((
            StatusCode::BAD_REQUEST,
            "Could not export APDA IDs in the selected format.",
        ));
    };
    let filename = format!(
        "attachment; filename=apda_board_{}s.{}",
        kind.key(),
        format.suffix()
    );
    crate::private((
        [
            (
                header::CONTENT_TYPE,
                HeaderValue::from_static(format.content_type()),
            ),
            (
                header::CONTENT_DISPOSITION,
                HeaderValue::from_str(&filename).expect("ASCII spreadsheet filename"),
            ),
        ],
        body,
    ))
}

pub async fn import_schools(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    multipart: Result<axum::extract::Multipart, axum::extract::multipart::MultipartRejection>,
) -> Response {
    import(&app, actor, &headers, Kind::School, multipart).await
}

pub async fn import_debaters(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    multipart: Result<axum::extract::Multipart, axum::extract::multipart::MultipartRejection>,
) -> Response {
    import(&app, actor, &headers, Kind::Debater, multipart).await
}

async fn import(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: Kind,
    multipart: Result<axum::extract::Multipart, axum::extract::multipart::MultipartRejection>,
) -> Response {
    use crate::spreadsheets::{self, Format};
    let Ok(mut multipart) = multipart else {
        return board::invalid_form();
    };
    let mut token = None;
    let mut file = None;
    loop {
        match multipart.next_field().await {
            Ok(Some(field)) => match field.name() {
                Some("csrfmiddlewaretoken") if token.is_none() => {
                    let Ok(value) = field.text().await else {
                        return board::invalid_form();
                    };
                    token = Some(value);
                }
                Some("file") if file.is_none() => {
                    let format = field
                        .file_name()
                        .and_then(|name| name.rsplit_once('.'))
                        .and_then(|(_, extension)| Format::parse(extension));
                    let Some(format) = format else {
                        return invalid_upload(actor, "Choose a CSV, XLS, or XLSX spreadsheet.");
                    };
                    let Ok(bytes) = field.bytes().await else {
                        return board::invalid_form();
                    };
                    file = Some((format, bytes));
                }
                _ => return board::invalid_form(),
            },
            Ok(None) => break,
            Err(_) => return board::invalid_form(),
        }
    }
    if !token
        .as_deref()
        .is_some_and(|token| board::csrf_valid(headers, token))
    {
        return board::csrf_error();
    }
    let Some((format, bytes)) = file else {
        return invalid_upload(actor, "Choose a spreadsheet to import.");
    };
    let parsed = tokio::task::spawn_blocking(move || {
        let rows = spreadsheets::read(format, &bytes).map_err(str::to_owned)?;
        apda_replacements(kind, rows)
    })
    .await;
    let replacements = match parsed {
        Ok(Ok(rows)) => rows,
        Ok(Err(error)) => return invalid_upload(actor, &error),
        Err(_) => return unavailable(),
    };
    let result: Result<(usize, usize), BoardError> = async {
        let mut db = app.db.begin().await?;
        board::lock_board(&mut db, actor).await?;
        let (find, update) = match kind {
            Kind::School => (
                "SELECT id FROM tab_school WHERE name = ? FOR UPDATE",
                "UPDATE tab_school SET apda_id = ? WHERE id = ?",
            ),
            Kind::Debater => (
                "SELECT id FROM tab_debater WHERE name = ? FOR UPDATE",
                "UPDATE tab_debater SET apda_id = ? WHERE id = ?",
            ),
        };
        let mut changed = 0;
        let mut ignored = 0;
        for (name, number) in replacements {
            let ids = sqlx::query_scalar::<_, i32>(find)
                .bind(name)
                .fetch_all(&mut *db)
                .await?;
            if ids.is_empty() {
                ignored += 1;
            }
            for id in ids {
                sqlx::query(update)
                    .bind(number)
                    .bind(id)
                    .execute(&mut *db)
                    .await?;
                changed += 1;
            }
        }
        db.commit().await?;
        Ok((changed, ignored))
    }
    .await;
    match result {
        Ok((updated, ignored)) => access::found(format!(
            "/apda-board/?ids_updated={updated}&rows_ignored={ignored}"
        )),
        Err(error) => board::respond_error(actor, error, "/apda-board/"),
    }
}

fn invalid_upload(actor: Actor, message: &str) -> Response {
    html::render(
        "error.html",
        context! {
            title => "Invalid APDA ID spreadsheet", heading => message, back => "/apda-board/",
            nav => Navigation::new(actor, true),
        },
        StatusCode::UNPROCESSABLE_ENTITY,
    )
}

fn apda_replacements(
    kind: Kind,
    rows: crate::spreadsheets::Rows,
) -> Result<Vec<(String, Option<i32>)>, String> {
    use crate::spreadsheets::normalized_header;
    let first = &rows[0].1;
    let first_is_data =
        !apda_cell(first, 0).is_empty() && apda_cell(first, 1).parse::<i32>().is_ok();
    let mut columns = [None; 2];
    if !first_is_data {
        for (number, heading) in first.iter().enumerate() {
            let normalized = normalized_header(heading);
            let field = match (kind, normalized.as_str()) {
                (_, "id" | "apdaid") => Some(1),
                (Kind::School, "school" | "schoolname" | "primaryschool")
                | (Kind::Debater, "debater" | "debatername" | "name") => Some(0),
                _ => None,
            };
            if let Some(index) = field
                && columns[index].replace(number).is_some()
            {
                return Err("The APDA ID spreadsheet contains a duplicate column.".into());
            }
        }
    }
    let header = columns.iter().all(Option::is_some);
    let names = columns[0].filter(|_| header).unwrap_or(0);
    let ids = columns[1].filter(|_| header).unwrap_or(1);
    let rows = &rows[usize::from(header)..];
    if rows.is_empty() {
        return Err("The spreadsheet has headers but no data rows.".into());
    }
    rows.iter()
        .map(|(line, row)| {
            let name = apda_cell(row, names);
            if name.is_empty() {
                return Err(format!("Row {line} needs a name."));
            }
            let number = apda_cell(row, ids);
            let id = if number.is_empty() {
                None
            } else {
                Some(
                    number
                        .parse::<i32>()
                        .map_err(|_| format!("Row {line} needs an integer APDA ID."))?,
                )
            };
            Ok((name.into(), id))
        })
        .collect()
}

fn apda_cell(row: &[String], index: usize) -> &str {
    row.get(index).map_or("", String::as_str)
}
