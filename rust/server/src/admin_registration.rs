use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::{NaiveDateTime, TimeZone};
use chrono_tz::America::New_York;
use minijinja::{Value, context};
use sqlx::{MySql, QueryBuilder};

use crate::{
    App, access,
    admin_changelist::{Page, Query, default_list, filter_ids},
    admin_html::{self, Shell, django_datetime},
    admin_records::{Column, choice_filter, headings, order_sql, search_terms},
    legacy_html::escape,
    legacy_session::Actor,
    private, unavailable,
};

const REGISTRATION: &[Column] = &[
    Column::new("school", "School", "s.name", true),
    Column::new("email", "Email", "t.email", false),
    Column::new(
        "herokunator_code",
        "Herokunator code",
        "t.herokunator_code",
        false,
    ),
    Column::new("created_at", "Created at", "t.created_at", true),
];

const CONFIG: &[Column] = &[
    Column::new(
        "allow_new_registrations",
        "Allow new registrations",
        "t.allow_new_registrations",
        false,
    ),
    Column::new(
        "new_registrations_open_eastern",
        "New registrations open (Eastern)",
        "t.new_registrations_open_at",
        false,
    )
    .unsortable(),
    Column::new(
        "new_registrations_close_eastern",
        "New registrations close (Eastern)",
        "t.new_registrations_close_at",
        false,
    )
    .unsortable(),
    Column::new(
        "allow_registration_edits",
        "Allow registration edits",
        "t.allow_registration_edits",
        false,
    ),
    Column::new(
        "registration_edits_open_eastern",
        "Registration edits open (Eastern)",
        "t.registration_edits_open_at",
        false,
    )
    .unsortable(),
    Column::new(
        "registration_edits_close_eastern",
        "Registration edits close (Eastern)",
        "t.registration_edits_close_at",
        false,
    )
    .unsortable(),
    Column::new(
        "crash_housing_enabled",
        "Crash housing enabled",
        "t.crash_housing_enabled",
        false,
    ),
    Column::new("updated_at", "Updated at", "t.updated_at", true),
];

const CHANGES: &[Column] = &[
    Column::new("created_at", "Created at", "t.created_at", true),
    Column::new("action", "Action", "t.action", false),
    Column::new("school_name", "School name", "t.school_name", false),
    Column::new("email", "Email", "t.email", false),
    Column::new(
        "registration_code",
        "Registration code",
        "t.registration_code",
        false,
    ),
    Column::new("summary", "Summary", "t.summary", false),
];

const EMAILS: &[Column] = &[
    Column::new("sent_at", "Sent at", "t.sent_at", true),
    Column::new("registration", "Registration", "t.registration_id", true),
    Column::new("email", "Email", "t.email", false),
    Column::new("successful", "Successful", "t.successful", false),
];
const SUCCESS_FILTER: &str = "successful__exact";
const REGISTRATION_LABEL: &str = "CONCAT(s.name, ' — ', r.email)";

#[derive(Clone, Copy)]
pub(super) enum ListKind {
    Registration,
    Config,
    Changes,
    Emails,
}

impl ListKind {
    pub fn find(name: &str) -> Option<Self> {
        match name {
            "registration" => Some(Self::Registration),
            "registrationconfig" => Some(Self::Config),
            "registrationchangelog" => Some(Self::Changes),
            "registrationconfirmationemaillog" => Some(Self::Emails),
            _ => None,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Registration => "registration",
            Self::Config => "registrationconfig",
            Self::Changes => "registrationchangelog",
            Self::Emails => "registrationconfirmationemaillog",
        }
    }

    fn columns(self) -> &'static [Column] {
        match self {
            Self::Registration => REGISTRATION,
            Self::Config => CONFIG,
            Self::Changes => CHANGES,
            Self::Emails => EMAILS,
        }
    }

    pub fn ids(self, query: &Query) -> Option<Vec<i32>> {
        if matches!(self, Self::Emails) {
            if query
                .get(SUCCESS_FILTER)
                .is_some_and(|value| !matches!(value, "0" | "1"))
            {
                return None;
            }
            query.record_ids(&[SUCCESS_FILTER])
        } else {
            query.record_ids(&[])
        }
    }

    pub fn filter(self, sql: &mut QueryBuilder<MySql>, query: &Query) {
        let fields: &[&str] = match self {
            Self::Registration => &["s.name", "t.email", "t.herokunator_code"],
            Self::Changes => &[
                "t.school_name",
                "t.email",
                "t.registration_code",
                "t.summary",
            ],
            Self::Emails => &["s.name", "t.email", "t.error_message"],
            Self::Config => return,
        };
        if matches!(self, Self::Emails)
            && let Some(successful) = query.get(SUCCESS_FILTER)
        {
            sql.push(" AND t.successful = ")
                .push_bind(successful == "1");
        }
        for term in search_terms(query.get("q").unwrap_or_default()) {
            let pattern = format!(
                "%{}%",
                term.replace('!', "!!")
                    .replace('%', "!%")
                    .replace('_', "!_")
            );
            sql.push(" AND (");
            let mut fields_query = sql.separated(" OR ");
            for field in fields {
                fields_query
                    .push(*field)
                    .push_unseparated(" LIKE ")
                    .push_bind_unseparated(pattern.clone())
                    .push_unseparated(" ESCAPE '!'");
            }
            sql.push(")");
        }
    }

    fn order(self, query: &Query, offset: usize) -> Vec<(usize, bool)> {
        let columns = self.columns();
        let Some(order) = query.get("o") else {
            return if matches!(self, Self::Changes | Self::Emails) {
                vec![(0, false)]
            } else {
                Vec::new()
            };
        };
        order
            .split('.')
            .filter_map(|part| {
                let index = part
                    .trim_start_matches('-')
                    .parse::<usize>()
                    .ok()?
                    .checked_sub(offset)?;
                columns
                    .get(index)?
                    .sortable
                    .then_some((index, !part.starts_with('-')))
            })
            .collect()
    }

    fn row_columns(self) -> String {
        self.columns()
            .iter()
            .map(|column| {
                let sql = if matches!(self, Self::Emails) && column.name == "registration" {
                    REGISTRATION_LABEL
                } else {
                    column.sql
                };
                format!("CAST({sql} AS CHAR)")
            })
            .collect::<Vec<_>>()
            .join(", ")
    }

    fn cell(self, column: &Column, value: Option<&str>) -> String {
        let Some(value) = value else {
            return "-".into();
        };
        match column.name {
            "created_at" | "updated_at" | "sent_at" => {
                parse_db_date(value).map_or_else(|| "-".into(), django_datetime)
            }
            "successful"
            | "allow_new_registrations"
            | "allow_registration_edits"
            | "crash_housing_enabled" => boolean_icon(value == "1"),
            "action" if matches!(self, Self::Changes) => match value {
                "created" | "updated" | "deleted" => admin_html::capitalized(value),
                _ => "-".into(),
            },
            name if name.ends_with("_eastern") => parse_db_date(value).map_or_else(
                || "-".into(),
                |date| {
                    New_York
                        .from_utc_datetime(&date)
                        .format("%Y-%m-%d %H:%M %Z")
                        .to_string()
                },
            ),
            _ => escape(value),
        }
    }
}

pub(super) fn boolean_icon(checked: bool) -> String {
    let (icon, alt) = if checked {
        ("yes", "True")
    } else {
        ("no", "False")
    };
    format!(r#"<img src="/static/admin/img/icon-{icon}.svg" alt="{alt}">"#)
}

pub(super) fn parse_db_date(value: &str) -> Option<NaiveDateTime> {
    NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S%.f").ok()
}

pub async fn list(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: ListKind,
    query: Query,
) -> Response {
    let config = default_list(kind.name()).expect("registration changelist");
    let model = &config.model;
    let path = model.url();
    let shell = match Shell::load(app, actor, headers, &path).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    if query
        .get("_to_field")
        .is_some_and(|name| !matches!(name, "" | "id"))
    {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }
    let Some(ids) = kind.ids(&query) else {
        return access::found(format!("{path}?e=1"));
    };
    let popup = query.get("_popup").is_some();
    let offset = usize::from(!popup);
    let order = kind.order(&query, offset);
    let columns = kind.columns();
    let result = async {
        let mut db = app.db.begin().await?;
        let mut all = QueryBuilder::new(format!("SELECT COUNT(*) FROM {}", config.table()));
        let full: i64 = all.build_query_scalar().fetch_one(&mut *db).await?;
        let from = format!(" FROM {} WHERE TRUE", config.source);
        let mut total = QueryBuilder::new(format!("SELECT COUNT(*){from}"));
        filter_ids(&mut total, &ids);
        kind.filter(&mut total, &query);
        let count = total.build_query_scalar::<i64>().fetch_one(&mut *db).await?;
        let Some(page) = Page::new(&query, count) else {
            return Ok(access::found(format!("{path}?e=1")));
        };
        let (limit, start) = page.window();
        let mut selection = QueryBuilder::new(format!(
            "SELECT t.id, CAST(JSON_ARRAY({}) AS CHAR){from}", kind.row_columns()
        ));
        filter_ids(&mut selection, &ids);
        kind.filter(&mut selection, &query);
        selection.push(" ORDER BY ").push(order_sql(columns, &order))
            .push(" LIMIT ").push_bind(limit).push(" OFFSET ").push_bind(start);
        let rows = selection.build_query_as::<(i32, String)>().fetch_all(&mut *db).await?;
        db.commit().await?;
        let suffix = escape(&query.suffix());
        let mut results = Vec::with_capacity(rows.len());
        for (id, row) in &rows {
            let values: Vec<Option<String>> = serde_json::from_str(row)
                .map_err(|error| sqlx::Error::Decode(error.into()))?;
            let opener = if popup { format!(r#" data-popup-opener="{id}""#) } else { String::new() };
            let mut cells = Vec::new();
            if !popup {
                cells.push(format!(r#"<td class="action-checkbox"><input type="checkbox" name="_selected_action" value="{id}" class="action-select"></td>"#));
            }
            cells.extend(columns.iter().zip(&values).enumerate().map(|(index, (column, value))| {
                let name = column.name;
                let nowrap = if column.nowrap { " nowrap" } else { "" };
                let value = kind.cell(column, value.as_deref());
                if index == 0 {
                    format!(r#"<th class="field-{name}{nowrap}"><a href="{path}{id}/change/{suffix}"{opener}>{value}</a></th>"#)
                } else {
                    format!(r#"<td class="field-{name}{nowrap}">{value}</td>"#)
                }
            }));
            results.push(cells);
        }
        let search_form = if matches!(kind, ListKind::Config) {
            "\n\n".into()
        } else {
            let Ok(markup) = admin_html::render_fragment("admin/search_form.html", context! {
                cl => context! {
                    search_fields => true,
                    query => query.get("q").unwrap_or_default().trim(),
                    result_count => count, full_result_count => full,
                    is_popup => popup, show_full_result_count => true, params => query.ordered_params(),
                },
                search_var => "q", is_popup_var => "_popup",
                show_result_count => count != full,
            }) else { return Ok(unavailable()); };
            markup
        };
        let mut filters = Vec::new();
        let mut filter_params = query.params.clone();
        let active_filter = filter_params.remove(SUCCESS_FILTER).is_some();
        if matches!(kind, ListKind::Emails) {
            let options = [("1".into(), "Yes".into()), ("0".into(), "No".into())];
            let Ok(filter) = choice_filter(&query, SUCCESS_FILTER, "successful", options) else {
                return Ok(unavailable());
            };
            filters.push(filter);
        }
        let opts = model.opts();
        let pager = page.context(&query);
        let mut result_headers: Vec<Value> = Vec::new();
        if !popup {
            result_headers.push(context! {
                class_attrib => r#" class="action-checkbox-column""#,
                text => r#"<input type="checkbox" id="action-toggle">"#,
            });
        }
        result_headers.extend(headings(columns, &order, &query, offset));
        let action_widget = format!(r#"<select name="action" required>
  <option value="" selected>---------</option>

  <option value="delete_selected">Delete selected {}</option>

</select>"#, model.plural);
        let title = if popup { format!("Select {}", model.singular) } else { format!("Select {} to change", model.singular) };
        let can_add = match kind {
            ListKind::Registration => true,
            ListKind::Config => full == 0,
            ListKind::Changes | ListKind::Emails => false,
        };
        Ok::<_, sqlx::Error>(shell.render("admin/change_list.html", context! {
            title, opts => &opts, is_popup => popup,
            cl => context! {
                opts => &opts, show_admin_actions => full > 0,
                result_count => count, result_list => &rows,
                has_filters => !filters.is_empty(), filter_specs => filters,
                has_active_filters => active_filter, clear_all_filters_qs => Query::encode(filter_params),
            },
            app_url => "/admin/registration/", model_url => &path,
            filter_suffix => query.add_suffix(),
            module_name => model.plural, has_add_permission => can_add,
            actions_on_top => true, action_form => !popup,
            action_fields => [
                context! { label => "Action:", widget => action_widget },
                context! { widget => r#"<input type="hidden" name="select_across" value="0" class="select-across">"# },
            ],
            actions_selection_counter => true,
            selection_note => format!("0 of {} selected", rows.len()),
            selection_note_all => format!("All {count} selected"),
            media => context! { js => admin_html::scripts() },
            search_form, result_headers, num_sorted_fields => order.len(), results,
            ..pager,
        }, headers))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

pub(super) async fn can_add(
    db: &mut sqlx::MySqlConnection,
    model: &str,
) -> Result<bool, sqlx::Error> {
    match model {
        "registrationconfig" => {
            sqlx::query_scalar("SELECT NOT EXISTS(SELECT 1 FROM registration_registrationconfig)")
                .fetch_one(db)
                .await
        }
        "registrationchangelog" | "registrationconfirmationemaillog" => Ok(false),
        _ => Ok(true),
    }
}

pub(super) async fn readonly_fields(
    db: &mut sqlx::MySqlConnection,
    model: &str,
    id: Option<i32>,
) -> Result<Vec<Value>, sqlx::Error> {
    use crate::admin_records::{linebreaks, readonly};
    use sqlx::Row;

    let Some(id) = id else {
        return Ok(Vec::new());
    };
    let query = match model {
        "registrationchangelog" =>
            "SELECT t.registration_id, CONCAT(s.name, ' — ', r.email) AS parent,
                t.registration_code, t.school_name, t.email, t.action, t.summary,
                CAST(t.changes AS CHAR) AS changes, CAST(t.snapshot AS CHAR) AS snapshot, t.created_at
             FROM registration_registrationchangelog t
             LEFT JOIN registration_registration r ON t.registration_id = r.id
             LEFT JOIN tab_school s ON r.school_id = s.id WHERE t.id = ?",
        "registrationconfirmationemaillog" =>
            "SELECT t.registration_id, CONCAT(s.name, ' — ', r.email) AS parent,
                t.email, t.successful, t.error_message, t.sent_at
             FROM registration_registrationconfirmationemaillog t
             JOIN registration_registration r ON t.registration_id = r.id
             JOIN tab_school s ON r.school_id = s.id WHERE t.id = ?",
        _ => return Ok(Vec::new()),
    };
    let row = sqlx::query(query).bind(id).fetch_one(db).await?;
    let parent_id: Option<i32> = row.try_get("registration_id")?;
    let parent: Option<String> = row.try_get("parent")?;
    let registration = parent_id.zip(parent).map_or_else(
        || "-".into(),
        |(id, label)| {
            format!(
                r#"<a href="/admin/registration/registration/{id}/change/">{}</a>"#,
                escape(&label)
            )
        },
    );
    let text = |name: &str, label| {
        row.try_get::<String, _>(name)
            .map(|value| readonly(name, label, linebreaks(&value)))
    };
    let date = |name: &str, label| {
        row.try_get::<NaiveDateTime, _>(name)
            .map(|value| readonly(name, label, django_datetime(value)))
    };
    let mut fields = vec![readonly("registration", "Registration", registration)];
    if model == "registrationchangelog" {
        let action: String = row.try_get("action")?;
        fields.extend([
            text("registration_code", "Registration code")?,
            text("school_name", "School name")?,
            text("email", "Email")?,
            readonly(
                "action",
                "Action",
                ListKind::Changes.cell(&CHANGES[1], Some(&action)),
            ),
            text("summary", "Summary")?,
            text("changes", "Changes")?,
            text("snapshot", "Snapshot")?,
            date("created_at", "Created at")?,
        ]);
    } else {
        fields.extend([
            text("email", "Email")?,
            readonly(
                "successful",
                "Successful",
                boolean_icon(row.try_get("successful")?),
            ),
            text("error_message", "Error message")?,
            date("sent_at", "Sent at")?,
        ]);
    }
    Ok(fields)
}
