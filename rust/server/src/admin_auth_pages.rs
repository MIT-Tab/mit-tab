use std::collections::{BTreeMap, BTreeSet};

use axum::{http::HeaderMap, response::Response};
use chrono::NaiveDateTime;
use minijinja::{Value, context};
use sqlx::{AssertSqlSafe, MySqlConnection, QueryBuilder};

use crate::{
    App, access,
    admin_changelist::{Model, Page, Query, filter_ids},
    admin_html::{self, Shell},
    admin_records::{Column, choice_filter, content_label, headings, order_sql, search_terms},
    legacy_html::escape,
    legacy_session::Actor,
    registration_form::Fields,
};

pub const USER: Model = Model {
    name: "user",
    singular: "user",
    plural: "users",
};
pub const GROUP: Model = Model {
    name: "group",
    singular: "group",
    plural: "groups",
};

pub struct AuthPage<'a> {
    pub app: &'a App,
    pub actor: Actor,
    pub headers: &'a HeaderMap,
    pub path: &'a str,
    pub query: Query,
    pub user: bool,
}

impl AuthPage<'_> {
    pub fn model(&self) -> &Model {
        if self.user { &USER } else { &GROUP }
    }

    pub fn table(&self) -> &'static str {
        if self.user { "auth_user" } else { "auth_group" }
    }

    pub fn name_column(&self) -> &'static str {
        if self.user { "username" } else { "name" }
    }

    pub fn popup(&self, post: Option<&Fields>) -> bool {
        self.query.get("_popup").is_some()
            || post.is_some_and(|post| !post.all("_popup").is_empty())
    }

    pub async fn render(&self, template: &str, page: Value) -> Response {
        let shell = match Shell::load(self.app, self.actor, self.headers, self.path).await {
            Ok(shell) => shell,
            Err(response) => return *response,
        };
        let model = self.model();
        shell.render(
            template,
            context! {
                opts => model.opts(), model_url => model.url(), app_url => "/admin/auth/", ..page,
            },
            self.headers,
        )
    }
}

pub type Errors = BTreeMap<&'static str, String>;

pub type UserRow = (
    i32,
    String,
    String,
    String,
    String,
    bool,
    bool,
    bool,
    String,
    Option<NaiveDateTime>,
    NaiveDateTime,
);

pub async fn load_user(db: &mut MySqlConnection, id: i32) -> Result<Option<UserRow>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, username, first_name, last_name, email, is_staff, is_active,
         is_superuser, password, last_login, date_joined FROM auth_user WHERE id = ?",
    )
    .bind(id)
    .fetch_optional(db)
    .await
}

const USERNAME_HELP: &str =
    "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.";
const PASSWORD_HELP: &str = "Enter the same password as before, for verification.";
const SELECT_HELP: &str = "Hold down “Control”, or “Command” on a Mac, to select more than one.";

pub async fn editor(
    page: &AuthPage<'_>,
    id: Option<i32>,
    password: bool,
    submitted: Option<&Fields>,
    errors: &Errors,
) -> Result<Response, sqlx::Error> {
    let model = page.model();
    let mut db = page.app.db.acquire().await?;
    let mut original = None;
    let mut sets = Vec::new();
    if page.user {
        if let Some(id) = id {
            let Some(user) = load_user(&mut db, id).await? else {
                return Ok(access::found("/admin/"));
            };
            original = Some(user.1.clone());
            if password {
                drop(db);
                let password1 = password_field("password1", "Password", true, None, errors, true);
                let password2 = password_field(
                    "password2",
                    "Password (again)",
                    false,
                    Some(PASSWORD_HELP),
                    errors,
                    true,
                );
                return Ok(page
                    .render(
                        "admin/auth/user/change_password.html",
                        context! {
                            title => format!("Change password: {}", escape(&user.1)),
                            object_url => format!("{}{id}/", model.url()),
                            original, is_popup => page.popup(submitted), is_popup_var => "_popup",
                            form => context! { password1, password2, errors },
                        },
                    )
                    .await);
            }
            let (
                _,
                name,
                first,
                last,
                email,
                staff,
                active,
                elevated,
                hash,
                last_login,
                date_joined,
            ) = user;
            let widgets = Widgets { submitted, errors };
            let username = widgets.username(&name, false);
            let help = format!(
                "Raw passwords are not stored, so there is no way to see this user’s password, but you can change the password using <a href=\"../../{id}/password/\">this form</a>."
            );
            let hash_display = widget(
                "password",
                "<label>Password:</label>".into(),
                hash_widget(&hash),
                Some(&help),
                None,
                false,
                None,
            );
            sets.push(fieldset(None, "", vec![username, hash_display]));
            sets.push(fieldset(
                Some("Personal info"),
                "",
                vec![
                    widgets.text("first_name", "First name", "text", &first, 150, false),
                    widgets.text("last_name", "Last name", "text", &last, 150, false),
                    widgets.text("email", "Email address", "email", &email, 254, false),
                ],
            ));
            let groups = sqlx::query_as::<_, (i32, String, bool)>(
                "SELECT g.id, g.name, EXISTS(SELECT 1 FROM auth_user_groups m WHERE m.user_id = ? AND m.group_id = g.id)
                 FROM auth_group g ORDER BY g.name, g.id",
            ).bind(id).fetch_all(&mut *db).await?;
            let permissions = permissions(&mut db, page.user, Some(id)).await?;
            sets.push(fieldset(Some("Permissions"), "", vec![
                widgets.checkbox("is_active", "Active", active, "Designates whether this user should be treated as active. Unselect this instead of deleting accounts."),
                widgets.checkbox("is_staff", "Staff status", staff, "Designates whether the user can log into this admin site."),
                widgets.checkbox("is_superuser", "Superuser status", elevated, "Designates that this user has all permissions without explicitly assigning them."),
                widgets.multi("groups", "Groups", "group", groups,
                    format!("The groups this user belongs to. A user will get all permissions granted to each of their groups. {SELECT_HELP}"), Some("/admin/auth/group/add/"))?,
                widgets.multi("user_permissions", "User permissions", "permission", permissions,
                    format!("Specific permissions for this user. {SELECT_HELP}"), None)?,
            ]));
            sets.push(fieldset(
                Some("Important dates"),
                "",
                vec![
                    widgets.date_time("last_login", "Last login", last_login, false),
                    widgets.date_time("date_joined", "Date joined", Some(date_joined), true),
                ],
            ));
        } else {
            let widgets = Widgets { submitted, errors };
            let username = widgets.username(page.query.get("username").unwrap_or_default(), true);
            sets.push(fieldset(
                None,
                "wide",
                vec![
                    username,
                    password_field("password1", "Password", false, None, errors, false),
                    password_field(
                        "password2",
                        "Password confirmation",
                        false,
                        Some(PASSWORD_HELP),
                        errors,
                        false,
                    ),
                ],
            ));
        }
    } else {
        if let Some(id) = id {
            original = sqlx::query_scalar("SELECT name FROM auth_group WHERE id = ?")
                .bind(id)
                .fetch_optional(&mut *db)
                .await?;
            if original.is_none() {
                return Ok(access::found("/admin/"));
            }
        }
        let permissions = permissions(&mut db, false, id).await?;
        let widgets = Widgets { submitted, errors };
        sets.push(fieldset(
            None,
            "",
            vec![
                widgets.text(
                    "name",
                    "Name",
                    "text",
                    original
                        .as_deref()
                        .or_else(|| page.query.get("name"))
                        .unwrap_or_default(),
                    150,
                    true,
                ),
                widgets.multi(
                    "permissions",
                    "Permissions",
                    "permission",
                    permissions,
                    SELECT_HELP.into(),
                    None,
                )?,
            ],
        ));
    }
    drop(db);
    let add = id.is_none();
    let popup = page.popup(submitted);
    let template = if add && page.user {
        "admin/auth/user/add_form.html"
    } else {
        "admin/change_form.html"
    };
    let media = if page.user && add {
        admin_html::scripts()
    } else if page.user {
        admin_html::account_editor_scripts()
    } else {
        admin_html::filtered_select_scripts()
    };
    let suffix =
        admin_html::preserved_filters(page.query.get("_changelist_filters").unwrap_or_default());
    let non_field_errors = errors
        .get("__all__")
        .map_or_else(String::new, |message| error_list(message, true));
    Ok(page.render(template, context! {
        title => format!("{} {}", if add { "Add" } else { "Change" }, model.singular),
        subtitle => &original, original, add, change => !add, is_popup => popup,
        is_popup_var => "_popup", to_field => page.query.get("_to_field"), to_field_var => "_to_field",
        form_url => &suffix, filter_suffix => &suffix, object_url => id.map(|id| format!("{}{id}/", model.url())),
        adminform => sets, errors => errors.keys().collect::<Vec<_>>(), non_field_errors,
        has_view_permission => true, can_change => true,
        show_delete_link => !add && !popup, show_save => true,
        show_save_and_add_another => !popup, show_save_and_continue => !popup,
        media, prepopulated_fields_json => "[]",
    }).await)
}

fn fieldset(name: Option<&str>, classes: &str, lines: Vec<Value>) -> Value {
    context! { name, classes, lines }
}

fn error_list(message: &str, nonfield: bool) -> String {
    let class = if nonfield {
        "errorlist nonfield"
    } else {
        "errorlist"
    };
    format!("<ul class=\"{class}\"><li>{}</li></ul>", escape(message))
}

fn widget(
    name: &str,
    label_tag: String,
    markup: String,
    help_text: Option<&str>,
    id_for_label: Option<String>,
    is_checkbox: bool,
    error: Option<&str>,
) -> Value {
    let errors = error.map_or_else(String::new, |message| error_list(message, false));
    context! {
        has_visible_field => true, errors,
        fields => [context! {
            field => context! { name, help_text, id_for_label },
            label_tag, widget => markup, is_checkbox,
        }],
    }
}

fn label(name: &str, description: &str, required: bool) -> String {
    let class = if required { " class=\"required\"" } else { "" };
    format!("<label{class} for=\"id_{name}\">{description}:</label>")
}

struct Widgets<'a> {
    submitted: Option<&'a Fields>,
    errors: &'a Errors,
}

impl Widgets<'_> {
    fn value<'a>(&'a self, name: &str, initial: &'a str) -> &'a str {
        self.submitted.map_or(initial, |post| {
            post.all(name)
                .last()
                .map(String::as_str)
                .unwrap_or_default()
        })
    }

    fn error(&self, name: &str) -> Option<&str> {
        self.errors.get(name).map(String::as_str)
    }

    fn text(
        &self,
        name: &str,
        description: &str,
        input: &str,
        initial: &str,
        max: usize,
        required: bool,
    ) -> Value {
        let value = value_attr(self.value(name, initial));
        let required_attr = if required { " required" } else { "" };
        widget(
            name,
            label(name, description, required),
            format!(
                "<input type=\"{input}\" name=\"{name}\"{value} class=\"vTextField\" maxlength=\"{max}\"{required_attr} id=\"id_{name}\">"
            ),
            None,
            None,
            false,
            self.error(name),
        )
    }

    fn username(&self, initial: &str, add: bool) -> Value {
        let value = value_attr(self.value("username", initial));
        let autofocus = if add { " autofocus" } else { "" };
        widget(
            "username",
            label("username", "Username", true),
            format!(
                "<input type=\"text\" name=\"username\"{value} class=\"vTextField\" maxlength=\"150\" autocapitalize=\"none\" autocomplete=\"username\"{autofocus} required id=\"id_username\">"
            ),
            Some(USERNAME_HELP),
            Some("id_username".into()),
            false,
            self.error("username"),
        )
    }

    fn checkbox(&self, name: &str, description: &str, initial: bool, help: &str) -> Value {
        let on = self.submitted.map_or(initial, |post| post.flag(name));
        let checked = if on { " checked" } else { "" };
        widget(
            name,
            format!("<label class=\"vCheckboxLabel\" for=\"id_{name}\">{description}</label>"),
            format!("<input type=\"checkbox\" name=\"{name}\" id=\"id_{name}\"{checked}>"),
            Some(help),
            Some(format!("id_{name}")),
            true,
            self.error(name),
        )
    }

    fn multi(
        &self,
        name: &str,
        description: &str,
        model: &str,
        rows: Vec<(i32, String, bool)>,
        help: String,
        add: Option<&str>,
    ) -> Result<Value, sqlx::Error> {
        let initial: Vec<_> = rows
            .iter()
            .filter(|(_, _, selected)| *selected)
            .map(|(id, _, _)| id.to_string())
            .collect();
        let selected = self
            .submitted
            .map_or(initial.as_slice(), |post| post.all(name));
        let options: Vec<_> = rows.into_iter().map(|(id, text, _)| (id, text)).collect();
        let select = admin_html::render_fragment("admin/model_select.html", context! {
            name, options, selected, multiple => true, filter_label => description.to_lowercase(),
        }).map_err(template_error)?;
        let markup = admin_html::render_fragment("admin/widgets/related_widget_wrapper.html", context! {
            name, model, rendered_widget => Value::from_safe_string(select.trim_end_matches('\n').into()),
            can_add_related => add.is_some(), add_related_url => add, url_params => "_to_field=id&_popup=1",
        }).map_err(template_error)?;
        Ok(widget(
            name,
            label(name, description, false),
            markup.trim().into(),
            Some(&help),
            Some(format!("id_{name}")),
            false,
            self.error(name),
        ))
    }

    fn date_time(
        &self,
        name: &str,
        description: &str,
        current: Option<NaiveDateTime>,
        required: bool,
    ) -> Value {
        let original_date = current
            .map(|value| value.format("%Y-%m-%d").to_string())
            .unwrap_or_default();
        let original_time = current
            .map(|value| value.format("%H:%M:%S").to_string())
            .unwrap_or_default();
        let date = value_attr(self.value(&format!("{name}_0"), &original_date));
        let time = value_attr(self.value(&format!("{name}_1"), &original_time));
        let required_attr = if required { " required" } else { "" };
        let mut markup = format!(
            "<p class=\"datetime\">\n  Date: <input type=\"text\" name=\"{name}_0\"{date} class=\"vDateField\" size=\"10\"{required_attr} id=\"id_{name}_0\">\n\n<br>\n  Time: <input type=\"text\" name=\"{name}_1\"{time} class=\"vTimeField\" size=\"8\"{required_attr} id=\"id_{name}_1\">\n\n\n</p>"
        );
        if required {
            markup.push_str(&format!("<input type=\"hidden\" name=\"initial-{name}_0\"{} id=\"initial-id_{name}_0\"><input type=\"hidden\" name=\"initial-{name}_1\"{} id=\"initial-id_{name}_1\">",
                value_attr(self.value(&format!("initial-{name}_0"), &original_date)),
                value_attr(self.value(&format!("initial-{name}_1"), &original_time))));
        }
        let label = format!(
            "<label{}>{description}:</label>",
            if required { " class=\"required\"" } else { "" }
        );
        widget(name, label, markup, None, None, false, self.error(name))
    }
}

fn password_field(
    name: &str,
    description: &str,
    autofocus: bool,
    help: Option<&str>,
    errors: &Errors,
    own_page: bool,
) -> Value {
    let autofocus = if autofocus { " autofocus" } else { "" };
    let markup = format!(
        "<input type=\"password\" name=\"{name}\" autocomplete=\"new-password\"{autofocus} required id=\"id_{name}\">"
    );
    if own_page {
        return context! {
            widget => markup, label_tag => format!("<label for=\"id_{name}\" class=\"required\">{description}:</label>"),
            help_text => help, id_for_label => format!("id_{name}"),
            errors => errors.get(name).map_or_else(String::new, |message| error_list(message, false)),
        };
    }
    widget(
        name,
        label(name, description, true),
        markup,
        help,
        Some(format!("id_{name}")),
        false,
        errors.get(name).map(String::as_str),
    )
}

fn value_attr(raw: &str) -> String {
    if raw.is_empty() {
        String::new()
    } else {
        format!(" value=\"{}\"", escape(raw))
    }
}

fn hash_widget(encoded: &str) -> String {
    let values: Vec<_> = encoded.split('$').collect();
    let mask = |value: &str| -> String {
        let visible = value.chars().take(6).collect::<String>();
        format!(
            "{}{}",
            escape(&visible),
            "*".repeat(value.chars().count().saturating_sub(6))
        )
    };
    let body = if encoded.is_empty() || encoded.starts_with('!') {
        "<strong>No password set.</strong>\n".into()
    } else if let [
        algorithm @ ("pbkdf2_sha256" | "pbkdf2_sha1"),
        iterations,
        salt,
        hash,
    ] = values.as_slice()
        && iterations.parse::<u32>().is_ok()
        && salt.len() > 6
        && hash.len() > 6
    {
        format!(
            "<strong>algorithm</strong>: <bdi>{algorithm}</bdi>\n\n<strong>iterations</strong>: <bdi>{iterations}</bdi>\n\n<strong>salt</strong>: <bdi>{}</bdi>\n\n<strong>hash</strong>: <bdi>{}</bdi>\n",
            mask(salt),
            mask(hash)
        )
    } else {
        "<strong>Invalid password format or unknown hashing algorithm.</strong>\n".into()
    };
    format!("<div disabled id=\"id_password\">\n\n{body}\n</div>")
}

async fn permissions(
    db: &mut MySqlConnection,
    user: bool,
    id: Option<i32>,
) -> Result<Vec<(i32, String, bool)>, sqlx::Error> {
    let (table, owner) = if user {
        ("auth_user_user_permissions", "user_id")
    } else {
        ("auth_group_permissions", "group_id")
    };
    let rows: Vec<(i32, String, String, String, bool)> = sqlx::query_as(AssertSqlSafe(format!(
        "SELECT p.id, c.app_label, c.model, p.name, EXISTS(
            SELECT 1 FROM {table} m WHERE m.{owner} = ? AND m.permission_id = p.id
         ) FROM auth_permission p JOIN django_content_type c ON c.id = p.content_type_id
         ORDER BY c.app_label, c.model, p.codename, p.id"
    )))
    .bind(id)
    .fetch_all(db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(id, app, model, label, chosen)| {
            (
                id,
                format!("{} | {label}", content_label(&app, &model)),
                chosen,
            )
        })
        .collect())
}

fn template_error(error: minijinja::Error) -> sqlx::Error {
    sqlx::Error::Protocol(format!(
        "Could not render authentication admin: {}",
        error.kind()
    ))
}

const USER_COLUMNS: &[Column] = &[
    Column::new("username", "Username", "t.username", false),
    Column::new("email", "Email address", "t.email", false),
    Column::new("first_name", "First name", "t.first_name", false),
    Column::new("last_name", "Last name", "t.last_name", false),
    Column::new("is_staff", "Staff status", "t.is_staff", false),
];
const FLAGS: &[(&str, &str, &str)] = &[
    ("is_staff__exact", "is_staff", "staff status"),
    ("is_superuser__exact", "is_superuser", "superuser status"),
    ("is_active__exact", "is_active", "active"),
];
const USER_FILTERS: &[&str] = &[
    "is_staff__exact",
    "is_superuser__exact",
    "is_active__exact",
    "groups__id__exact",
    "groups__isnull",
];

pub struct Selection {
    ids: Vec<i32>,
    group: Option<i32>,
    no_group: bool,
}

impl Selection {
    pub fn read(page: &AuthPage<'_>) -> Option<Self> {
        let query = &page.query;
        let ids = query.record_ids(if page.user { USER_FILTERS } else { &[] })?;
        let group = query
            .get("groups__id__exact")
            .map(str::parse::<i32>)
            .transpose()
            .ok()?;
        if FLAGS.iter().any(|(key, _, _)| {
            query
                .get(key)
                .is_some_and(|value| !matches!(value, "1" | "0"))
        }) {
            return None;
        }
        let no_group = query
            .get("groups__isnull")
            .is_some_and(|value| value != "False");
        Some(Self {
            ids,
            group,
            no_group,
        })
    }

    pub fn constrain(&self, page: &AuthPage<'_>, statement: &mut QueryBuilder<sqlx::MySql>) {
        statement
            .push(" FROM ")
            .push(page.table())
            .push(" t WHERE TRUE");
        filter_ids(statement, &self.ids);
        for &(key, column, _) in FLAGS {
            if let Some(flag) = page.query.get(key) {
                statement
                    .push(" AND t.")
                    .push(column)
                    .push(" = ")
                    .push_bind(flag == "1");
            }
        }
        if let Some(group) = self.group {
            statement.push(" AND EXISTS(SELECT 1 FROM auth_user_groups m WHERE m.user_id = t.id AND m.group_id = ")
                .push_bind(group).push(")");
        }
        if self.no_group {
            statement
                .push(" AND NOT EXISTS(SELECT 1 FROM auth_user_groups m WHERE m.user_id = t.id)");
        }
        let column = page.name_column();
        for term in search_terms(page.query.get("q").unwrap_or_default()) {
            statement
                .push(" AND (LOCATE(")
                .push_bind(term.clone())
                .push(", t.")
                .push(column)
                .push(") > 0");
            if page.user {
                for column in ["first_name", "last_name", "email"] {
                    statement
                        .push(" OR LOCATE(")
                        .push_bind(term.clone())
                        .push(", t.")
                        .push(column)
                        .push(") > 0");
                }
            }
            statement.push(")");
        }
    }
}

pub async fn listing(page: &AuthPage<'_>) -> Result<Response, sqlx::Error> {
    let model = page.model();
    let path = model.url();
    let Some(selection) = Selection::read(page) else {
        return Ok(access::found(format!("{path}?e=1")));
    };
    let query = &page.query;
    let popup = page.popup(None);
    let header_offset = usize::from(!popup);
    let mut db = page.app.db.begin().await?;
    let mut total = QueryBuilder::new("SELECT COUNT(*)");
    selection.constrain(page, &mut total);
    let count: i64 = total.build_query_scalar().fetch_one(&mut *db).await?;
    let full: i64 = QueryBuilder::new(format!("SELECT COUNT(*) FROM {}", page.table()))
        .build_query_scalar()
        .fetch_one(&mut *db)
        .await?;
    let Some(window) = Page::new(query, count) else {
        return Ok(access::found(format!("{path}?e=1")));
    };
    let order = query.get("o").map_or_else(
        || vec![(0, true)],
        |raw| {
            raw.split('.')
                .filter_map(|piece| {
                    let index = piece
                        .trim_start_matches('-')
                        .parse::<usize>()
                        .ok()?
                        .checked_sub(header_offset)?;
                    (index < USER_COLUMNS.len()).then_some((index, !piece.starts_with('-')))
                })
                .collect()
        },
    );
    let ordering = if page.user {
        order_sql(USER_COLUMNS, &order)
    } else {
        "t.name, t.id DESC".into()
    };
    let mut sql = QueryBuilder::new(if page.user {
        "SELECT t.id, t.username, t.email, t.first_name, t.last_name, t.is_staff"
    } else {
        "SELECT t.id, t.name, '', '', '', FALSE"
    });
    selection.constrain(page, &mut sql);
    let (limit, offset) = window.window();
    let rows: Vec<(i32, String, String, String, String, bool)> = sql
        .push(" ORDER BY ")
        .push(ordering)
        .push(" LIMIT ")
        .push_bind(limit)
        .push(" OFFSET ")
        .push_bind(offset)
        .build_query_as()
        .fetch_all(&mut *db)
        .await?;
    let mut filters = Vec::new();
    let mut clear = query.params.clone();
    if page.user {
        for &(field, _, title) in FLAGS {
            clear.remove(field);
            filters.push(
                choice_filter(
                    query,
                    field,
                    title,
                    [("1".into(), "Yes".into()), ("0".into(), "No".into())],
                )
                .map_err(template_error)?,
            );
        }
        clear.remove("groups__id__exact");
        clear.remove("groups__isnull");
        let groups: Vec<(i32, String)> =
            sqlx::query_as("SELECT id, name FROM auth_group ORDER BY name, id")
                .fetch_all(&mut *db)
                .await?;
        if !groups.is_empty() {
            filters.push(
                group_filter(query, selection.group, selection.no_group, groups)
                    .map_err(template_error)?,
            );
        }
    }
    db.commit().await?;
    let search = admin_html::render_fragment(
        "admin/search_form.html",
        context! {
            cl => context! { search_fields => true, query => query.get("q").unwrap_or_default(),
                              result_count => count, full_result_count => full, is_popup => popup,
                              show_full_result_count => true, params => query.ordered_params() },
            show_result_count => count != full, search_var => "q", is_popup_var => "_popup",
        },
    )
    .map_err(template_error)?;
    let mut result_headers = Vec::new();
    if !popup {
        result_headers.push(
            context! { class_attrib => r#" class="action-checkbox-column""#,
            text => r#"<input type="checkbox" id="action-toggle">"# },
        );
    }
    if page.user {
        result_headers.extend(headings(USER_COLUMNS, &order, query, header_offset));
    } else {
        result_headers
            .push(context! { class_attrib => r#" class="column-__str__""#, text => "Group" });
    }
    let suffix = escape(&query.suffix());
    let field = if page.user { "username" } else { "__str__" };
    let results: Vec<_> = rows.iter().map(|(id, name, email, first, last, staff)| {
        let mut cells = Vec::new();
        let opener = if popup { format!(r#" data-popup-opener="{id}""#) } else {
            cells.push(format!(r#"<td class="action-checkbox"><input type="checkbox" name="_selected_action" value="{id}" class="action-select"></td>"#));
            String::new()
        };
        cells.push(format!(r#"<th class="field-{field}"><a href="{path}{id}/change/{suffix}"{opener}>{}</a></th>"#, escape(name)));
        if page.user {
            for (field, label) in [("email", email), ("first_name", first), ("last_name", last)] {
                cells.push(format!(r#"<td class="field-{field}">{}</td>"#, escape(label)));
            }
            let (icon, alt) = if *staff { ("yes", "True") } else { ("no", "False") };
            cells.push(format!(r#"<td class="field-is_staff"><img src="/static/admin/img/icon-{icon}.svg" alt="{alt}"></td>"#));
        }
        cells
    }).collect();
    let action = format!(
        "<select name=\"action\" required>\n  <option value=\"\" selected>---------</option>\n\n  <option value=\"delete_selected\">Delete selected {}</option>\n\n</select>",
        model.plural
    );
    let pager = window.context(query);
    Ok(page.render("admin/change_list.html", context! {
        title => if popup { format!("Select {}", model.singular) } else { format!("Select {} to change", model.singular) },
        is_popup => popup, has_add_permission => true, filter_suffix => query.add_suffix(),
        module_name => model.plural, search_form => search, result_headers,
        num_sorted_fields => order.len(), results,
        cl => context! { opts => model.opts(), show_admin_actions => full > 0, result_count => count, result_list => &rows,
                         has_filters => !filters.is_empty(), has_active_filters => clear != query.params,
                         clear_all_filters_qs => Query::encode(clear), filter_specs => filters },
        actions_on_top => true, action_form => !popup,
        action_fields => [
            context! { label => "Action:", widget => action },
            context! { widget => r#"<input type="hidden" name="select_across" value="0" class="select-across">"# },
        ],
        actions_selection_counter => true, selection_note => format!("0 of {} selected", rows.len()),
        selection_note_all => format!("All {count} selected"),
        media => context! { js => admin_html::scripts() }, ..pager,
    }).await)
}

fn group_filter(
    query: &Query,
    selected: Option<i32>,
    empty: bool,
    groups: Vec<(i32, String)>,
) -> Result<String, minijinja::Error> {
    let mut base = query.params.clone();
    base.remove("groups__id__exact");
    base.remove("groups__isnull");
    let mut choices = vec![
        context! { display => "All", selected => selected.is_none() && !empty,
        query_string => Query::encode(base.clone()) },
    ];
    for (id, label) in groups {
        let mut params = base.clone();
        params.insert("groups__id__exact".into(), id.to_string());
        choices.push(
            context! { display => label, selected => selected == Some(id),
            query_string => Query::encode(params) },
        );
    }
    base.insert("groups__isnull".into(), "True".into());
    choices
        .push(context! { display => "-", selected => empty, query_string => Query::encode(base) });
    admin_html::render_fragment("admin/filter.html", context! { title => "groups", choices })
}

pub async fn objects(
    db: &mut MySqlConnection,
    page: &AuthPage<'_>,
    selected: Option<&BTreeSet<i32>>,
    lock: bool,
) -> Result<Vec<(i32, String)>, sqlx::Error> {
    if selected.is_some_and(BTreeSet::is_empty) {
        return Ok(Vec::new());
    }
    let Some(selection) = Selection::read(page) else {
        return Ok(Vec::new());
    };
    let column = page.name_column();
    let mut sql = QueryBuilder::new(format!("SELECT t.id, t.{column}"));
    selection.constrain(page, &mut sql);
    if let Some(selected) = selected {
        sql.push(" AND t.id IN (");
        let mut rows = sql.separated(", ");
        for id in selected {
            rows.push_bind(*id);
        }
        sql.push(")");
    }
    sql.push(" ORDER BY t.").push(column).push(", t.id DESC");
    if lock {
        sql.push(" FOR UPDATE");
    }
    sql.build_query_as().fetch_all(db).await
}

pub async fn history(page: &AuthPage<'_>, id: i32) -> Result<Response, sqlx::Error> {
    let model = page.model();
    let mut db = page.app.db.acquire().await?;
    let Some(name): Option<String> = sqlx::query_scalar(AssertSqlSafe(format!(
        "SELECT {} FROM {} WHERE id = ?",
        page.name_column(),
        page.table(),
    )))
    .bind(id)
    .fetch_optional(&mut *db)
    .await?
    else {
        return Ok(access::found("/admin/"));
    };
    let filter = "FROM django_admin_log l JOIN django_content_type t ON l.content_type_id = t.id
        JOIN auth_user u ON l.user_id = u.id WHERE t.app_label = 'auth' AND t.model = ? AND l.object_id = ?";
    let count: i64 = sqlx::query_scalar(AssertSqlSafe(format!("SELECT COUNT(*) {filter}")))
        .bind(model.name)
        .bind(id.to_string())
        .fetch_one(&mut *db)
        .await?;
    let count = u32::try_from(count).unwrap_or(u32::MAX);
    let limits = crate::admin_changelist::PER_PAGE;
    let pages = count.div_ceil(limits).max(1);
    let number = u32::try_from(page.query.requested())
        .ok()
        .filter(|page| (1..=pages).contains(page))
        .unwrap_or(pages);
    let records: Vec<(NaiveDateTime, String, String, String)> = sqlx::query_as(AssertSqlSafe(format!(
        "SELECT l.action_time, u.username, TRIM(CONCAT(u.first_name, ' ', u.last_name)), l.change_message
         {filter} ORDER BY l.action_time, l.id LIMIT ? OFFSET ?",
    ))).bind(model.name).bind(id.to_string()).bind(limits).bind((number - 1) * limits).fetch_all(&mut *db).await?;
    drop(db);
    let action_list: Vec<_> = records
        .into_iter()
        .map(|(date, username, full, message)| {
            context! { action_time => admin_html::django_datetime(date),
            user => context! {get_username => username, get_full_name => full},
            get_change_message => admin_html::admin_change_message(&message) }
        })
        .collect();
    Ok(page.render("admin/object_history.html", context! {
        title => format!("Change history: {name}"), object => name, object_url => format!("{}{id}/", model.url()),
        module_name => admin_html::capitalized(model.plural),
        action_list, count, page => number, pages, page_var => "p",
        pagination_required => pages > 1, page_range => (1..=pages).collect::<Vec<_>>(),
    }).await)
}
