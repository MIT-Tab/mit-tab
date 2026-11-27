use std::sync::LazyLock;

use axum::{
    http::{HeaderMap, StatusCode},
    response::{Html, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{Datelike, NaiveDateTime, Timelike};
use minijinja::{Environment, Value, context};
use ring::{
    hmac,
    rand::{SecureRandom, SystemRandom},
};
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access, legacy_html,
    legacy_session::{Actor, cookie},
    login::{self, TemplateCsrf},
    private,
    settings::Settings,
    unavailable,
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
    clear_notice: bool,
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
        let challenges = model_enabled(&mut db, "tab", "outroundchallenge")
            .await
            .map_err(|_| Box::new(unavailable()))?;
        let has_registration_config: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM registration_registrationconfig)")
                .fetch_one(&mut *db)
                .await
                .map_err(|_| Box::new(unavailable()))?;
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
        let notice_cookie = cookie(headers, ADMIN_NOTICE);
        let messages = if let Some(value) = notice_cookie {
            notice(&mut db, &app.admin_notice_key, headers, id, value)
                .await
                .map_err(|_| Box::new(unavailable()))?
                .map(|text| [context! { text => Value::from_safe_string(text), tags => "success" }])
        } else {
            None
        };
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
            messages,
        };
        Ok(Self {
            applications,
            context,
            csrf,
            clear_notice: notice_cookie.is_some(),
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
        if self.clear_notice {
            login::set_cookie(
                &mut response,
                ADMIN_NOTICE,
                "",
                0,
                login::secure_request(headers),
                true,
            );
        }
        response
    }
}

pub async fn model_enabled(
    db: &mut MySqlConnection,
    application: &str,
    model: &str,
) -> Result<bool, sqlx::Error> {
    if (application, model) != ("tab", "outroundchallenge") {
        return Ok(true);
    }
    let settings = Settings::load(db, &["outround_pairing_mode"]).await?;
    Ok(matches!(
        settings.value("outround_pairing_mode"),
        Some(crate::settings::SettingValue::Number(1 | 2))
    ))
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
    env.add_filter("capfirst", |value: String| capitalized(&value));
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
        upstream!("auth/user/add_form.html"),
        upstream!("auth/user/change_password.html"),
        upstream!("change_list.html"),
        upstream!("change_list_results.html"),
        upstream!("change_list_object_tools.html"),
        upstream!("actions.html"),
        upstream!("search_form.html"),
        upstream!("filter.html"),
        upstream!("pagination.html"),
        upstream!("object_history.html"),
        upstream!("delete_confirmation.html"),
        upstream!("delete_selected_confirmation.html"),
        upstream!("includes/object_delete_summary.html"),
        upstream!("change_form_object_tools.html"),
        upstream!("submit_line.html"),
        upstream!("includes/fieldset.html"),
        upstream!("edit_inline/tabular.html"),
        upstream!("prepopulated_fields_js.html"),
        upstream!("popup_response.html"),
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
        (
            "admin/model_select.html",
            include_str!("../templates/admin_model_select.html"),
        ),
    ] {
        env.add_template_owned(name, translate(source))
            .unwrap_or_else(|error| panic!("admin template {name}: {error}"));
    }
}

fn translate(source: &str) -> String {
    let source = source
        .replace("{{ block.super }}", "{{ super() }}")
        .replace(
            "{% csrf_token %}{% block form_top %}",
            r#"{% csrf_token %}{% if pairing_revision %}<input type="hidden" name="mittab_pairing_revision" value="{{ pairing_revision }}">{% endif %}{% if registration_revision %}<input type="hidden" name="mittab_registration_revision" value="{{ registration_revision }}">{% endif %}{% if registration_delete_revision %}<input type="hidden" name="mittab_registration_delete_revision" value="{{ registration_delete_revision }}">{% endif %}{% if resource_revision %}<input type="hidden" name="{{ resource_revision_name }}" value="{{ resource_revision }}">{% endif %}{% block form_top %}"#,
        )
        .replace(
            r#"<input type="hidden" name="post" value="yes">"#,
            r#"<input type="hidden" name="post" value="yes">{% if delete_scope %}<input type="hidden" name="mittab_delete_scope" value="{{ delete_scope }}">{% endif %}"#,
        )
        .replace("{% blocktranslate with filter_title=title %} By {{ filter_title }} {% endblocktranslate %}", " By {{ title }} ")
        .replace("{% blocktranslate count counter=cl.result_count %}{{ counter }} result{% plural %}{{ counter }} results{% endblocktranslate %}", "{{ cl.result_count }} result{% if cl.result_count != 1 %}s{% endif %}")
        .replace("{% blocktranslate with full_result_count=cl.full_result_count %}{{ full_result_count }} total{% endblocktranslate %}", "{{ cl.full_result_count }} total")
        .replace("in cl.params.items %}", "in cl.params %}")
        .replace("pair.0", "pair[0]")
        .replace("pair.1", "pair[1]")
        .replace("|iriencode", "")
        .replace("{{ field.contents }}", "{{ field.contents|safe }}")
        .replace("in inline_admin_formset %}", "in inline_admin_formset.rows %}")
        .replace("in inline_admin_form %}", "in inline_admin_form.fieldsets %}")
        .replace("forloop.last", "loop.last")
        .replace("forloop.counter0", "loop.index0")
        .replace("{{ inline_admin_form|cell_count }}", "{{ inline_admin_formset.cell_count }}")
        .replace("inline_admin_formset.has_change_permission|yesno:'inlinechangelink,inlineviewlink'", "('inlinechangelink' if inline_admin_formset.has_change_permission else 'inlineviewlink')")
        .replace("field.help_text|striptags", "field.help_text")
        .replace("{{ field.field.errors.as_ul }}", "{{ field.errors|safe }}")
        .replace("{{ cl.formset.management_form }}", "{{ cl.formset.management_form|safe }}")
        .replace("{{ cl.formset.non_form_errors }}", "{{ cl.formset.non_form_errors|default('')|safe }}")
        .replace("{{ inline_admin_formset.formset.management_form }}", "{{ inline_admin_formset.formset.management_form|safe }}")
        .replace("{{ inline_admin_formset.formset.non_form_errors }}", "{{ inline_admin_formset.formset.non_form_errors|safe }}")
        .replace("{{ inline_admin_form.form.non_field_errors }}", "{{ inline_admin_form.form.non_field_errors|safe }}")
        .replace("{{ inline_admin_form.pk_field.field }}", "{{ inline_admin_form.pk_field.field|safe }}")
        .replace("{{ inline_admin_form.fk_field.field }}", "{{ inline_admin_form.fk_field.field|safe }}")
        .replace("{{ inline_admin_form.deletion_field.field }}", "{{ inline_admin_form.deletion_field.field|safe }}")
        .replace("{{ original.get_username }}", "{{ original }}")
        .replace("{% blocktranslate with username=original %}", "")
        .replace("<strong>{{ username }}</strong>", "<strong>{{ original }}</strong>")
        .replace(
            "{% blocktranslate count counter=form.errors.items|length %}Please correct the error below.{% plural %}Please correct the errors below.{% endblocktranslate %}",
            "{% if form.errors|length == 1 %}Please correct the error below.{% else %}Please correct the errors below.{% endif %}",
        )
        .replace("{{ form.password1 }}", "{{ form.password1.widget|safe }}")
        .replace("{{ form.password2 }}", "{{ form.password2.widget|safe }}")
        .replace("{{ form.password1.errors }}", "{{ form.password1.errors|safe }}")
        .replace("{{ form.password2.errors }}", "{{ form.password2.errors|safe }}")
        .replace("{{ form.password1.label_tag }}", "{{ form.password1.label_tag|safe }}")
        .replace("{{ form.password2.label_tag }}", "{{ form.password2.label_tag|safe }}")
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
        .replace("{{ message|capfirst }}", "{{ message.text }}")
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
            [
                "url",
                "inline_admin_form.model_admin.opts|admin_urlname:'change'",
                ..,
            ] => {
                translated.push_str("{{ inline_admin_form.change_url }}");
            }
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
            ["add_preserved_filters", "changelist_url"] => {
                translated.push_str("{{ changelist_url }}{{ close_suffix }}")
            }
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

const ADMIN_MEDIA: &[&str] = &[
    "vendor/jquery/jquery.min.js",
    "jquery.init.js",
    "core.js",
    "admin/RelatedObjectLookups.js",
    "actions.js",
    "urlify.js",
    "prepopulate.js",
    "vendor/xregexp/xregexp.min.js",
];

pub fn scripts() -> String {
    ADMIN_MEDIA
        .iter()
        .map(|script| format!("<script src=\"/static/admin/js/{script}\"></script>"))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn filtered_select_scripts() -> String {
    form_scripts(true, false)
}

pub fn registration_admin_scripts() -> String {
    let mut scripts = ADMIN_MEDIA.to_vec();
    scripts.insert(3, "inlines.js");
    scripts
        .iter()
        .map(|script| format!("<script src=\"/static/admin/js/{script}\"></script>"))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn account_editor_scripts() -> String {
    form_scripts(true, true)
}

fn form_scripts(filtered_select: bool, dates: bool) -> String {
    ADMIN_MEDIA
        .iter()
        .flat_map(|&script| match script {
            "vendor/jquery/jquery.min.js" if dates => [Some(script), Some("calendar.js")],
            "jquery.init.js" if dates => [Some(script), Some("admin/DateTimeShortcuts.js")],
            "admin/RelatedObjectLookups.js" if filtered_select => {
                [Some(script), Some("SelectBox.js")]
            }
            "actions.js" if filtered_select => [Some(script), Some("SelectFilter2.js")],
            _ => [Some(script), None],
        })
        .flatten()
        .map(|script| format!("<script src=\"/static/admin/js/{script}\"></script>"))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn preserved_filters(query: &str) -> String {
    if query.is_empty() {
        String::new()
    } else {
        format!(
            "?{}",
            url::form_urlencoded::Serializer::new(String::new())
                .append_pair("_changelist_filters", query)
                .finish()
        )
    }
}

pub fn paginator_links(page: u32, pages: u32, href: impl Fn(u32) -> String) -> Vec<String> {
    let mut links = Vec::new();
    let mut previous = 0;
    let numbers = if pages <= 10 {
        (1..=pages).collect()
    } else {
        (1..=2)
            .chain(page.saturating_sub(3).max(1)..=page.saturating_add(3).min(pages))
            .chain(pages - 1..=pages)
            .collect::<std::collections::BTreeSet<_>>()
    };
    for number in numbers {
        if number > previous + 1 {
            links.push("… ".into());
        }
        links.push(if number == page {
            format!(r#"<span class="this-page">{number}</span> "#)
        } else {
            let href = legacy_html::escape(&href(number));
            let end = if number == pages {
                r#" class="end""#
            } else {
                ""
            };
            format!(r#"<a href="{href}"{end}>{number}</a> "#)
        });
        previous = number;
    }
    links
}

pub fn django_datetime(time: NaiveDateTime) -> String {
    let month = [
        "Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.",
        "Dec.",
    ][time.month0() as usize];
    let clock = match (time.hour(), time.minute()) {
        (0, 0) => "midnight".into(),
        (12, 0) => "noon".into(),
        (hour, minute) => {
            let suffix = if hour < 12 { "a.m." } else { "p.m." };
            let hour = (hour + 11) % 12 + 1;
            if minute == 0 {
                format!("{hour} {suffix}")
            } else {
                format!("{hour}:{minute:02} {suffix}")
            }
        }
    };
    format!("{month} {}, {}, {clock}", time.day(), time.year())
}

pub fn admin_change_message(message: &str) -> String {
    let Ok(entries) = serde_json::from_str::<Vec<serde_json::Value>>(message) else {
        return message.into();
    };
    let messages = entries
        .iter()
        .filter_map(|entry| {
            let inline_name = |message: &serde_json::Value| {
                Some(format!(
                    "{} “{}”",
                    message["name"].as_str()?,
                    message["object"].as_str()?
                ))
            };
            if let Some(added) = entry.get("added") {
                return Some(
                    inline_name(added)
                        .map_or_else(|| "Added.".into(), |name| format!("Added {name}.")),
                );
            }
            if let Some(deleted) = entry.get("deleted") {
                return Some(
                    inline_name(deleted)
                        .map_or_else(|| "Deleted.".into(), |name| format!("Deleted {name}.")),
                );
            }
            let changed = &entry["changed"];
            let fields: Vec<_> = changed["fields"]
                .as_array()?
                .iter()
                .filter_map(serde_json::Value::as_str)
                .collect();
            let (last, rest) = fields.split_last()?;
            let fields = if rest.is_empty() {
                (*last).to_owned()
            } else {
                format!("{} and {last}", rest.join(", "))
            };
            Some(inline_name(changed).map_or_else(
                || format!("Changed {fields}."),
                |name| format!("Changed {fields} for {name}."),
            ))
        })
        .collect::<Vec<_>>();
    if messages.is_empty() {
        "No fields changed.".into()
    } else {
        messages.join(" ")
    }
}

pub fn capitalized(value: &str) -> String {
    let mut letters = value.chars();
    match letters.next() {
        Some(first) => format!("{}{}", first.to_uppercase(), letters.as_str()),
        None => value.into(),
    }
}

const ADMIN_NOTICE: &str = "mittab_admin_notice";

pub async fn log_action(
    db: &mut MySqlConnection,
    model: &str,
    user: i32,
    id: i32,
    description: &str,
    flag: u8,
    message: String,
) -> Result<u64, sqlx::Error> {
    let result = sqlx::query(
        "INSERT INTO django_admin_log (action_time, object_id, object_repr, action_flag,
            change_message, content_type_id, user_id)
         VALUES (UTC_TIMESTAMP(6), ?, ?, ?, ?,
            (SELECT id FROM django_content_type WHERE app_label = ? AND model = ?), ?)",
    )
    .bind(id.to_string())
    .bind(description.chars().take(200).collect::<String>())
    .bind(flag)
    .bind(message)
    .bind(crate::admin_changelist::application(model))
    .bind(model)
    .bind(user)
    .execute(db)
    .await?;
    Ok(result.last_insert_id())
}

pub fn notice_key(secret: Option<&str>) -> Result<hmac::Key, &'static str> {
    if let Some(secret) = secret {
        let key = hmac::Key::new(hmac::HMAC_SHA256, secret.as_bytes());
        let derived = hmac::sign(&key, b"MIT-Tab Rust admin notices v1");
        return Ok(hmac::Key::new(hmac::HMAC_SHA256, derived.as_ref()));
    }
    let mut seed = [0; 32];
    SystemRandom::new()
        .fill(&mut seed)
        .map_err(|_| "Unable to initialize administration notices")?;
    Ok(hmac::Key::new(hmac::HMAC_SHA256, &seed))
}

pub fn redirect_with_notice(
    app: &App,
    headers: &HeaderMap,
    target: String,
    log: u64,
    contents: &str,
) -> Response {
    redirect_with_notice_for_session(
        app,
        headers,
        target,
        log,
        contents,
        cookie(headers, "sessionid"),
    )
}

pub fn redirect_with_notice_for_session(
    app: &App,
    headers: &HeaderMap,
    target: String,
    log: u64,
    contents: &str,
    session: Option<&str>,
) -> Response {
    let mut response = access::found(target);
    if let Some(session) = session {
        let body = format!("{log}.{contents}");
        let tag = hmac::sign(
            &app.admin_notice_key,
            format!("{session}:{body}").as_bytes(),
        );
        let encoded = URL_SAFE_NO_PAD.encode(tag.as_ref());
        login::set_cookie(
            &mut response,
            ADMIN_NOTICE,
            &format!("{body}.{encoded}"),
            60,
            login::secure_request(headers),
            true,
        );
    }
    response
}

async fn notice(
    db: &mut MySqlConnection,
    key: &hmac::Key,
    headers: &HeaderMap,
    user: i32,
    raw: &str,
) -> Result<Option<String>, sqlx::Error> {
    let Some(session) = cookie(headers, "sessionid") else {
        return Ok(None);
    };
    let Some((body, tag)) = raw.rsplit_once('.') else {
        return Ok(None);
    };
    let Ok(tag) = URL_SAFE_NO_PAD.decode(tag) else {
        return Ok(None);
    };
    if hmac::verify(key, format!("{session}:{body}").as_bytes(), &tag).is_err() {
        return Ok(None);
    }
    let Some((log, action)) = body.split_once('.') else {
        return Ok(None);
    };
    let Ok(log) = log.parse::<u64>() else {
        return Ok(None);
    };
    let data = sqlx::query_as::<_, (String, String, String, String, u8)>(
        "SELECT t.app_label, t.model, l.object_id, l.object_repr, l.action_flag
         FROM django_admin_log l JOIN django_content_type t ON t.id = l.content_type_id
         WHERE l.id = ? AND l.user_id = ? AND t.app_label IN ('tab', 'auth', 'registration')
           AND l.action_time > UTC_TIMESTAMP(6) - INTERVAL 1 MINUTE",
    )
    .bind(log)
    .bind(user)
    .fetch_optional(db)
    .await?;
    let Some((app, model, id, description, flag)) = data else {
        return Ok(None);
    };
    let model = match (app.as_str(), model.as_str()) {
        ("auth", "user") => crate::admin_auth_pages::USER,
        ("auth", "group") => crate::admin_auth_pages::GROUP,
        ("tab" | "registration", model) => {
            let Some(resource) = crate::admin_changelist::default_list(model) else {
                return Ok(None);
            };
            resource.model
        }
        _ => return Ok(None),
    };
    let singular = model.singular;
    let description = legacy_html::escape(&description);
    let response = match (flag, action) {
        (1 | 2, "save" | "continue" | "another") => {
            let verb = if flag == 1 { "added" } else { "changed" };
            let Ok(id) = id.parse::<i32>() else {
                return Ok(None);
            };
            let extra = match action {
                "continue" => " You may edit it again below.".into(),
                "another" => format!(" You may add another {singular} below."),
                _ => String::new(),
            };
            format!(
                r#"The {singular} “<a href="{}{id}/change/">{description}</a>” was {verb} successfully.{extra}"#,
                model.url()
            )
        }
        (2, "password") if model.name == "user" => "Password changed successfully.".into(),
        (2, list) if list.starts_with("list-") => {
            let Ok(count) = list[5..].parse::<usize>() else {
                return Ok(None);
            };
            if count == 1 {
                format!("1 {singular} was changed successfully.")
            } else {
                format!("{count} {} were changed successfully.", model.plural)
            }
        }
        (3, "delete") => format!("The {singular} “{description}” was deleted successfully."),
        (3, bulk) if bulk.starts_with("bulk-") => {
            let Ok(count) = bulk[5..].parse::<usize>() else {
                return Ok(None);
            };
            let name = if count == 1 { singular } else { model.plural };
            format!("Successfully deleted {count} {name}.")
        }
        _ => return Ok(None),
    };
    Ok(Some(response))
}
