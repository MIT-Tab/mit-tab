use axum::{
    Extension, Router,
    extract::{Path, RawQuery, Request, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use minijinja::context;
use sqlx::AssertSqlSafe;

use crate::{
    App, access, admin_accounts, admin_changelist, admin_html, admin_records, diagnostics,
    html::{self, Navigation},
    legacy_session::Actor,
    private, unavailable,
};

pub fn routes() -> Router<App> {
    let records = [
        "/admin/tab/auditevent/add/",
        "/admin/tab/auditevent/{id}/delete/",
        "/admin/tab/manualjudgeassignment/add/",
        "/admin/tab/manualjudgeassignment/{id}/delete/",
    ]
    .into_iter()
    .fold(Router::new(), |router, path| {
        router.route(path, get(deny_readonly).post(deny_readonly))
    });
    Router::new()
        .route("/admin", get(login))
        .route("/admin/", get(index))
        .route("/admin/login/", get(login))
        .route("/admin/jsi18n/", get(diagnostics::js_catalog))
        .route("/admin/{application}/", get(app_index))
        .route("/admin/{application}/{model}/", get(model))
        .route(
            "/admin/{application}/{model}/{id}/change/",
            get(change).post(readonly_write),
        )
        .route("/admin/{application}/{model}/{id}/history/", get(history))
        .merge(records)
        .merge(admin_accounts::routes())
        .merge(crate::admin_scores::routes())
        .merge(crate::admin_model_forms::routes())
}

async fn index(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let shell = match admin_html::Shell::load(&app, actor, &headers, "/admin/").await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let page = context! { title => "Site administration", app_list => &shell.applications };
    shell.render("admin/index.html", page, &headers)
}

async fn app_index(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(application): Path<String>,
    headers: HeaderMap,
) -> Response {
    let path = format!("/admin/{application}/");
    let shell = match admin_html::Shell::load(&app, actor, &headers, &path).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let Some(found) = shell
        .applications
        .iter()
        .find(|app| app.app_label == application)
    else {
        return missing().await;
    };
    let page = context! {
        title => format!("{} administration", found.name),
        app_label => &found.app_label,
        app_list => vec![found],
    };
    shell.render("admin/app_index.html", page, &headers)
}

async fn login() -> Response {
    access::found("/admin/")
}

struct Resource {
    title: &'static str,
    table: &'static str,
    columns: &'static [(&'static str, &'static str)],
    detail: &'static [(&'static str, &'static str)],
}

fn report(application: &str, model: &str) -> Option<Resource> {
    Some(match (application, model) {
        ("tab", "bye") => Resource {
            title: "Byes",
            table: "tab_bye",
            columns: &[("Round", "round_number"), ("Team ID", "bye_team_id")],
            detail: &[],
        },
        ("tab", "noshow") => Resource {
            title: "No-Shows",
            table: "tab_noshow",
            columns: &[("Round", "round_number"), ("Team ID", "no_show_team_id")],
            detail: &[],
        },
        ("tab", "breakingteam") => Resource {
            title: "Breaking Teams",
            table: "tab_breakingteam",
            columns: &[
                ("Team ID", "team_id"),
                ("Division", "type_of_team"),
                ("Seed", "seed"),
                ("Effective Seed", "effective_seed"),
            ],
            detail: &[],
        },
        ("tab", "outroundchallenge") => Resource {
            title: "Elimination Challenges",
            table: "tab_outroundchallenge",
            columns: &[
                ("Division", "type_of_round"),
                ("Stage Size", "num_teams"),
                ("Challenger ID", "challenger_id"),
                ("Challenged ID", "challenged_id"),
            ],
            detail: &[],
        },
        ("tab", "usertournamentsetuppreference") => Resource {
            title: "Tournament Setup Preferences",
            table: "tab_usertournamentsetuppreference",
            columns: &[
                ("User ID", "user_id"),
                ("Checklist Hidden", "hide_tournament_todo"),
            ],
            detail: &[],
        },
        ("registration", "registrationchangelog") => Resource {
            title: "Registration Changes",
            table: "registration_registrationchangelog",
            columns: &[
                ("Created", "created_at"),
                ("School", "school_name"),
                ("Action", "action"),
                ("Summary", "summary"),
            ],
            detail: &[("Changes", "changes")],
        },
        _ => return None,
    })
}

fn destination(application: &str, model: &str, record: Option<i32>) -> Option<String> {
    let result = match (application, model) {
        ("tab", "debater") => ("/view_debaters/", Some("/debater")),
        ("tab", "team") => ("/view_teams/", Some("/team")),
        ("tab", "school") => ("/view_schools/", Some("/school")),
        ("tab", "judge") => ("/view_judges/", Some("/judge")),
        ("tab", "room") => ("/view_rooms/", Some("/room")),
        ("tab", "roomtag") => ("/manage-room-tags", Some("/room-tag")),
        ("tab", "rankinggroup") => ("/manage-ranking-groups", Some("/ranking-group")),
        ("tab", "scratch") => ("/scratches/view/", None),
        ("tab", "checkin" | "roomcheckin") => ("/batch_checkin/", None),
        ("tab", "round") => {
            return Some(record.map_or("/pairings/view_rounds/".into(), |id| {
                format!("/round/{id}/result/")
            }));
        }
        ("tab", "outround") => {
            return Some(record.map_or("/outround_pairing".into(), |id| {
                format!("/outround/{id}/result/")
            }));
        }
        ("tab", "tabsettings") => ("/settings_form", None),
        ("registration", "registrationconfirmationemaillog") => ("/email_management/", None),
        (
            "registration",
            "registration" | "registrationconfig" | "infolink" | "registrationlink" | "infotext",
        ) => ("/registration/manage/", None),
        _ => return None,
    };
    Some(match (result.1, record) {
        (Some(prefix), Some(id)) => format!("{prefix}/{id}/"),
        _ => result.0.into(),
    })
}

async fn model(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((application, model)): Path<(String, String)>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    if let Some(record) = admin_records::Record::find(&application, &model) {
        return admin_records::list(&app, actor, &headers, record, raw).await;
    }
    if let Some(response) =
        admin_changelist::get(&app, actor, &headers, &application, &model, raw.clone()).await
    {
        return response;
    }
    let query = admin_changelist::Query::new(raw);
    if let Some(link) = destination(&application, &model, None) {
        return access::found(link);
    }
    let Some(resource) = report(&application, &model) else {
        return missing().await;
    };
    let title = resource.title;
    let keys: Vec<_> = resource.columns.iter().map(|(label, _)| *label).collect();
    let fields: Vec<_> = resource
        .columns
        .iter()
        .map(|(_, name)| format!("CAST(`{name}` AS CHAR)"))
        .collect();
    let number = query
        .get("page")
        .and_then(|page| page.parse::<u64>().ok())
        .unwrap_or(1)
        .clamp(1, 1_000_000);
    let query = query.get("q").unwrap_or_default().trim();
    let sql = format!(
        "SELECT id, CAST(JSON_ARRAY({}) AS CHAR) FROM `{}`
         WHERE ? = '' OR LOCATE(?, CONCAT_WS(' ', {})) > 0
         ORDER BY id DESC LIMIT 51 OFFSET ?",
        fields.join(", "),
        resource.table,
        fields.join(", "),
    );
    let data = sqlx::query_as::<_, (i32, String)>(AssertSqlSafe(sql))
        .bind(query)
        .bind(query)
        .bind((number - 1) * 50)
        .fetch_all(&app.db)
        .await;
    let Ok(data) = data else { return unavailable() };
    let page_query = url::form_urlencoded::Serializer::new(String::new())
        .append_pair("q", query)
        .finish();
    let more = data.len() > 50;
    let records: Result<Vec<_>, _> = data
        .into_iter()
        .take(50)
        .map(|(id, data)| {
            serde_json::from_str::<Vec<Option<String>>>(&data).map(|values| (id, values))
        })
        .collect();
    let Ok(records) = records else {
        return unavailable();
    };
    html::render(
        "administration.html",
        context! {
            title, nav => Navigation::new(actor, true), keys, records, query, page_query, number,
            previous => (number > 1).then_some(number - 1), next => more.then_some(number + 1),
        },
        StatusCode::OK,
    )
}

async fn change(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((application, model, id)): Path<(String, String, i32)>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    if let Some(record) = admin_records::Record::find(&application, &model) {
        return admin_records::detail(&app, actor, &headers, record, id, raw, false).await;
    }
    if let Some(link) = destination(&application, &model, Some(id)) {
        return access::found(link);
    }
    let Some(resource) = report(&application, &model) else {
        return missing().await;
    };
    let columns: Vec<_> = resource.columns.iter().chain(resource.detail).collect();
    let fields: Vec<_> = columns
        .iter()
        .map(|(_, name)| format!("CAST(`{name}` AS CHAR)"))
        .collect();
    let sql = format!(
        "SELECT CAST(JSON_ARRAY({}) AS CHAR) FROM `{}` WHERE id = ?",
        fields.join(", "),
        resource.table
    );
    let mut db = match app.db.begin().await {
        Ok(db) => db,
        Err(_) => return unavailable(),
    };
    match admin_html::model_enabled(&mut db, &application, &model).await {
        Ok(true) => {}
        Ok(false) => return private((StatusCode::FORBIDDEN, "Access denied.")),
        Err(_) => return unavailable(),
    }
    let row = sqlx::query_scalar::<_, String>(AssertSqlSafe(sql))
        .bind(id)
        .fetch_optional(&mut *db)
        .await;
    if db.commit().await.is_err() {
        return unavailable();
    }
    let row = match row {
        Ok(Some(row)) => match serde_json::from_str::<Vec<Option<String>>>(&row) {
            Ok(record) => record,
            Err(_) => return unavailable(),
        },
        Ok(None) => return missing().await,
        Err(_) => return unavailable(),
    };
    let detail: Vec<_> = columns
        .into_iter()
        .map(|(title, _)| title)
        .zip(row)
        .collect();
    html::render(
        "administration.html",
        context! {
            title => resource.title, nav => Navigation::new(actor, true), detail, id,
            back => format!("/admin/{application}/{model}/"),
        },
        StatusCode::OK,
    )
}

async fn history(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((application, model, id)): Path<(String, String, i32)>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    match admin_records::Record::find(&application, &model) {
        Some(record) => admin_records::detail(&app, actor, &headers, record, id, raw, true).await,
        None => missing().await,
    }
}

async fn readonly_write(Path(params): Path<std::collections::HashMap<String, String>>) -> Response {
    let record = params
        .get("application")
        .zip(params.get("model"))
        .and_then(|(application, model)| admin_records::Record::find(application, model));
    if record.is_some() {
        deny_readonly().await
    } else {
        private(StatusCode::METHOD_NOT_ALLOWED)
    }
}

async fn deny_readonly() -> Response {
    private((StatusCode::FORBIDDEN, "This record is read-only."))
}

async fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Administration page not found."))
}

pub async fn fallback(State(app): State<App>, request: Request) -> Response {
    if request.uri().path().starts_with("/admin/") {
        return match access::admin_actor(&app, request.headers(), request.uri().path()).await {
            Ok(_) => missing().await,
            Err(response) => *response,
        };
    }
    not_found().await
}

pub async fn not_found() -> Response {
    error_page(
        StatusCode::NOT_FOUND,
        "Page Not Found",
        "The requested page could not be found.",
    )
}

pub async fn internal_error() -> Response {
    error_page(
        StatusCode::INTERNAL_SERVER_ERROR,
        "Server Error",
        "MIT-Tab could not complete this request.",
    )
}

fn error_page(status: StatusCode, title: &str, detail: &str) -> Response {
    html::render(
        "error.html",
        context! {
            title, heading => title, detail, nav => Navigation::new(Actor::Anonymous, true),
        },
        status,
    )
}
