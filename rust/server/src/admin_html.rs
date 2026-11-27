use std::sync::LazyLock;

use axum::{
    http::{HeaderMap, StatusCode},
    response::{Html, Response},
};
use minijinja::{Environment, Value, context};
use serde::{Deserialize, Serialize};

use crate::{
    App, access, legacy_html, legacy_session::Actor, login::TemplateCsrf, private,
    settings::Settings, unavailable,
};

#[derive(Clone, Deserialize, Serialize)]
pub struct Application {
    pub app_label: String,
    pub name: String,
    app_url: String,
    models: Vec<Model>,
}

#[derive(Clone, Deserialize, Serialize)]
struct Model {
    name: String,
    object_name: String,
    admin_url: String,
    add_url: Option<String>,
    view_only: bool,
}

static APPLICATIONS: LazyLock<Vec<Application>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../templates/django_admin/applications.json"))
        .expect("valid Django admin menu")
});

static TEMPLATE_ENV: LazyLock<Environment<'static>> = LazyLock::new(|| {
    let mut env = legacy_html::environment();
    templates(&mut env);
    env
});

pub fn render_fragment(template: &str, page: Value) -> Result<String, minijinja::Error> {
    TEMPLATE_ENV.get_template(template)?.render(page)
}

pub struct Shell {
    pub applications: Vec<Application>,
    pub context: Value,
    csrf: TemplateCsrf,
}

impl Shell {
    pub async fn load(
        app: &App,
        actor: Actor,
        headers: &HeaderMap,
        path: &str,
    ) -> Result<Self, Box<Response>> {
        let Actor::User {
            id,
            board: false,
            staff: true,
            superuser: true,
        } = actor
        else {
            return Err(Box::new(access::access_denied()));
        };
        let csrf = TemplateCsrf::new(headers).map_err(|_| Box::new(unavailable()))?;
        let mut db = app
            .db
            .acquire()
            .await
            .map_err(|_| Box::new(unavailable()))?;
        let (short_name, has_usable_password) = sqlx::query_as::<_, (String, bool)>(
            "SELECT IF(first_name = '', username, first_name), LEFT(password, 1) <> '!'
             FROM auth_user WHERE id = ?",
        )
        .bind(id)
        .fetch_one(&mut *db)
        .await
        .map_err(|_| Box::new(unavailable()))?;
        let settings = Settings::load(&mut db, &["outround_pairing_mode"])
            .await
            .map_err(|_| Box::new(unavailable()))?;
        let has_registration_config: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM registration_registrationconfig)")
                .fetch_one(&mut *db)
                .await
                .map_err(|_| Box::new(unavailable()))?;
        let challenges = matches!(settings.integer("outround_pairing_mode", 0), 1 | 2);
        let mut applications = APPLICATIONS.clone();
        for application in &mut applications {
            application
                .models
                .retain(|model| model.object_name != "OutroundChallenge" || challenges);
            if has_registration_config
                && let Some(model) = application
                    .models
                    .iter_mut()
                    .find(|model| model.object_name == "RegistrationConfig")
            {
                model.add_url = None;
            }
        }
        let context = context! {
            available_apps => &applications,
            has_permission => true,
            is_nav_sidebar_enabled => true,
            request => context! { path },
            site_url => "/",
            user => context! {
                short_name,
                is_active => true,
                is_staff => true,
                has_usable_password,
            },
            csrf_input => csrf.input(),
        };
        Ok(Self {
            applications,
            context,
            csrf,
        })
    }

    pub fn render(self, template: &str, page: Value, headers: &HeaderMap) -> Response {
        let shell = self.context;
        let mut response = match render_fragment(template, context! { ..shell, ..page }) {
            Ok(body) => private((StatusCode::OK, Html(body))),
            Err(error) => {
                eprintln!("Failed to render {template}: {}", error.kind());
                unavailable()
            }
        };
        self.csrf.set_cookie(&mut response, headers);
        response
    }
}

fn templates(env: &mut Environment<'static>) {
    env.add_filter("admin_spaceless", |value: String| {
        let mut output = String::with_capacity(value.len());
        let mut start = 0;
        for (index, ch) in value.char_indices() {
            if ch == '>' {
                output.push_str(&value[start..=index]);
                let rest = &value[index + 1..];
                let next = rest.trim_start_matches(char::is_whitespace);
                start = if next.starts_with('<') {
                    value.len() - next.len()
                } else {
                    index + 1
                };
            }
        }
        output.push_str(&value[start..]);
        Value::from_safe_string(output.trim().to_owned())
    });
    env.add_filter("admin_truncate", |value: String, limit: usize| {
        let words: Vec<_> = value.split_whitespace().collect();
        if words.len() > limit {
            format!("{} …", words[..limit].join(" "))
        } else {
            words.join(" ")
        }
    });
    env.add_filter("capfirst", |value: String| {
        let mut characters = value.chars();
        match characters.next() {
            Some(first) => format!("{}{}", first.to_uppercase(), characters.as_str()),
            None => value,
        }
    });
    macro_rules! upstream {
        ($name:literal) => {
            (
                concat!("admin/", $name),
                include_str!(concat!("../templates/django_admin/", $name)),
            )
        };
    }
    for (name, source) in [
        upstream!("base.html"),
        upstream!("app_index.html"),
        upstream!("app_list.html"),
        upstream!("nav_sidebar.html"),
        upstream!("color_theme_toggle.html"),
        upstream!("change_form.html"),
        upstream!("change_list.html"),
        upstream!("change_list_results.html"),
        upstream!("change_list_object_tools.html"),
        upstream!("actions.html"),
        upstream!("pagination.html"),
        upstream!("object_history.html"),
        upstream!("delete_confirmation.html"),
        upstream!("delete_selected_confirmation.html"),
        upstream!("includes/object_delete_summary.html"),
        upstream!("change_form_object_tools.html"),
        upstream!("submit_line.html"),
        upstream!("includes/fieldset.html"),
        upstream!("prepopulated_fields_js.html"),
        upstream!("widgets/related_widget_wrapper.html"),
        (
            "admin/base_site.html",
            include_str!("../../../mittab/templates/admin/base_site.html"),
        ),
        (
            "admin/index.html",
            include_str!("../../../mittab/templates/admin/index.html"),
        ),
        (
            "admin/score_widget.html",
            include_str!("../templates/admin_score_widget.html"),
        ),
    ] {
        env.add_template_owned(name, translate(source))
            .unwrap_or_else(|error| panic!("admin template {name}: {error}"));
    }
}

fn translate(source: &str) -> String {
    let source = source
        .replace("{{ block.super }}", "{{ super() }}")
        .replace("LANGUAGE_CODE|default:\"en-us\"", "\"en-us\"")
        .replace("LANGUAGE_BIDI|yesno:'rtl,ltr,auto'", "\"ltr\"")
        .replace("request.path|urlencode", "request.path")
        .replace("original|truncatewords:\"18\"", "original|admin_truncate(18)")
        .replace(
            "{% blocktranslate with name=app.name %}Models in the {{ name }} application{% endblocktranslate %}",
            "Models in the {{ app.name }} application",
        )
        .replace(
            "{% blocktranslate with name=opts.verbose_name %}Add {{ name }}{% endblocktranslate %}",
            "Add {{ opts.verbose_name }}",
        )
        .replace(
            "{% blocktranslate count counter=errors|length %}Please correct the error below.{% plural %}Please correct the errors below.{% endblocktranslate %}",
            "{% if errors|length == 1 %}Please correct the error below.{% else %}Please correct the errors below.{% endif %}",
        )
        .replace("{{ message|capfirst }}", "{{ message.text|capfirst }}")
        .replace("{{ field.field }}", "{{ field.widget|safe }}")
        .replace("{{ field.label_tag }}", "{{ field.label_tag|safe }}")
        .replace("in fieldset %}", "in fieldset.lines %}")
        .replace("in line %}", "in line.fields %}")
        .replace("{{ line.errors }}", "{{ line.errors|safe }}")
        .replace("{{ field.errors }}", "{{ field.errors|safe }}")
        .replace("{{ adminform.form.non_field_errors }}", "{{ non_field_errors|safe }}")
        .replace("{{ media }}", "{{ media|safe }}")
        .replace("{{ media.js }}", "{{ media.js|safe }}")
        .replace("{{ media.css }}", "{{ media.css|safe }}")
        .replace("{{ header.class_attrib }}", "{{ header.class_attrib|safe }}")
        .replace("{{ header.text|capfirst }}", "{{ header.text|safe }}")
        .replace("{{ item }}", "{{ item|safe }}")
        .replace("{{ field }}", "{{ field.widget|safe }}")
        .replace("in action_form %}", "in action_fields %}")
        .replace("action_index|default:0", "action_index|default(0)")
        .replace("header.ascending|yesno:'ascending,descending'", "('ascending' if header.ascending else 'descending')")
        .replace(
            "{% blocktranslate with cl.opts.verbose_name as name %}Add {{ name }}{% endblocktranslate %}",
            "Add {{ cl.opts.verbose_name }}",
        )
        .replace(
            "{% blocktranslate with cl.result_count as total_count %}Select all {{ total_count }} {{ module_name }}{% endblocktranslate %}",
            "Select all {{ cl.result_count }} {{ module_name }}",
        )
        .replace(
            "{% blocktranslate with priority_number=header.sort_priority %}Sorting priority: {{ priority_number }}{% endblocktranslate %}",
            "Sorting priority: {{ header.sort_priority }}",
        )
        .replace(
            "{% blocktranslate count counter=cl.formset.total_error_count %}Please correct the error below.{% plural %}Please correct the errors below.{% endblocktranslate %}",
            "{% if cl.formset.total_error_count == 1 %}Please correct the error below.{% else %}Please correct the errors below.{% endif %}",
        )
        .replace("object|truncatewords:\"18\"", "object|admin_truncate(18)")
        .replace("action.action_time|date:\"DATETIME_FORMAT\"", "action.action_time")
        .replace("|unlocalize", "")
        .replace("|unordered_list", "|safe")
        .replace(
            "{% blocktranslate with escaped_object=object %}",
            "",
        )
        .replace("{{ escaped_object }}", "{{ object }}")
        .replace(
            "{% blocktranslate count counter=action_list.paginator.count %}entry{% plural %}entries{% endblocktranslate %}",
            "{% if count == 1 %}entry{% else %}entries{% endif %}",
        )
        .replace("{{ action_list.paginator.count }}", "{{ count }}")
        .replace("action_list.paginator.ELLIPSIS", "'…'")
        .replace("action_list.paginator.num_pages", "pages")
        .replace("action_list.number", "page")
        .replace("{% blocktranslate %}", "")
        .replace("{% endblocktranslate %}", "");
    let mut translated = String::with_capacity(source.len());
    let mut rest = source.as_str();
    while let Some((before, tail)) = rest.split_once("{%") {
        translated.push_str(before);
        let (tag, after) = tail.split_once("%}").expect("closed Django admin tag");
        rest = after;
        let raw = tag.trim();
        let words: Vec<_> = raw.split_whitespace().collect();
        let literal = |word: &str| word.trim_matches(['\'', '"']).to_owned();
        match words.as_slice() {
            ["load", ..] | ["get_current_language", ..] | ["get_current_language_bidi", ..] => {}
            ["trans" | "translate", ..] => {
                let (_, value) = raw
                    .split_once(char::is_whitespace)
                    .expect("literal translation");
                translated.push_str(&literal(value.trim()));
            }
            ["static", path] => {
                translated.push_str("/static/");
                translated.push_str(&literal(path));
            }
            ["now", "\"Z\""] => translated.push('0'),
            ["firstof", "user.get_short_name", "user.get_username"] => {
                translated.push_str("{{ user.short_name }}")
            }
            ["csrf_token"] => translated.push_str("{{ csrf_input|safe }}"),
            ["url", "opts|admin_urlname:'change'", ..] => {
                translated.push_str("{{ object_url }}change/");
            }
            ["url", "opts|admin_urlname:'history'", ..] => {
                translated.push_str("{% set history_url = object_url ~ 'history/' %}")
            }
            ["url", "opts|admin_urlname:'delete'", ..] => {
                translated.push_str("{% set delete_url = object_url ~ 'delete/' %}")
            }
            [
                "url",
                "opts|admin_urlname:'changelist'",
                "as",
                "changelist_url",
            ] => translated.push_str("{% set changelist_url = model_url %}"),
            ["url", "opts|admin_urlname:'changelist'"] => translated.push_str("{{ model_url }}"),
            ["url", "cl.opts|admin_urlname:'add'", "as", "add_url"] => {
                translated.push_str("{% set add_url = model_url ~ 'add/' %}");
            }
            [
                "url",
                "'admin:app_list'",
                "app_label=opts.app_label" | "app_label=cl.opts.app_label",
            ] => translated.push_str("{{ app_url }}"),
            ["url", "'django-admindocs-docroot'", "as", "docsroot"] => {}
            ["url", path] => translated.push_str(match literal(path).as_str() {
                "admin:index" => "/admin/",
                "admin:logout" => "/admin/logout/",
                "admin:password_change" => "/admin/password_change/",
                "admin:jsi18n" => "/admin/jsi18n/",
                "tournament_todo" => "/setup/tournament-todo/",
                path => panic!("unsupported Django admin URL {path}"),
            }),
            ["add_preserved_filters", value, ..] => {
                translated.push_str(&format!("{{{{ {value} }}}}{{{{ filter_suffix }}}}"))
            }
            ["change_form_object_tools"] => {
                translated.push_str("{% include 'admin/change_form_object_tools.html' %}")
            }
            ["prepopulated_fields_js"] => {
                translated.push_str("{% include 'admin/prepopulated_fields_js.html' %}")
            }
            ["submit_row"] => translated.push_str("{% include 'admin/submit_line.html' %}"),
            ["change_list_object_tools"] => {
                translated.push_str("{% include 'admin/change_list_object_tools.html' %}")
            }
            ["admin_actions"] => translated.push_str("{% include 'admin/actions.html' %}"),
            ["result_list", "cl"] => {
                translated.push_str("{% include 'admin/change_list_results.html' %}")
            }
            ["pagination", "cl"] => translated.push_str("{% include 'admin/pagination.html' %}"),
            ["paginator_number", "cl", "i"] => translated.push_str("{{ i|safe }}"),
            ["search_form", "cl"] => translated.push_str("{{ search_form|safe }}"),
            ["date_hierarchy", "cl"] => translated.push_str("{{ date_hierarchy|safe }}"),
            ["admin_list_filter", "cl", "spec"] => translated.push_str("{{ spec|safe }}"),
            ["spaceless"] => translated.push_str("{% filter admin_spaceless %}"),
            ["endspaceless"] => translated.push_str("{% endfilter %}"),
            ["block", name] => {
                translated.push_str(&format!("{{% block {} %}}", name.replace('-', "_")))
            }
            ["endblock", _] => translated.push_str("{% endblock %}"),
            [
                "include",
                name,
                "with",
                "app_list=available_apps",
                "show_changelinks=False",
            ] => {
                translated.push_str(&format!("{{% with app_list=available_apps, show_changelinks=false %}}{{% include {name} %}}{{% endwith %}}"));
            }
            [
                "include",
                name,
                "with",
                "app_list=app_list",
                "show_changelinks=True",
            ] => {
                translated.push_str(&format!(
                    "{{% with show_changelinks=true %}}{{% include {name} %}}{{% endwith %}}"
                ));
            }
            _ => {
                translated.push_str("{%");
                translated.push_str(tag);
                translated.push_str("%}");
            }
        }
    }
    translated.push_str(rest);
    translated
}
