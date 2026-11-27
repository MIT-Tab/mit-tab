use std::{collections::BTreeMap, fmt::Write, io, sync::LazyLock};

use axum::{
    http::StatusCode,
    response::{Html, Response},
};
use chrono::{NaiveDateTime, Timelike};
use minijinja::{Environment, UndefinedBehavior, Value, context};
use serde::Serialize;
use serde_json::ser::Formatter;
use sqlx::MySqlConnection;

use crate::{
    homepage_admin, html,
    legacy_session::Actor,
    private,
    settings::Settings,
    staff_roles::{self, Preset},
    unavailable,
};

pub(crate) struct DjangoJson {
    pub ascii_only: bool,
}

impl Formatter for DjangoJson {
    fn begin_array_value<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_key<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        self.begin_array_value(writer, first)
    }

    fn begin_object_value<W: ?Sized + io::Write>(&mut self, writer: &mut W) -> io::Result<()> {
        writer.write_all(b": ")
    }

    fn write_f64<W: ?Sized + io::Write>(&mut self, writer: &mut W, value: f64) -> io::Result<()> {
        let decimal = format!("{value:?}");
        if let Some((mantissa, exponent)) = decimal.split_once('e') {
            let exponent: i32 = exponent.parse().expect("f64 exponent");
            write!(writer, "{mantissa}e{exponent:+03}")
        } else {
            writer.write_all(decimal.as_bytes())
        }
    }

    fn write_string_fragment<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        fragment: &str,
    ) -> io::Result<()> {
        if !self.ascii_only {
            return writer.write_all(fragment.as_bytes());
        }
        for character in fragment.chars() {
            if character < '\u{007f}' {
                writer.write_all(&[character as u8])?;
            } else {
                for unit in character.encode_utf16(&mut [0; 2]) {
                    write!(writer, "\\u{unit:04x}")?;
                }
            }
        }
        Ok(())
    }
}

static URLS: LazyLock<BTreeMap<String, String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../templates/legacy_navigation_urls.json"))
        .expect("valid legacy navigation URLs")
});

// Match Python's Unicode casefold, including multi-character folds and final sigma.
static CASEFOLD: LazyLock<BTreeMap<char, &str>> = LazyLock::new(|| {
    include_str!("casefold.tsv")
        .lines()
        .skip(1)
        .map(|line| {
            let (key, value) = line.split_once('\t').expect("casefold mapping row");
            (key.chars().next().expect("casefold mapping key"), value)
        })
        .collect()
});

pub(crate) fn casefold(name: &str) -> String {
    let mut folded = String::with_capacity(name.len());
    for ch in name.chars() {
        if let Some(replacement) = CASEFOLD.get(&ch) {
            folded.push_str(replacement);
        } else {
            folded.push(ch);
        }
    }
    folded
}

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
    env.add_global("urls", Value::from_serialize(&*URLS));
    env.add_global("DEFAULT_MESSAGE_LEVELS", context! { ERROR => 40 });
    env.add_function("legacy_bundle", html::legacy_bundle);
    env.add_filter("django_default", |value: Value, fallback: Value| {
        if value.is_true() { value } else { fallback }
    });
    env.add_filter("django_floatformat", |value: Value, places: i64| {
        use bigdecimal::{BigDecimal, RoundingMode};

        // Django rounds the shortest decimal representation, not the binary fraction.
        value.to_string().parse::<BigDecimal>().map_or_else(
            |_| String::new(),
            |decimal| {
                decimal
                    .with_scale_round(places, RoundingMode::HalfUp)
                    .to_plain_string()
            },
        )
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
    env.add_filter("isolated_email_preview", isolated_email_preview);
    env.add_filter("django_truncatechars", |value: String, limit: usize| {
        use unicode_normalization::{UnicodeNormalization, char::canonical_combining_class};
        let value: String = value.nfc().collect();
        if value
            .chars()
            .filter(|&c| canonical_combining_class(c) == 0)
            .count()
            <= limit
        {
            return value;
        }
        let cutoff = value
            .char_indices()
            .filter(|&(_, c)| canonical_combining_class(c) == 0)
            .nth(limit.saturating_sub(1))
            .map_or(0, |(offset, _)| offset);
        format!("{}…", &value[..cutoff])
    });
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
            "common/403.html",
            include_str!("../../../mittab/templates/common/403.html"),
        ),
        (
            "common/404.html",
            include_str!("../../../mittab/templates/common/404.html"),
        ),
        (
            "common/500.html",
            include_str!("../../../mittab/templates/common/500.html"),
        ),
        (
            "tab/email_management.html",
            include_str!("../../../mittab/templates/tab/email_management.html"),
        ),
        (
            "tab/_team_code_emails.html",
            include_str!("../../../mittab/templates/tab/_team_code_emails.html"),
        ),
        (
            "tab/_email_preview.html",
            include_str!("../../../mittab/templates/tab/_email_preview.html"),
        ),
        (
            "emails/base.html",
            include_str!("../../../mittab/templates/emails/base.html"),
        ),
        (
            "emails/team_portal.html",
            include_str!("../../../mittab/templates/emails/team_portal.html"),
        ),
        (
            "registration/_tournament_link_table.html",
            include_str!("../../../mittab/templates/registration/_tournament_link_table.html"),
        ),
        (
            "registration/_tournament_link_form.html",
            include_str!("../../../mittab/templates/registration/_tournament_link_form.html"),
        ),
        (
            "registration/admin_list.html",
            include_str!("../../../mittab/templates/registration/admin_list.html"),
        ),
        (
            "tab/public_homepage.html",
            include_str!("../../../mittab/templates/tab/public_homepage.html"),
        ),
        (
            "registration/portal.html",
            include_str!("../../../mittab/templates/registration/portal.html"),
        ),
        (
            "registration/code_lookup.html",
            include_str!("../../../mittab/templates/registration/code_lookup.html"),
        ),
        (
            "registration/_team_form.html",
            include_str!("../../../mittab/templates/registration/_team_form.html"),
        ),
        (
            "registration/_judge_form.html",
            include_str!("../../../mittab/templates/registration/_judge_form.html"),
        ),
        (
            "registration/_debater_card.html",
            include_str!("../../../mittab/templates/registration/_debater_card.html"),
        ),
        (
            "registration/_input_group.html",
            include_str!("../../../mittab/templates/registration/_input_group.html"),
        ),
        (
            "registration/team_portal.html",
            include_str!("../../../mittab/templates/registration/team_portal.html"),
        ),
        (
            "registration/team_portal_search.html",
            include_str!("../../../mittab/templates/registration/team_portal_search.html"),
        ),
        (
            "public/e_ballot_search.html",
            include_str!("../../../mittab/templates/public/e_ballot_search.html"),
        ),
        (
            "public/judge_portal.html",
            include_str!("../../../mittab/templates/public/judge_portal.html"),
        ),
        (
            "ballots/previous_ballots.html",
            include_str!("../../../mittab/templates/ballots/previous_ballots.html"),
        ),
        (
            "ballots/ballot_submitted.html",
            include_str!("../../../mittab/templates/ballots/ballot_submitted.html"),
        ),
        (
            "ballots/round_entry.html",
            include_str!("../../../mittab/templates/ballots/round_entry.html"),
        ),
        (
            "ballots/round_entry_multiple.html",
            include_str!("../../../mittab/templates/ballots/round_entry_multiple.html"),
        ),
        (
            "ballots/_form.html",
            include_str!("../../../mittab/templates/ballots/_form.html"),
        ),
        (
            "emails/judge_ballot_code.html",
            include_str!("../../../mittab/templates/emails/judge_ballot_code.html"),
        ),
        (
            "emails/registration_confirmation.html",
            include_str!("../../../mittab/templates/emails/registration_confirmation.html"),
        ),
        (
            "emails/written_rfd.html",
            include_str!("../../../mittab/templates/emails/written_rfd.html"),
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
            "public/teams.html",
            include_str!("../../../mittab/templates/public/teams.html"),
        ),
        (
            "public/judges.html",
            include_str!("../../../mittab/templates/public/judges.html"),
        ),
        (
            "public/expected_records.html",
            include_str!("../../../mittab/templates/public/expected_records.html"),
        ),
        (
            "public/_roster_changes.html",
            include_str!("../../../mittab/templates/public/_roster_changes.html"),
        ),
        (
            "motions/manage_motions.html",
            include_str!("../../../mittab/templates/motions/manage_motions.html"),
        ),
        (
            "motions/edit_motion.html",
            include_str!("../../../mittab/templates/motions/edit_motion.html"),
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
            "common/data_entry.html",
            include_str!("../../../mittab/templates/common/data_entry.html"),
        ),
        (
            "common/data_entry_multiple.html",
            include_str!("../../../mittab/templates/common/data_entry_multiple.html"),
        ),
        (
            "common/_form.html",
            include_str!("../../../mittab/templates/common/_form.html"),
        ),
        (
            "common/data_upload.html",
            include_str!("../../../mittab/templates/common/data_upload.html"),
        ),
        (
            "common/_upload_error.html",
            include_str!("../../../mittab/templates/common/_upload_error.html"),
        ),
        (
            "tab/school_detail.html",
            include_str!("../../../mittab/templates/tab/school_detail.html"),
        ),
        (
            "tab/room_detail.html",
            include_str!("../../../mittab/templates/tab/room_detail.html"),
        ),
        (
            "tab/debater_detail.html",
            include_str!("../../../mittab/templates/tab/debater_detail.html"),
        ),
        (
            "tab/team_detail.html",
            include_str!("../../../mittab/templates/tab/team_detail.html"),
        ),
        (
            "tab/judge_detail.html",
            include_str!("../../../mittab/templates/tab/judge_detail.html"),
        ),
        (
            "tab/rank_teams.html",
            include_str!("../../../mittab/templates/tab/rank_teams.html"),
        ),
        (
            "tab/judge_speaker_points.html",
            include_str!("../../../mittab/templates/tab/judge_speaker_points.html"),
        ),
        (
            "tab/_speaker_points_chart.html",
            include_str!("../../../mittab/templates/tab/_speaker_points_chart.html"),
        ),
        (
            "pairing/manage_ranking_groups.html",
            include_str!("../../../mittab/templates/pairing/manage_ranking_groups.html"),
        ),
        (
            "pairing/manage_room_tags.html",
            include_str!("../../../mittab/templates/pairing/manage_room_tags.html"),
        ),
        (
            "tab/rank_debaters.html",
            include_str!("../../../mittab/templates/tab/rank_debaters.html"),
        ),
        (
            "tab/rank_teams_component.html",
            include_str!("../../../mittab/templates/tab/rank_teams_component.html"),
        ),
        (
            "tab/rank_debaters_component.html",
            include_str!("../../../mittab/templates/tab/rank_debaters_component.html"),
        ),
        (
            "tab/_rank_teams_table.html",
            include_str!("../../../mittab/templates/tab/_rank_teams_table.html"),
        ),
        (
            "tab/_rank_debaters_table.html",
            include_str!("../../../mittab/templates/tab/_rank_debaters_table.html"),
        ),
        (
            "common/_audit_trail.html",
            include_str!("../../../mittab/templates/common/_audit_trail.html"),
        ),
        (
            "ui/form.html",
            include_str!("../../../mittab/templates/ui/form.html"),
        ),
        (
            "common/index.html",
            include_str!("../../../mittab/templates/common/index.html"),
        ),
        (
            "common/_index_list.html",
            include_str!("../../../mittab/templates/common/_index_list.html"),
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
            "apda_board/tab_cards.html",
            include_str!("../../../mittab/templates/apda_board/tab_cards.html"),
        ),
        (
            "public/tab_cards.html",
            include_str!("../../../mittab/templates/public/tab_cards.html"),
        ),
        (
            "tab/tab_card.html",
            include_str!("../../../mittab/templates/tab/tab_card.html"),
        ),
        (
            "tab/pretty_tab_card.html",
            include_str!("../../../mittab/templates/tab/pretty_tab_card.html"),
        ),
        (
            "tab/all_tab_cards.html",
            include_str!("../../../mittab/templates/tab/all_tab_cards.html"),
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
            "tab/round_stats.html",
            include_str!("../../../mittab/templates/tab/round_stats.html"),
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
            | ["staff_can", ..] => {}
            ["tournament_logo_url", "as", "current_logo"] if name == "tab/public_homepage.html" => {
                output.push_str("{% set current_logo = logo %}");
            }
            ["tournament_logo_url", ..] => {}
            ["autoescape", "on"] | ["endautoescape"] => {}
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
            ["url", "'board_tab_card'", "item.id"] => {
                output.push_str("/apda-board/tab-card/{{ item.id }}/");
            }
            ["static", "'css/tab-cards-print.css'"] => output.push_str("/static/css/tab-cards-print.css"),
            ["url", "'board_inrounds'", "number"] => {
                output.push_str("/apda-board/inrounds/{{ number }}/");
            }
            ["url", "'view_judge'", "judge.id"] => output.push_str("/judge/{{ judge.id }}/"),
            ["url", "'export_roster'", "kind", "extension"] => {
                output.push_str("/export_data/{{ kind }}.{{ extension }}");
            }
            ["url", "'export_roster'", "'scratches'", "extension"] => {
                output.push_str("/export_data/scratches.{{ extension }}");
            }
            ["url", "'view_team'", "row.team.id"] => output.push_str("/team/{{ row.team.id }}/"),
            ["url", "'judge_portal'", "ballot_code"] => output.push_str("{{ portal_url }}"),
            ["url", "'enter_e_ballot'", "ballot_code"] => output.push_str("{{ current_ballot_url }}"),
            ["url", "'registration_portal_edit'", "registration.herokunator_code"] => {
                output.push_str("{{ registration.edit_url }}");
            }
            ["url", "'tournament_link_create'", "link_kind=link_kind"] => {
                output.push_str("/registration/manage/links/{{ link_kind }}/");
            }
            ["url", "'tournament_link_update'", "link_kind=link_kind", "link_id=link.pk"] => {
                output.push_str("/registration/manage/links/{{ link_kind }}/{{ link.pk }}/");
            }
            ["url", "'tournament_link_delete'", "link_kind=link_kind", "link_id=link.pk"] => {
                output.push_str("/registration/manage/links/{{ link_kind }}/{{ link.pk }}/delete/");
            }
            ["url", "'tournament_link_create'", "link_kind='info'"] => {
                output.push_str("/registration/manage/links/info/");
            }
            ["url", "'tournament_link_update'", "link_kind='info'", "link_id=link.pk"] => {
                output.push_str("/registration/manage/links/info/{{ link.pk }}/");
            }
            ["url", "'tournament_link_delete'", "link_kind='info'", "link_id=link.pk"] => {
                output.push_str("/registration/manage/links/info/{{ link.pk }}/delete/");
            }
            ["url", "'info_text_create'"] => output.push_str("/registration/manage/info-text/"),
            ["url", "'info_text_update'", "info_text_id=item.pk"] => {
                output.push_str("/registration/manage/info-text/{{ item.pk }}/");
            }
            ["url", "'info_text_delete'", "info_text_id=item.pk"] => {
                output.push_str("/registration/manage/info-text/{{ item.pk }}/delete/");
            }
            ["url", "'registration_portal_edit'", "summary.herokunator_code"] => {
                output.push_str("/registration/{{ summary.herokunator_code }}/");
            }
            ["url", "'registration_portal_edit'", "row.registration.herokunator_code"] => {
                output.push_str("{{ row.registration.url }}");
            }
            ["url", "'edit_motion'", "motion.pk"] => output.push_str("/motions/{{ motion.id }}/edit/"),
            ["url", "'toggle_motion_published'", "motion.pk"] => output.push_str("/motions/{{ motion.id }}/toggle-published/"),
            ["url", "'delete_motion'", "motion.pk"] => output.push_str("/motions/{{ motion.id }}/delete/"),
            ["return_to_input"] if name == "common/data_entry_multiple.html" => output.push_str(
                "{% if scratch_revision %}<input type=\"hidden\" name=\"mittab_scratch_revision\" value=\"{{ scratch_revision }}\">{% endif %}{% with redirect_target=return_path|default(request.path) %}{% include 'common/_return_to_input.html' %}{% endwith %}",
            ),
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
            ["render_bundle", bundle, kind] => {
                output.push_str(&format!("{{{{ legacy_bundle({bundle}, {kind}) }}}}"));
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
            ["outround_form", "form"] => output.push_str("{% include 'outrounds/_form.html' %}"),
            ["round_form", "form", "gov_team", "opp_team"] => output.push_str("{% include 'ballots/_form.html' %}"),
            ["csrf_token"] => output.push_str("{{ csrf_input|safe }}"),
            ["widthratio", "num_rounds", "1", "72"] => output.push_str("{{ table_width }}"),
            ["render_form", "form"] => output.push_str("{% include 'ui/form.html' %}"),
            ["render_form", "form", "files_last=True"] => output.push_str(
                concat!(
                    "{% with files_last=true %}{% include 'ui/form.html' %}{% endwith %}",
                    "{% if room_checkin_revision or team_checkins or judge_checkin_revision %}<input type=\"hidden\" name=\"mittab_checkins_present\" value=\"1\">{% endif %}",
                    "{% if room_checkin_revision %}<input type=\"hidden\" name=\"mittab_room_checkin_revision\" value=\"{{ room_checkin_revision }}\">{% endif %}",
                    "{% if team_checkin_initial %}<input type=\"hidden\" name=\"mittab_team_checkin_initial\" value=\"{{ team_checkin_initial }}\">{% endif %}",
                    "{% if judge_checkin_revision %}<input type=\"hidden\" name=\"mittab_judge_checkin_revision\" value=\"{{ judge_checkin_revision }}\">{% endif %}",
                    "{% if judge_edit_revision %}<input type=\"hidden\" name=\"mittab_judge_edit_revision\" value=\"{{ judge_edit_revision }}\">{% endif %}",
                ),
            ),
            ["render_field", "field"] if name == "ui/form.html" => output.push_str(
                "{% with checkbox=field.checkbox, choices=field.choices, widget_html=field.input|safe, help_id=field.help_id, error_id=field.error_id %}{% include 'ui/field.html' %}{% endwith %}",
            ),
            ["render_field", field] if name == "ballots/_form.html" => output.push_str(&format!(
                "{{% with field={field}, widget_html={field}.input|safe, help_id={field}.help_id, error_id={field}.error_id %}}{{% include 'ui/field.html' %}}{{% endwith %}}"
            )),
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
        .replace(
            "|floatformat:2",
            if matches!(name, "tab/round_stats.html" | "tab/tab_card.html") {
                "|django_floatformat(2)"
            } else {
                ""
            },
        )
        .replace(" is not None", " is not none")
        .replace("section.rows.0.", "section.rows[0].")
        // Django renders this unresolved lookup as the empty filter group.
        .replace("{{forloop.parent.counter}}", "")
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
        "motions/manage_motions.html" => translated
            .replace("motion.is_published", "motion.published")
            .replace("motion.info_slide|truncatechars:100", "motion.info_preview")
            .replace("motion.motion_text|truncatechars:150", "motion.motion_preview")
            .replace(
                "{{ csrf_input|safe }}\n                                                <button",
                "{{ csrf_input|safe }}{% if motion.revision %}<input type=\"hidden\" name=\"motion_revision\" value=\"{{ motion.revision }}\">{% endif %}\n                                                <button",
            )
            .replace(
                "{{ csrf_input|safe }}\n                        <button",
                "{{ csrf_input|safe }}{% if list_revision %}<input type=\"hidden\" name=\"motion_revision\" value=\"{{ list_revision }}\">{% endif %}\n                        <button",
            ),
        "motions/edit_motion.html" => translated
            .replace("motion.is_published", "motion.published")
            .replace("motion.created_at|date:\"M d, Y H:i\"", "motion.created")
            .replace("motion.updated_at|date:\"M d, Y H:i\"", "motion.updated")
            .replace("round_id|stringformat:\"s\"", "round_id")
            .replace("\"inround_\"|add:inround_value", "(\"inround_\" ~ inround_value)")
            .replace("\"outround_\"|add:round_id", "(\"outround_\" ~ round_id)")
            .replace(
                "{{ csrf_input|safe }}",
                "{{ csrf_input|safe }}{% if motion.revision %}<input type=\"hidden\" name=\"motion_revision\" value=\"{{ motion.revision }}\">{% endif %}",
            ),
        "tab/settings_form.html" => translated
            .replace("{{ field }}", "{{ field.html|safe }}")
            .replace("{{ field.errors }}", "{{ field.errors|safe }}"),
        "ui/form.html" => translated
            .replace("field|uses_file_input", "field.is_file")
            .replace("{{ field }}", "{{ field.input|safe }}"),
        "tab/school_detail.html" => translated.replace("team.debaters.all", "team.debaters"),
        "tab/room_detail.html" => translated
            .replace("round.judges.all", "round.judges")
            .replace("judge != round.chair", "judge.id != round.chair_id")
            .replace("judge != outround.chair", "judge.id != outround.chair_id"),
        "tab/debater_detail.html" => translated
            .replace("round.round.judges.all", "round.round.judges")
            .replace("judge != round.round.chair", "judge.id != round.round.chair_id")
            .replace("debater_obj.ranking_groups.count", "ranking_groups")
            .replace("debater_obj.ranking_groups.all", "ranking_groups")
            .replace("{{ team_school.team }}", "{{ team_school.team.name }}")
            .replace("{{ team_school.school }}", "{{ team_school.school.name }}")
            .replace("{{ team_school.hybrid_school }}", "{{ team_school.hybrid_school.name }}"),
        "tab/team_detail.html" => translated
            .replace("team_obj.debaters.all", "team_obj.debaters")
            .replace("team_obj.ranking_groups.count", "team_obj.ranking_groups")
            .replace("team_obj.ranking_groups.all", "team_obj.ranking_groups"),
        "common/_audit_trail.html" => translated.replace(
            "event.changes.fields|join:\", \"",
            "event.changes.fields|join(\", \")",
        ),
        "tab/all_tab_cards.html" => translated.replace("loop.index|divisibleby:2", "loop.index % 2 == 0"),
        "tab/tab_card.html" => {
            let mut translated = translated.replace("|slice:\":1\"", "");
            for index in 0..7 {
                translated = translated.replace(&format!("round_stat.{index}"), &format!("round_stat[{index}]"));
            }
            translated
        }
        "apda_board/tab_cards.html" => translated.replace("|join:\", \"", "|join(\", \")"),
        "common/data_upload.html" => translated.replace("kind|capfirst", "kind|capitalize"),
        "common/data_entry_multiple.html" => translated
            .replace("event.changes.fields|join:\", \" ", "event.changes.fields|join(\", \") ")
            .replace(
                "{{ csrf_input|safe }}",
                "{{ csrf_input|safe }}{% if scratch_error %}<div class=\"alert alert-danger\" role=\"alert\">{{ scratch_error }}</div>{% endif %}",
            ),
        "tab/rank_teams_component.html" | "tab/rank_debaters_component.html" => {
            // Direct fragment requests can also be a saved form's return target.
            [
                "{% if ranking_notice %}<div class=\"col-12\"><div class=\"alert alert-{{ 'danger' if ranking_notice.error else 'success' }}\">{{ ranking_notice.text }}</div></div>{% endif %}",
                &translated,
            ].concat()
        }
        "tab/round_stats.html" => translated
            .replace("|floatformat:1", "|django_floatformat(1)")
            .replace("|default:\"N/A\"", "|django_default(\"N/A\")"),
        "tab/_rank_teams_table.html" => translated
            .replace("|default_if_none:True", "|default(true)")
            .replace("teams.0|length", "teams[0]|django_length")
            .replace("team.0.", "team[0].")
            .replace("team.1", "team[1]")
            .replace("team.2", "team[2]")
            .replace("team.3", "team[3]")
            .replace("team.4", "team[4]"),
        "tab/_rank_debaters_table.html" => translated.replace("debaters.0.", "debaters[0]."),
        "ballots/previous_ballots.html" => translated
            .replace("row.round.round_number", "row.number")
            .replace("row.round.gov_team.display", "row.government")
            .replace("row.round.opp_team.display", "row.opposition")
            .replace("{% if row.winner %}{{ row.winner.display }}{% else %}{{ row.round.get_victor_display }}{% endif %}", "{{ row.outcome }}"),
        "ballots/_form.html" => translated
            .replace("form.errors|django_pluralize", "form.errors|length|django_pluralize")
            .replace("{{ hidden }}", "{{ hidden|safe }}"),
        "ballots/ballot_submitted.html" => translated
            .replace("<form method=\"post\" action=\"\">", "<form method=\"post\" action=\"{{ rfd_action }}\">")
            .replace("|default:\"No written RFD has been entered.\"", "|django_default(\"No written RFD has been entered.\")"),
        "registration/admin_list.html" => {
            let mut translated = translated
                .replace("form.judge_list_is_public|yesno:'true,false'", "form.judge_list_is_public|django_yesno('true,false')")
                .replace("registration.teams.all", "registration.teams")
                .replace("registration.judges.all", "registration.judges")
                .replace("team.debaters.all", "team.debaters")
                .replace("judge.schools.all", "judge.schools")
                .replace("registration.change_logs.all", "registration.change_logs")
                .replace("logs|slice:\":3\"", "logs")
                .replace("log.created_at|date:\"M j, H:i\"", "log.created_display")
                .replace("registration.created_at|date:\"M j, Y, H:i\"", "registration.created_display")
                .replace("registration.updated_at|date:\"M j, Y, H:i\"", "registration.updated_display")
                .replacen("{{ csrf_input|safe }}", "{{ csrf_input|safe }}{{ settings_revision|safe }}", 1)
                .replace(
                    "<input type=\"hidden\" name=\"registration_id\" value=\"{{ registration.id }}\">",
                    "<input type=\"hidden\" name=\"registration_id\" value=\"{{ registration.id }}\">{{ registration.delete_field }}",
                );
            for key in [
                "confirm_judge_list_publication", "allow_new_registrations", "allow_registration_edits",
                "new_registrations_open_at", "new_registrations_close_at", "registration_edits_open_at",
                "registration_edits_close_at", "crash_housing_enabled", "team_name_changes_allowed",
                "disc_scratches_open", "disc_scratch_quantity",
            ] {
                translated = translated.replace(&format!("{{{{ form.{key} }}}}"), &format!("{{{{ form.{key}.input }}}}"));
            }
            translated
        }
        "tab/public_homepage.html" => translated
            .replace("{{ form.tournament_name }}", "{{ form.tournament_name.input }}")
            .replace("{{ form.theme_color }}", "{{ form.theme_color.input }}")
            .replace("{{ form.tournament_logo }}", "{{ form.tournament_logo.input }}")
            .replace("|striptags", "")
            .replace("|default:'#00438A'", "|django_default('#00438A')"),
        "registration/portal.html" => translated
            .replace("{{ csrf_input|safe }}", "{{ csrf_input|safe }}{{ revision_input|safe }}")
            .replace("summary.teams.all", "summary.teams")
            .replace("summary.judges.all", "summary.judges")
            .replace("team.debaters.all", "team.debaters")
            .replace("management_form }}", "management_form|safe }}"),
        "registration/_team_form.html" => translated
            .replace("{{ hidden }}", "{{ hidden|safe }}")
            .replace("{{ form.team_school_source }}", "{{ form.team_school_source.input }}")
            .replace("{{ form.seed_choice }}", "{{ form.seed_choice.input }}")
            .replace("class=\"registration-subform\"", "class=\"registration-subform{% if form.deleted %} d-none{% endif %}\""),
        "registration/_judge_form.html" => translated
            .replace("{{ hidden }}", "{{ hidden|safe }}")
            .replace("{{ form.needs_crash_housing }}", "{{ form.needs_crash_housing.input }}")
            .replace("{{ form.requests_wgm_housing }}", "{{ form.requests_wgm_housing.input }}")
            .replace("{{ form.schools }}", "{{ form.schools.input }}")
            .replace("{{ availability.field }}", "{{ availability.field.input }}")
            .replace("class=\"registration-subform\"", "class=\"registration-subform{% if form.deleted %} d-none{% endif %}\""),
        "registration/_input_group.html" => translated
            .replace("margin|default:''", "margin|default('')")
            .replace("{{ field }}", "{{ field.input }}"),
        "registration/_debater_card.html" => {
            let mut translated = translated
                .replace("prefix|add:", "prefix ~ ")
                .replace("{{ housing_field }}", "{{ housing_field.input }}")
                .replace("{{ wgm_field }}", "{{ wgm_field.input }}");
            for key in ["name", "email", "school", "novice", "housing", "wgm"] {
                translated = translated.replace(
                    &format!("form|get_field:{key}_key"), &format!("form[{key}_key]"),
                );
            }
            translated
        }
        "registration/team_portal.html" => translated
            .replace("team.debaters.all", "team.debaters")
            .replace("r.opponent.debaters.all", "r.opponent.debaters")
            .replace("judge != r.chair", "judge.id != r.chair.id")
            .replace("{{ name_form.name }}", "{{ name_form.name.input|safe }}")
            .replace("for field in scratch_form", "for field in scratch_form.fields")
            .replace("{{ field }}", "{{ field.input|safe }}"),
        "pairing/manage_room_tags.html" => translated.replace("tag.tag", "tag.name"),
        "pairing/manage_ranking_groups.html" => translated
            .replace("group.teams.count", "group.team_count")
            .replace("group.debaters.count", "group.debater_count"),
        "tab/judge_speaker_points.html" => translated
            .replace("{{ filters.minimum_rounds }}", "{{ filters.minimum_rounds.html|safe }}")
            .replace("metrics|django_pluralize", "metrics|length|django_pluralize")
            .replace("minimum_rounds|django_pluralize", "('' if minimum_rounds == '1' else 's')")
            .replace("row.mean }}", "row.mean_text }}")
            .replace("row.generosity }}", "row.generosity_text }}")
            .replace("|unlocalize", ""),
        "tab/_speaker_points_chart.html" => translated
            .replace("point.mean }}", "point.mean_text }}")
            .replace("point.generosity }}", "point.generosity_text }}")
            .replace("|unlocalize", ""),
        "tab/_email_preview.html" => translated.replace(
            "|default:email.to_address", "|django_default(email.to_address)",
        ).replace("{{ email.html_body }}", "{{ email.html_body|isolated_email_preview }}"),
        "public/teams.html" => translated
            .replace("team.display", "team.name")
            .replace("team.school.display", "team.school")
            .replace("team.hybrid_school.name", "team.hybrid")
            .replace("team.debaters_display", "team.debaters")
            .replace("team.novice_status_display", "team.division")
            .replace("team.get_seed_display", "team.seed"),
        "public/expected_records.html" => translated.replace("|floatformat:\"-2\"", ""),
        _ => translated,
    };
    ["default", "date", "yesno", "pluralize", "truncatechars"]
        .into_iter()
        .fold(translated, translate_filter_arguments)
}

fn isolated_email_preview(html: String) -> String {
    const POLICY: &str = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'\">";
    let source = html.trim_start_matches(|c: char| c.is_ascii_whitespace() || c == '\u{feff}');
    let doctype_end = source
        .get(..9)
        .filter(|prefix| prefix.eq_ignore_ascii_case("<!doctype"))
        .and_then(|_| source.find('>'))
        .map_or(0, |offset| offset + 1);
    let (doctype, rest) = source.split_at(doctype_end);
    format!("{doctype}{POLICY}{rest}")
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
        "U" => time.and_utc().timestamp().to_string(),
        "M j, g:i a" => format!(
            "{} {}",
            time.format("%b %-d, %-I:%M"),
            if time.hour() < 12 { "a.m." } else { "p.m." }
        ),
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
        can_add_scratches => superuser || matches!(preset, Some(Preset::Scratches | Preset::ScratchRead)),
        can_view_scratches => superuser || matches!(preset, Some(Preset::ScratchRead)),
        can_checkins => superuser || preset.is_some_and(Preset::checkins),
        can_data_entry => superuser || preset.is_some_and(Preset::data_entry),
    })
}

pub async fn forbidden(
    db: &mut MySqlConnection,
    actor: Actor,
    path: &str,
) -> Result<Response, sqlx::Error> {
    let shell = shell(db, actor, path).await?;
    Ok(render(
        "common/403.html",
        shell,
        context! {},
        StatusCode::FORBIDDEN,
    ))
}

pub fn render_fragment(template: &str, page: Value) -> Result<String, minijinja::Error> {
    TEMPLATE_ENV.get_template(template)?.render(page)
}

pub fn render(template: &str, shell: Value, page: Value, status: StatusCode) -> Response {
    match render_fragment(template, context! { ..shell, ..page }) {
        Ok(page) => private((status, Html(page))),
        Err(error) => {
            eprintln!("Failed to render {template}: {}", error.kind());
            unavailable()
        }
    }
}
