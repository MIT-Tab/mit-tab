use std::collections::{BTreeMap, BTreeSet};

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{RawQuery, State},
    http::{HeaderMap, Method, StatusCode, Uri},
    response::{Html, Response},
    routing::get,
};
use chrono::NaiveDateTime;
use minijinja::{Value, context};
use sqlx::{AssertSqlSafe, MySqlConnection, QueryBuilder};

use crate::{
    App, access,
    admin_changelist::{self, Query, filter_ids},
    admin_html::{self, Shell},
    admin_model_data::{BoundField, Form, Kind, MANAGED, Resource},
    entity_store,
    legacy_html::escape,
    legacy_session::Actor,
    login, private, publication,
    registration_form::Fields,
    roster_log,
    settings::Settings,
    staff_pairing::lock_round_if_configured,
    unavailable,
};

pub fn routes() -> Router<App> {
    MANAGED.iter().fold(Router::new(), |router, model| {
        let model = format!("/admin/tab/{model}/");
        router
            .route(&model, get(entry).post(entry))
            .route(&format!("{model}add/"), get(entry).post(entry))
            .route(&format!("{model}{{id}}/change/"), get(entry).post(entry))
            .route(&format!("{model}{{id}}/delete/"), get(entry).post(entry))
            .route(&format!("{model}{{id}}/history/"), get(entry))
    })
}

#[derive(Clone, Copy)]
enum Screen {
    List,
    Add,
    Change(i32),
    Delete(i32),
    History(i32),
}

impl Screen {
    fn find(path: &str) -> Option<(&str, Self)> {
        let segments: Vec<_> = path.trim_matches('/').split('/').collect();
        match segments.as_slice() {
            ["admin", "tab", model] => Some((model, Self::List)),
            ["admin", "tab", model, "add"] => Some((model, Self::Add)),
            ["admin", "tab", model, id, operation] => {
                let id = id.parse().ok()?;
                let screen = match *operation {
                    "change" => Self::Change(id),
                    "delete" => Self::Delete(id),
                    "history" => Self::History(id),
                    _ => return None,
                };
                Some((model, screen))
            }
            _ => None,
        }
    }
}

struct Page<'a> {
    shell: Shell,
    resource: Resource,
    query: Query,
    model_url: String,
    headers: &'a HeaderMap,
    user: i32,
    target: &'a str,
}

impl Page<'_> {
    fn markup(self, template: &str, context: Value) -> Response {
        let base = context! {
            opts => self.resource.list.model.opts(),
            app_url => "/admin/tab/",
            model_url => &self.model_url,
        };
        self.shell
            .render(template, context! { ..base, ..context }, self.headers)
    }

    fn preserved(&self) -> &str {
        self.query.get("_changelist_filters").unwrap_or_default()
    }

    fn list_url(&self) -> String {
        let params = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(url::form_urlencoded::parse(self.preserved().as_bytes()))
            .finish();
        if params.is_empty() {
            self.model_url.clone()
        } else {
            format!("{}?{params}", self.model_url)
        }
    }

    fn add_or_change_url(&self, id: Option<i32>) -> String {
        let target = id.map_or_else(
            || format!("{}add/", self.model_url),
            |id| format!("{}{id}/change/", self.model_url),
        );
        format!(
            "{target}{}",
            admin_html::preserved_filters(self.preserved())
        )
    }
}

async fn entry(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    uri: Uri,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
    method: Method,
    body: Bytes,
) -> Response {
    let Some((model, screen)) = Screen::find(uri.path()) else {
        return private((StatusCode::NOT_FOUND, "Administration page not found."));
    };
    let Actor::User {
        id: user,
        board: false,
        staff: true,
        superuser: true,
    } = actor
    else {
        return access::access_denied();
    };
    if matches!(screen, Screen::List) && method != Method::POST {
        return admin_changelist::get(&app, actor, &headers, "tab", model, raw)
            .await
            .unwrap_or_else(unavailable);
    }
    let post = (method == Method::POST).then(|| Fields::parse(&body));
    if let Some(post) = &post
        && !login::valid_csrf(&headers, post.get("csrfmiddlewaretoken"))
    {
        return login::csrf_error();
    }
    let Some(resource) = Resource::find(model) else {
        return private((StatusCode::NOT_FOUND, "Administration page not found."));
    };
    let query = Query::new(raw);
    if query
        .get("_to_field")
        .is_some_and(|field| !matches!(field, "" | "id"))
        || post
            .as_ref()
            .is_some_and(|post| !matches!(post.get("_to_field"), "" | "id"))
    {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }
    let shell = match Shell::load(&app, actor, &headers, uri.path()).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let model_url = resource.list.model.url();
    let page = Page {
        shell,
        resource,
        query,
        model_url,
        user,
        headers: &headers,
        target: uri
            .path_and_query()
            .map_or_else(|| uri.path(), |query| query.as_str()),
    };
    let result = match screen {
        Screen::List => bulk(&app, page, &post.expect("list POST")).await,
        Screen::Add => change(&app, page, None, post.as_ref()).await,
        Screen::Change(id) => change(&app, page, Some(id), post.as_ref()).await,
        Screen::Delete(id) => delete(&app, page, id, post.as_ref()).await,
        Screen::History(id) => history(&app, page, id).await,
    };
    result.unwrap_or_else(|_| unavailable())
}

async fn change(
    app: &App,
    page: Page<'_>,
    id: Option<i32>,
    post: Option<&Fields>,
) -> Result<Response, sqlx::Error> {
    let resource = &page.resource;
    let mut db = app.db.begin().await?;
    if post.is_some() {
        lock_round_if_configured(&mut db).await?;
    }
    let (initial, original) = if let Some(id) = id {
        let Some(initial) = resource.load(&mut db, id, post.is_some()).await? else {
            return Ok(access::found("/admin/"));
        };
        (Some(initial), resource.describe(&mut db, id).await?)
    } else {
        (None, None)
    };
    let mut form = Form::load(resource, &mut db, initial.as_deref(), post).await?;
    if let Some(post) = post
        && let Some(values) = form.clean(resource, &mut db, id).await?
    {
        let effects = match resource.list.model.name {
            "tabsettings" => SettingEffects::for_keys(
                initial
                    .iter()
                    .filter_map(|row| row[0].as_deref())
                    .chain(values[0].as_deref()),
            ),
            "breakingteam" => SettingEffects {
                placements: true,
                roster: false,
            },
            _ => SettingEffects::default(),
        };
        effects.refresh(&mut db).await?;
        match resource.save(&mut db, id, &values).await {
            Ok(saved) => {
                effects.refresh(&mut db).await?;
                let description = resource
                    .describe(&mut db, saved)
                    .await?
                    .ok_or(sqlx::Error::RowNotFound)?;
                let (flag, message) = change_log(resource, initial.as_deref(), &values);
                let log_id = admin_html::log_action(
                    &mut db,
                    resource.list.model.name,
                    page.user,
                    saved,
                    &description,
                    flag,
                    message,
                )
                .await?;
                db.commit().await?;
                if !post.all("_popup").is_empty() {
                    return Ok(popup_response(
                        id.map(|_| "change"),
                        saved,
                        Some(&description),
                    ));
                }
                let (destination, notice) = if !post.all("_continue").is_empty() {
                    (page.add_or_change_url(Some(saved)), "continue")
                } else if !post.all("_addanother").is_empty() {
                    (page.add_or_change_url(None), "another")
                } else {
                    (page.list_url(), "save")
                };
                return Ok(admin_html::redirect_with_notice(
                    app,
                    page.headers,
                    destination,
                    log_id,
                    notice,
                ));
            }
            Err(sqlx::Error::Database(error))
                if error.is_unique_violation() && resource.unique.is_some() =>
            {
                form.mark_duplicate(resource);
                db.rollback().await?;
                return Ok(render_form(page, id, original.as_deref(), form, Some(post)));
            }
            Err(error) => return Err(error),
        }
    }
    db.commit().await?;
    Ok(render_form(page, id, original.as_deref(), form, post))
}

fn render_form(
    page: Page<'_>,
    id: Option<i32>,
    subtitle: Option<&str>,
    form: Form,
    post: Option<&Fields>,
) -> Response {
    let resource = &page.resource;
    let widgets = form
        .fields
        .iter()
        .map(field_line)
        .collect::<Result<Vec<_>, _>>();
    let Ok(widgets) = widgets else {
        return unavailable();
    };
    let errors = form
        .fields
        .iter()
        .filter(|field| field.error.is_some())
        .count()
        + usize::from(form.non_field_error.is_some());
    let add = id.is_none();
    let action = if add { "Add" } else { "Change" };
    let popup = page.query.get("_popup").is_some()
        || post.is_some_and(|post| !post.all("_popup").is_empty());
    let suffix = admin_html::preserved_filters(page.preserved());
    let object_url = id.map(|id| format!("{}{id}/", page.model_url));
    let to_field = page
        .query
        .get("_to_field")
        .or_else(|| post.map(|post| post.get("_to_field")));
    let ctx = context! {
        title => format!("{action} {}", resource.list.model.singular),
        original => subtitle, subtitle, change => !add, add,
        is_popup => popup, is_popup_var => "_popup", to_field, to_field_var => "_to_field",
        object_url, form_url => &suffix, filter_suffix => &suffix,
        adminform => [context! { classes => "", lines => widgets }],
        show_delete_link => !popup && !add, show_save => true,
        show_save_and_add_another => !popup, show_save_and_continue => !popup,
        has_view_permission => true, can_change => true,
        errors => (0..errors).collect::<Vec<_>>(),
        non_field_errors => form.non_field_error.map(|message| error_list(message, true)).unwrap_or_default(),
        media => admin_html::scripts(), prepopulated_fields_json => "[]",
    };
    page.markup("admin/change_form.html", ctx)
}

fn decimal_equal(old: Option<&str>, new: Option<&str>) -> bool {
    let number = |value: Option<&str>| value.and_then(|value| value.parse::<f64>().ok());
    number(old) == number(new)
}

fn change_log(
    resource: &Resource,
    initial: Option<&[Option<String>]>,
    values: &[Option<String>],
) -> (u8, String) {
    let Some(old) = initial else {
        return (1, r#"[{"added": {}}]"#.into());
    };
    let names: Vec<_> = resource
        .fields
        .iter()
        .zip(old.iter().zip(values))
        .filter_map(|(field, (old, new))| {
            let equal = match field.kind {
                Kind::Decimal => decimal_equal(old.as_deref(), new.as_deref()),
                _ => old == new,
            };
            (!equal).then_some(field.label)
        })
        .collect();
    let message = if names.is_empty() {
        "[]".into()
    } else {
        serde_json::json!([{"changed": {"fields": names}}]).to_string()
    };
    (2, message)
}

fn error_list(message: &str, non_field: bool) -> String {
    let class = if non_field {
        "errorlist nonfield"
    } else {
        "errorlist"
    };
    format!("<ul class=\"{class}\"><li>{}</li></ul>", escape(message))
}

fn field_line(bound: &BoundField) -> Result<Value, minijinja::Error> {
    let field = bound.field;
    let name = field.name;
    let checked = bound.value.as_deref() == Some("1");
    let required = if field.required { " required" } else { "" };
    let value = bound
        .value
        .as_ref()
        .filter(|value| !value.is_empty())
        .map_or_else(String::new, |value| {
            format!(r#" value="{}""#, escape(value))
        });
    let widget = match field.kind {
        Kind::Text(max) => format!(
            r#"<input type="text" name="{name}"{value} class="vTextField" maxlength="{max}"{required} id="id_{name}">"#
        ),
        Kind::Integer => format!(
            r#"<input type="number" name="{name}"{value} class="vIntegerField"{required} id="id_{name}">"#
        ),
        Kind::Decimal => format!(
            r#"<input type="number" name="{name}"{value} step="0.01"{required} id="id_{name}">"#
        ),
        Kind::Checkbox => {
            let state = if checked { " checked" } else { "" };
            format!(r#"<input type="checkbox" name="{name}" id="id_{name}"{state}>"#)
        }
        Kind::Choice(_) | Kind::Relation(_) => {
            let select = admin_html::render_fragment(
                "admin/model_select.html",
                context! {
                    name, options => &bound.options, selected => bound.value.as_deref(),
                    blank => matches!(field.kind, Kind::Relation(_)),
                    required => matches!(field.kind, Kind::Relation(_)),
                },
            )?;
            if let Kind::Relation(relation) = field.kind {
                admin_html::render_fragment("admin/widgets/related_widget_wrapper.html", context! {
                    name, model => relation.model,
                    rendered_widget => Value::from_safe_string(select.trim_end_matches('\n').into()),
                    change_related_template_url => format!("{}__fk__/change/", relation.admin_url),
                    add_related_url => format!("{}add/", relation.admin_url),
                    url_params => "_to_field=id&_popup=1",
                    view_related_url_params => "_to_field=id",
                    can_change_related => true, can_add_related => true, can_view_related => true,
                })?.trim().to_owned()
            } else {
                select.trim_end_matches('\n').to_owned()
            }
        }
    };
    let class = if field.required {
        r#" class="required""#
    } else {
        ""
    };
    let checkbox = matches!(field.kind, Kind::Checkbox);
    let label_class = if checkbox {
        r#" class="vCheckboxLabel""#
    } else {
        class
    };
    let errors = bound
        .error
        .as_deref()
        .map(|message| error_list(message, false))
        .unwrap_or_default();
    let suffix = if checkbox { "" } else { ":" };
    Ok(context! {
        has_visible_field => true, errors,
        fields => [context! {
            field => context! {name}, widget, is_checkbox => checkbox,
            label_tag => format!("<label{label_class} for=\"id_{name}\">{}{suffix}</label>", field.label),
        }],
    })
}

fn popup_response(action: Option<&str>, id: i32, description: Option<&str>) -> Response {
    let quoted = |value: &str| {
        let mut json = String::new();
        for unit in serde_json::to_string(value)
            .expect("serialize text")
            .encode_utf16()
        {
            if unit > 0x7f {
                json.push_str(&format!("\\u{unit:04x}"));
            } else {
                json.push(char::from_u32(u32::from(unit)).expect("ASCII"));
            }
        }
        json
    };
    let value = quoted(&id.to_string());
    let data = match (action, description) {
        (Some("change"), Some(description)) => format!(
            r#"{{"action": "change", "value": {value}, "obj": {}, "new_value": {value}}}"#,
            quoted(description)
        ),
        (None, Some(description)) => {
            format!(r#"{{"value": {value}, "obj": {}}}"#, quoted(description))
        }
        _ => format!(r#"{{"action": "delete", "value": {value}}}"#),
    };
    match admin_html::render_fragment(
        "admin/popup_response.html",
        context! { popup_response_data => data },
    ) {
        Ok(body) => private(Html(body)),
        Err(_) => unavailable(),
    }
}

#[derive(Default)]
struct SettingEffects {
    placements: bool,
    roster: bool,
}

impl SettingEffects {
    fn for_keys<'a>(keys: impl IntoIterator<Item = &'a str>) -> Self {
        let mut effects = Self::default();
        for key in keys {
            effects.placements |= publication::VISIBILITY_KEYS.contains(&key);
            effects.roster |= key == "teams_public";
        }
        effects
    }

    async fn for_deleted_keys(db: &mut MySqlConnection, ids: &[i32]) -> Result<Self, sqlx::Error> {
        let mut query = QueryBuilder::new(
            "SELECT DISTINCT `key` FROM tab_tabsettings
             WHERE `key` IN ('teams_public', 'var_teams_visible', 'nov_teams_visible') AND id IN (",
        );
        let mut selected = query.separated(", ");
        for id in ids {
            selected.push_bind(*id);
        }
        let keys = query
            .push(")")
            .build_query_scalar::<String>()
            .fetch_all(db)
            .await?;
        Ok(Self::for_keys(keys.iter().map(String::as_str)))
    }

    async fn refresh(&self, db: &mut MySqlConnection) -> Result<(), sqlx::Error> {
        if self.placements {
            let settings = Settings::load(db, publication::VISIBILITY_KEYS).await?;
            publication::refresh_placements(db, &settings).await?;
        }
        if self.roster {
            roster_log::enabled(db).await?;
        }
        Ok(())
    }
}

const TAG_RELATIONS: &[(&str, &str, &str, &str)] = &[
    (
        "tab_team_required_room_tags",
        "Team_required_room_tags",
        "Team-roomtag relationship",
        "team-roomtag relationships",
    ),
    (
        "tab_judge_required_room_tags",
        "Judge_required_room_tags",
        "Judge-roomtag relationship",
        "judge-roomtag relationships",
    ),
    (
        "tab_room_tags",
        "Room_tags",
        "Room-roomtag relationship",
        "room-roomtag relationships",
    ),
];

struct DeletionSummary {
    counts: Vec<(&'static str, usize)>,
    objects: Vec<String>,
}

struct Removed {
    rows: Vec<(i32, String)>,
}

impl Removed {
    async fn read(
        db: &mut MySqlConnection,
        resource: &Resource,
        filter: &[i32],
        selected: Option<&BTreeSet<i32>>,
        lock: bool,
    ) -> Result<Self, sqlx::Error> {
        let list = &resource.list;
        let mut sql = QueryBuilder::new(format!(
            "SELECT t.id, {} FROM {} WHERE TRUE",
            list.label, list.source
        ));
        filter_ids(&mut sql, filter);
        if let Some(ids) = selected {
            if ids.is_empty() {
                return Ok(Self { rows: Vec::new() });
            }
            sql.push(" AND t.id IN (");
            let mut selector = sql.separated(", ");
            for id in ids {
                selector.push_bind(id);
            }
            sql.push(")");
        }
        sql.push(" ORDER BY ").push(list.ordering);
        if lock {
            sql.push(" FOR UPDATE");
        }
        let rows = sql.build_query_as().fetch_all(db).await?;
        Ok(Self { rows })
    }

    async fn summary(
        &self,
        db: &mut MySqlConnection,
        page: &Page<'_>,
    ) -> Result<DeletionSummary, sqlx::Error> {
        let mut children: BTreeMap<i32, Vec<String>> = BTreeMap::new();
        let model = &page.resource.list.model;
        let mut counts = vec![(model.plural, self.rows.len())];
        if model.name == "roomtag" {
            for &(table, object_name, singular, plural) in TAG_RELATIONS {
                let mut sql = QueryBuilder::new(format!(
                    "SELECT id, roomtag_id FROM {table} WHERE roomtag_id IN ("
                ));
                let mut ids = sql.separated(", ");
                for (id, _) in &self.rows {
                    ids.push_bind(*id);
                }
                let links = sql
                    .push(") ORDER BY id")
                    .build_query_as::<(i32, i32)>()
                    .fetch_all(&mut *db)
                    .await?;
                if links.is_empty() {
                    continue;
                }
                counts.push((plural, links.len()));
                for (id, tag) in links {
                    children.entry(tag).or_default().push(format!(
                        "		<li>{singular}: {object_name} object ({id})</li>"
                    ));
                }
            }
        }
        let title = admin_html::capitalized(model.singular);
        let objects = self
            .rows
            .iter()
            .map(|(id, label)| {
                let item = format!(
                    "	<li>{title}: <a href=\"{}{id}/change/\">{}</a>",
                    page.model_url,
                    escape(label)
                );
                if let Some(children) = children.get(id) {
                    format!("{item}\n\t<ul>\n{}\n\t</ul>\n\t</li>", children.join("\n"))
                } else {
                    format!("{item}</li>")
                }
            })
            .collect();
        Ok(DeletionSummary { counts, objects })
    }

    async fn execute(&self, db: &mut MySqlConnection, page: &Page<'_>) -> Result<u64, sqlx::Error> {
        let mut last_log = 0;
        for (id, name) in &self.rows {
            last_log = admin_html::log_action(
                db,
                page.resource.list.model.name,
                page.user,
                *id,
                name,
                3,
                String::new(),
            )
            .await?;
        }
        let ids: Vec<_> = self.rows.iter().map(|(id, _)| *id).collect();
        let effects = match page.resource.list.model.name {
            "tabsettings" => SettingEffects::for_deleted_keys(db, &ids).await?,
            "breakingteam" => SettingEffects {
                placements: true,
                roster: false,
            },
            _ => SettingEffects::default(),
        };
        effects.refresh(db).await?;
        if page.resource.list.model.name == "roomtag" {
            let links: Vec<_> = TAG_RELATIONS
                .iter()
                .map(|&(table, _, _, _)| (table, "roomtag_id"))
                .collect();
            entity_store::delete_links(db, &ids, &links).await?;
        }
        entity_store::delete_links(db, &ids, &[(page.resource.list.table(), "id")]).await?;
        effects.refresh(db).await?;
        Ok(last_log)
    }
}

async fn delete(
    app: &App,
    page: Page<'_>,
    id: i32,
    post: Option<&Fields>,
) -> Result<Response, sqlx::Error> {
    let mut db = app.db.begin().await?;
    let confirm = post.is_some_and(|form| form.get("post") == "yes");
    if post.is_some() && !confirm {
        return Ok(private((
            StatusCode::BAD_REQUEST,
            "Confirm the deletion first.",
        )));
    }
    if confirm {
        lock_round_if_configured(&mut db).await?;
    }
    let records = Removed::read(&mut db, &page.resource, &[id], None, confirm).await?;
    let Some((_, description)) = records.rows.first() else {
        return Ok(access::found("/admin/"));
    };
    if confirm {
        let log = records.execute(&mut db, &page).await?;
        db.commit().await?;
        if post.is_some_and(|post| !post.all("_popup").is_empty()) {
            return Ok(popup_response(Some("delete"), id, None));
        }
        return Ok(admin_html::redirect_with_notice(
            app,
            page.headers,
            page.list_url(),
            log,
            "delete",
        ));
    }
    let summary = records.summary(&mut db, &page).await?;
    db.commit().await?;
    let model = &page.resource.list.model;
    let ctx = context! {
        title => "Are you sure?", media => admin_html::scripts(),
        object_url => format!("{}{id}/", page.model_url), object => description,
        deleted_objects => summary.objects.join("\n"),
        object_name => model.singular, model_count => summary.counts,
        is_popup => page.query.get("_popup").is_some(), is_popup_var => "_popup",
        to_field => page.query.get("_to_field"), to_field_var => "_to_field",
    };
    Ok(page.markup("admin/delete_confirmation.html", ctx))
}

async fn bulk(app: &App, page: Page<'_>, post: &Fields) -> Result<Response, sqlx::Error> {
    let Some(filter) = page.query.record_ids(&[]) else {
        return Ok(access::found(format!("{}?e=1", page.model_url)));
    };
    if post.get("action") != "delete_selected" {
        return Ok(access::found(page.target));
    }
    let Ok(selected) = post
        .all("_selected_action")
        .iter()
        .map(|value| value.parse::<i32>())
        .collect::<Result<BTreeSet<_>, _>>()
    else {
        return Ok(private((
            StatusCode::BAD_REQUEST,
            "Invalid record selection.",
        )));
    };
    let confirm = post.get("post") == "yes";
    let selected = (confirm || !post.flag("select_across")).then_some(&selected);
    let mut db = app.db.begin().await?;
    if confirm {
        lock_round_if_configured(&mut db).await?;
    }
    let records = Removed::read(&mut db, &page.resource, &filter, selected, confirm).await?;
    if records.rows.is_empty() {
        return Ok(access::found(page.target));
    }
    if confirm {
        let log = records.execute(&mut db, &page).await?;
        db.commit().await?;
        let notice = format!("bulk-{}", records.rows.len());
        return Ok(admin_html::redirect_with_notice(
            app,
            page.headers,
            page.target.to_owned(),
            log,
            &notice,
        ));
    }
    let summary = records.summary(&mut db, &page).await?;
    db.commit().await?;
    let count = records.rows.len();
    let plural = page.resource.list.model.plural;
    let ctx = context! {
        title => "Are you sure?", media => admin_html::scripts(),
        objects_name => if count == 1 { page.resource.list.model.singular } else { plural },
        model_count => summary.counts,
        deletable_objects => [summary.objects.join("\n")],
        queryset => records.rows.iter().map(|(id, _)| context! {pk => id}).collect::<Vec<_>>(),
        action_checkbox_name => "_selected_action",
    };
    Ok(page.markup("admin/delete_selected_confirmation.html", ctx))
}

async fn history(app: &App, page: Page<'_>, id: i32) -> Result<Response, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    let Some(description) = page.resource.describe(&mut db, id).await? else {
        return Ok(access::found("/admin/"));
    };
    let model = &page.resource.list.model;
    let filter = "FROM django_admin_log l JOIN django_content_type t ON l.content_type_id = t.id
        JOIN auth_user u ON l.user_id = u.id
        WHERE t.app_label = 'tab' AND t.model = ? AND l.object_id = ?";
    let object_id = id.to_string();
    let count: i64 = sqlx::query_scalar(AssertSqlSafe(format!("SELECT COUNT(*) {filter}")))
        .bind(model.name)
        .bind(&object_id)
        .fetch_one(&mut *db)
        .await?;
    let count = u32::try_from(count).unwrap_or(u32::MAX);
    let page_size = admin_changelist::PER_PAGE;
    let pages = count.div_ceil(page_size).max(1);
    let number = u32::try_from(page.query.requested())
        .ok()
        .filter(|page| (1..=pages).contains(page))
        .unwrap_or(pages);
    let log = sqlx::query_as::<_,(NaiveDateTime, String, String, String)>(AssertSqlSafe(format!(
        "SELECT l.action_time, u.username, TRIM(CONCAT(u.first_name, ' ', u.last_name)), l.change_message
         {filter} ORDER BY l.action_time, l.id LIMIT ? OFFSET ?"
    )))
    .bind(model.name).bind(object_id).bind(page_size).bind((number - 1) * page_size)
    .fetch_all(&mut *db).await?;
    let action_list: Vec<_> = log
        .into_iter()
        .map(|(date, user, name, message)| {
            context! {
                action_time => admin_html::django_datetime(date),
                user => context! { get_username => user, get_full_name => name },
                get_change_message => admin_html::admin_change_message(&message),
            }
        })
        .collect();
    let ctx = context! {
        title => format!("Change history: {description}"), object => description,
        object_url => format!("{}{id}/", page.model_url),
        module_name => admin_html::capitalized(model.plural),
        action_list, count, page => number, pages, pagination_required => pages > 1,
        page_range => (1..=pages).collect::<Vec<_>>(), page_var => "p",
    };
    Ok(page.markup("admin/object_history.html", ctx))
}
