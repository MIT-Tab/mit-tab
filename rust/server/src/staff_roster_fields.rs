use minijinja::{Value, context};
use sqlx::{AssertSqlSafe, MySqlConnection};

use crate::{legacy_html::escape, staff_roster_data::Kind};

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

fn widget(id: &str, label: &str, input: String, error: Option<String>, checkbox: bool) -> Value {
    let label = escape(label);
    let label_tag = Value::from_safe_string(format!(r#"<label for="{id}">{label}</label>"#));
    let error_id = error.as_ref().map(|_| format!("{id}_error"));
    context! { label_tag, input, errors => error.into_iter().collect::<Vec<_>>(), error_id, checkbox }
}

pub fn input(
    name: &str,
    label: &str,
    kind: &str,
    value: &str,
    attributes_before_class: &str,
    required: bool,
    error: Option<String>,
) -> Value {
    let id = format!("id_{name}");
    let value = if value.is_empty() {
        String::new()
    } else {
        format!(r#" value="{}""#, escape(value))
    };
    let attrs = attributes(&id, "form-control", error.is_some());
    let required = if required { " required" } else { "" };
    let input = format!(
        r#"<input type="{kind}" name="{name}"{value}{attributes_before_class} {attrs}{required} id="{id}">"#,
    );
    widget(&id, label, input, error, false)
}

pub fn multi_select(
    name: &str,
    label: &str,
    options: &[(String, String)],
    selected: &[String],
    error: Option<String>,
) -> Value {
    use std::fmt::Write;

    let id = format!("id_{name}");
    let attrs = attributes(&id, "form-control", error.is_some());
    let mut input = format!(r#"<select name="{name}" {attrs} id="{id}" multiple>"#);
    for (value, label) in options {
        let checked = if selected.contains(value) {
            " selected"
        } else {
            ""
        };
        let value = escape(value);
        let label = escape(label);
        let _ = write!(
            input,
            "\n  <option value=\"{value}\"{checked}>{label}</option>\n"
        );
    }
    input.push_str("\n</select>");
    widget(&id, label, input, error, false)
}

pub fn checkbox(name: &str, label: &str, value: bool) -> Value {
    let id = format!("id_{name}");
    let checked = if value { " checked" } else { "" };
    let input = format!(
        r#"<input type="checkbox" name="{name}" class="form-check-input" id="{id}"{checked}>"#
    );
    widget(&id, label, input, None, true)
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
