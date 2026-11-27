use std::{collections::BTreeMap, sync::LazyLock};

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::{Value, context};
use sqlx::{AssertSqlSafe, MySql, MySqlConnection, QueryBuilder};

use crate::{
    App, access,
    admin_changelist::{Model, PER_PAGE, Page, Query, filter_ids},
    admin_html::{self, Shell, django_datetime},
    legacy_html::escape,
    legacy_session::Actor,
    private, unavailable,
};

const EVENTS: &[(&str, &str)] = &[
    ("create", "Created"),
    ("edit", "Edited"),
    ("manual_judge_assign", "Manual judge assignment"),
    ("manual_judge_remove", "Manual judge removal"),
    ("manual_chair_change", "Manual chair change"),
    ("judges_paired", "Automatic judge assignment"),
];

static CONTENT_TYPES: LazyLock<BTreeMap<String, String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../templates/django_admin/content_type_names.json"
    ))
    .expect("valid Django content type names")
});

const MANUAL_FROM: &str = " FROM tab_manualjudgeassignment t
    JOIN tab_round r ON r.id = t.round_id
    JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
    JOIN tab_judge j ON j.id = t.judge_id LEFT JOIN auth_user u ON u.id = t.created_by_id";
const ROUND_LABEL: &str = "CONCAT('Round ', r.round_number, ' between ', g.name, ' and ', o.name)";
const AUDIT_FROM: &str = " FROM tab_auditevent t LEFT JOIN auth_user u ON u.id = t.user_id";

type ContentType = (i32, String, String);
type ManualRow = (
    i32,
    i32,
    String,
    i32,
    String,
    Option<i32>,
    Option<String>,
    NaiveDateTime,
);
type AuditRow = (
    i32,
    u32,
    String,
    String,
    String,
    String,
    Option<i32>,
    Option<String>,
    NaiveDateTime,
    String,
    String,
);

#[derive(Clone, Copy)]
pub enum Record {
    Audit,
    Manual,
}

impl Record {
    pub fn find(application: &str, model: &str) -> Option<Self> {
        match (application, model) {
            ("tab", "auditevent") => Some(Self::Audit),
            ("tab", "manualjudgeassignment") => Some(Self::Manual),
            _ => None,
        }
    }

    fn model(self) -> Model {
        match self {
            Self::Audit => Model {
                name: "auditevent",
                singular: "audit event",
                plural: "audit events",
            },
            Self::Manual => Model {
                name: "manualjudgeassignment",
                singular: "manual judge assignment",
                plural: "manual judge assignments",
            },
        }
    }

    fn columns(self) -> [Column; 4] {
        match self {
            Self::Audit => [
                Column::new("created_at", "Created at", "t.created_at", true),
                Column::new("event_type", "Event type", "t.event_type", false),
                Column::new("object_repr", "Object repr", "t.object_repr", false),
                Column::new("user", "User", "t.user_id", true),
            ],
            Self::Manual => [
                Column::new("round", "Round", "t.round_id", true),
                Column::new("judge", "Judge", "j.name", true),
                Column::new("created_by", "Created by", "t.created_by_id", true),
                Column::new("created_at", "Created at", "t.created_at", true),
            ],
        }
    }

    fn default_sort(self) -> usize {
        match self {
            Self::Audit => 0,
            Self::Manual => 3,
        }
    }
}

#[derive(Clone, Copy)]
pub(super) struct Column {
    pub(crate) name: &'static str,
    pub(crate) label: &'static str,
    pub(super) sql: &'static str,
    pub(super) nowrap: bool,
    pub(super) sortable: bool,
}

impl Column {
    pub(super) const fn new(
        name: &'static str,
        label: &'static str,
        sql: &'static str,
        nowrap: bool,
    ) -> Self {
        Self {
            name,
            label,
            sql,
            nowrap,
            sortable: true,
        }
    }

    pub(super) const fn unsortable(mut self) -> Self {
        self.sortable = false;
        self
    }
}

fn sort_order(kind: Record, query: &Query) -> Vec<(usize, bool)> {
    let Some(order) = query.get("o") else {
        return vec![(kind.default_sort(), false)];
    };
    order
        .split('.')
        .filter_map(|part| {
            let index: usize = part.trim_start_matches('-').parse().ok()?;
            (index < 4).then_some((index, !part.starts_with('-')))
        })
        .collect()
}

pub(super) fn order_sql(columns: &[Column], order: &[(usize, bool)]) -> String {
    let mut order: Vec<_> = order
        .iter()
        .map(|&(index, ascending)| {
            format!(
                "{} {}",
                columns[index].sql,
                if ascending { "ASC" } else { "DESC" }
            )
        })
        .collect();
    order.push("t.id DESC".into());
    order.join(", ")
}

pub(super) fn headings(
    columns: &[Column],
    order: &[(usize, bool)],
    query: &Query,
    offset: usize,
) -> Vec<Value> {
    let encode = |index: usize, ascending: bool| {
        format!("{}{}", if ascending { "" } else { "-" }, index + offset)
    };
    columns
        .iter()
        .enumerate()
        .map(|(index, column)| {
            if !column.sortable {
                return context! {
                    class_attrib => format!(r#" class="column-{}""#, column.name),
                    text => column.label,
                    sortable => false,
                };
            }
            let mut class = format!("sortable column-{}", column.name);
            let position = order.iter().position(|&(column, _)| column == index);
            let ascending = position.is_some_and(|position| order[position].1);
            if position.is_some() {
                class.push_str(if ascending {
                    " sorted ascending"
                } else {
                    " sorted descending"
                });
            }
            let remove = order
                .iter()
                .filter(|&&(column, _)| column != index)
                .map(|&(column, asc)| encode(column, asc))
                .collect::<Vec<_>>();
            let primary = std::iter::once(encode(index, !ascending))
                .chain(remove.iter().cloned())
                .collect::<Vec<_>>();
            let toggle = order
                .iter()
                .map(|&(column, asc)| encode(column, if column == index { !asc } else { asc }))
                .collect::<Vec<_>>();
            context! {
                class_attrib => format!(r#" class="{class}""#),
                text => column.label,
                sortable => true,
                ascending,
                sort_priority => position.map_or(0, |position| position + 1),
                url_primary => query.url("o", &primary.join(".")),
                url_remove => query.url("o", &remove.join(".")),
                url_toggle => query.url("o", &toggle.join(".")),
            }
        })
        .collect()
}

pub(super) fn content_label(application: &str, model: &str) -> String {
    CONTENT_TYPES
        .get(&format!("{application}.{model}"))
        .map_or_else(|| model.into(), |name| format!("{application} | {name}"))
}

fn event_label(event: &str) -> &str {
    EVENTS
        .iter()
        .find(|&&(value, _)| value == event)
        .map_or("-", |&(_, label)| label)
}

pub(super) fn choice_filter(
    query: &Query,
    field: &str,
    title: &str,
    values: impl IntoIterator<Item = (String, String)>,
) -> Result<String, minijinja::Error> {
    let mut params = query.params.clone();
    let selected = params.remove(field);
    let mut choices = vec![context! {
        display => "All", selected => selected.is_none(), query_string => Query::encode(params),
    }];
    choices.extend(values.into_iter().map(|(value, label)| {
        context! {
            display => label, selected => selected.as_deref() == Some(value.as_str()),
            query_string => query.url(field, &value),
        }
    }));
    admin_html::render_fragment("admin/filter.html", context! { title, choices })
}

pub(super) fn search_terms(mut text: &str) -> Vec<String> {
    let mut terms = Vec::new();
    loop {
        text = text.trim_start();
        if text.is_empty() {
            break;
        }
        let end = search_token_end(text);
        let term = &text[..end];
        let quote = term.as_bytes()[0] as char;
        if matches!(quote, '\'' | '"') && term.len() > 1 && term.ends_with(quote) {
            terms.push(
                term[1..term.len() - 1]
                    .replace(&format!("\\{quote}"), &quote.to_string())
                    .replace("\\\\", "\\"),
            );
        } else {
            terms.push(term.to_owned());
        }
        text = &text[end..];
    }
    terms
}

fn search_token_end(text: &str) -> usize {
    let mut characters = text.char_indices().peekable();
    let mut end = 0;
    let mut has_phrase = false;
    while let Some((index, character)) = characters.next() {
        if character.is_whitespace() {
            return index;
        }
        if matches!(character, '\'' | '"') {
            let mut closed = false;
            while let Some((_, next)) = characters.next() {
                if next == character {
                    closed = true;
                    break;
                }
                if next == '\\' {
                    characters.next();
                }
            }
            if !closed {
                if has_phrase {
                    return index;
                }
                return text.find(char::is_whitespace).unwrap_or(text.len());
            }
            has_phrase = true;
        }
        end = characters.peek().map_or(text.len(), |(index, _)| *index);
    }
    end
}

fn filter_audit(
    statement: &mut QueryBuilder<MySql>,
    query: &Query,
    content: Option<i32>,
    ids: &[i32],
) {
    statement.push(AUDIT_FROM).push(" WHERE TRUE");
    filter_ids(statement, ids);
    if let Some(value) = query.get("event_type__exact") {
        statement
            .push(" AND t.event_type = ")
            .push_bind(value.to_owned());
    }
    if let Some(content) = content {
        statement
            .push(" AND t.content_type_id = ")
            .push_bind(content);
    }
    for term in search_terms(query.get("q").unwrap_or_default()) {
        statement
            .push(" AND (LOCATE(")
            .push_bind(term.clone())
            .push(", t.object_repr) > 0 OR LOCATE(")
            .push_bind(term)
            .push(", t.note) > 0)");
    }
}

async fn content_types(db: &mut MySqlConnection) -> Result<Vec<ContentType>, sqlx::Error> {
    sqlx::query_as("SELECT id, app_label, model FROM django_content_type ORDER BY app_label, model")
        .fetch_all(db)
        .await
}

pub async fn list(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: Record,
    raw: Option<String>,
) -> Response {
    let model = kind.model();
    let path = model.url();
    let shell = match Shell::load(app, actor, headers, &path).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let query = Query::new(raw);
    if query
        .get("_to_field")
        .is_some_and(|name| !matches!(name, "" | "id"))
    {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }
    let popup = query.get("_popup").is_some();
    let filters: &[&str] = match kind {
        Record::Audit => &["event_type__exact", "content_type__id__exact"],
        Record::Manual => &[],
    };
    let Some(ids) = query.record_ids(filters) else {
        return access::found(format!("{path}?e=1"));
    };
    let result = async {
        let mut db = app.db.begin().await?;
        let columns = kind.columns();
        let order = sort_order(kind, &query);
        let sort = order_sql(&columns, &order);
        let mut filters = Vec::new();
        let mut active_filters = false;
        let mut clear = String::new();
        let mut search = "\n\n".to_owned();
        let page;
        let rows: Vec<(i32, [String; 4])> = match kind {
            Record::Audit => {
                let content = match query.get("content_type__id__exact").map(str::parse::<i32>).transpose() {
                    Ok(content) => content,
                    Err(_) => return Ok(access::found(format!("{path}?e=1"))),
                };
                let mut total = QueryBuilder::new("SELECT COUNT(*)");
                filter_audit(&mut total, &query, content, &ids);
                let count = total.build_query_scalar::<i64>().fetch_one(&mut *db).await?;
                let Some(window) = Page::new(&query, count) else {
                    return Ok(access::found(format!("{path}?e=1")));
                };
                page = window;
                let full: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM tab_auditevent")
                    .fetch_one(&mut *db).await?;
                let (limit, offset) = page.window();
                let mut statement = QueryBuilder::new(
                    "SELECT t.id, t.created_at, t.event_type, t.object_repr, u.username"
                );
                filter_audit(&mut statement, &query, content, &ids);
                statement.push(" ORDER BY ").push(&sort).push(" LIMIT ").push_bind(limit).push(" OFFSET ").push_bind(offset);
                let records = statement.build_query_as::<(i32, NaiveDateTime, String, String, Option<String>)>()
                    .fetch_all(&mut *db).await?;
                let entries = content_types(&mut db).await?;
                let mut params = query.params.clone();
                active_filters = params.remove("event_type__exact").is_some() | params.remove("content_type__id__exact").is_some();
                clear = Query::encode(params);
                let markup = (|| -> Result<_, minijinja::Error> {
                    filters.push(choice_filter(&query, "event_type__exact", "event type",
                        EVENTS.iter().map(|&(value, label)| (value.to_owned(), label.to_owned())))?);
                    if entries.len() > 1 {
                        filters.push(choice_filter(&query, "content_type__id__exact", "content type",
                            entries.into_iter().map(|(id, app, model)| (id.to_string(), content_label(&app, &model))))?);
                    }
                    admin_html::render_fragment("admin/search_form.html", context! {
                        cl => context! {
                            search_fields => true,
                            query => query.get("q").unwrap_or_default(),
                            result_count => count, full_result_count => full,
                            is_popup => popup,
                            show_full_result_count => true,
                            params => query.ordered_params(),
                        },
                        search_var => "q",
                        is_popup_var => "_popup",
                        show_result_count => count != full,
                    })
                })();
                let Ok(markup) = markup else { return Ok(unavailable()); };
                search = markup;
                records.into_iter().map(|(id, date, event, description, user)| (id, [
                    django_datetime(date), event_label(&event).into(), description,
                    user.unwrap_or_else(|| "-".into()),
                ])).collect()
            }
            Record::Manual => {
                let mut total = QueryBuilder::new("SELECT COUNT(*) FROM tab_manualjudgeassignment t WHERE TRUE");
                filter_ids(&mut total, &ids);
                let count = total.build_query_scalar::<i64>().fetch_one(&mut *db).await?;
                let Some(window) = Page::new(&query, count) else {
                    return Ok(access::found(format!("{path}?e=1")));
                };
                page = window;
                let (limit, offset) = page.window();
                let mut selection = QueryBuilder::new(format!(
                    "SELECT t.id, {ROUND_LABEL}, j.name, u.username, t.created_at {MANUAL_FROM} WHERE TRUE"
                ));
                filter_ids(&mut selection, &ids);
                selection.push(" ORDER BY ").push(&sort).push(" LIMIT ").push_bind(limit)
                    .push(" OFFSET ").push_bind(offset);
                selection.build_query_as::<(i32, String, String, Option<String>, NaiveDateTime)>()
                .fetch_all(&mut *db).await?
                .into_iter().map(|(id, round, judge, user, date)| (id, [
                    round, judge, user.unwrap_or_else(|| "-".into()), django_datetime(date),
                ])).collect()
            }
        };
        db.commit().await?;
        let suffix = escape(&query.suffix());
        let results: Vec<_> = rows.iter().map(|(id, cells)| {
            let opener = if popup { format!(r#" data-popup-opener="{id}""#) } else { String::new() };
            columns.iter().zip(cells).enumerate().map(|(index, (column, value))| {
                let extra = if column.nowrap { " nowrap" } else { "" };
                let cell = escape(value);
                let field = column.name;
                if index == 0 {
                    format!(r#"<th class="field-{field}{extra}"><a href="{path}{id}/change/{suffix}"{opener}>{cell}</a></th>"#)
                } else {
                    format!(r#"<td class="field-{field}{extra}">{cell}</td>"#)
                }
            }).collect::<Vec<_>>()
        }).collect();
        let opts = model.opts();
        let pager = page.context(&query);
        let title = if popup { format!("Select {}", model.singular) } else { format!("Select {} to view", model.singular) };
        Ok::<_, sqlx::Error>(shell.render("admin/change_list.html", context! {
            title, opts => &opts, is_popup => popup,
            cl => context! {
                opts => &opts,
                result_list => &rows,
                result_count => page.count,
                has_filters => !filters.is_empty(),
                has_active_filters => active_filters,
                clear_all_filters_qs => clear,
                filter_specs => filters,
            },
            app_url => "/admin/tab/", model_url => path, module_name => model.plural,
            actions_on_top => true,
            search_form => search,
            media => context! { js => admin_html::scripts() },
            result_headers => headings(&columns, &order, &query, 0),
            num_sorted_fields => order.len(),
            results,
            ..pager,
        }, headers))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

async fn record(
    db: &mut MySqlConnection,
    kind: Record,
    id: i32,
) -> Result<Option<(String, Vec<Value>)>, sqlx::Error> {
    let result = match kind {
        Record::Manual => {
            let row = sqlx::query_as::<_, ManualRow>(AssertSqlSafe(format!(
                "SELECT t.id, r.id, {ROUND_LABEL}, j.id, j.name, u.id, u.username, t.created_at
                 {MANUAL_FROM} WHERE t.id = ?"
            )))
            .bind(id)
            .fetch_optional(db)
            .await?;
            row.map(
                |(_, round_id, round, judge_id, judge, user_id, user, date)| {
                    (
                        format!("{judge} manually assigned to {round}"),
                        vec![
                            readonly(
                                "round",
                                "Round",
                                link("/admin/tab/round/", round_id, &round),
                            ),
                            readonly(
                                "judge",
                                "Judge",
                                link("/admin/tab/judge/", judge_id, &judge),
                            ),
                            readonly(
                                "created_by",
                                "Created by",
                                user_id.zip(user).map_or_else(
                                    || "-".into(),
                                    |(id, name)| link("/admin/auth/user/", id, &name),
                                ),
                            ),
                            readonly("created_at", "Created at", escape(&django_datetime(date))),
                        ],
                    )
                },
            )
        }
        Record::Audit => {
            let row = sqlx::query_as::<_, AuditRow>(AssertSqlSafe(format!(
                "SELECT t.id, t.object_id, t.event_type, ct.app_label, ct.model, t.object_repr,
                     u.id, u.username, t.created_at, CAST(t.changes AS CHAR), t.note {AUDIT_FROM}
                      JOIN django_content_type ct ON ct.id = t.content_type_id WHERE t.id = ?"
            )))
            .bind(id)
            .fetch_optional(db)
            .await?;
            row.map(
                |(
                    _,
                    target,
                    event,
                    application,
                    model,
                    original,
                    user_id,
                    user,
                    date,
                    changes,
                    note,
                )| {
                    let event_name = EVENTS
                        .iter()
                        .find(|&&(value, _)| value == event)
                        .map_or(event.as_str(), |&(_, label)| label);
                    let description = format!(
                        "{event_name} {original} by {}",
                        user.as_deref().unwrap_or("Unknown")
                    );
                    (
                        description,
                        vec![
                            readonly(
                                "content_type",
                                "Content type",
                                escape(&content_label(&application, &model)),
                            ),
                            readonly("object_id", "Object id", target.to_string()),
                            readonly("event_type", "Event type", escape(event_label(&event))),
                            readonly(
                                "user",
                                "User",
                                user_id.zip(user).map_or_else(
                                    || "-".into(),
                                    |(id, name)| link("/admin/auth/user/", id, &name),
                                ),
                            ),
                            readonly("created_at", "Created at", escape(&django_datetime(date))),
                            readonly("object_repr", "Object repr", linebreaks(&original)),
                            readonly("changes", "Changes", linebreaks(&changes)),
                            readonly("note", "Note", linebreaks(&note)),
                        ],
                    )
                },
            )
        }
    };
    Ok(result)
}

fn link(path: &str, id: i32, name: &str) -> String {
    format!(r#"<a href="{path}{id}/change/">{}</a>"#, escape(name))
}

pub(super) fn linebreaks(raw: &str) -> String {
    escape(raw)
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .replace('\n', "<br>")
}

pub(super) fn readonly(name: &str, label: &str, contents: String) -> Value {
    context! {
        has_visible_field => true,
        fields => [context! {
            is_readonly => true,
            field => context! { name },
            label_tag => format!("<label>{label}:</label>"),
            contents,
        }],
    }
}

pub async fn detail(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: Record,
    id: i32,
    raw: Option<String>,
    history: bool,
) -> Response {
    let model = kind.model();
    let model_url = model.url();
    let object_url = format!("{model_url}{id}/");
    let screen = if history { "history" } else { "change" };
    let shell = match Shell::load(app, actor, headers, &format!("{object_url}{screen}/")).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let query = Query::new(raw);
    let to_field = query.get("_to_field");
    if to_field.is_some_and(|name| !matches!(name, "" | "id")) {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }
    let popup = query.get("_popup").is_some();
    let result =
        async {
            let mut db = app.db.acquire().await?;
            let Some((description, fields)) = record(&mut db, kind, id).await? else {
                return Ok(access::found("/admin/"));
            };
            let preserved = query.get("_changelist_filters").unwrap_or_default();
            let close_query = url::form_urlencoded::Serializer::new(String::new())
                .extend_pairs(
                    url::form_urlencoded::parse(preserved.as_bytes())
                        .filter(|(_, value)| !value.is_empty()),
                )
                .finish();
            let close_suffix = if close_query.is_empty() {
                String::new()
            } else {
                format!("?{close_query}")
            };
            let base = context! {
                opts => model.opts(), app_url => "/admin/tab/", model_url, object_url,
                filter_suffix => admin_html::preserved_filters(preserved),
            form_url => admin_html::preserved_filters(preserved),
                close_suffix,
            };
            if history {
                let filter =
                    "FROM django_admin_log l JOIN django_content_type t ON l.content_type_id = t.id
                JOIN auth_user u ON u.id = l.user_id
                WHERE t.app_label = 'tab' AND t.model = ? AND l.object_id = ?";
                let object_id = id.to_string();
                let count: i64 =
                    sqlx::query_scalar(AssertSqlSafe(format!("SELECT COUNT(*) {filter}")))
                        .bind(model.name)
                        .bind(&object_id)
                        .fetch_one(&mut *db)
                        .await?;
                let count = u32::try_from(count).unwrap_or(u32::MAX);
                let pages = count.div_ceil(PER_PAGE).max(1);
                let page = query.requested().clamp(1, i64::from(pages)) as u32;
                let log = sqlx::query_as::<_, (NaiveDateTime, String, String, String)>(
                    AssertSqlSafe(format!(
                    "SELECT l.action_time, u.username, TRIM(CONCAT(u.first_name, ' ', u.last_name)),
                     l.change_message {filter} ORDER BY l.action_time, l.id LIMIT ? OFFSET ?"
                )),
                )
                .bind(model.name)
                .bind(object_id)
                .bind(PER_PAGE)
                .bind((page - 1) * PER_PAGE)
                .fetch_all(&mut *db)
                .await?;
                let action_list: Vec<_> = log
                    .into_iter()
                    .map(|(date, user, name, message)| {
                        context! {
                            action_time => django_datetime(date),
                            user => context! { get_username => user, get_full_name => name },
                            get_change_message => admin_html::admin_change_message(&message),
                        }
                    })
                    .collect();
                return Ok(shell.render(
                    "admin/object_history.html",
                    context! {
                        title => format!("Change history: {description}"), object => description,
                        module_name => admin_html::capitalized(model.plural),
                        action_list, count, page, pages,
                        pagination_required => pages > 1,
                        page_range => (1..=pages).collect::<Vec<_>>(), page_var => "p",
                        ..base,
                    },
                    headers,
                ));
            }
            Ok::<_, sqlx::Error>(shell.render("admin/change_form.html", context! {
            title => format!("View {}", model.singular),
            original => &description, subtitle => &description, change => true,
            is_popup => popup, is_popup_var => "_popup", to_field, to_field_var => "_to_field",
            adminform => [context! { classes => "", lines => fields }],
            has_view_permission => true, show_close => true,
            prepopulated_fields_json => "[]", media => admin_html::scripts(),
            ..base,
        }, headers))
        }
        .await;
    result.unwrap_or_else(|_| unavailable())
}
