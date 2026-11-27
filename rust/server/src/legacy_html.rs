use std::{collections::BTreeMap, fmt::Write, sync::LazyLock};

use axum::{
    http::StatusCode,
    response::{Html, Response},
};
use chrono::{NaiveDateTime, Timelike};
use minijinja::{Environment, UndefinedBehavior, Value, context};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    homepage_admin, html,
    legacy_session::Actor,
    private,
    settings::Settings,
    staff_roles::{self, Preset},
    unavailable,
};

static URLS: LazyLock<BTreeMap<String, String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../templates/legacy_navigation_urls.json"))
        .expect("valid legacy navigation URLs")
});

pub fn environment() -> Environment<'static> {
    let mut env = Environment::new();
    env.set_keep_trailing_newline(true);
    env.set_undefined_behavior(UndefinedBehavior::Chainable);
    env.set_formatter(|out, _, value| {
        if value.is_undefined() || value.is_none() {
            return Ok(());
        }
        if value.is_safe() {
            write!(out, "{value}")?;
        } else {
            out.write_str(&escape(&value.to_string()))?;
        }
        Ok(())
    });
    env.add_filter("escape", |value: Value| {
        if value.is_safe() {
            value
        } else {
            Value::from_safe_string(escape(&value.to_string()))
        }
    });
    env.add_filter("linebreaksbr", |value: Value| {
        let text = if value.is_safe() {
            value.to_string()
        } else {
            escape(&value.to_string())
        };
        Value::from_safe_string(
            text.replace("\r\n", "\n")
                .replace('\r', "\n")
                .replace('\n', "<br>"),
        )
    });
    env
}

static TEMPLATE_ENV: LazyLock<Environment<'static>> = LazyLock::new(|| {
    let mut env = environment();
    env.add_function("legacy_bundle", html::legacy_bundle);
    env.add_filter("django_default", |value: Value, fallback: Value| {
        if value.is_true() { value } else { fallback }
    });
    env.add_filter("django_pluralize", |value: i64, suffix: Option<String>| {
        if value == 1 {
            String::new()
        } else {
            suffix.unwrap_or_else(|| "s".into())
        }
    });
    env.add_filter("django_yesno", |value: Value, labels: String| {
        let mut choices = labels.split(',');
        let yes = choices.next().unwrap_or_default();
        let no = choices.next().unwrap_or_default();
        if value.is_true() {
            yes.into()
        } else {
            String::from(no)
        }
    });
    env.add_filter("django_date", django_date);
    env.add_filter("django_length", |value: Value| value.len().unwrap_or(0));
    env.add_filter("escapejs", |value: String| {
        let mut escaped = String::with_capacity(value.len());
        for character in value.chars() {
            match character {
                '\\'
                | '\''
                | '"'
                | '>'
                | '<'
                | '&'
                | '='
                | '-'
                | ';'
                | '`'
                | '\u{2028}'
                | '\u{2029}'
                | '\0'..='\u{001f}' => {
                    let _ = write!(escaped, "\\u{:04X}", character as u32);
                }
                _ => escaped.push(character),
            }
        }
        Value::from_safe_string(escaped)
    });
    env.add_function("legacy_active", |path: String, target: Value| {
        if target.as_str() == Some(path.as_str()) {
            "active"
        } else {
            ""
        }
    });
    for (name, template) in [
        (
            "base/__wide.html",
            include_str!("../../../mittab/templates/base/__wide.html"),
        ),
        (
            "base/__normal.html",
            include_str!("../../../mittab/templates/base/__normal.html"),
        ),
        (
            "base/_navigation.html",
            include_str!("../../../mittab/templates/base/_navigation.html"),
        ),
        (
            "base/_public_navigation.html",
            include_str!("../../../mittab/templates/base/_public_navigation.html"),
        ),
        (
            "public/access_error.html",
            include_str!("../../../mittab/templates/public/access_error.html"),
        ),
        (
            "public/home.html",
            include_str!("../../../mittab/templates/public/home.html"),
        ),
        (
            "public/_external_information.html",
            include_str!("../../../mittab/templates/public/_external_information.html"),
        ),
        (
            "public/motions.html",
            include_str!("../../../mittab/templates/public/motions.html"),
        ),
        (
            "public/public_ballots.html",
            include_str!("../../../mittab/templates/public/public_ballots.html"),
        ),
        (
            "public/public_speaker_rankings.html",
            include_str!("../../../mittab/templates/public/public_speaker_rankings.html"),
        ),
        (
            "public/public_team_results.html",
            include_str!("../../../mittab/templates/public/public_team_results.html"),
        ),
        (
            "public/outround_pairing.html",
            include_str!("../../../mittab/templates/public/outround_pairing.html"),
        ),
        (
            "public/pairing_display.html",
            include_str!("../../../mittab/templates/public/pairing_display.html"),
        ),
        (
            "public/missing_ballots.html",
            include_str!("../../../mittab/templates/public/missing_ballots.html"),
        ),
        (
            "public/_pairing_navigation.html",
            include_str!("../../../mittab/templates/public/_pairing_navigation.html"),
        ),
        (
            "batch_check_in/check_in.html",
            include_str!("../../../mittab/templates/batch_check_in/check_in.html"),
        ),
        (
            "batch_check_in/_checkin_pane.html",
            include_str!("../../../mittab/templates/batch_check_in/_checkin_pane.html"),
        ),
        (
            "batch_check_in/history.html",
            include_str!("../../../mittab/templates/batch_check_in/history.html"),
        ),
        (
            "common/list_data.html",
            include_str!("../../../mittab/templates/common/list_data.html"),
        ),
        (
            "common/_quick_search.html",
            include_str!("../../../mittab/templates/common/_quick_search.html"),
        ),
        (
            "apda_board/outrounds.html",
            include_str!("../../../mittab/templates/apda_board/outrounds.html"),
        ),
        (
            "apda_board/_pairing.html",
            include_str!("../../../mittab/templates/apda_board/_pairing.html"),
        ),
        (
            "outrounds/pairing_base.html",
            include_str!("../../../mittab/templates/outrounds/pairing_base.html"),
        ),
        (
            "outrounds/pairing_card.html",
            include_str!("../../../mittab/templates/outrounds/pairing_card.html"),
        ),
        (
            "outrounds/ballot.html",
            include_str!("../../../mittab/templates/outrounds/ballot.html"),
        ),
        (
            "outrounds/_form.html",
            include_str!("../../../mittab/templates/outrounds/_form.html"),
        ),
        (
            "outrounds/forum_result.html",
            include_str!("../../../mittab/templates/outrounds/forum_result.html"),
        ),
        (
            "pairing/judge_dropdown.html",
            include_str!("../../../mittab/templates/pairing/judge_dropdown.html"),
        ),
        (
            "pairing/room_dropdown.html",
            include_str!("../../../mittab/templates/pairing/room_dropdown.html"),
        ),
        (
            "pairing/team_dropdown.html",
            include_str!("../../../mittab/templates/pairing/team_dropdown.html"),
        ),
        (
            "pairing/pair_round.html",
            include_str!("../../../mittab/templates/pairing/pair_round.html"),
        ),
        (
            "pairing/confirm_re_pair.html",
            include_str!("../../../mittab/templates/pairing/confirm_re_pair.html"),
        ),
        (
            "pairing/pairing_control.html",
            include_str!("../../../mittab/templates/pairing/pairing_control.html"),
        ),
        (
            "pairing/_round_tabs.html",
            include_str!("../../../mittab/templates/pairing/_round_tabs.html"),
        ),
        (
            "pairing/_pairing_card.html",
            include_str!("../../../mittab/templates/pairing/_pairing_card.html"),
        ),
        (
            "pairing/_manual_history.html",
            include_str!("../../../mittab/templates/pairing/_manual_history.html"),
        ),
        (
            "common/_return_to_input.html",
            include_str!("../../../mittab/templates/common/_return_to_input.html"),
        ),
        (
            "tab/backup_list.html",
            include_str!("../../../mittab/templates/tab/backup_list.html"),
        ),
        (
            "ui/field.html",
            include_str!("../../../mittab/templates/ui/field.html"),
        ),
        (
            "public/staff_login.html",
            include_str!("../../../mittab/templates/public/staff_login.html"),
        ),
        (
            "tab/start_tournament.html",
            include_str!("../../../mittab/templates/tab/start_tournament.html"),
        ),
        (
            "tab/tournament_todo.html",
            include_str!("../../../mittab/templates/tab/tournament_todo.html"),
        ),
        (
            "tab/settings_form.html",
            include_str!("../../../mittab/templates/tab/settings_form.html"),
        ),
        (
            "tab/_custom_staff_role_form.html",
            include_str!("../../../mittab/templates/tab/_custom_staff_role_form.html"),
        ),
    ] {
        env.add_template_owned(name, translate(name, template))
            .expect("valid legacy template");
    }
    env
});

pub(crate) fn escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

fn tag_words(tag: &str) -> Vec<&str> {
    let mut words = Vec::new();
    let mut start = None;
    let mut quote = None;
    let mut escaped = false;
    for (index, character) in tag.char_indices() {
        match quote {
            Some(end) => {
                if escaped {
                    escaped = false;
                } else if character == '\\' {
                    escaped = true;
                } else if character == end {
                    quote = None;
                }
            }
            None if character.is_whitespace() => {
                if let Some(begin) = start.take() {
                    words.push(&tag[begin..index]);
                }
            }
            None => {
                start.get_or_insert(index);
                if matches!(character, '\'' | '"') {
                    quote = Some(character);
                }
            }
        }
    }
    if let Some(begin) = start {
        words.push(&tag[begin..]);
    }
    words
}

fn translate(name: &str, source: &str) -> String {
    let mut output = String::new();
    let mut rest = source;
    while let Some((before, tail)) = rest.split_once("{%") {
        output.push_str(before);
        let (raw, after) = tail.split_once("%}").expect("closed template tag");
        rest = after;
        let words = tag_words(raw);
        let argument = |text: &str| text.trim_matches(['\'', '"']).to_owned();
        match words.as_slice() {
            ["load", ..]
            | ["public_navigation_pages", ..]
            | ["motions_enabled", ..]
            | ["is_apda_board", ..]
            | ["tournament_logo_url", ..]
            | ["staff_can", ..] => {}
            ["url", name, "as", variable] => {
                let name = argument(name);
                assert!(URLS.contains_key(&name), "unknown legacy URL {name}");
                output.push_str(&format!("{{% set {variable} = urls.{name} %}}"));
            }
            ["url", "'next_outround'", "pending.type", "pending.previous_size"] => {
                output.push_str("/outround_pairing/pair/{{ pending.kind }}/{{ pending.previous_size }}/");
            }
            ["url", "'outround_pretty_pair'", "section.type_of_round"] => {
                output.push_str("/public/outrounds/{{ section.type_of_round }}/");
            }
            ["url", "'outround_pretty_pair'", division @ ("0" | "1")] => {
                output.push_str(&format!("/public/outrounds/{division}/"));
            }
            ["url", "'previous_pretty_pair'", "pairing_round"] => {
                output.push_str("/public/pairings/{{ pairing_round }}/");
            }
            ["return_to_input", "return_path"] => output.push_str(
                "{% with redirect_target=return_path %}{% include 'common/_return_to_input.html' %}{% endwith %}",
            ),
            ["url", "'board_outround_edit'", "pairing.id"] => {
                output.push_str("/apda-board/outround/{{ pairing.id }}/");
            }
            ["url", "'view_round'", "number"] => output.push_str("/round/{{ number }}/"),
            ["url", "'board_inrounds'", "number"] => {
                output.push_str("/apda-board/inrounds/{{ number }}/");
            }
            ["url", "'view_judge'", "judge.id"] => output.push_str("/judge/{{ judge.id }}/"),
            ["return_to_input"] => output.push_str(
                "{{ round_input|safe }}{% with redirect_target=return_path|default(request.path) %}{% include 'common/_return_to_input.html' %}{% endwith %}",
            ),
            ["return_to_input", "request.path"] => output.push_str(
                "{{ round_input|safe }}{% with redirect_target=request.path %}{% include 'common/_return_to_input.html' %}{% endwith %}",
            ),
            ["is_manual_judge_assignment", "pairing.id", "judge.id", "as", "is_manual_assignment"] => {
                output.push_str("{% set is_manual_assignment = judge.manual_label %}");
            }
            ["manual_judge_assignment_label", "pairing.id", "judge.id", "as", "manual_assignment_label"] => {
                output.push_str("{% set manual_assignment_label = judge.manual_label %}");
            }
            ["judge_team_count", "judge", "pairing", "as", "rejudge_count"] => {
                output.push_str("{% set rejudge_count = judge.rejudge_count %}");
            }
            ["manual_judge_audit_events", "pairing.id", "as", "manual_events"] => {
                output.push_str("{% set manual_events = pairing.manual_events %}");
            }
            ["url", name] => {
                let name = argument(name);
                assert!(URLS.contains_key(&name), "unknown legacy URL {name}");
                output.push_str(&format!("{{{{ urls.{name} }}}}"));
            }
            ["active", "request", target] => {
                output.push_str(&format!("{{{{ legacy_active(request.path, {target}) }}}}"));
            }
            ["render_bundle", bundle] => {
                output.push_str(&format!("{{{{ legacy_bundle({bundle}) }}}}"));
            }
            ["theme_css_variables"] => output.push_str("{{ theme_variables }}"),
            ["favicon_url"] => output.push_str("{{ urls.favicon }}"),
            ["tournament_name"] => output.push_str("{{ tournament }}"),
            ["endblock", _] => output.push_str("{% endblock %}"),
            ["empty"] => output.push_str("{% else %}"),
            // Django includes keep their local URL aliases out of the calling template.
            ["include", "\"base/_navigation.html\""] => output.push_str(
                "{% with %}{% include 'base/_navigation.html' %}{% endwith %}",
            ),
            ["include", template, "with", "compact_staff=True"] => {
                output.push_str(&format!(
                    "{{% with compact_staff=true %}}{{% include {template} %}}{{% endwith %}}"
                ));
            }
            ["include", template, "with", fields @ ..] => {
                let fields = fields.join(", ").replace("=True", "=true").replace("=False", "=false");
                output.push_str(&format!(
                    "{{% with {fields} %}}{{% include {template} %}}{{% endwith %}}"
                ));
            }
            ["with", fields @ ..] if fields.len() > 1 && fields.iter().all(|field| field.contains('=')) => {
                output.push_str(&format!("{{% with {} %}}", fields.join(", ")));
            }
            ["quick_search"] => output.push_str("{% include 'common/_quick_search.html' %}"),
            ["for", "filter_group", "in", "filters"] => output.push_str(
                "{% for filter_group in filters %}{% set filter_group_index = loop.index %}",
            ),
            ["outround_form", "form"] => output.push_str("{% include 'outrounds/_form.html' %}"),
            ["csrf_token"] => output.push_str("{{ csrf_input|safe }}"),
            ["render_field", field, ..] => {
                let field = field.strip_prefix("form.").unwrap_or(field);
                output.push_str(&format!(
                    "{{% with field=widgets.{field}.field, checkbox=widgets.{field}.checkbox, widget_html=widgets.{field}.input|safe %}}{{% include \"ui/field.html\" %}}{{% endwith %}}"
                ));
            }
            _ => {
                output.push_str("{%");
                output.push_str(raw);
                output.push_str("%}");
            }
        }
    }
    output.push_str(rest);
    let translated = output
        .replace("|lookup:forloop.counter0", "[loop.index0]")
        .replace("undo.cells|length", "undo.cells|django_length")
        .replace("{{ row.school }}", "{{ row.school.name }}")
        .replace("{{ message }}", "{{ message.text }}")
        .replace("|with_return_to", "")
        .replace(
            "challenge_context.automatic_pair.0.",
            "challenge_context.automatic_pair[0].",
        )
        .replace(
            "challenge_context.automatic_pair.1.",
            "challenge_context.automatic_pair[1].",
        )
        .replace("pairing.judges.all", "pairing.judges")
        .replace("pairing.chair == judge", "pairing.chair_id == judge.id")
        .replace("judge == pairing.chair", "judge.is_chair")
        .replace("if judge.rank > warning", "if judge.high_rank")
        .replace("if judge.rank %}", "if judge.has_rank %}")
        .replace("|default_if_none:''", "")
        .replace("|date:'Y-m-d H:i:s T'", "")
        .replace("|floatformat:2", "")
        .replace(" is not None", " is not none")
        .replace("section.rows.0.", "section.rows[0].")
        .replace("forloop.parent.counter", "filter_group_index")
        .replace("forloop.counter0|divisibleby:2", "loop.index0 % 2 == 0")
        .replace("forloop.counter", "loop.index")
        .replace("forloop.first", "loop.first")
        .replace("item.0", "item[0]")
        .replace("item.1", "item[1]")
        .replace("item.2", "item[2]")
        .replace("item.3", "item[3]")
        .replace("item.4", "item[4]")
        .replace("forloop.last", "loop.last")
        .replace("|pluralize ", "|django_pluralize ");
    let translated = match name {
        "tab/start_tournament.html" | "tab/_custom_staff_role_form.html" => {
            setup_widgets(translated)
        }
        "tab/settings_form.html" => translated
            .replace("{{ field }}", "{{ field.html|safe }}")
            .replace("{{ field.errors }}", "{{ field.errors|safe }}"),
        _ => translated,
    };
    ["default", "date", "yesno", "pluralize"]
        .into_iter()
        .fold(translated, translate_filter_arguments)
}

fn setup_widgets(source: String) -> String {
    let source = source
        // The input named "action" shadows form.action in the browser.
        .replace(
            "name=\"action\" value=\"send_invite\"",
            "name=\"wizard_action\" value=\"send_invite\"",
        )
        .replace(
            "for role_form in custom_staff_formset %",
            "for role_form in custom_staff_formset.rows %",
        )
        .replace(
            "{{ custom_staff_formset.management_form }}",
            "{{ custom_staff_formset.management_form|safe }}",
        )
        .replace(
            "{{ custom_staff_formset.non_form_errors }}",
            "{{ custom_staff_formset.non_form_errors|safe }}",
        );
    [
        ("form", "tournament_name host_school tournament_type sanctioned ga_room facebook_url tournament_director tab_director equity_officers tournament_observer open_speaks open_ranks non_field_errors"),
        ("role_form", "DELETE title names"),
        ("invite_form", "email username permission_preset"),
    ]
    .into_iter()
    .fold(source, |mut source, (form, fields)| {
        for field in fields.split_ascii_whitespace() {
            let property = if field == "non_field_errors" { "" } else { ".html" };
            source = source.replace(&format!("{{{{ {form}.{field} }}}}"), &format!("{{{{ {form}.{field}{property}|safe }}}}"));
        }
        source
    })
}

fn translate_filter_arguments(source: String, filter: &str) -> String {
    let marker = format!("|{filter}:");
    let mut rest = source.as_str();
    let mut output = String::with_capacity(rest.len());
    while let Some((before, argument)) = rest.split_once(&marker) {
        output.push_str(before);
        let length = match argument.as_bytes().first() {
            Some(quote @ (b'\'' | b'"')) => argument[1..]
                .bytes()
                .position(|byte| byte == *quote)
                .map_or(0, |index| index + 2),
            _ => argument.bytes().take_while(u8::is_ascii_digit).count(),
        };
        if length == 0 {
            output.push_str(&marker);
            rest = argument;
            continue;
        }
        let (value, after) = argument.split_at(length);
        output.push_str(&format!("|django_{filter}({value})"));
        rest = after;
    }
    output.push_str(rest);
    output
}

fn django_date(value: String, format: String) -> String {
    let Ok(time) = value.parse::<NaiveDateTime>() else {
        return String::new();
    };
    match format.as_str() {
        "c" => time.and_utc().to_rfc3339_opts(
            if time.nanosecond() == 0 {
                chrono::SecondsFormat::Secs
            } else {
                chrono::SecondsFormat::Micros
            },
            false,
        ),
        "M j, Y, g:i:s a T" => format!(
            "{} {}. UTC",
            time.format("%b %-d, %Y, %-I:%M:%S"),
            if time.hour() < 12 { "a.m" } else { "p.m" }
        ),
        _ => String::new(),
    }
}

#[derive(Serialize)]
struct PublicPage {
    title: &'static str,
    url: &'static str,
}

pub async fn shell(
    db: &mut MySqlConnection,
    actor: Actor,
    path: &str,
) -> Result<Value, sqlx::Error> {
    let settings = Settings::load(
        db,
        &[
            "theme_color",
            "pairing_released",
            "judges_public",
            "teams_public",
            "motions_enabled",
            "var_teams_visible",
            "nov_teams_visible",
            "publish_tab_cards_with_first_outround",
        ],
    )
    .await?;
    let (prelims, varsity, novice, teams, speakers, ballots) = sqlx::query_as::<_, (bool, bool, bool, bool, bool, bool)>(
        "SELECT EXISTS(SELECT 1 FROM tab_round),
          EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = 0 AND num_teams >= ?),
          EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = 1 AND num_teams >= ?),
          EXISTS(SELECT 1 FROM tab_publicdisplaysetting WHERE slug = 'team' AND display_type = 'ranking' AND is_enabled = TRUE),
          EXISTS(SELECT 1 FROM tab_publicdisplaysetting WHERE slug IN ('varsity','novice') AND display_type = 'ranking' AND is_enabled = TRUE),
          EXISTS(SELECT 1 FROM tab_publicdisplaysetting WHERE display_type = 'ballot' AND is_enabled = TRUE)",
    )
    .bind(settings.integer("var_teams_visible", 256))
    .bind(settings.integer("nov_teams_visible", 256))
    .fetch_one(&mut *db)
    .await?;
    let motions = settings.flag("motions_enabled", false);
    let pages: Vec<_> = [
        (prelims, "Released Pairings", "/public/pairings/"),
        (
            prelims && settings.integer("pairing_released", 0) == 1,
            "Missing Ballots",
            "/public/missing-ballots/",
        ),
        (true, "Judge Portal and Ballots", "/public/e-ballots/"),
        (true, "Team Portal", "/team_portal/"),
        (
            settings.flag("judges_public", false),
            "Judge List",
            "/public/judges/",
        ),
        (
            settings.flag("teams_public", false),
            "Team List",
            "/public/teams/",
        ),
        (varsity, "Varsity Outrounds", "/public/outrounds/0/"),
        (novice, "Novice Outrounds", "/public/outrounds/1/"),
        (teams, "Public Team Results", "/public/team-rankings/"),
        (
            speakers,
            "Public Speaker Results",
            "/public/speaker-rankings/",
        ),
        (ballots, "Public Ballots", "/public/ballots/"),
        (motions, "Motions", "/public/motions/"),
        (
            (varsity || novice) && settings.flag("publish_tab_cards_with_first_outround", true),
            "Tab Cards",
            "/public/tab-cards/",
        ),
    ]
    .into_iter()
    .filter(|(enabled, _, _)| *enabled)
    .map(|(_, title, url)| PublicPage { title, url })
    .collect();
    let superuser = matches!(
        actor,
        Actor::User {
            superuser: true,
            ..
        }
    );
    let board = matches!(actor, Actor::User { board: true, .. });
    let preset = match actor {
        Actor::User {
            id,
            staff: true,
            superuser: false,
            board: false,
        } => staff_roles::preset_on(db, id).await?,
        _ => None,
    };
    Ok(context! {
        urls => &*URLS,
        request => context! { path },
        user => context! { is_authenticated => actor != Actor::Anonymous, is_superuser => superuser },
        theme_variables => homepage_admin::theme_variables(&settings.string("theme_color", "")),
        public_pages => pages,
        motions_enabled_flag => motions,
        is_apda_board_user => board,
        can_add_scratches => superuser || matches!(preset, Some(Preset::Scratches | Preset::ScratchRead | Preset::Assistant)),
        can_view_scratches => superuser || matches!(preset, Some(Preset::ScratchRead | Preset::Assistant)),
        can_checkins => superuser || preset.is_some_and(Preset::checkins),
        can_data_entry => superuser || preset.is_some_and(Preset::data_entry),
    })
}

pub fn render(template: &str, shell: Value, page: Value, status: StatusCode) -> Response {
    let result = TEMPLATE_ENV
        .get_template(template)
        .and_then(|template| template.render(context! { ..shell, ..page }));
    match result {
        Ok(page) => private((status, Html(page))),
        Err(error) => {
            eprintln!("Failed to render {template}: {}", error.kind());
            unavailable()
        }
    }
}
