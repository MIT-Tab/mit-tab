use axum::{
    Extension, Form, Router,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use chrono::{Datelike, NaiveDateTime, Timelike};
use minijinja::{Value, context};
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access, admin_html, legacy_html, legacy_session::Actor, login, private,
    registration_form::Fields, round_audit, staff_pairing::lock_round, unavailable,
};

const ADMIN_MEDIA: &[&str] = &[
    "vendor/jquery/jquery.min.js",
    "jquery.init.js",
    "core.js",
    "admin/RelatedObjectLookups.js",
    "actions.js",
    "urlify.js",
    "prepopulate.js",
    "vendor/xregexp/xregexp.min.js",
];
const STATISTICS: &str = "/admin/tab/roundstats/";

pub fn routes() -> Router<App> {
    Router::new()
        .route(STATISTICS, get(scores).post(delete_selected_scores))
        .route(
            "/admin/tab/roundstats/{id}/change/",
            get(score).post(edit_score),
        )
        .route(
            "/admin/tab/roundstats/add/",
            get(add_score).post(create_score),
        )
        .route("/admin/tab/roundstats/{id}/history/", get(score_history))
        .route(
            "/admin/tab/roundstats/{id}/delete/",
            get(confirm_delete).post(delete),
        )
}

#[derive(Default, Deserialize)]
struct ScoreList {
    p: Option<u32>,
    all: Option<String>,
}

const PER_PAGE: u32 = 100;

fn score_opts() -> Value {
    context! {
        app_label => "tab",
        model_name => "roundstats",
        verbose_name => "round stats",
        verbose_name_plural => "round stats",
        app_config => context! { verbose_name => "MIT Tab" },
    }
}

fn scripts() -> String {
    ADMIN_MEDIA
        .iter()
        .map(|script| format!("<script src=\"/static/admin/js/{script}\"></script>"))
        .collect::<Vec<_>>()
        .join("\n")
}

async fn scores(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(query): Query<ScoreList>,
) -> Response {
    let shell = match admin_html::Shell::load(&app, actor, &headers, STATISTICS).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let result = async {
        let mut db = app.db.begin().await?;
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM tab_roundstats")
            .fetch_one(&mut *db).await?;
        let count = u32::try_from(total).unwrap_or(u32::MAX);
        let page = query.p.unwrap_or(1).max(1);
        let pages = count.div_ceil(PER_PAGE).max(1);
        if page > pages {
            return Ok(access::found(format!("{STATISTICS}?e=1")));
        }
        let show_all = query.all.is_some() && count <= 200;
        let limit = if show_all { count } else { PER_PAGE };
        let offset = if show_all { 0 } else { (page - 1) * PER_PAGE };
        let rows = sqlx::query_as::<_, (i32, String)>(
            "SELECT s.id, CONCAT('Results for ', d.name, ' in round ', r.round_number)
             FROM tab_roundstats s JOIN tab_debater d ON s.debater_id = d.id
             JOIN tab_round r ON s.round_id = r.id ORDER BY s.id DESC LIMIT ? OFFSET ?",
        ).bind(limit).bind(offset).fetch_all(&mut *db).await?;
        db.commit().await?;
        let mut filters = url::form_urlencoded::Serializer::new(String::new());
        if let Some(page) = query.p {
            filters.append_pair("p", &page.to_string());
        }
        if let Some(all) = query.all {
            filters.append_pair("all", &all);
        }
        let filters = filters.finish();
        let filter_suffix = preserved_filters(&filters);
        let results = rows.iter().map(|(id, label)| [
            format!(r#"<td class="action-checkbox"><input type="checkbox" name="_selected_action" value="{id}" class="action-select"></td>"#),
            format!(r#"<th class="field-__str__"><a href="{STATISTICS}{id}/change/{}">{}</a></th>"#, legacy_html::escape(&filter_suffix), legacy_html::escape(label)),
        ]).collect::<Vec<_>>();
        let initial = r#"<select name="action" required>
  <option value="" selected>---------</option>

  <option value="delete_selected">Delete selected round stats</option>

</select>"#;
        let opts = score_opts();
        let page_range = paginator_links(page, pages);
        let context = context! {
            title => "Select round stats to change",
            opts => &opts,
            cl => context! {
                opts => &opts,
                show_admin_actions => !rows.is_empty(),
                result_count => count,
                result_list => &rows,
            },
            app_url => "/admin/tab/",
            model_url => STATISTICS,
            filter_suffix,
            module_name => "round stats",
            has_add_permission => true,
            actions_on_top => true,
            action_form => true,
            action_fields => [
                context! { label => "Action:", widget => initial },
                context! { widget => r#"<input type="hidden" name="select_across" value="0" class="select-across">"# },
            ],
            actions_selection_counter => true,
            selection_note => format!("0 of {} selected", rows.len()),
            selection_note_all => format!("All {count} selected"),
            search_form => "\n\n",
            media => context! { js => scripts() },
            result_headers => [
                context! { class_attrib => r#" class="action-checkbox-column""#, text => r#"<input type="checkbox" id="action-toggle">"# },
                context! { class_attrib => r#" class="column-__str__""#, text => "Round stats" },
            ],
            results,
            pagination_required => !show_all && pages > 1,
            page_range,
            show_all_url => (!show_all && pages > 1 && count <= 200).then_some("?all="),
        };
        Ok::<_, sqlx::Error>(shell.render("admin/change_list.html", context, &headers))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

fn preserved_filters(query: &str) -> String {
    if query.is_empty() {
        String::new()
    } else {
        format!(
            "?{}",
            url::form_urlencoded::Serializer::new(String::new())
                .append_pair("_changelist_filters", query)
                .finish()
        )
    }
}

fn paginator_links(page: u32, pages: u32) -> Vec<String> {
    let mut links = Vec::new();
    let mut previous = 0;
    let numbers = if pages <= 10 {
        (1..=pages).collect()
    } else {
        (1..=2)
            .chain(page.saturating_sub(3).max(1)..=page.saturating_add(3).min(pages))
            .chain(pages - 1..=pages)
            .collect::<std::collections::BTreeSet<_>>()
    };
    for number in numbers {
        if number > previous + 1 {
            links.push("… ".into());
        }
        links.push(if number == page {
            format!(r#"<span class="this-page">{number}</span> "#)
        } else {
            let href = format!("?p={number}");
            let end = if number == pages {
                r#" class="end""#
            } else {
                ""
            };
            format!(r#"<a href="{href}"{end}>{number}</a> "#)
        });
        previous = number;
    }
    links
}

#[derive(Serialize)]
struct Score {
    round_id: i32,
    debater_id: i32,
    role: Option<String>,
    speaks: String,
    ranks: String,
}

async fn score_row(db: &mut MySqlConnection, id: i32) -> Result<Option<Score>, sqlx::Error> {
    type Row = (i32, i32, Option<String>, String, String);
    Ok(sqlx::query_as::<_, Row>(
        "SELECT round_id, debater_id, debater_role, CAST(speaks AS CHAR), CAST(ranks AS CHAR)
         FROM tab_roundstats WHERE id = ?",
    )
    .bind(id)
    .fetch_optional(db)
    .await?
    .map(|(round_id, debater_id, role, speaks, ranks)| Score {
        round_id,
        debater_id,
        role,
        speaks,
        ranks,
    }))
}

#[derive(Deserialize)]
struct ChangeQuery {
    _popup: Option<String>,
    _to_field: Option<String>,
    _changelist_filters: Option<String>,
}

async fn score(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    Query(query): Query<ChangeQuery>,
) -> Response {
    score_form(&app, actor, &headers, query, Some(id)).await
}

async fn add_score(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(query): Query<ChangeQuery>,
) -> Response {
    score_form(&app, actor, &headers, query, None).await
}

async fn score_form(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    query: ChangeQuery,
    id: Option<i32>,
) -> Response {
    if query
        ._to_field
        .as_deref()
        .is_some_and(|field| field != "id")
    {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }
    let object_url = id.map(|id| format!("{STATISTICS}{id}/"));
    let path = object_url.as_ref().map_or_else(
        || format!("{STATISTICS}add/"),
        |url| format!("{url}change/"),
    );
    let shell = match admin_html::Shell::load(app, actor, headers, &path).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let result = async {
        let mut db = app.db.acquire().await?;
        let score = if let Some(id) = id {
            let Some(score) = score_row(&mut db, id).await? else {
                return Ok(missing());
            };
            Some(score)
        } else {
            None
        };
        let debaters =
            sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_debater ORDER BY name")
                .fetch_all(&mut *db)
                .await?;
        let rounds = sqlx::query_as::<_, (i32, String)>(
            "SELECT r.id, CONCAT('Round ', r.round_number, ' between ', g.name, ' and ', o.name)
             FROM tab_round r INNER JOIN tab_team g ON g.id = r.gov_team_id
             INNER JOIN tab_team o ON o.id = r.opp_team_id
             ORDER BY r.id",
        )
        .fetch_all(&mut *db)
        .await?;
        let subtitle = if let Some(score) = &score {
            sqlx::query_scalar::<_, String>(
                "SELECT CONCAT('Results for ', d.name, ' in round ', r.round_number)
                 FROM tab_debater d, tab_round r WHERE d.id = ? AND r.id = ?",
            )
            .bind(score.debater_id)
            .bind(score.round_id)
            .fetch_optional(&mut *db)
            .await?
        } else {
            None
        };
        drop(db);
        let score = score.as_ref();
        let widgets = (|| -> Result<Vec<_>, minijinja::Error> {
            let mut fields = vec![
                score_field(
                    "debater",
                    "Debater",
                    related_widget("debater", debaters, score.map(|score| score.debater_id))?,
                ),
                score_field(
                    "round",
                    "Round",
                    related_widget("round", rounds, score.map(|score| score.round_id))?,
                ),
            ];
            for (name, label, value) in [
                ("speaks", "Speaks", score.map(|score| score.speaks.as_str())),
                ("ranks", "Ranks", score.map(|score| score.ranks.as_str())),
                (
                    "debater_role",
                    "Debater role",
                    score.and_then(|score| score.role.as_deref()),
                ),
            ] {
                let widget = admin_html::render_fragment(
                    "admin/score_widget.html",
                    context! { name, value },
                )?;
                fields.push(score_field(
                    name,
                    label,
                    widget.trim_end_matches('\n').into(),
                ));
            }
            Ok(fields)
        })();
        let Ok(fields) = widgets else {
            return Ok(unavailable());
        };
        let popup = query._popup.is_some();
        let add = id.is_none();
        let page = context! {
            title => if add { "Add round stats" } else { "Change round stats" },
            original => &subtitle,
            subtitle,
            change => !add,
            add,
            is_popup => popup,
            is_popup_var => "_popup",
            to_field => query._to_field,
            to_field_var => "_to_field",
            opts => score_opts(),
            app_url => "/admin/tab/",
            model_url => STATISTICS,
            object_url,
            filter_suffix => preserved_filters(query._changelist_filters.as_deref().unwrap_or("")),
            adminform => vec![context! { classes => "", lines => fields }],
            show_delete_link => !popup && !add,
            show_save => true,
            show_save_and_add_another => !popup,
            show_save_and_continue => !popup,
            can_change => true,
            has_view_permission => true,
            prepopulated_fields_json => "[]",
            media => scripts(),
        };
        Ok::<_, sqlx::Error>(shell.render("admin/change_form.html", page, headers))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn score_field(name: &str, label: &str, widget: String) -> Value {
    context! {
        has_visible_field => true,
        fields => vec![context! {
            field => context! { name },
            label_tag => format!("<label class=\"required\" for=\"id_{name}\">{label}:</label>"),
            widget,
        }],
    }
}

fn related_widget(
    name: &str,
    options: Vec<(i32, String)>,
    selected: Option<i32>,
) -> Result<String, minijinja::Error> {
    let select = admin_html::render_fragment(
        "admin/score_widget.html",
        context! { name, options, selected },
    )?;
    admin_html::render_fragment(
        "admin/widgets/related_widget_wrapper.html",
        context! {
            name,
            model => name,
            rendered_widget => Value::from_safe_string(select.trim_end_matches('\n').into()),
            change_related_template_url => format!("/admin/tab/{name}/__fk__/change/"),
            add_related_url => format!("/admin/tab/{name}/add/"),
            url_params => "_to_field=id&_popup=1",
            view_related_url_params => "_to_field=id",
            can_change_related => true,
            can_add_related => true,
            can_view_related => true,
        },
    )
    .map(|html| html.trim().into())
}

#[derive(Deserialize)]
struct ScoreForm {
    csrfmiddlewaretoken: String,
    #[serde(alias = "round")]
    round_id: i32,
    #[serde(alias = "debater")]
    debater_id: i32,
    #[serde(alias = "debater_role")]
    role: String,
    speaks: String,
    ranks: String,
    _continue: Option<String>,
    _addanother: Option<String>,
}

fn decimal(value: &str) -> bool {
    let value = value.trim();
    let digits = value.strip_prefix(['-', '+']).unwrap_or(value);
    let (integer, fraction) = digits.split_once('.').unwrap_or((digits, ""));
    !digits.is_empty()
        && (!integer.is_empty() || !fraction.is_empty())
        && integer.trim_start_matches('0').len() <= 2
        && fraction.len() <= 4
        && integer
            .bytes()
            .chain(fraction.bytes())
            .all(|byte| byte.is_ascii_digit())
}

async fn edit_score(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    Form(form): Form<ScoreForm>,
) -> Response {
    save_score(&app, actor, Some(id), &headers, form).await
}

async fn create_score(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<ScoreForm>,
) -> Response {
    save_score(&app, actor, None, &headers, form).await
}

async fn save_score(
    app: &App,
    actor: Actor,
    id: Option<i32>,
    headers: &HeaderMap,
    form: ScoreForm,
) -> Response {
    if !login::valid_csrf(headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    let Actor::User { id: user, .. } = actor else {
        return access::access_denied();
    };
    let role = form.role.trim();
    if !decimal(&form.speaks) || !decimal(&form.ranks) || role.chars().count() > 4 {
        return private((
            StatusCode::UNPROCESSABLE_ENTITY,
            "Enter valid scores and a debater role of at most four characters.",
        ));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let old_score = if let Some(id) = id {
            let Some(score) = score_row(&mut db, id).await? else { return Ok(missing()); };
            Some(score)
        } else {
            None
        };
        let old_round = old_score.as_ref().map(|score| score.round_id);
        let mut before = score_audit(&mut db, form.round_id).await?;
        if let Some(previous) = old_round.filter(|previous| *previous != form.round_id) {
            before.extend(score_audit(&mut db, previous).await?);
        }
        let valid_debater: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM tab_debater WHERE id = ?)")
            .bind(form.debater_id).fetch_one(&mut *db).await?;
        if !before.contains_key(&form.round_id) || !valid_debater {
            return Ok(private((StatusCode::UNPROCESSABLE_ENTITY, "Choose an existing round and debater.")));
        }
        let role = (!role.is_empty()).then_some(role);
        let saved_id = if let Some(id) = id {
            sqlx::query("UPDATE tab_roundstats SET round_id = ?, debater_id = ?, debater_role = ?,
                           speaks = CAST(? AS DECIMAL(6,4)), ranks = CAST(? AS DECIMAL(6,4)) WHERE id = ?")
                .bind(form.round_id).bind(form.debater_id).bind(role)
                .bind(form.speaks.trim()).bind(form.ranks.trim()).bind(id).execute(&mut *db).await?;
            id as u64
        } else {
            sqlx::query("INSERT INTO tab_roundstats (round_id, debater_id, debater_role, speaks, ranks)
                         VALUES (?, ?, ?, CAST(? AS DECIMAL(6,4)), CAST(? AS DECIMAL(6,4)))")
                .bind(form.round_id).bind(form.debater_id).bind(role)
                .bind(form.speaks.trim()).bind(form.ranks.trim()).execute(&mut *db).await?.last_insert_id()
        };
        let mut after = score_audit(&mut db, form.round_id).await?;
        if let Some(previous) = old_round.filter(|previous| *previous != form.round_id) {
            after.extend(score_audit(&mut db, previous).await?);
        }
        round_audit::record(&mut db, &before, &after, user, "admin scores").await?;
        if let Ok(object_id) = i32::try_from(saved_id)
            && let Some(description) = description(&mut db, object_id).await?
        {
            let (flag, message) = if let Some(old) = old_score {
                let current = score_row(&mut db, object_id).await?.ok_or(sqlx::Error::RowNotFound)?;
                let fields: Vec<_> = [
                    (old.debater_id != current.debater_id, "Debater"),
                    (old.round_id != current.round_id, "Round"),
                    (old.speaks != current.speaks, "Speaks"),
                    (old.ranks != current.ranks, "Ranks"),
                    (old.role != current.role, "Debater role"),
                ].into_iter().filter_map(|(changed, name)| changed.then_some(name)).collect();
                let message = if fields.is_empty() { serde_json::json!([]) } else {
                    serde_json::json!([{ "changed": { "fields": fields } }])
                };
                (2, message)
            } else {
                (1, serde_json::json!([{ "added": {} }]))
            };
            log_score(&mut db, user, saved_id, &description, flag, message.to_string()).await?;
        }
        db.commit().await?;
        let path = if form._continue.is_some() {
            format!("{STATISTICS}{saved_id}/change/")
        } else if form._addanother.is_some() {
            format!("{STATISTICS}add/")
        } else {
            STATISTICS.to_owned()
        };
        Ok::<_, sqlx::Error>(access::found(path))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

pub(crate) async fn score_audit(
    db: &mut MySqlConnection,
    id: i32,
) -> Result<std::collections::BTreeMap<i32, round_audit::Snapshot>, sqlx::Error> {
    let round: Option<i32> = sqlx::query_scalar("SELECT round_number FROM tab_round WHERE id = ?")
        .bind(id)
        .fetch_optional(&mut *db)
        .await?;
    match round {
        Some(round) => round_audit::capture_one(db, round, id).await,
        None => Ok(Default::default()),
    }
}

async fn delete_selected_scores(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let fields = Fields::parse(&body);
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Actor::User {
        id: user,
        board: false,
        staff: true,
        superuser: true,
    } = actor
    else {
        return access::access_denied();
    };
    if fields.get("action") != "delete_selected" {
        return access::found(STATISTICS);
    }
    let selected = fields
        .all("_selected_action")
        .iter()
        .map(|value| value.parse::<i32>())
        .collect::<Result<std::collections::BTreeSet<_>, _>>();
    let Ok(selected) = selected else {
        return private((StatusCode::BAD_REQUEST, "Invalid score selection."));
    };
    let confirmed = fields.get("post") == "yes";
    if selected.is_empty() && (confirmed || !fields.flag("select_across")) {
        return access::found(STATISTICS);
    }
    let result = async {
        let mut db = app.db.begin().await?;
        if confirmed {
            lock_round(&mut db).await?;
        }
        let mut query = sqlx::QueryBuilder::<sqlx::MySql>::new(
            "SELECT s.id, s.round_id, CONCAT('Results for ', d.name, ' in round ', r.round_number)
             FROM tab_roundstats s JOIN tab_debater d ON s.debater_id = d.id
             JOIN tab_round r ON s.round_id = r.id",
        );
        if confirmed || !fields.flag("select_across") {
            query.push(" WHERE s.id IN (");
            let mut options = query.separated(", ");
            for id in selected { options.push_bind(id); }
            query.push(")");
        }
        query.push(" ORDER BY s.id DESC");
        let scores = query.build_query_as::<(i32, i32, String)>().fetch_all(&mut *db).await?;
        if scores.is_empty() {
            return Ok(access::found(STATISTICS));
        }
        if confirmed {
            let rounds = scores.iter().map(|(_, round, _)| *round)
                .collect::<std::collections::BTreeSet<_>>();
            let mut before = std::collections::BTreeMap::new();
            for round in &rounds {
                before.extend(score_audit(&mut db, *round).await?);
            }
            for (id, _, description) in &scores {
                log_score(&mut db, user, *id as u64, description, 3, String::new()).await?;
            }
            let mut delete = sqlx::QueryBuilder::<sqlx::MySql>::new("DELETE FROM tab_roundstats WHERE id IN (");
            let mut options = delete.separated(", ");
            for (id, _, _) in &scores {
                options.push_bind(id);
            }
            delete.push(")").build().execute(&mut *db).await?;
            let mut after = std::collections::BTreeMap::new();
            for round in rounds {
                after.extend(score_audit(&mut db, round).await?);
            }
            round_audit::record(&mut db, &before, &after, user, "admin scores").await?;
            db.commit().await?;
            return Ok(access::found(STATISTICS));
        }
        db.commit().await?;
        let shell = match admin_html::Shell::load(&app, actor, &headers, STATISTICS).await {
            Ok(shell) => shell,
            Err(response) => return Ok(*response),
        };
        let context = context! {
            title => "Are you sure?",
            media => scripts(),
            opts => score_opts(),
            app_url => "/admin/tab/",
            model_url => STATISTICS,
            objects_name => "round stats",
            model_count => [("round stats", scores.len())],
            deletable_objects => [scores.iter().map(|(id, _, text)| score_delete_item(*id, text)).collect::<Vec<_>>().join("\n")],
            queryset => scores.iter().map(|(id, _, _)| context! { pk => id }).collect::<Vec<_>>(),
            action_checkbox_name => "_selected_action",
        };
        Ok::<_, sqlx::Error>(shell.render("admin/delete_selected_confirmation.html", context, &headers))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

fn score_delete_item(id: i32, description: &str) -> String {
    format!(
        "\t<li>Round stats: <a href=\"{STATISTICS}{id}/change/\">{}</a></li>",
        legacy_html::escape(description),
    )
}

async fn score_history(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    Query(query): Query<ScoreList>,
) -> Response {
    let object_url = format!("{STATISTICS}{id}/");
    let shell = match admin_html::Shell::load(
        &app,
        actor,
        &headers,
        &format!("{object_url}history/"),
    )
    .await
    {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let result = async {
        let mut db = app.db.acquire().await?;
        let Some(description) = description(&mut db, id).await? else {
            return Ok(missing());
        };
        let filter =
            "FROM django_admin_log l JOIN django_content_type t ON l.content_type_id = t.id
             JOIN auth_user u ON l.user_id = u.id
             WHERE t.app_label = 'tab' AND t.model = 'roundstats' AND l.object_id = ?";
        let object_id = id.to_string();
        let count: i64 =
            sqlx::query_scalar(sqlx::AssertSqlSafe(format!("SELECT COUNT(*) {filter}")))
                .bind(&object_id)
                .fetch_one(&mut *db)
                .await?;
        let count = u32::try_from(count).unwrap_or(u32::MAX);
        let pages = count.div_ceil(PER_PAGE).max(1);
        let page = query.p.unwrap_or(1).clamp(1, pages);
        let log = sqlx::query_as::<_, (NaiveDateTime, String, String, String)>(
            sqlx::AssertSqlSafe(format!(
                "SELECT l.action_time, u.username, TRIM(CONCAT(u.first_name, ' ', u.last_name)),
                  l.change_message {filter} ORDER BY l.action_time, l.id LIMIT ? OFFSET ?"
            )),
        )
        .bind(object_id)
        .bind(PER_PAGE)
        .bind((page - 1) * PER_PAGE)
        .fetch_all(&mut *db)
        .await?;
        let action_list = log
            .into_iter()
            .map(|(time, username, name, message)| {
                context! {
                    action_time => django_datetime(time),
                    user => context! { get_username => username, get_full_name => name },
                    get_change_message => admin_change_message(&message),
                }
            })
            .collect::<Vec<_>>();
        let context = context! {
            title => format!("Change history: {description}"),
            object => description,
            object_url,
            opts => score_opts(),
            app_url => "/admin/tab/",
            model_url => STATISTICS,
            module_name => "Round stats",
            action_list, count, page, pages,
            pagination_required => pages > 1,
            page_range => (1..=pages).collect::<Vec<_>>(),
            page_var => "p",
        };
        Ok::<_, sqlx::Error>(shell.render("admin/object_history.html", context, &headers))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn django_datetime(time: NaiveDateTime) -> String {
    let month = [
        "Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.",
        "Dec.",
    ][time.month0() as usize];
    let clock = match (time.hour(), time.minute()) {
        (0, 0) => "midnight".into(),
        (12, 0) => "noon".into(),
        (hour, minute) => {
            let suffix = if hour < 12 { "a.m." } else { "p.m." };
            let hour = (hour + 11) % 12 + 1;
            if minute == 0 {
                format!("{hour} {suffix}")
            } else {
                format!("{hour}:{minute:02} {suffix}")
            }
        }
    };
    format!("{month} {}, {}, {clock}", time.day(), time.year())
}

fn admin_change_message(message: &str) -> String {
    let Ok(entries) = serde_json::from_str::<Vec<serde_json::Value>>(message) else {
        return message.into();
    };
    let messages = entries
        .iter()
        .filter_map(|entry| {
            if entry.get("added").is_some() {
                return Some("Added.".into());
            }
            if entry.get("deleted").is_some() {
                return Some("Deleted.".into());
            }
            let fields: Vec<_> = entry["changed"]["fields"]
                .as_array()?
                .iter()
                .filter_map(serde_json::Value::as_str)
                .collect();
            let (last, rest) = fields.split_last()?;
            if rest.is_empty() {
                Some(format!("Changed {last}."))
            } else {
                Some(format!("Changed {} and {last}.", rest.join(", ")))
            }
        })
        .collect::<Vec<_>>();
    if messages.is_empty() {
        "No fields changed.".into()
    } else {
        messages.join(" ")
    }
}

async fn log_score(
    db: &mut MySqlConnection,
    user: i32,
    score: u64,
    description: &str,
    flag: u8,
    message: String,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO django_admin_log (action_time, object_id, object_repr, action_flag,
            change_message, content_type_id, user_id)
         VALUES (UTC_TIMESTAMP(6), ?, ?, ?, ?,
            (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = 'roundstats'), ?)",
    )
    .bind(score.to_string()).bind(description.chars().take(200).collect::<String>())
    .bind(flag).bind(message).bind(user).execute(db).await?;
    Ok(())
}

async fn description(db: &mut MySqlConnection, id: i32) -> Result<Option<String>, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT CONCAT('Results for ', d.name, ' in round ', r.round_number)
         FROM tab_roundstats s JOIN tab_debater d ON d.id = s.debater_id
         JOIN tab_round r ON r.id = s.round_id WHERE s.id = ?",
    )
    .bind(id)
    .fetch_optional(db)
    .await
}

async fn confirm_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    Query(query): Query<ChangeQuery>,
) -> Response {
    let object_url = format!("{STATISTICS}{id}/");
    let shell =
        match admin_html::Shell::load(&app, actor, &headers, &format!("{object_url}delete/")).await
        {
            Ok(shell) => shell,
            Err(response) => return *response,
        };
    let result = async {
        let mut db = app.db.acquire().await?;
        let Some(description) = description(&mut db, id).await? else {
            return Ok(missing());
        };
        let page = context! {
            title => "Are you sure?",
            media => scripts(),
            opts => score_opts(),
            app_url => "/admin/tab/",
            model_url => STATISTICS,
            object_url,
            filter_suffix => preserved_filters(query._changelist_filters.as_deref().unwrap_or("")),
            deleted_objects => score_delete_item(id, &description),
            object => description,
            object_name => "round stats",
            model_count => [("round stats", 1)],
            is_popup => query._popup.is_some(),
            is_popup_var => "_popup",
            to_field => query._to_field,
            to_field_var => "_to_field",
        };
        Ok::<_, sqlx::Error>(shell.render("admin/delete_confirmation.html", page, &headers))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Deserialize)]
struct DeleteForm {
    csrfmiddlewaretoken: String,
    confirm: Option<String>,
    post: Option<String>,
}

async fn delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    Form(form): Form<DeleteForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    if form.confirm.as_deref() != Some("on") && form.post.as_deref() != Some("yes") {
        return private((
            StatusCode::UNPROCESSABLE_ENTITY,
            "Confirm the deletion first.",
        ));
    }
    let Actor::User { id: user, .. } = actor else {
        return access::access_denied();
    };
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let Some(score) = score_row(&mut db, id).await? else {
            return Ok(missing());
        };
        let before = score_audit(&mut db, score.round_id).await?;
        if let Some(description) = description(&mut db, id).await? {
            log_score(&mut db, user, id as u64, &description, 3, String::new()).await?;
        }
        sqlx::query("DELETE FROM tab_roundstats WHERE id = ?")
            .bind(id)
            .execute(&mut *db)
            .await?;
        let after = score_audit(&mut db, score.round_id).await?;
        round_audit::record(&mut db, &before, &after, user, "admin scores").await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(STATISTICS))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Administration page not found."))
}
