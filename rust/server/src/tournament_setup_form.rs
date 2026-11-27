use std::{collections::BTreeMap, fmt::Write};

use minijinja::{Value, context};

use super::{Data, FormFields, TOURNAMENT_TYPES, role_errors, truthy};
use crate::{legacy_html::escape, staff_invites};

pub(super) type Errors = BTreeMap<String, String>;

const DISCLOSURE: &[(&str, &str)] = &[
    ("", "Choose a policy…"),
    ("1", "Open — judges disclose this information"),
    ("2", "Closed — judges do not disclose this information"),
];

const PRESET_SUMMARIES: [&str; 6] = [
    "Unrestricted tab staff access.",
    "One-purpose scratch entry role.",
    "Scratch entry plus read-only scratch review.",
    "Check-in desk role.",
    "Roster and room data maintenance role.",
    "Data entry plus check-in support.",
];

struct Field<'a> {
    name: &'a str,
    value: &'a str,
    error: Option<&'a str>,
}

impl<'a> Field<'a> {
    fn new(name: &'a str, fields: &'a FormFields, errors: &'a Errors) -> Self {
        Self {
            name,
            value: fields.get(name).map_or("", String::as_str),
            error: errors.get(name).map(String::as_str),
        }
    }

    fn class(&self, base: &str) -> String {
        if self.error.is_some() {
            format!(r#" class="{base} is-invalid""#)
        } else {
            format!(r#" class="{base}""#)
        }
    }

    fn context(&self, label: &str, help_text: &str, html: String) -> Value {
        context! {
            id_for_label => format!("id_{}", self.name), label, help_text,
            html, errors => self.error.into_iter().collect::<Vec<_>>(),
            value => self.value,
        }
    }

    fn input(&self, kind: &str, attrs: &str) -> String {
        let value = if self.value.is_empty() {
            String::new()
        } else {
            format!(r#" value="{}""#, escape(self.value))
        };
        format!(
            r#"<input type="{kind}" name="{}"{value}{attrs} id="id_{}">"#,
            self.name, self.name
        )
    }

    fn textarea(&self, attrs: &str) -> String {
        format!(
            "<textarea name=\"{}\" cols=\"40\" rows=\"2\"{attrs} id=\"id_{}\">\n{}</textarea>",
            self.name,
            self.name,
            escape(self.value)
        )
    }

    fn select<'b>(
        &self,
        options: impl IntoIterator<Item = (&'b str, &'b str)>,
        required: bool,
    ) -> String {
        let class = self.class("custom-select");
        let required = if required { " required" } else { "" };
        let mut html = format!(
            "<select name=\"{}\"{class}{required} id=\"id_{}\">\n",
            self.name, self.name
        );
        for (value, label) in options {
            let selected = if self.value == value { " selected" } else { "" };
            let _ = write!(
                html,
                "  <option value=\"{}\"{selected}>{}</option>\n\n",
                escape(value),
                escape(label)
            );
        }
        html.push_str("</select>");
        html
    }
}

fn basics(data: &Data, fields: &FormFields, errors: &Errors, bound: bool) -> Value {
    let mut widgets = BTreeMap::new();
    for (name, label, help, kind, attrs) in [
        (
            "tournament_name",
            "Tournament name",
            "",
            "text",
            r#" placeholder="e.g. MIT Spring Invitational" autocomplete="organization" maxlength="200" required"#,
        ),
        (
            "ga_room",
            "GA location / room",
            "The room or online location attendees should use for general assembly.",
            "text",
            r#" placeholder="e.g. Building 26, Room 100" maxlength="500" required"#,
        ),
        (
            "facebook_url",
            "Facebook link",
            "The tournament's Facebook event or announcement page.",
            "url",
            r#" placeholder="https://www.facebook.com/events/..." autocomplete="url" maxlength="500" required"#,
        ),
        (
            "tournament_director",
            "Tournament director",
            "",
            "text",
            r#" autocomplete="name" maxlength="200" required"#,
        ),
        (
            "tab_director",
            "Tab director",
            "",
            "text",
            r#" autocomplete="name" maxlength="200" required"#,
        ),
        (
            "tournament_observer",
            "Tournament observer",
            "",
            "text",
            r#" autocomplete="name" maxlength="200" required"#,
        ),
    ] {
        let field = Field::new(name, fields, errors);
        let html = field.input(kind, &(field.class("form-control") + attrs));
        widgets.insert(name, field.context(label, help, html));
    }
    let equity = Field::new("equity_officers", fields, errors);
    widgets.insert(
        equity.name,
        equity.context(
            "Equity officer(s)",
            "Enter one name per line.",
            equity.textarea(&(equity.class("form-control") + r#" maxlength="200" required"#)),
        ),
    );

    let school = Field::new("host_school", fields, errors);
    let mut schools: Vec<_> = data
        .schools
        .iter()
        .map(|school| (school.value.as_str(), school.name.as_str()))
        .collect();
    if !school.value.is_empty() && !schools.iter().any(|(value, _)| *value == school.value) {
        let label = if bound {
            "Selected school"
        } else {
            data.fields
                .get("host_school_name")
                .filter(|name| !name.is_empty())
                .map_or("Selected school", String::as_str)
        };
        schools.push((school.value, label));
        schools.sort_by_key(|(_, name)| name.to_lowercase());
    }
    widgets.insert(
        school.name,
        school.context(
            "Host school",
            "Schools are synced from the APDA results directory.",
            school.select(
                std::iter::once(("", "Choose a school…")).chain(schools),
                true,
            ),
        ),
    );

    let kind = Field::new("tournament_type", fields, errors);
    let options: Vec<_> = TOURNAMENT_TYPES
        .iter()
        .enumerate()
        .map(|(index, &label)| (index.to_string(), label))
        .collect();
    widgets.insert(
        kind.name,
        kind.context(
            "Tournament type",
            "This determines which season points the live results can award.",
            kind.select(
                options
                    .iter()
                    .map(|(value, label)| (value.as_str(), *label)),
                false,
            ),
        ),
    );

    let sanctioned = Field::new("sanctioned", fields, errors);
    // Django's CheckboxInput emits checked for any submitted value except
    // the literal "false" and empty strings.
    let checked = if !sanctioned.value.is_empty() && !sanctioned.value.eq_ignore_ascii_case("false")
    {
        " checked"
    } else {
        ""
    };
    widgets.insert(sanctioned.name, sanctioned.context("Sanctioned APDA tournament",
        "Only sanctioned tournaments are included in the live APDA leaderboards.",
        format!(r#"<input type="checkbox" name="sanctioned" class="custom-control-input" id="id_sanctioned"{checked}>"#)));

    for (name, label, help) in [
        (
            "open_speaks",
            "Speaker points",
            "Choose whether judges tell debaters their speaker points.",
        ),
        (
            "open_ranks",
            "Speaker ranks",
            "Choose whether judges tell debaters their speaker ranks (open ranks are shared only if debaters opt in).",
        ),
    ] {
        let mut field = Field::new(name, fields, errors);
        if !bound && field.value == "0" {
            field.value = "";
        }
        widgets.insert(
            name,
            field.context(label, help, field.select(DISCLOSURE.iter().copied(), true)),
        );
    }
    widgets.insert(
        "errors",
        Value::from(errors.keys().any(|name| !name.starts_with("custom_staff-"))),
    );
    Value::from_serialize(widgets)
}

fn custom_staff_row(
    index: &str,
    title: &str,
    names: &str,
    deleted: &str,
    errors: &Errors,
) -> Value {
    let title_name = format!("custom_staff-{index}-title");
    let names_name = format!("custom_staff-{index}-names");
    let delete_name = format!("custom_staff-{index}-DELETE");
    let [deleted_title_error, deleted_names_error] = if truthy(deleted) {
        role_errors(title.trim(), names.trim())
    } else {
        [None, None]
    };
    let title = Field {
        name: &title_name,
        value: title,
        error: errors
            .get(&title_name)
            .map(String::as_str)
            .or(deleted_title_error.as_deref()),
    };
    let names = Field {
        name: &names_name,
        value: names,
        error: errors
            .get(&names_name)
            .map(String::as_str)
            .or(deleted_names_error.as_deref()),
    };
    let delete = Field {
        name: &delete_name,
        value: deleted,
        error: None,
    };
    context! {
        title => title.context("Title", "", title.input("text", r#" class="form-control" placeholder="e.g. Food Director" maxlength="100""#)),
        names => names.context("Name(s)", "Enter one name per line.", names.textarea(r#" class="form-control" maxlength="500""#)),
        DELETE => delete.context("", "", delete.input("hidden", "")),
    }
}

fn staff_formset(data: &Data, fields: Option<&FormFields>, errors: &Errors) -> Value {
    let rows: Vec<_> = if let Some(fields) = fields {
        let count = fields
            .get("custom_staff-TOTAL_FORMS")
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(0);
        (0..count.min(2000))
            .map(|index| {
                let read = |name| {
                    fields
                        .get(&format!("custom_staff-{index}-{name}"))
                        .map_or("", String::as_str)
                };
                custom_staff_row(
                    &index.to_string(),
                    read("title"),
                    read("names"),
                    read("DELETE"),
                    errors,
                )
            })
            .collect()
    } else {
        data.roles
            .iter()
            .enumerate()
            .map(|(index, role)| {
                custom_staff_row(&index.to_string(), &role.title, &role.names, "", errors)
            })
            .collect()
    };
    let initial_count = rows.len().to_string();
    let mut management_form = String::new();
    for (suffix, initial) in [
        ("TOTAL_FORMS", initial_count.as_str()),
        ("INITIAL_FORMS", initial_count.as_str()),
        ("MIN_NUM_FORMS", "0"),
        ("MAX_NUM_FORMS", "1000"),
    ] {
        let name = format!("custom_staff-{suffix}");
        let value = fields.map_or(initial, |form| form.get(&name).map_or("", String::as_str));
        management_form.push_str(
            &Field {
                name: &name,
                value,
                error: None,
            }
            .input("hidden", ""),
        );
    }
    let non_form_errors = errors.get("custom_staff-__all__").map(|error| {
        format!(
            r#"<ul class="errorlist nonform"><li>{}</li></ul>"#,
            escape(error)
        )
    });
    context! {
        rows, management_form, non_form_errors => non_form_errors.unwrap_or_default(),
        empty_form => custom_staff_row("__prefix__", "", "", "", errors),
        errors => errors.keys().any(|name| name.starts_with("custom_staff-")),
    }
}

fn invite_form(fields: &FormFields, errors: &Errors, bound: bool) -> Value {
    let email = Field::new("invite-email", fields, errors);
    let username = Field::new("invite-username", fields, errors);
    let mut preset = Field::new("invite-permission_preset", fields, errors);
    if !bound {
        preset.value = "full_tab_staff";
    }
    let address = format!(
        r#" maxlength="320"{} placeholder="colleague@example.com" required"#,
        email.class("form-control")
    );
    let handle = format!(
        r#" maxlength="150"{} placeholder="e.g. jsmith" required"#,
        username.class("form-control")
    );
    context! {
        email => email.context("Email", "", email.input("email", &address)),
        username => username.context("Username", "", username.input("text", &handle)),
        permission_preset => preset.context("Permission preset", "", preset.select(staff_invites::ROLES.iter().map(|&(id, label, _)| (id, label)), false)),
        non_field_errors => errors.get("__all__").into_iter().collect::<Vec<_>>(),
    }
}

pub(super) fn page(
    data: &Data,
    invite_enabled: bool,
    bound: Option<(&FormFields, &Errors)>,
    invite_bound: Option<(&FormFields, &Errors)>,
) -> Value {
    let blank = FormFields::new();
    let empty_errors = Errors::new();
    let (fields, errors) = bound.unwrap_or((&data.fields, &empty_errors));
    let (invite_fields, invite_errors) = invite_bound.unwrap_or((&blank, &empty_errors));
    let invite_form =
        invite_enabled.then(|| invite_form(invite_fields, invite_errors, invite_bound.is_some()));
    let details: Vec<_> = staff_invites::ROLES
        .iter()
        .zip(PRESET_SUMMARIES)
        .map(|((_, label, _), summary)| context! { label, summary })
        .collect();
    context! {
        form => basics(data, fields, errors, bound.is_some()),
        custom_staff_formset => staff_formset(data, bound.map(|(fields, _)| fields), errors),
        invite_form,
        invite_count => data.fields.get(staff_invites::INVITE_COUNT).and_then(|count| count.parse::<i32>().ok()).unwrap_or_default(),
        staff_preset_details => details, is_initial_setup => !data.complete,
    }
}
