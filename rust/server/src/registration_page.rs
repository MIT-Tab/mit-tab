use std::{collections::BTreeMap, fmt::Write};

use minijinja::{Value, context};

use crate::{
    legacy_html::escape,
    registration_form::{
        Fields, Judge, MAX_JUDGES, MAX_TEAMS, Registration, Team, integer_input, valid_email,
    },
    school_directory::SchoolChoice,
};

type WidgetErrors = Vec<String>;

struct Form<'a> {
    prefix: String,
    posted: Option<&'a Fields>,
    widgets: BTreeMap<String, Value>,
    hidden: Vec<String>,
    has_errors: bool,
}

impl<'a> Form<'a> {
    fn new(prefix: impl Into<String>, posted: Option<&'a Fields>) -> Self {
        Self {
            prefix: prefix.into(),
            posted,
            widgets: BTreeMap::new(),
            hidden: Vec::new(),
            has_errors: false,
        }
    }

    fn name(&self, field: &str) -> String {
        format!("{}{field}", self.prefix)
    }

    fn value(&self, field: &str, initial: &str) -> String {
        self.posted
            .map_or(initial, |posted| {
                posted
                    .all(&self.name(field))
                    .last()
                    .map_or("", String::as_str)
            })
            .to_owned()
    }

    fn checked(&self, field: &str, initial: bool) -> bool {
        self.posted
            .map_or(initial, |posted| posted.flag(&self.name(field)))
    }

    fn hidden(&mut self, field: &str, initial: &str, attrs: &str) {
        let name = self.name(field);
        let value = self.value(field, initial);
        self.hidden
            .push(input_markup("hidden", &name, &value, attrs));
    }

    fn widget(&mut self, field: &str, input: String, errors: WidgetErrors) {
        self.has_errors |= !errors.is_empty();
        let id_for_label = format!("id_{}", self.name(field));
        let errors = Value::from_safe_string(error_list(errors));
        self.widgets.insert(
            field.into(),
            context! { input => Value::from_safe_string(input), id_for_label, errors },
        );
    }

    fn input(&mut self, field: &str, kind: &str, initial: &str, attrs: &str, errors: WidgetErrors) {
        self.widget(
            field,
            input_markup(kind, &self.name(field), &self.value(field, initial), attrs),
            errors,
        );
    }

    fn select(
        &mut self,
        field: &str,
        choices: &[(&str, &str)],
        selected: &[&str],
        attrs: &str,
        multiple: bool,
        errors: WidgetErrors,
    ) {
        let name = self.name(field);
        let multiple = if multiple { " multiple" } else { "" };
        let mut input = format!(r#"<select name="{name}"{attrs} id="id_{name}"{multiple}>"#);
        for &(value, label) in choices {
            let selected = if selected.contains(&value) {
                " selected"
            } else {
                ""
            };
            let _ = write!(
                input,
                "\n  <option value=\"{}\"{selected}>{}</option>\n",
                escape(value),
                escape(label)
            );
        }
        input.push_str("\n</select>");
        self.widget(field, input, errors);
    }

    fn checkbox(&mut self, field: &str, selected: bool) {
        let name = self.name(field);
        let checked = if self.checked(field, selected) {
            " checked"
        } else {
            ""
        };
        let input = format!(
            r#"<input type="checkbox" name="{name}" class="custom-control-input" id="id_{name}"{checked}>"#
        );
        self.widget(field, input, Vec::new());
    }

    fn finish(self, non_field_errors: WidgetErrors) -> Value {
        context! {
            is_bound => self.posted.is_some(),
            hidden_fields => self.hidden,
            has_errors => self.has_errors || !non_field_errors.is_empty(),
            non_field_errors,
            ..Value::from_serialize(self.widgets)
        }
    }
}

fn attribute(name: &str, value: &str) -> String {
    format!(r#" {name}="{}""#, escape(value))
}

fn input_markup(kind: &str, name: &str, value: &str, attrs: &str) -> String {
    let value = if value.is_empty() {
        String::new()
    } else {
        attribute("value", value)
    };
    format!(r#"<input type="{kind}" name="{name}"{value}{attrs} id="id_{name}">"#)
}

fn error_list(errors: WidgetErrors) -> String {
    if errors.is_empty() {
        return String::new();
    }
    let mut markup = String::from(r#"<ul class="errorlist">"#);
    for error in errors {
        let _ = write!(markup, "<li>{}</li>", escape(&error));
    }
    markup.push_str("</ul>");
    markup
}

fn error_if(condition: bool, message: &str) -> WidgetErrors {
    condition.then(|| message.to_owned()).into_iter().collect()
}

fn text_errors(bound: bool, value: &str, max: usize, required: bool) -> WidgetErrors {
    if !bound {
        return Vec::new();
    }
    let length = value.trim().chars().count();
    if length > max {
        return vec![format!(
            "Ensure this value has at most {max} characters (it has {length})."
        )];
    }
    if required && value.trim().is_empty() {
        return vec!["This field is required.".into()];
    }
    error_if(value.contains('\0'), "Null characters are not allowed.")
}

fn email_errors(bound: bool, value: &str, required: bool) -> WidgetErrors {
    let errors = text_errors(bound, value, 254, required);
    if !errors.is_empty() {
        return errors;
    }
    error_if(
        bound && !value.trim().is_empty() && !valid_email(value.trim()),
        "Enter a valid email address.",
    )
}

fn school_choices<'a>(
    choices: &'a [SchoolChoice],
    current: &'a str,
    name: &'a str,
) -> Vec<(&'a str, &'a str)> {
    let mut choices: Vec<_> = choices
        .iter()
        .map(|s| (s.value.as_str(), s.name.as_str()))
        .collect();
    if !current.is_empty() && !choices.iter().any(|&(value, _)| current == value) {
        choices.push((
            current,
            if name.is_empty() {
                "Selected School"
            } else {
                name
            },
        ));
        choices.sort_by_cached_key(|&(_, label)| label.to_lowercase());
    }
    choices.insert(0, ("", "Select school"));
    choices
}

fn contact(
    registration: &Registration,
    schools: &[SchoolChoice],
    posted: Option<&Fields>,
    error: Option<&str>,
) -> Value {
    let mut form = Form::new("", posted);
    let school = form.value("school", &registration.school.value);
    let school_name = form.value("school_name", &registration.school.name);
    let email = form.value("email", &registration.email);
    let bound = posted.is_some();
    let school_required = error_if(bound && school.trim().is_empty(), "This field is required.");
    let email_errors = email_errors(bound, &email, true);
    let show_error = school_required.is_empty() && email_errors.is_empty();
    form.select(
        "school",
        &school_choices(schools, &school, &school_name),
        &[&school],
        r#" class="form-control" data-school-select="registration" required"#,
        false,
        school_required,
    );
    form.input(
        "email",
        "email",
        &registration.email,
        r#" maxlength="254" class="form-control" required"#,
        email_errors,
    );
    for (field, initial) in [
        ("dietary_requests", &registration.dietary_requests),
        ("housing_requests", &registration.housing_requests),
    ] {
        let value = form.value(field, initial);
        form.widget(field, format!(
            "<textarea name=\"{field}\" cols=\"40\" rows=\"3\" maxlength=\"2000\" class=\"form-control\" placeholder=\"Share any requests or details staff should know.\" id=\"id_{field}\">\n{}</textarea>",
            escape(&value),
        ), text_errors(bound, &value, 2000, false));
    }
    let errors = if show_error {
        error.into_iter().map(str::to_owned).collect()
    } else {
        Vec::new()
    };
    form.finish(errors)
}

fn formset_team(
    prefix: &str,
    team: &Team,
    schools: &[SchoolChoice],
    posted: Option<&Fields>,
    existing: bool,
) -> Value {
    let mut form = Form::new(prefix, posted);
    form.hidden("team_id", &number(team.id), "");
    let deleted = form.checked("DELETE", false);
    if existing || deleted {
        form.hidden("DELETE", "", "");
    }
    let name = form.value("name", &team.name);
    let bound = posted.is_some() && !deleted;
    let name_error = text_errors(bound, &name, 30, false);
    let mut has_field_errors = !name_error.is_empty();
    form.input(
        "name",
        "text",
        &team.name,
        r#" maxlength="30" class="form-control""#,
        name_error,
    );
    let seed = form.value("seed_choice", &team.seed.to_string());
    let seed_options = [
        ("0", "Unseeded"),
        ("1", "Free Seed"),
        ("2", "Half Seed"),
        ("3", "Full Seed"),
    ];
    let seed_errors = choice_errors(bound, &seed, &seed_options);
    has_field_errors |= !seed_errors.is_empty();
    form.select(
        "seed_choice",
        &seed_options,
        &[&seed],
        r#" class="form-control form-control-sm" data-team-seed="true""#,
        false,
        seed_errors,
    );
    let source = form.value(
        "team_school_source",
        if team.second_primary {
            "debater_two"
        } else {
            "debater_one"
        },
    );
    let sources = [("debater_one", "Debater 1"), ("debater_two", "Debater 2")];
    let source_errors = choice_errors(bound, &source, &sources);
    has_field_errors |= !source_errors.is_empty();
    form.select(
        "team_school_source",
        &sources,
        &[&source],
        r#" class="form-control form-control-sm""#,
        false,
        source_errors,
    );

    let mut names = Vec::new();
    let mut emails = Vec::new();
    let mut selected_schools = Vec::new();
    for (key, member) in [
        ("debater_one", &team.members[0]),
        ("debater_two", &team.members[1]),
    ] {
        let field = |name| format!("{key}_{name}");
        let value = form.value(&field("name"), &member.name);
        form.hidden(&field("id"), &number(member.id), "");
        form.hidden(
            &field("use_private_email"),
            if member.use_private_email { "on" } else { "" },
            "",
        );
        form.hidden(&field("apda_id"), &number(member.apda_id), "");
        form.hidden(
            &field("qualified"),
            if member.qualified {
                "True"
            } else if existing {
                "False"
            } else {
                ""
            },
            r#" value="""#,
        );
        let options = if value.is_empty() {
            vec![("", "Select a school first")]
        } else {
            vec![("", "Select a debater"), (&value, &value)]
        };
        let attrs = format!(
            r#" class="form-control" data-debater-input="{key}" data-apda-target="id_{}""#,
            form.name(&field("apda_id"))
        ) + &attribute("data-current-value", &value);
        let errors = text_errors(bound, &value, 30, false);
        has_field_errors |= !errors.is_empty();
        form.select(&field("name"), &options, &[&value], &attrs, false, errors);
        let email = form.value(&field("email"), &member.email);
        let use_private = form.checked(&field("use_private_email"), member.use_private_email);
        let masked = use_private && email.contains("***");
        let value_attr = if masked { "" } else { &email };
        let errors = email_errors(bound, value_attr, false);
        has_field_errors |= !errors.is_empty();
        let email_field = field("email");
        let markup = input_markup(
            "email",
            &form.name(&email_field),
            value_attr,
            r#" maxlength="254" class="form-control" placeholder="debater@example.com""#,
        );
        form.widget(&email_field, markup, errors);
        names.push(!value.trim().is_empty());
        emails.push(!value_attr.trim().is_empty() || use_private);
        let status = form.value(&field("novice_status"), &member.novice_status.to_string());
        let options = [("0", "Varsity"), ("1", "Novice")];
        let errors = choice_errors(bound, &status, &options);
        has_field_errors |= !errors.is_empty();
        form.select(
            &field("novice_status"),
            &options,
            &[&status],
            r#" class="form-control form-control-sm""#,
            false,
            errors,
        );
        form.checkbox(&field("needs_crash_housing"), member.needs_crash_housing);
        form.checkbox(&field("requests_wgm_housing"), member.requests_wgm_housing);
        let school = form.value(&field("school"), &member.school.value);
        let school_name = form.value(&field("school_name"), &member.school.name);
        let attrs = format!(
            r#" class="form-control" data-school-select="team" data-prefill-from-registration="true" data-name-id="id_{}""#,
            form.name(&field("name"))
        );
        form.select(
            &field("school"),
            &school_choices(schools, &school, &school_name),
            &[&school],
            &attrs,
            false,
            Vec::new(),
        );
        selected_schools.push(!school.is_empty());
    }
    let occupied = !name.trim().is_empty()
        || names.iter().any(|&field| field)
        || emails.iter().any(|&field| field);
    let errors = if !bound || !occupied || has_field_errors {
        Vec::new()
    } else if !names.iter().all(|&field| field) {
        vec!["Each team needs two debaters".into()]
    } else if !emails.iter().all(|&field| field) {
        vec!["Each debater needs an email".into()]
    } else if !selected_schools[usize::from(source == "debater_two")] {
        vec!["Select a school for the primary debater".into()]
    } else {
        error_if(
            !selected_schools.iter().all(|&field| field),
            "Select a school for each debater",
        )
    };
    let widgets = form.finish(errors);
    context! { deleted, ..widgets }
}

fn choice_errors(bound: bool, value: &str, choices: &[(&str, &str)]) -> WidgetErrors {
    if bound && !value.is_empty() && !choices.iter().any(|&(choice, _)| value == choice) {
        vec![format!(
            "Select a valid choice. {value} is not one of the available choices."
        )]
    } else {
        Vec::new()
    }
}

fn formset_judge(
    prefix: &str,
    judge: &Judge,
    schools: &[SchoolChoice],
    last_round: i32,
    posted: Option<&Fields>,
    existing: bool,
) -> Value {
    let mut form = Form::new(prefix, posted);
    form.hidden("registration_judge_id", &number(judge.id), "");
    form.hidden("judge_id", &number(judge.id), "");
    let deleted = form.checked("DELETE", false);
    if existing || deleted {
        form.hidden("DELETE", "", "");
    }
    let initial = judge.experience_display.as_deref().unwrap_or_default();
    let (name, email, experience) = (
        form.value("name", &judge.name),
        form.value("email", &judge.email),
        form.value("experience", initial),
    );
    let mut availability = Vec::new();
    for round in 0..=last_round {
        let (field, label) = if round == 0 {
            ("availability_outround".into(), "Outrounds".into())
        } else {
            (
                format!("availability_round_{round}"),
                format!("Round {round}"),
            )
        };
        form.checkbox(&field, judge.rounds.contains(&round));
        availability.push(context! { field => &form.widgets[&field], label });
    }
    let school_name = form.name("schools");
    let selected: Vec<_> = if let Some(posted) = posted {
        posted
            .all(&school_name)
            .iter()
            .map(String::as_str)
            .collect()
    } else {
        judge.schools.iter().map(|s| s.value.as_str()).collect()
    };
    let mut choices: Vec<_> = schools
        .iter()
        .map(|s| (s.value.as_str(), s.name.as_str()))
        .collect();
    for school in &judge.schools {
        if !choices.iter().any(|&(value, _)| value == school.value) {
            choices.push((&school.value, &school.name));
        }
    }
    choices.sort_by_cached_key(|&(_, name)| name.to_lowercase());
    let bound = posted.is_some()
        && !deleted
        && (existing
            || !name.is_empty()
            || !email.is_empty()
            || !experience.is_empty()
            || !selected.is_empty()
            || (0..=last_round).any(|round| {
                form.checked(
                    &if round == 0 {
                        "availability_outround".into()
                    } else {
                        format!("availability_round_{round}")
                    },
                    false,
                )
            }));
    form.input(
        "name",
        "text",
        &judge.name,
        r#" maxlength="30" class="form-control""#,
        text_errors(bound, &name, 30, true),
    );
    form.input(
        "email",
        "email",
        &judge.email,
        r#" maxlength="254" class="form-control" placeholder="Email (sent to judge, not saved)""#,
        email_errors(bound, &email, true),
    );
    let experience_errors = if !bound {
        Vec::new()
    } else if experience.trim().is_empty() {
        vec!["This field is required.".into()]
    } else {
        match integer_input(experience.trim()) {
            Ok(0..=10) => Vec::new(),
            Ok(n) if n < 0 => vec!["Ensure this value is greater than or equal to 0.".into()],
            Ok(_) => vec!["Ensure this value is less than or equal to 10.".into()],
            Err(_) => vec!["Enter a whole number.".into()],
        }
    };
    form.input(
        "experience",
        "number",
        initial,
        r#" min="0" max="10" step="1" class="form-control" placeholder="0-10""#,
        experience_errors,
    );
    form.checkbox("needs_crash_housing", judge.needs_crash_housing);
    form.checkbox("requests_wgm_housing", judge.requests_wgm_housing);
    let school_errors: Vec<_> = selected
        .iter()
        .find(|&&value| {
            bound
                && !value.starts_with("custom:")
                && !choices.iter().any(|&(choice, _)| value == choice)
        })
        .map(|value| format!("Select a valid choice. {value} is not one of the available choices."))
        .into_iter()
        .collect();
    form.select("schools", &choices, &selected, r#" class="form-control judge-schools-select" data-judge-school-select="true" data-prefill-from-registration="true""#, true, school_errors);
    let widgets = form.finish(Vec::new());
    context! { availability_fields => availability, deleted, ..widgets }
}

fn number(value: Option<i32>) -> String {
    value.map_or_else(String::new, |value| value.to_string())
}

struct Management {
    markup: String,
    total: usize,
    initial: usize,
}

fn management(prefix: &str, count: usize, max: usize, fields: Option<&Fields>) -> Management {
    let mut markup = String::new();
    let mut total = count;
    let mut initial = count;
    for (field, default) in [
        ("TOTAL_FORMS", count),
        ("INITIAL_FORMS", count),
        ("MIN_NUM_FORMS", 0),
        ("MAX_NUM_FORMS", max),
    ] {
        let name = format!("{prefix}-{field}");
        let value = fields.map_or_else(
            || default.to_string(),
            |posted| posted.get(&name).to_owned(),
        );
        match field {
            "TOTAL_FORMS" => total = value.parse::<usize>().unwrap_or(0).min(max),
            "INITIAL_FORMS" => initial = value.parse::<usize>().unwrap_or(0).min(max),
            _ => {}
        }
        markup.push_str(&input_markup("hidden", &name, &value, ""));
    }
    Management {
        markup,
        total,
        initial,
    }
}

pub(super) fn context_for_forms(
    registration: &Registration,
    schools: &[SchoolChoice],
    last_round: i32,
    posted: Option<&Fields>,
    error: Option<&str>,
) -> Value {
    let blank_team = Team::default();
    let blank_judge = Judge::default();
    let tm = management("teams", registration.teams.len(), MAX_TEAMS, posted);
    let jm = management("judges", registration.judges.len(), MAX_JUDGES, posted);
    let teams: Vec<_> = (0..tm.total)
        .map(|i| {
            formset_team(
                &format!("teams-{i}-"),
                registration.teams.get(i).unwrap_or(&blank_team),
                schools,
                posted,
                i < tm.initial,
            )
        })
        .collect();
    let judges: Vec<_> = (0..jm.total)
        .map(|i| {
            formset_judge(
                &format!("judges-{i}-"),
                registration.judges.get(i).unwrap_or(&blank_judge),
                schools,
                last_round,
                posted,
                i < jm.initial,
            )
        })
        .collect();
    let top_error = error.filter(|message| {
        message.starts_with("Registration updates are") || message.starts_with("New registrations")
    });
    let registration_form = contact(registration, schools, posted, top_error);
    let has_errors = |form: &Value| {
        form.get_attr("has_errors")
            .is_ok_and(|value| value.is_true())
    };
    let row_has_errors = teams.iter().chain(&judges).any(has_errors);
    let team_error = error.filter(|message| {
        let already_reported =
            if message.starts_with("Select a school, enter a valid contact email") {
                has_errors(&registration_form)
            } else {
                row_has_errors
                    && (message.starts_with("Enter a valid name, email")
                        || message.starts_with("Enter a valid team name and seed")
                        || message.starts_with("Each team")
                        || message.starts_with("Each debater")
                        || *message == "Check the registration form and try again.")
            };
        top_error.is_none() && !already_reported
    });
    let judge_error = error.filter(|message| *message == "Add at least one team or one judge");
    context! {
        registration_form,
        team_formset => context! {
            forms => teams, management_form => tm.markup, non_form_errors => team_error.into_iter().collect::<Vec<_>>(),
            empty_form => formset_team("teams-__prefix__-", &blank_team, &[], None, false),
        },
        judge_formset => context! {
            forms => judges, management_form => jm.markup, non_form_errors => judge_error.into_iter().collect::<Vec<_>>(),
            empty_form => formset_judge("judges-__prefix__-", &blank_judge, schools, last_round, None, false),
        },
    }
}

pub(super) fn summary(registration: &Registration, code: &str) -> Value {
    let teams: Vec<_> = registration.teams.iter().map(|team| context! {
        name => &team.name, seed => team.seed, FREE_SEED => 1,
        debaters => team.members.iter().filter(|member| member.id.is_some()).map(|member| &member.name)
            .chain(&team.extra_member_names).map(|name| context! { name }).collect::<Vec<_>>(),
    }).collect();
    let judges: Vec<_> = registration
        .judges
        .iter()
        .map(|judge| context! { name => &judge.name })
        .collect();
    context! {
        school => context! {name => &registration.school.name},
        email => &registration.email,
        herokunator_code => code,
        teams, judges,
    }
}

pub(super) fn revision_input(revision: Option<&str>) -> Value {
    Value::from_safe_string(revision.map_or_else(String::new, |value| {
        input_markup("hidden", "revision", value, "")
    }))
}
