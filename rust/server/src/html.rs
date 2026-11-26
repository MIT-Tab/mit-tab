use std::{collections::HashMap, sync::LazyLock};

use axum::{
    http::{HeaderMap, StatusCode},
    response::{Html, Response},
};
use minijinja::{Environment, Value};
use serde::{Deserialize, Serialize};

use crate::{
    legacy_session::{Actor, cookie},
    login, passwords, private, unavailable,
};

static ENV: LazyLock<Environment<'static>> = LazyLock::new(|| {
    let mut env = Environment::new();
    for (name, source) in [
        ("base.html", include_str!("../templates/base.html")),
        ("home.html", include_str!("../templates/home.html")),
        (
            "home_resources.html",
            include_str!("../templates/home_resources.html"),
        ),
        (
            "staff_login.html",
            include_str!("../templates/staff_login.html"),
        ),
        ("motions.html", include_str!("../templates/motions.html")),
        ("pairings.html", include_str!("../templates/pairings.html")),
        (
            "missing_ballots.html",
            include_str!("../templates/missing_ballots.html"),
        ),
        ("teams.html", include_str!("../templates/teams.html")),
        ("judges.html", include_str!("../templates/judges.html")),
        (
            "expected_records.html",
            include_str!("../templates/expected_records.html"),
        ),
        (
            "public_table.html",
            include_str!("../templates/public_table.html"),
        ),
        (
            "roster_changes.html",
            include_str!("../templates/roster_changes.html"),
        ),
        (
            "public_footer.html",
            include_str!("../templates/public_footer.html"),
        ),
        (
            "manage_motions.html",
            include_str!("../templates/manage_motions.html"),
        ),
        (
            "edit_motion.html",
            include_str!("../templates/edit_motion.html"),
        ),
        (
            "motion_fields.html",
            include_str!("../templates/motion_fields.html"),
        ),
        (
            "pair_prelim.html",
            include_str!("../templates/pair_prelim.html"),
        ),
        (
            "staff_pairings.html",
            include_str!("../templates/staff_pairings.html"),
        ),
        (
            "ballot_entry.html",
            include_str!("../templates/ballot_entry.html"),
        ),
        (
            "judge_search.html",
            include_str!("../templates/judge_search.html"),
        ),
        (
            "judge_portal.html",
            include_str!("../templates/judge_portal.html"),
        ),
        (
            "judge_previous.html",
            include_str!("../templates/judge_previous.html"),
        ),
        (
            "judge_submitted.html",
            include_str!("../templates/judge_submitted.html"),
        ),
        (
            "board_nav.html",
            include_str!("../templates/board_nav.html"),
        ),
        (
            "board_home.html",
            include_str!("../templates/board_home.html"),
        ),
        (
            "board_history.html",
            include_str!("../templates/board_history.html"),
        ),
        (
            "board_pairing.html",
            include_str!("../templates/board_pairing.html"),
        ),
        (
            "board_inrounds.html",
            include_str!("../templates/board_inrounds.html"),
        ),
        (
            "board_judges.html",
            include_str!("../templates/board_judges.html"),
        ),
        (
            "board_outrounds.html",
            include_str!("../templates/board_outrounds.html"),
        ),
        (
            "tournament_setup.html",
            include_str!("../templates/tournament_setup.html"),
        ),
        (
            "checklist.html",
            include_str!("../templates/checklist.html"),
        ),
        (
            "settings_form.html",
            include_str!("../templates/settings_form.html"),
        ),
        ("checkin.html", include_str!("../templates/checkin.html")),
        (
            "checkin_history.html",
            include_str!("../templates/checkin_history.html"),
        ),
        (
            "ranking_control.html",
            include_str!("../templates/ranking_control.html"),
        ),
        (
            "public_team_rankings.html",
            include_str!("../templates/public_team_rankings.html"),
        ),
        (
            "public_speaker_rankings.html",
            include_str!("../templates/public_speaker_rankings.html"),
        ),
        (
            "public_ballots.html",
            include_str!("../templates/public_ballots.html"),
        ),
        (
            "public_outrounds.html",
            include_str!("../templates/public_outrounds.html"),
        ),
        (
            "tab_cards.html",
            include_str!("../templates/tab_cards.html"),
        ),
        ("error.html", include_str!("../templates/error.html")),
    ] {
        env.add_template(name, source)
            .expect("built-in page template");
    }
    env.add_function("webpack_files", webpack_files);
    env
});

#[derive(Default, Deserialize)]
struct WebpackManifest {
    chunks: HashMap<String, Vec<String>>,
}

static WEBPACK: LazyLock<WebpackManifest> = LazyLock::new(|| {
    let path =
        std::env::var_os("MITTAB_WEBPACK_STATS").unwrap_or_else(|| "webpack-stats.json".into());
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
});

#[derive(Serialize)]
struct Asset {
    href: String,
    stylesheet: bool,
}

fn webpack_files(bundle: String) -> Value {
    let files = WEBPACK
        .chunks
        .get(&bundle)
        .cloned()
        .unwrap_or_else(|| vec![format!("{bundle}.css")]);
    let assets: Vec<_> = files
        .into_iter()
        .filter(|name| {
            name.bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"-_.".contains(&byte))
                && (name.ends_with(".css") || name.ends_with(".js"))
        })
        .map(|name| Asset {
            stylesheet: name.ends_with(".css"),
            href: format!("/static/webpack_bundles/{name}"),
        })
        .collect();
    Value::from_serialize(assets)
}

#[derive(Serialize)]
pub struct Navigation {
    pub authenticated: bool,
    pub board: bool,
    pub tab: bool,
    pub motions_enabled: bool,
    pub teams_public: bool,
    pub judges_public: bool,
}

impl Navigation {
    pub fn new(actor: Actor, motions_enabled: bool) -> Self {
        Self {
            authenticated: !matches!(actor, Actor::Anonymous),
            board: matches!(actor, Actor::User { board: true, .. }),
            tab: matches!(
                actor,
                Actor::User {
                    board: false,
                    superuser: true,
                    ..
                }
            ),
            motions_enabled,
            teams_public: false,
            judges_public: false,
        }
    }
}

pub fn render(template: &str, context: Value, status: StatusCode) -> Response {
    let rendered = ENV
        .get_template(template)
        .and_then(|page| page.render(context));
    match rendered {
        Ok(page) => private((status, Html(page))),
        Err(_) => unavailable(),
    }
}

pub fn page_csrf(headers: &HeaderMap) -> Result<String, &'static str> {
    match cookie(headers, "csrftoken").filter(|value| login::valid_csrf_token(value)) {
        Some(token) => Ok(token.into()),
        None => passwords::random_secret(),
    }
}

pub fn form_response(mut response: Response, headers: &HeaderMap, csrf: &str) -> Response {
    if cookie(headers, "csrftoken") != Some(csrf) {
        login::set_cookie(
            &mut response,
            "csrftoken",
            csrf,
            31_449_600,
            login::secure_request(headers),
            false,
        );
    }
    response
}
