use std::{
    collections::{HashMap, HashSet},
    sync::LazyLock,
};

use axum::{
    Extension, Form, Json,
    extract::State,
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
};
use chrono::Utc;
use minijinja::context;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::MySqlConnection;

use crate::{
    App, access, legacy_html,
    legacy_session::Actor,
    login, private, publication,
    settings::{SettingValue, Settings, put},
    staff_pairing::lock_round,
    tournament_setup::{CHECKLIST, truthy},
    unavailable,
};

#[derive(Deserialize)]
struct Config {
    before_tournament_steps: Vec<Step>,
    day_of_steps: Vec<Step>,
}

#[derive(Deserialize)]
struct Step {
    slug: String,
    title: String,
    description: String,
    optional_label: Option<String>,
    url_name: String,
    completion_rule: Option<String>,
}

static CONFIG: LazyLock<Config> = LazyLock::new(|| {
    serde_yaml_ng::from_str(include_str!(
        "../../../mittab/apps/tab/config/tournament_todo_steps.yaml"
    ))
    .expect("built-in tournament checklist config")
});

fn steps() -> impl Iterator<Item = &'static Step> {
    CONFIG
        .before_tournament_steps
        .iter()
        .chain(&CONFIG.day_of_steps)
}

fn url(name: &str) -> &'static str {
    match name {
        "settings_form" => "/settings_form",
        "registration_admin" => "/registration/manage/",
        "public_homepage" => "/settings/public-homepage/",
        "invite_staff" => "/staff/invite/",
        "upload_data" => "/import_data/",
        "manage_room_tags" => "/manage-room-tags",
        "add_scratch" => "/enter_scratch/",
        "batch_checkin" => "/batch_checkin/",
        "email_management" => "/email_management/",
        "view_status" => "/pairings/status/",
        "manage_motions" => "/motions/",
        "forum_post" => "/forum_post",
        "public_rankings_control" => "/rankings/public-control/",
        "outround_pairing_view_default" => "/outround_pairing",
        _ => "",
    }
}

#[derive(Serialize)]
struct StepState<'a> {
    slug: &'a str,
    title: &'a str,
    description: &'a str,
    optional_label: Option<&'a str>,
    url: &'static str,
    checked: bool,
    auto_completed: bool,
}

#[derive(Serialize)]
struct Section<'a> {
    phase: &'static str,
    title: &'static str,
    steps: Vec<StepState<'a>>,
    completed: usize,
    total: usize,
    progress_percent: usize,
}

fn group<'a>(
    phase: &'static str,
    title: &'static str,
    steps: &'a [Step],
    auto: &HashSet<&str>,
    settings: &Settings,
) -> Section<'a> {
    let steps: Vec<_> = steps
        .iter()
        .map(|step| {
            let automatic = step
                .completion_rule
                .as_deref()
                .is_some_and(|rule| auto.contains(rule));
            StepState {
                slug: &step.slug,
                title: &step.title,
                description: &step.description,
                optional_label: step.optional_label.as_deref(),
                url: url(&step.url_name),
                checked: automatic || settings.flag(&format!("todo_{}", step.slug), false),
                auto_completed: automatic,
            }
        })
        .collect();
    let completed = steps.iter().filter(|step| step.checked).count();
    let total = steps.len();
    let progress_percent = (100 * completed).checked_div(total).unwrap_or(0);
    Section {
        phase,
        title,
        steps,
        completed,
        total,
        progress_percent,
    }
}

async fn automatic(db: &mut MySqlConnection) -> Result<HashSet<&'static str>, sqlx::Error> {
    let settings = Settings::load(
        db,
        &[
            "tournament_name",
            "settings_tabs_seen",
            "tot_rounds",
            "var_teams_visible",
            "nov_teams_visible",
        ],
    )
    .await?;
    let last = settings.integer("tot_rounds", 0);
    let flags = sqlx::query_as::<_, (bool, bool, bool, bool, bool, bool, bool, bool, bool, bool)>(
        "SELECT
            EXISTS(SELECT 1 FROM tab_school) AND EXISTS(SELECT 1 FROM tab_team)
             AND EXISTS(SELECT 1 FROM tab_debater) AND EXISTS(SELECT 1 FROM tab_room)
             AND EXISTS(SELECT 1 FROM tab_judge),
            EXISTS(SELECT 1 FROM tab_room_tags),
            EXISTS(SELECT 1 FROM tab_scratch),
            EXISTS(SELECT 1 FROM tab_team WHERE checked_in = TRUE)
             AND EXISTS(SELECT 1 FROM tab_checkin WHERE round_number = 1)
             AND EXISTS(SELECT 1 FROM tab_roomcheckin WHERE round_number = 1),
            EXISTS(SELECT 1 FROM tab_round WHERE round_number = 1),
            EXISTS(SELECT 1 FROM tab_judgecodeemaillog),
            (? > 0 AND EXISTS(SELECT 1 FROM tab_round WHERE round_number = ?)
             AND NOT EXISTS(SELECT 1 FROM tab_round WHERE round_number = ? AND victor = 0)),
            EXISTS(SELECT 1 FROM tab_outround),
            EXISTS(SELECT 1 FROM tab_outround) AND NOT EXISTS(SELECT 1 FROM tab_outround WHERE victor = 0),
            EXISTS(SELECT 1 FROM tab_writtenrfdemaillog)",
    ).bind(last).bind(last).bind(last).fetch_one(&mut *db).await?;
    let placements = publication::refresh_placements(db, &settings).await?;
    let completed_at = publication::completion_time(db, &settings).await?;
    let name = settings.string("tournament_name", "");
    let seen = settings.string("settings_tabs_seen", "");
    let seen: HashSet<_> = seen.split(',').map(str::trim).collect();
    let reviewed = crate::settings_form::categories().all(|id| seen.contains(id));
    Ok([
        (
            "tournament_name_set",
            !name.trim().is_empty() && name.trim() != "New Tournament",
        ),
        ("settings_reviewed", reviewed),
        ("core_data_imported", flags.0),
        ("accessible_room_tags_added", flags.1),
        ("scratches_entered", flags.2),
        ("entities_checked_in", flags.3),
        ("round_one_paired", flags.4),
        ("judge_codes_sent", flags.5),
        ("final_inround_completed", flags.6),
        ("outrounds_paired", flags.7),
        ("outrounds_completed", flags.8),
        ("written_rfds_sent", flags.9),
        (
            "team_results_published",
            placements.iter().any(|rows| !rows.is_empty()),
        ),
        (
            "speaker_results_published",
            publication::speakers_released(&placements, completed_at, Utc::now()),
        ),
    ]
    .into_iter()
    .filter_map(|(rule, completed)| completed.then_some(rule))
    .collect())
}

async fn states(
    db: &mut MySqlConnection,
    auto: &HashSet<&str>,
) -> Result<Vec<Section<'static>>, sqlx::Error> {
    let keys: Vec<_> = steps().map(|step| format!("todo_{}", step.slug)).collect();
    let refs: Vec<_> = keys.iter().map(String::as_str).collect();
    let settings = Settings::load(db, &refs).await?;
    for (step, key) in steps().zip(&keys) {
        if step
            .completion_rule
            .as_deref()
            .is_some_and(|rule| auto.contains(rule))
            && !settings.flag(key, false)
        {
            put(db, key, SettingValue::Number(1)).await?;
        }
    }
    // After persisting automatic completions, form construction applies the
    // current automatic rule results even when the stored preference was false.
    // Manual values continue to reflect the same transaction snapshot.
    let before = group(
        "before_tournament",
        "Before Tournament",
        &CONFIG.before_tournament_steps,
        auto,
        &settings,
    );
    let event = group(
        "day_of",
        "Day Of Tournament",
        &CONFIG.day_of_steps,
        auto,
        &settings,
    );
    Ok(vec![before, event])
}

fn user(actor: Actor) -> Option<i32> {
    match actor {
        Actor::User {
            id, board: false, ..
        } => Some(id),
        _ => None,
    }
}

pub async fn hide_preference(db: &mut MySqlConnection, user: i32) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, bool>(
        "SELECT hide_tournament_todo FROM tab_usertournamentsetuppreference WHERE user_id = ?",
    )
    .bind(user)
    .fetch_optional(db)
    .await?
    .unwrap_or(false))
}

async fn save_preference(
    db: &mut MySqlConnection,
    user: i32,
    hidden: bool,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO tab_usertournamentsetuppreference (user_id, hide_tournament_todo) VALUES (?, ?)
         ON DUPLICATE KEY UPDATE hide_tournament_todo = ?",
    ).bind(user).bind(hidden).bind(hidden).execute(db).await?;
    Ok(())
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    let result = async {
        let mut db = app.db.begin().await?;
        crate::staff_pairing::lock_round_if_configured(&mut db).await?;
        let hide = hide_preference(&mut db, user).await?;
        let auto = automatic(&mut db).await?;
        let sections = states(&mut db, &auto).await?;
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        db.commit().await?;
        let Ok(csrf) = login::TemplateCsrf::new(&headers) else {
            return Ok(unavailable());
        };
        let mut response = legacy_html::render(
            "tab/tournament_todo.html",
            shell,
            context! {
                sections, hide_tournament_todo => hide, csrf_input => csrf.input(),
                return_path => return_path(&uri),
            },
            StatusCode::OK,
        );
        csrf.set_cookie(&mut response, &headers);
        Ok::<_, sqlx::Error>(response)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn return_path(uri: &Uri) -> String {
    if let Some(query) = uri.query() {
        let params: HashMap<_, _> = url::form_urlencoded::parse(query.as_bytes()).collect();
        for name in ["return_to", "next"] {
            if let Some(value) = params.get(name)
                && !login::local_next(value).is_empty()
            {
                return value.to_string();
            }
        }
    }
    uri.path_and_query()
        .map_or(CHECKLIST, |value| value.as_str())
        .to_string()
}

pub async fn save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<HashMap<String, String>>,
) -> Response {
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    if !login::valid_csrf(
        &headers,
        form.get("csrfmiddlewaretoken").map_or("", String::as_str),
    ) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let auto = automatic(&mut db).await?;
        let hide = form
            .get("hide_tournament_todo")
            .is_some_and(|value| truthy(value));
        save_preference(&mut db, user, hide).await?;
        for step in steps() {
            let checked = step
                .completion_rule
                .as_deref()
                .is_some_and(|rule| auto.contains(rule))
                || form
                    .get(&format!("step_{}", step.slug))
                    .is_some_and(|value| truthy(value));
            put(
                &mut db,
                &format!("todo_{}", step.slug),
                SettingValue::Number(i32::from(checked)),
            )
            .await?;
        }
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(CHECKLIST))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Deserialize)]
pub struct Toggle {
    csrfmiddlewaretoken: String,
    field: String,
    checked: String,
}

pub async fn toggle(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Toggle>,
) -> Response {
    let Some(user) = user(actor) else {
        return access::access_denied();
    };
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let field = form.field.trim();
        let checked = truthy(&form.checked);
        if field == "hide_tournament_todo" {
            save_preference(&mut db, user, checked).await?;
            db.commit().await?;
            return Ok(private(Json(json!({"ok": true, "field": field, "checked": checked}))));
        }
        let Some(step) = field.strip_prefix("step_").and_then(|slug| steps().find(|step| step.slug == slug)) else {
            return Ok(private((StatusCode::BAD_REQUEST, Json(json!({"error": "Unknown checklist field."})))));
        };
        let auto = automatic(&mut db).await?;
        let auto_complete = step.completion_rule.as_deref().is_some_and(|rule| auto.contains(rule));
        let checked = checked || auto_complete;
        put(&mut db, &format!("todo_{}", step.slug), SettingValue::Number(i32::from(checked))).await?;
        let sections = states(&mut db, &auto).await?;
        db.commit().await?;
        let section = sections.iter().find(|section| section.steps.iter().any(|state| state.slug == step.slug))
            .expect("configured checklist section");
        Ok::<_, sqlx::Error>(private(Json(json!({
            "ok": true, "field": field, "checked": checked, "auto_completed": auto_complete,
            "phase": section.phase, "section_completed": section.completed, "section_total": section.total,
            "section_progress_percent": section.progress_percent,
        }))))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}
