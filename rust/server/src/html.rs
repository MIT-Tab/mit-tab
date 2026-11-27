use std::{collections::HashMap, sync::LazyLock};

use axum::{
    http::{HeaderMap, StatusCode},
    response::{Html, Response},
};
use minijinja::{Environment, Value};
use serde::{Deserialize, Serialize};

use crate::{
    legacy_session::{Actor, cookie},
    login, private, unavailable,
};

static ENV: LazyLock<Environment<'static>> = LazyLock::new(|| {
    let mut env = Environment::new();
    for (name, source) in [
        (
            "roster_groups.html",
            include_str!("../templates/roster_groups.html"),
        ),
        (
            "roster_log_admin.html",
            include_str!("../templates/roster_log_admin.html"),
        ),
        (
            "ranking_tables.html",
            include_str!("../templates/ranking_tables.html"),
        ),
        (
            "staff_rankings.html",
            include_str!("../templates/staff_rankings.html"),
        ),
        (
            "forum_preview.html",
            include_str!("../templates/forum_preview.html"),
        ),
        (
            "judge_report.html",
            include_str!("../templates/judge_report.html"),
        ),
        (
            "tournament_maintenance.html",
            include_str!("../templates/tournament_maintenance.html"),
        ),
        (
            "admin_repair.html",
            include_str!("../templates/admin_repair.html"),
        ),
        (
            "administration.html",
            include_str!("../templates/administration.html"),
        ),
        (
            "admin_accounts.html",
            include_str!("../templates/admin_accounts.html"),
        ),
        (
            "diagnostics.html",
            include_str!("../templates/diagnostics.html"),
        ),
        ("base.html", include_str!("../templates/base.html")),
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
        (
            "ranking_control.html",
            include_str!("../templates/ranking_control.html"),
        ),
        (
            "public_outrounds.html",
            include_str!("../templates/public_outrounds.html"),
        ),
        (
            "tab_cards.html",
            include_str!("../templates/tab_cards.html"),
        ),
        (
            "board_apda_id.html",
            include_str!("../templates/board_apda_id.html"),
        ),
        (
            "code_lookup.html",
            include_str!("../templates/code_lookup.html"),
        ),
        (
            "team_portal.html",
            include_str!("../templates/team_portal.html"),
        ),
        (
            "registration.html",
            include_str!("../templates/registration.html"),
        ),
        (
            "registration_admin.html",
            include_str!("../templates/registration_admin.html"),
        ),
        (
            "resource_forms.html",
            include_str!("../templates/resource_forms.html"),
        ),
        (
            "homepage_admin.html",
            include_str!("../templates/homepage_admin.html"),
        ),
        (
            "staff_invite.html",
            include_str!("../templates/staff_invite.html"),
        ),
        (
            "staff_invite_confirm.html",
            include_str!("../templates/staff_invite_confirm.html"),
        ),
        (
            "email_admin.html",
            include_str!("../templates/email_admin.html"),
        ),
        (
            "staff_roster_list.html",
            include_str!("../templates/staff_roster_list.html"),
        ),
        (
            "staff_roster_edit.html",
            include_str!("../templates/staff_roster_edit.html"),
        ),
        (
            "staff_roster_delete.html",
            include_str!("../templates/staff_roster_delete.html"),
        ),
        (
            "scratches_fields.html",
            include_str!("../templates/scratches_fields.html"),
        ),
        (
            "scratches_form.html",
            include_str!("../templates/scratches_form.html"),
        ),
        (
            "scratches_list.html",
            include_str!("../templates/scratches_list.html"),
        ),
        (
            "scratches_delete.html",
            include_str!("../templates/scratches_delete.html"),
        ),
        (
            "roster_spreadsheets.html",
            include_str!("../templates/roster_spreadsheets.html"),
        ),
        (
            "staff_round_edit.html",
            include_str!("../templates/staff_round_edit.html"),
        ),
        (
            "staff_round_choices.html",
            include_str!("../templates/staff_round_choices.html"),
        ),
        (
            "quick_judge_checkin.html",
            include_str!("../templates/quick_judge_checkin.html"),
        ),
        (
            "round_stats.html",
            include_str!("../templates/round_stats.html"),
        ),
        (
            "staff_break.html",
            include_str!("../templates/staff_break.html"),
        ),
        (
            "backup_detail.html",
            include_str!("../templates/backup_detail.html"),
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
    Value::from_serialize(bundle_assets(bundle))
}

pub fn legacy_bundle(bundle: String) -> Value {
    let tags = bundle_assets(bundle)
        .into_iter()
        .map(|asset| {
            if asset.stylesheet {
                format!("<link href=\"{}\" rel=\"stylesheet\"/>", asset.href)
            } else {
                format!("<script src=\"{}\"></script>", asset.href)
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    Value::from_safe_string(tags)
}

fn bundle_assets(bundle: String) -> Vec<Asset> {
    let files = WEBPACK
        .chunks
        .get(&bundle)
        .cloned()
        .unwrap_or_else(|| vec![format!("{bundle}.css")]);
    files
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
        .collect()
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
        None => login::new_csrf_code(),
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
