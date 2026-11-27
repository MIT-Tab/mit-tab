use std::{collections::BTreeSet, fmt::Write, num::IntErrorKind};

use chrono::NaiveDateTime;
use serde_json::Value as JsonValue;

use minijinja::{Value, context};
use sqlx::{AssertSqlSafe, MySqlConnection};

use crate::{
    admin_html::django_datetime,
    admin_model_data::valid_email,
    judge_portal::code_format,
    legacy_html::escape,
    registration_form::{Fields, integer_input},
    staff_roster_data::Kind,
};

pub async fn name_error(
    db: &mut MySqlConnection,
    kind: Kind,
    id: Option<i32>,
    name: &str,
    message: &str,
) -> Result<Option<String>, sqlx::Error> {
    let name = name.trim();
    let length = name.chars().count();
    let limit = kind.limit();
    let message = if name.is_empty() {
        "This field is required.".into()
    } else if length > limit {
        format!("Ensure this value has at most {limit} characters (it has {length}).")
    } else if name.contains('\0') {
        "Null characters are not allowed.".into()
    } else if name.chars().any(char::is_control) {
        message.into()
    } else {
        let duplicate = format!(
            "SELECT EXISTS(SELECT 1 FROM {} WHERE name = ? AND id <> COALESCE(?, 0))",
            kind.table()
        );
        if message == "That name or code already exists."
            || sqlx::query_scalar::<_, bool>(AssertSqlSafe(duplicate))
                .bind(name)
                .bind(id)
                .fetch_one(db)
                .await?
        {
            format!("{} with this Name already exists.", kind.singular())
        } else {
            return Ok(None);
        }
    };
    Ok(Some(message))
}

fn attributes(id: &str, style: &str, invalid: bool) -> String {
    if invalid {
        format!(r#"class="{style} is-invalid" aria-invalid="true" aria-describedby="{id}_error""#)
    } else {
        format!(r#"class="{style}""#)
    }
}

fn widget(id: &str, label: &str, input: String, errors: Vec<String>, checkbox: bool) -> Value {
    let label = escape(label);
    let label_tag = Value::from_safe_string(format!(r#"<label for="{id}">{label}</label>"#));
    let error_id = (!errors.is_empty()).then(|| format!("{id}_error"));
    context! { label_tag, input, errors, error_id, checkbox }
}

pub fn input(
    name: &str,
    label: &str,
    kind: &str,
    value: &str,
    attributes_before_class: &str,
    required: bool,
    errors: impl IntoIterator<Item = String>,
) -> Value {
    let id = format!("id_{name}");
    let value = if value.is_empty() {
        String::new()
    } else {
        format!(r#" value="{}""#, escape(value))
    };
    let errors: Vec<_> = errors.into_iter().collect();
    let attrs = attributes(&id, "form-control", !errors.is_empty());
    let required = if required { " required" } else { "" };
    let input = format!(
        r#"<input type="{kind}" name="{name}"{value}{attributes_before_class} {attrs}{required} id="{id}">"#,
    );
    widget(&id, label, input, errors, false)
}

pub fn multi_select(
    name: &str,
    label: &str,
    options: &[(String, String)],
    selected: &[String],
    error: Option<String>,
) -> Value {
    let id = format!("id_{name}");
    let attrs = attributes(&id, "form-control", error.is_some());
    let mut input = format!(r#"<select name="{name}" {attrs} id="{id}" multiple>"#);
    for (value, label) in options {
        option(&mut input, value, label, selected.contains(value));
    }
    input.push_str("\n</select>");
    widget(&id, label, input, error.into_iter().collect(), false)
}

pub fn select(
    name: &str,
    label: &str,
    options: &[(String, String)],
    selected: &str,
    required: bool,
    blank: bool,
    error: Option<String>,
) -> Value {
    let id = format!("id_{name}");
    let attrs = attributes(&id, "form-control", error.is_some());
    let required = if required { " required" } else { "" };
    let mut input = format!(r#"<select name="{name}" {attrs}{required} id="{id}">"#);
    if blank {
        option(&mut input, "", "---------", selected.is_empty());
    }
    for (value, label) in options {
        option(&mut input, value, label, selected == value);
    }
    input.push_str("\n</select>");
    widget(&id, label, input, error.into_iter().collect(), false)
}

fn option(input: &mut String, value: &str, label: &str, selected: bool) {
    let selected = if selected { " selected" } else { "" };
    let value = escape(value);
    let label = escape(label);
    let _ = write!(
        input,
        "\n  <option value=\"{value}\"{selected}>{label}</option>\n"
    );
}

pub fn apda_id_error(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let error = integer_input(value).err()?;
    Some(
        match error.kind() {
            IntErrorKind::PosOverflow => "Ensure this value is less than or equal to 2147483647.",
            IntErrorKind::NegOverflow => {
                "Ensure this value is greater than or equal to -2147483648."
            }
            _ => "Enter a whole number.",
        }
        .into(),
    )
}

pub fn email_errors(value: &str) -> Vec<String> {
    let mut errors = Vec::new();
    if !value.is_empty() && !valid_email(value) {
        errors.push("Enter a valid email address.".into());
    }
    let length = value.chars().count();
    if length > 254 {
        errors.push(format!(
            "Ensure this value has at most 254 characters (it has {length})."
        ));
    }
    if value.contains('\0') {
        errors.push("Null characters are not allowed.".into());
    }
    errors
}

pub fn checkbox_value(fields: &Fields, name: &str) -> bool {
    fields
        .all(name)
        .last()
        .is_some_and(|value| !value.is_empty() && !value.eq_ignore_ascii_case("false"))
}

pub fn checkbox(name: &str, label: &str, value: bool) -> Value {
    checkbox_with_help(name, label, value, None)
}

pub fn checkbox_with_help(name: &str, label: &str, value: bool, help_text: Option<&str>) -> Value {
    let id = format!("id_{name}");
    let checked = if value { " checked" } else { "" };
    let help_id = help_text.map(|_| format!("{id}_helptext"));
    let described_by = help_id
        .as_ref()
        .map_or_else(String::new, |id| format!(r#" aria-describedby="{id}""#));
    let input = format!(
        r#"<input type="checkbox" name="{name}" class="form-check-input"{described_by} id="{id}"{checked}>"#
    );
    let field = widget(&id, label, input, Vec::new(), true);
    context! { help_id, help_text, ..field }
}

pub const WING_CONFLICT: &str = "A judge cannot both request a wing and request no wings.";

pub fn judge_code_errors(value: &str) -> Vec<String> {
    let length = value.chars().count();
    let mut errors = Vec::new();
    if length > 255 {
        errors.push(format!(
            "Ensure this value has at most 255 characters (it has {length})."
        ));
    }
    if value.contains('\0') {
        errors.push("Null characters are not allowed.".into());
    }
    if !errors.is_empty() || value.is_empty() {
        return errors;
    }
    if length > 30 {
        errors.push(format!(
            "Ensure this value has at most 30 characters (it has {length})."
        ));
    }
    if !code_format(value) {
        errors.push("Ballot code must be legacy alphanumeric text or a hyphenated word-word code with an optional alphanumeric token.".into());
    }
    errors
}

pub fn judge_experience(value: &str) -> Result<Option<i32>, &'static str> {
    if value.is_empty() {
        return Ok(None);
    }
    let number = integer_input(value).map_err(|_| "Enter a whole number.")?;
    if number < 0 {
        Err("Ensure this value is greater than or equal to 0.")
    } else if number > 10 {
        Err("Ensure this value is less than or equal to 10.")
    } else {
        Ok(Some(number))
    }
}

pub fn rank_input(value: &str) -> Result<String, &'static str> {
    const INVALID: &str = "Enter a number.";
    let value = value.trim();
    if value.is_empty() {
        return Err("This field is required.");
    }
    let unsigned = value.strip_prefix(['-', '+']).unwrap_or(value);
    let (mantissa, exponent) = match unsigned.split_once(['e', 'E']) {
        Some((mantissa, exponent)) => (mantissa, exponent.parse::<i64>().map_err(|_| INVALID)?),
        None => (unsigned, 0),
    };
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    if mantissa.is_empty()
        || (whole.is_empty() && fraction.is_empty())
        || !whole
            .bytes()
            .chain(fraction.bytes())
            .all(|byte| byte.is_ascii_digit())
    {
        return Err(INVALID);
    }
    let digits = format!("{whole}{fraction}");
    let digits = digits.trim_start_matches('0');
    let exponent = exponent.saturating_sub(fraction.len() as i64);
    let places = exponent.saturating_neg().max(0);
    let total = if exponent > 0 && !digits.is_empty() {
        (digits.len() as i64).saturating_add(exponent)
    } else {
        (digits.len().max(1) as i64).max(places)
    };
    if total > 4 {
        return Err("Ensure that there are no more than 4 digits in total.");
    }
    if places > 2 {
        return Err("Ensure that there are no more than 2 decimal places.");
    }
    if total - places > 2 {
        return Err("Ensure that there are no more than 2 digits before the decimal point.");
    }
    if digits.is_empty() {
        return Ok("0.00".into());
    }
    let cents = digits.parse::<i32>().map_err(|_| INVALID)? * 10_i32.pow((exponent + 2) as u32);
    let sign = if value.starts_with('-') { "-" } else { "" };
    Ok(format!("{sign}{}.{:02}", cents / 100, cents % 100))
}

pub const MEDIA: &str = concat!(
    "<link href=\"/static/admin/css/widgets.css\" media=\"all\" rel=\"stylesheet\">\n",
    "<script src=\"/admin/jsi18n\"></script>",
);

pub type Choices = Vec<(String, String)>;

pub async fn options(
    db: &mut MySqlConnection,
    query: &'static str,
) -> Result<Choices, sqlx::Error> {
    Ok(sqlx::query_as::<_, (i32, String)>(query)
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(id, label)| (id.to_string(), label))
        .collect())
}

pub const REQUIRED: &str = "This field is required.";
pub const INVALID_CHOICE: &str =
    "Select a valid choice. That choice is not one of the available choices.";
pub fn reference_options(choices: &Choices, value: &str, required: bool) -> Option<String> {
    if value.is_empty() {
        required.then(|| REQUIRED.into())
    } else {
        let id = value.trim().parse::<i32>().ok().map(|id| id.to_string());
        (!choices
            .iter()
            .any(|(id_string, _)| Some(id_string) == id.as_ref()))
        .then(|| INVALID_CHOICE.into())
    }
}

pub fn multi_error(choices: &Choices, values: &[String]) -> Option<String> {
    let existing: BTreeSet<_> = choices.iter().map(|(id, _)| id.as_str()).collect();
    values.iter().find_map(|value| {
        let Ok(id) = value.trim().parse::<i32>() else {
            return Some(format!("“{value}” is not a valid value."));
        };
        (!existing.contains(id.to_string().as_str()))
            .then(|| format!("Select a valid choice. {value} is not one of the available choices."))
    })
}

pub async fn audit_history(
    db: &mut MySqlConnection,
    kind: Kind,
    id: i32,
) -> Result<Vec<Value>, sqlx::Error> {
    let rows = sqlx::query_as::<
        _,
        (
            String,
            String,
            NaiveDateTime,
            String,
            String,
        ),
    >(
        "SELECT e.event_type, COALESCE(u.username, 'Unknown'), e.created_at, e.note, CAST(e.changes AS CHAR)
         FROM tab_auditevent e JOIN django_content_type c ON e.content_type_id = c.id
         LEFT JOIN auth_user u ON e.user_id = u.id
         WHERE c.app_label = 'tab' AND c.model = ? AND e.object_id = ?
         ORDER BY e.created_at DESC, e.id DESC",
    )
    .bind(kind.model())
    .bind(id)
    .fetch_all(db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(event, user, date, note, changes)| {
            let label = crate::admin_records::event_label(&event);
            let label = if label == "-" { &event } else { label };
            context! {
                    label, user_display => user, created_at => django_datetime(date), note,
                    changes => serde_json::from_str::<JsonValue>(&changes).unwrap_or_default(),
            }
        })
        .collect())
}
