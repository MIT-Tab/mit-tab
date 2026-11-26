use std::{
    collections::{BTreeSet, HashMap},
    sync::LazyLock,
};

use axum::{
    Extension, Form,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access,
    html::{self, Navigation},
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
    fields: Vec<FieldView<'a>>,
}

#[derive(Serialize)]
struct FieldView<'a> {
    form_name: String,
    label: String,
    description: &'a str,
    kind: Kind,
    disabled: bool,
    value: String,
    checked: bool,
    choices: &'a [(i32, String)],
    minimum: Option<i32>,
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
            "tot_rounds" => Some(1),
            _ => None,
        }
    }

    fn disabled(&self, disc_scratches_open: bool) -> bool {
        self.name == "judges_public" && disc_scratches_open
    }

    fn view(&self, settings: &Settings, scratch: bool, form: Option<&FormValues>) -> FieldView<'_> {
        let disabled = self.disabled(scratch);
        let raw = if let Some(form) = form.filter(|_| !disabled) {
            form.get(&self.form_name()).cloned().unwrap_or_default()
        } else {
            settings.string(&self.name, &self.default_value())
        };
        let value = if self.kind == Kind::Datetime {
            parse_date(&raw).map_or(raw.clone(), |date| {
                date.format("%Y-%m-%dT%H:%M").to_string()
            })
        } else {
            raw.clone()
        };
        FieldView {
            form_name: self.form_name(),
            label: self.label(),
            kind: self.kind,
            disabled,
            value,
            checked: truthy(&raw),
            choices: &self.choices,
            minimum: self.minimum(),
            description: if disabled {
                "Close discretionary scratches before hiding the judge list."
            } else {
                &self.description
            },
        }
    }
}

fn parse_date(value: &str) -> Option<NaiveDateTime> {
    ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]
        .iter()
        .find_map(|format| NaiveDateTime::parse_from_str(value, format).ok())
}

type FormValues = HashMap<String, String>;

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
    headers: HeaderMap,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let (settings, scratch) = current(&mut db).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(render(actor, &headers, &settings, scratch, None, None))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn render(
    actor: Actor,
    headers: &HeaderMap,
    settings: &Settings,
    scratch: bool,
    form: Option<&FormValues>,
    error: Option<String>,
) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
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
                .map(|field| field.view(settings, scratch, form))
                .collect(),
        })
        .collect();
    let status = if error.is_some() {
        StatusCode::UNPROCESSABLE_ENTITY
    } else {
        StatusCode::OK
    };
    let response = html::render(
        "settings_form.html",
        context! {
            title => "Tab Settings", categories, csrf, message => error,
            has_error => error.is_some(), nav => Navigation::new(actor, true),
        },
        status,
    );
    html::form_response(response, headers, &csrf)
}

enum Value {
    Number(i32),
    Text(String),
}

fn submitted(field: &Field, form: &FormValues) -> Option<Value> {
    let raw = form
        .get(&field.form_name())
        .map_or("", |value| value.trim());
    if field.kind == Kind::Boolean {
        return Some(Value::Number(i32::from(truthy(raw))));
    }
    if field.kind == Kind::Text {
        return (raw.chars().count() <= 200).then(|| Value::Text(raw.into()));
    }
    if field.kind == Kind::Datetime {
        return if raw.is_empty() {
            Some(Value::Text(String::new()))
        } else {
            parse_date(raw).map(|date| Value::Text(date.format("%Y-%m-%d %H:%M").to_string()))
        };
    }
    let value = raw.parse::<i32>().ok()?;
    if field.kind == Kind::Choice && !field.choices.iter().any(|(choice, _)| *choice == value) {
        return None;
    }
    field
        .minimum()
        .is_none_or(|minimum| value >= minimum)
        .then_some(Value::Number(value))
}

pub async fn save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
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
        for field in fields().filter(|field| !field.disabled(scratch)) {
            let Some(value) = submitted(field, &form) else {
                return Ok(render(
                    actor,
                    &headers,
                    &settings,
                    scratch,
                    Some(&form),
                    Some(format!("Enter a valid value for {}.", field.label())),
                ));
            };
            saved.push((field.name.as_str(), value));
        }
        for (key, value) in &saved {
            let value = match value {
                Value::Number(number) => SettingValue::Number(*number),
                Value::Text(text) => SettingValue::Text(text),
            };
            put(&mut db, key, value).await?;
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
        let settings = Settings::load(&mut db, &["settings_tabs_seen"]).await?;
        let previous = settings.string("settings_tabs_seen", "");
        let mut seen: BTreeSet<_> = previous.split(',').filter(|id| !id.is_empty()).collect();
        if seen.insert(&form.category) {
            let value = seen.into_iter().collect::<Vec<_>>().join(",");
            put(&mut db, "settings_tabs_seen", SettingValue::Text(&value)).await?;
        }
        db.commit().await?;
        Ok::<_, sqlx::Error>(private(StatusCode::NO_CONTENT))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}
