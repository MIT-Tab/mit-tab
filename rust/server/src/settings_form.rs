use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    fmt::Write,
    sync::LazyLock,
};

use axum::{
    Extension, Form,
    extract::State,
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access, legacy_html,
    legacy_session::Actor,
    login, private,
    settings::{SettingValue, Settings, put},
    staff_pairing::lock_round,
    tournament_setup::truthy,
    unavailable,
};

const PATH: &str = "/settings_form";

#[derive(Deserialize)]
struct CategoryConfig {
    category: Category,
    settings: Vec<Field>,
}

#[derive(Deserialize)]
struct Category {
    id: String,
    name: String,
    description: String,
    order: i32,
}

#[derive(Deserialize)]
struct Field {
    name: String,
    label: Option<String>,
    description: String,
    value: serde_yaml_ng::Value,
    #[serde(rename = "type", default)]
    kind: Kind,
    #[serde(default)]
    choices: Vec<(i32, String)>,
}

#[derive(Clone, Copy, Default, Eq, PartialEq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
enum Kind {
    Boolean,
    Choice,
    Text,
    Datetime,
    #[default]
    Integer,
}

static CONFIG: LazyLock<Vec<CategoryConfig>> = LazyLock::new(|| {
    let mut categories: Vec<CategoryConfig> = [
        include_str!("../../../settings/advanced.yaml"),
        include_str!("../../../settings/display.yaml"),
        include_str!("../../../settings/judges.yaml"),
        include_str!("../../../settings/speaks.yaml"),
        include_str!("../../../settings/tab_policy.yaml"),
    ]
    .into_iter()
    .map(|yaml| serde_yaml_ng::from_str(yaml).expect("built-in settings YAML"))
    .collect();
    categories.sort_by_key(|category| category.category.order);
    categories
});

pub fn categories() -> impl Iterator<Item = &'static str> {
    CONFIG.iter().map(|category| category.category.id.as_str())
}

fn fields() -> impl Iterator<Item = &'static Field> {
    CONFIG.iter().flat_map(|category| &category.settings)
}

#[derive(Serialize)]
struct CategoryView<'a> {
    id: &'a str,
    name: &'a str,
    description: &'a str,
    fields: Vec<minijinja::Value>,
}

impl Field {
    fn form_name(&self) -> String {
        format!("setting_{}", self.name)
    }

    fn label(&self) -> String {
        self.label.clone().unwrap_or_else(|| {
            self.name
                .split('_')
                .map(|word| {
                    let mut chars = word.chars();
                    chars.next().map_or(String::new(), |first| {
                        first.to_uppercase().collect::<String>() + chars.as_str()
                    })
                })
                .collect::<Vec<_>>()
                .join(" ")
        })
    }

    fn default_value(&self) -> String {
        match &self.value {
            serde_yaml_ng::Value::Bool(value) => i32::from(*value).to_string(),
            serde_yaml_ng::Value::Number(value) => value.to_string(),
            serde_yaml_ng::Value::String(value) => value.clone(),
            _ => String::new(),
        }
    }

    fn minimum(&self) -> Option<i32> {
        match self.name.as_str() {
            "nov_teams_to_break" | "var_teams_to_break" | "min_byes" => Some(0),
            _ => None,
        }
    }

    fn disabled(&self, disc_scratches_open: bool) -> bool {
        self.name == "judges_public" && disc_scratches_open
    }

    fn view(
        &self,
        settings: &Settings,
        scratch: bool,
        form: Option<&FormValues>,
        errors: &FormErrors,
    ) -> minijinja::Value {
        let name = self.form_name();
        let id = format!("id_{name}");
        let disabled = self.disabled(scratch);
        let stored = settings.value(&self.name);
        let form = form.filter(|_| !disabled);
        let raw = if let Some(form) = form {
            form.get(&name).cloned()
        } else {
            Some(settings.string(&self.name, &self.default_value()))
        };
        let value = raw.as_deref();
        let printed_date;
        let widget_value = if self.kind == Kind::Datetime && form.is_none() {
            printed_date = value.and_then(parse_date).map_or_else(
                || value.unwrap_or_default().into(),
                |date| date.format("%Y-%m-%dT%H:%M").to_string(),
            );
            Some(printed_date.as_str())
        } else {
            value
        };
        let checked = if let Some(form) = form {
            checked_input(form.get(&name))
        } else {
            stored.map_or(self.value.as_bool().unwrap_or_default(), |value| {
                value == SettingValue::Number(1)
            })
        };
        let description = if disabled {
            "Close disc scratches before hiding the judge list."
        } else {
            &self.description
        };
        let html = self.widget(&name, &id, widget_value);
        let errors = errors
            .get(self.name.as_str())
            .map(|error| {
                format!(
                    r#"<ul class="errorlist"><li>{}</li></ul>"#,
                    legacy_html::escape(error)
                )
            })
            .unwrap_or_default();
        context! {
            name, id_for_label => id, label => self.label(), help_text => description, html, errors,
            value => checked, field => context! {
                disabled, widget => context! {input_type => if self.kind == Kind::Boolean { "checkbox" } else { "" }},
            },
        }
    }

    fn widget(&self, name: &str, id: &str, value: Option<&str>) -> String {
        if self.kind == Kind::Choice {
            let required = if self.choices.is_empty() {
                " required"
            } else {
                ""
            };
            let mut html =
                format!("<select name=\"{name}\" class=\"form-control\"{required} id=\"{id}\">\n");
            for (number, label) in &self.choices {
                let selected = if value == Some(number.to_string().as_str()) {
                    " selected"
                } else {
                    ""
                };
                let _ = write!(
                    html,
                    "  <option value=\"{number}\"{selected}>{}</option>\n\n",
                    legacy_html::escape(label)
                );
            }
            html.push_str("</select>");
            return html;
        }
        let (kind, attributes) = match self.kind {
            Kind::Integer => (
                "number",
                self.minimum().map_or(" required".to_string(), |value| {
                    format!(" min=\"{value}\" required")
                }),
            ),
            Kind::Text => ("text", r#" style="min-width: 300px;""#.to_string()),
            Kind::Datetime => ("datetime-local", String::new()),
            _ => return String::new(),
        };
        let value = value
            .filter(|value| self.kind == Kind::Datetime || !value.is_empty())
            .map(|value| format!(r#" value="{}""#, legacy_html::escape(value)))
            .unwrap_or_default();
        format!(
            r#"<input type="{kind}" name="{name}"{value} class="form-control"{attributes} id="{id}">"#
        )
    }

    fn unchanged(&self, settings: &Settings, value: &Value, form: &FormValues) -> bool {
        let Some(stored) = settings.value(&self.name) else {
            return false;
        };
        if stored == value.as_setting() {
            return true;
        }
        match (self.kind, stored, value) {
            (Kind::Boolean, original, Value::Number(0)) => {
                // A historically text-backed flag renders unchecked in Django.
                // Leave it alone on an ordinary form save with no explicit value.
                original != SettingValue::Number(1) && !form.contains_key(&self.form_name())
            }
            (Kind::Integer, SettingValue::Text(original), Value::Number(number)) => {
                integer_input(original) == Some(*number)
            }
            (Kind::Choice, SettingValue::Text(original), Value::Number(number)) => {
                original == number.to_string()
            }
            (Kind::Datetime, SettingValue::Text(original), Value::Text(text)) => {
                !text.is_empty() && parse_date(original) == parse_date(text)
            }
            _ => false,
        }
    }
}

fn parse_date(value: &str) -> Option<NaiveDateTime> {
    ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]
        .iter()
        .find_map(|format| NaiveDateTime::parse_from_str(value, format).ok())
}

type FormValues = HashMap<String, String>;
type FormErrors = BTreeMap<&'static str, String>;

fn checked_input(value: Option<&String>) -> bool {
    value.is_some_and(|value| truthy(value))
}

fn integer_input(raw: &str) -> Option<i32> {
    let whole = raw
        .rsplit_once('.')
        .filter(|(_, digits)| digits.bytes().all(|digit| digit == b'0'))
        .map_or(raw, |(whole, _)| whole);
    whole.parse().ok()
}

async fn current(db: &mut MySqlConnection) -> Result<(Settings, bool), sqlx::Error> {
    let keys: Vec<_> = fields().map(|field| field.name.as_str()).collect();
    let settings = Settings::load(db, &keys).await?;
    let scratch = sqlx::query_scalar::<_, bool>(
        "SELECT disc_scratches_open FROM registration_registrationconfig ORDER BY id LIMIT 1",
    )
    .fetch_optional(db)
    .await?
    .unwrap_or(false);
    Ok((settings, scratch))
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let (settings, scratch) = current(&mut db).await?;
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(render(
            shell,
            &headers,
            &settings,
            scratch,
            None,
            &FormErrors::new(),
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn render(
    shell: minijinja::Value,
    headers: &HeaderMap,
    settings: &Settings,
    scratch: bool,
    form: Option<&FormValues>,
    errors: &FormErrors,
) -> Response {
    let Ok(csrf) = login::TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let categories: Vec<_> = CONFIG
        .iter()
        .map(|category| CategoryView {
            id: &category.category.id,
            name: &category.category.name,
            description: &category.category.description,
            fields: category
                .settings
                .iter()
                .map(|field| field.view(settings, scratch, form, errors))
                .collect(),
        })
        .collect();
    let status = if !errors.is_empty() {
        StatusCode::UNPROCESSABLE_ENTITY
    } else {
        StatusCode::OK
    };
    let mut response = legacy_html::render(
        "tab/settings_form.html",
        shell,
        context! {
            title => "Tab Settings", categories, csrf_input => csrf.input(), form => context! {errors},
        },
        status,
    );
    csrf.set_cookie(&mut response, headers);
    response
}

enum Value {
    Number(i32),
    Text(String),
}

impl Value {
    fn as_setting(&self) -> SettingValue<'_> {
        match self {
            Self::Number(number) => SettingValue::Number(*number),
            Self::Text(text) => SettingValue::Text(text),
        }
    }
}

fn submitted(field: &Field, form: &FormValues) -> Result<Value, String> {
    let raw = form
        .get(&field.form_name())
        .map_or("", |value| value.trim());
    if field.kind == Kind::Boolean {
        return Ok(Value::Number(i32::from(checked_input(
            form.get(&field.form_name()),
        ))));
    }
    if field.kind == Kind::Text {
        let count = raw.chars().count();
        return if count <= 200 {
            Ok(Value::Text(raw.into()))
        } else {
            Err(format!(
                "Ensure this value has at most 200 characters (it has {count})."
            ))
        };
    }
    if field.kind == Kind::Datetime {
        return if raw.is_empty() {
            Ok(Value::Text(String::new()))
        } else {
            parse_date(raw)
                .map(|date| Value::Text(date.format("%Y-%m-%d %H:%M").to_string()))
                .ok_or_else(|| "Enter a valid date/time.".into())
        };
    }
    if raw.is_empty() {
        return Err("This field is required.".into());
    }
    if field.kind == Kind::Choice {
        return field
            .choices
            .iter()
            .find(|(choice, _)| choice.to_string() == raw)
            .map(|(choice, _)| Value::Number(*choice))
            .ok_or_else(|| {
                format!("Select a valid choice. {raw} is not one of the available choices.")
            });
    }
    let value = integer_input(raw).ok_or_else(|| "Enter a whole number.".to_string())?;
    let minimum = if field.name == "tot_rounds" {
        Some(1)
    } else {
        field.minimum()
    };
    if let Some(minimum) = minimum
        && value < minimum
    {
        return Err(format!(
            "Ensure this value is greater than or equal to {minimum}."
        ));
    }
    Ok(Value::Number(value))
}

pub async fn save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    uri: Uri,
    headers: HeaderMap,
    Form(form): Form<FormValues>,
) -> Response {
    if !login::valid_csrf(
        &headers,
        form.get("csrfmiddlewaretoken").map_or("", String::as_str),
    ) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        sqlx::query(
            "SELECT id FROM registration_registrationconfig ORDER BY id LIMIT 1 FOR UPDATE",
        )
        .fetch_optional(&mut *db)
        .await?;
        let (settings, scratch) = current(&mut db).await?;
        let mut saved = Vec::new();
        let mut errors = FormErrors::new();
        for field in fields().filter(|field| !field.disabled(scratch)) {
            match submitted(field, &form) {
                Ok(value) => saved.push((field, value)),
                Err(error) => {
                    errors.insert(field.name.as_str(), error);
                }
            }
        }
        if !errors.is_empty() {
            let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
            db.rollback().await?;
            return Ok(render(
                shell,
                &headers,
                &settings,
                scratch,
                Some(&form),
                &errors,
            ));
        }
        for (field, value) in &saved {
            if !field.unchanged(&settings, value, &form) {
                put(&mut db, &field.name, value.as_setting()).await?;
            }
        }
        if let Some(visited) = form.get("visited_categories") {
            record_categories(&mut db, visited.split(',')).await?;
        }
        crate::roster_log::enabled(&mut db).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(PATH))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Deserialize)]
pub struct Viewed {
    csrfmiddlewaretoken: String,
    category: String,
}

pub async fn viewed(
    State(app): State<App>,
    headers: HeaderMap,
    Form(form): Form<Viewed>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    if !categories().any(|category| category == form.category) {
        return private((StatusCode::BAD_REQUEST, "Unknown settings category."));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        record_categories(&mut db, [form.category.as_str()]).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(private(StatusCode::NO_CONTENT))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn record_categories<'a>(
    db: &mut MySqlConnection,
    visited: impl IntoIterator<Item = &'a str>,
) -> Result<(), sqlx::Error> {
    let settings = Settings::load(db, &["settings_tabs_seen"]).await?;
    let previous = settings.string("settings_tabs_seen", "");
    let mut seen: BTreeSet<_> = previous.split(',').filter(|id| !id.is_empty()).collect();
    let count = seen.len();
    seen.extend(
        visited
            .into_iter()
            .filter_map(|id| categories().find(|category| id == *category)),
    );
    if seen.len() != count {
        let value = seen.into_iter().collect::<Vec<_>>().join(",");
        put(db, "settings_tabs_seen", SettingValue::Text(&value)).await?;
    }
    Ok(())
}
