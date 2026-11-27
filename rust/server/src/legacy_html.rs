use std::{collections::BTreeMap, sync::LazyLock};

use axum::{
    http::StatusCode,
    response::{Html, Response},
};
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
    ] {
        env.add_template_owned(name, translate(template))
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

fn translate(source: &str) -> String {
    let mut output = String::new();
    let mut rest = source;
    while let Some((before, tail)) = rest.split_once("{%") {
        output.push_str(before);
        let (raw, after) = tail.split_once("%}").expect("closed template tag");
        rest = after;
        let words: Vec<_> = raw.split_whitespace().collect();
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
            ["url", "'view_round'", "number"] => output.push_str("/round/{{ number }}/"),
            ["url", "'board_inrounds'", "number"] => {
                output.push_str("/apda-board/inrounds/{{ number }}/");
            }
            ["url", "'view_judge'", "judge.id"] => output.push_str("/judge/{{ judge.id }}/"),
            ["return_to_input"] => output.push_str(
                "{{ round_input|safe }}{% with redirect_target=request.path %}{% include 'common/_return_to_input.html' %}{% endwith %}",
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
            ["include", template, "with", "compact_staff=True"] => {
                output.push_str(&format!(
                    "{{% with compact_staff=true %}}{{% include {template} %}}{{% endwith %}}"
                ));
            }
            ["csrf_token"] => output.push_str("{{ csrf_input|safe }}"),
            ["render_field", field, ..] => output.push_str(&format!(
                "{{% with field=widgets.{field}.field, checkbox=widgets.{field}.checkbox, widget_html=widgets.{field}.input|safe %}}{{% include \"ui/field.html\" %}}{{% endwith %}}"
            )),
            _ => {
                output.push_str("{%");
                output.push_str(raw);
                output.push_str("%}");
            }
        }
    }
    output.push_str(rest);
    output
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
        .replace("if judge.rank > warning", "if judge.high_rank")
        .replace("if judge.rank %}", "if judge.has_rank %}")
        .replace("|default_if_none:''", "")
        .replace("|date:'Y-m-d H:i:s T'", "")
        .replace("|floatformat:2", "")
        .replace(" is not None", " is not none")
        .replace("section.rows.0.", "section.rows[0].")
        .replace("forloop.counter", "loop.index")
        .replace("forloop.last", "loop.last")
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
