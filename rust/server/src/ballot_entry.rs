use std::collections::HashMap;

use axum::{
    Extension,
    extract::{Path, Request, State},
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
};
use chrono::{FixedOffset, NaiveDateTime, Utc};
use minijinja::{Value, context};
use mittab_tabulation::ballot_entry::{Ballot, ROLE_NAMES, ROLES, Score, SpeakPolicy, score_panel};
use sqlx::{MySqlConnection, Row};

use crate::{
    App, access,
    legacy_html::{self, escape},
    legacy_session::Actor,
    login::{self, TemplateCsrf},
    private,
    registration_form::{Fields, integer_input},
    round_audit,
    settings::Settings,
    staff_pairing::{ChangeError, lock_round},
    staff_roster::{form_return_path, form_return_target, submitted_fields},
    staff_roster_fields::{input, select},
    staff_roster_notice, unavailable,
};

pub const FORM_SETTINGS: &[&str] = &[
    "decimal_speaks",
    "min_speak",
    "max_speak",
    "written_rfd_first_round",
    "written_rfd_deadline",
];

pub const SPEAK_WARNING_SETTINGS: &[&str] = &[
    "warn_judges_about_speaks",
    "low_speak_warning_threshold",
    "high_speak_warning_threshold",
];

pub const OUTCOMES: [(i32, &str); 6] = [
    (1, "Government"),
    (2, "Opposition"),
    (3, "Government by forfeit"),
    (4, "Opposition by forfeit"),
    (5, "Both lose"),
    (6, "Both win"),
];

pub(super) const DJANGO_OUTCOMES: [&str; 7] = [
    "UNKNOWN",
    "GOV",
    "OPP",
    "GOV via Forfeit",
    "OPP via Forfeit",
    "ALL DROP",
    "ALL WIN",
];

pub type Submitted = HashMap<String, String>;
const MAX_SCORE_TEXT_LEN: usize = 64;

struct Team {
    id: i32,
    name: String,
    speakers: Vec<Speaker>,
}

struct Speaker {
    id: i32,
    name: String,
}

pub struct Debate {
    id: i32,
    number: i32,
    winner: i32,
    has_scores: bool,
    rfd: String,
    teams: [Team; 2],
    scores: HashMap<String, (i32, String, i32)>,
}

pub struct RfdAccess {
    pub allowed: bool,
    pub open: bool,
    pub deadline: String,
}

pub fn rfd_access(settings: &Settings, round: i32) -> RfdAccess {
    let first = settings.integer("written_rfd_first_round", -1);
    let raw = settings.string("written_rfd_deadline", "");
    let est = FixedOffset::west_opt(5 * 3600).expect("fixed offset");
    let deadline = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M",
    ]
    .iter()
    .find_map(|format| NaiveDateTime::parse_from_str(raw.trim(), format).ok())
    .and_then(|local| local.and_local_timezone(est).single());
    let allowed = first >= 1 && round >= first;
    RfdAccess {
        allowed,
        open: allowed && deadline.is_some_and(|deadline| Utc::now() <= deadline),
        deadline: deadline.map_or_else(String::new, |date| {
            date.format("%b %d, %Y at %I:%M %p EST").to_string()
        }),
    }
}

fn policy(settings: &Settings) -> SpeakPolicy {
    let step = match settings.integer("decimal_speaks", 0) {
        1 | -3 => 5_000,
        2 | -2 => 1_000,
        3 | -1 => 100,
        _ => 10_000,
    };
    SpeakPolicy {
        minimum: settings
            .integer("min_speak", 0)
            .saturating_mul(10_000)
            .max(-999_999),
        maximum: settings
            .integer("max_speak", 50)
            .saturating_mul(10_000)
            .min(999_999),
        step,
    }
}

pub async fn load(db: &mut MySqlConnection, id: i32) -> Result<Option<Debate>, sqlx::Error> {
    let Some(row) = sqlx::query(
        "SELECT r.round_number, r.victor, r.rfd, g.id gov, g.name government, o.id opp, o.name opposition,
                EXISTS(SELECT 1 FROM tab_roundstats WHERE round_id = r.id) has_scores
           FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id
           JOIN tab_team o ON o.id = r.opp_team_id WHERE r.id = ?",
    ).bind(id).fetch_optional(&mut *db).await? else {
        return Ok(None);
    };
    let mut teams = [
        Team {
            id: row.try_get("gov")?,
            name: row.try_get("government")?,
            speakers: Vec::new(),
        },
        Team {
            id: row.try_get("opp")?,
            name: row.try_get("opposition")?,
            speakers: Vec::new(),
        },
    ];
    for (team_id, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
        "SELECT m.team_id, d.id, d.name FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id
         WHERE m.team_id IN (?, ?) ORDER BY d.name, d.id",
    ).bind(teams[0].id).bind(teams[1].id).fetch_all(&mut *db).await? {
        for team in teams.iter_mut().filter(|team| team.id == team_id) {
            team.speakers.push(Speaker { id, name: name.clone() });
        }
    }
    let scores = sqlx::query_as::<_, (String, i32, String, String)>(
        "SELECT debater_role, debater_id, CAST(speaks AS CHAR), CAST(ranks AS CHAR)
         FROM tab_roundstats WHERE round_id = ? AND debater_role IN ('pm','mg','lo','mo') ORDER BY id",
    ).bind(id).fetch_all(db).await?.into_iter().map(|(role, id, speaks, ranks)| {
        (role, (id, speaks, display_rank(&ranks)))
    }).collect();
    Ok(Some(Debate {
        id,
        number: row.try_get("round_number")?,
        winner: row.try_get("victor")?,
        has_scores: row.try_get("has_scores")?,
        rfd: row.try_get("rfd")?,
        teams,
        scores,
    }))
}

pub(super) fn display_rank(value: &str) -> i32 {
    value
        .parse::<f64>()
        .expect("stored decimal rank")
        .round_ties_even() as i32
}

#[derive(Default)]
pub struct BallotErrors {
    fields: HashMap<String, String>,
}

fn django_choices(options: &[&str], start: i32) -> Vec<(String, String)> {
    (start..)
        .zip(options)
        .map(|(index, text)| (index.to_string(), (*text).to_owned()))
        .collect()
}

fn speaker_choices(debate: &Debate, side: usize) -> Vec<(String, String)> {
    std::iter::once(("-1".into(), "---".into()))
        .chain(
            debate.teams[side]
                .speakers
                .iter()
                .map(|speaker| (speaker.id.to_string(), speaker.name.clone())),
        )
        .collect()
}

fn score_step(settings: &Settings) -> (&'static str, &'static str) {
    match policy(settings).step {
        5_000 => ("0.5", "Speaks must be in half-point increments"),
        1_000 => ("0.1", "Speaks may have at most 1 decimal digit"),
        100 => ("0.01", "Speaks may have at most 2 decimal digits"),
        _ => ("1", "Speaks must be whole numbers"),
    }
}

fn choice(raw: &str, options: &[(String, String)]) -> Result<i32, String> {
    if raw.is_empty() {
        return Err("This field is required.".into());
    }
    if !options.iter().any(|(choice, _)| choice == raw) {
        return Err(format!(
            "Select a valid choice. {raw} is not one of the available choices."
        ));
    }
    Ok(raw.parse().expect("numeric ballot choice"))
}

fn parse_speaks(raw: &str, settings: &Settings) -> Result<i32, String> {
    if raw.len() > MAX_SCORE_TEXT_LEN {
        return Err("Enter a valid speaker score.".into());
    }
    let raw = raw.trim();
    if raw.is_empty() {
        return Err("This field is required.".into());
    }
    if raw.rsplit_once(['e', 'E']).is_some_and(|(_, exponent)| {
        exponent
            .parse::<i64>()
            .is_ok_and(|value| !(-999..=999).contains(&value))
    }) {
        return Err("Enter a valid speaker score.".into());
    }
    let parsed: bigdecimal::BigDecimal = raw.parse().map_err(|_| "Enter a number.")?;
    let policy = policy(settings);
    // Compare using Decimal before converting to the database's fixed precision.
    let whole_limit = |limit| bigdecimal::BigDecimal::new(limit, 4);
    if parsed < whole_limit(policy.minimum.into()) || parsed > whole_limit(policy.maximum.into()) {
        return Err(format!(
            "{parsed} is an entirely invalid speaker score, try again."
        ));
    }
    let (integer, scale) = parsed.as_bigint_and_exponent();
    let digits = integer.to_string();
    if digits == "0" {
        return Ok(0);
    }
    let significant = digits.trim_end_matches('0');
    let shift = 4_i64
        .saturating_sub(scale)
        .saturating_add((digits.len() - significant.len()) as i64);
    let invalid_step = || score_step(settings).1.to_owned();
    let factor = u32::try_from(shift)
        .ok()
        .and_then(|shift| 10_i32.checked_pow(shift))
        .ok_or_else(invalid_step)?;
    let units = significant
        .parse::<i32>()
        .ok()
        .and_then(|n| n.checked_mul(factor))
        .ok_or_else(invalid_step)?;
    if units % policy.step != 0 {
        return Err(invalid_step());
    }
    Ok(units)
}

struct JudgeRules<'a> {
    code: &'a str,
    min: i32,
    max: i32,
}

fn ballot_value<'a>(submitted: &'a Submitted, prefix: &str, name: &str) -> &'a str {
    let value = if prefix.is_empty() {
        submitted.get(name)
    } else {
        submitted.get(&format!("{prefix}{name}"))
    };
    value.map_or("", String::as_str)
}

pub fn judge_ballot(
    debate: &Debate,
    settings: &Settings,
    min: i32,
    max: i32,
    code: &str,
    submitted: &Submitted,
) -> Result<WriteBallot, BallotErrors> {
    validate_ballot(
        debate,
        settings,
        submitted,
        "",
        Some(JudgeRules { code, min, max }),
    )
    .map(|ballot| scored_ballot(&ballot))
}

fn validate_ballot(
    debate: &Debate,
    settings: &Settings,
    submitted: &Submitted,
    prefix: &str,
    judge: Option<JudgeRules<'_>>,
) -> Result<Ballot, BallotErrors> {
    let field = |name: &str| ballot_value(submitted, prefix, name);
    let mut errors = BallotErrors::default();
    let mut parse = |name: &str, result: Result<i32, String>| {
        result.unwrap_or_else(|error| {
            errors.fields.insert(name.into(), error);
            0
        })
    };
    let winner = parse(
        "winner",
        choice(field("winner"), &django_choices(&DJANGO_OUTCOMES, 0)),
    );
    let ranks = django_choices(&["1", "2", "3", "4"], 1);
    let teams = [speaker_choices(debate, 0), speaker_choices(debate, 1)];
    let scores = std::array::from_fn(|index| {
        let role = ROLES[index];
        let debater = format!("{role}_debater");
        let speaks = format!("{role}_speaks");
        let rank = format!("{role}_ranks");
        Score {
            debater: parse(&debater, choice(field(&debater), &teams[index / 2])),
            speaks: parse(&speaks, parse_speaks(field(&speaks), settings)),
            rank: parse(&rank, choice(field(&rank), &ranks)),
        }
    });
    if judge.is_none() && integer_input(field("round_instance").trim()).is_err() {
        let message = if field("round_instance").is_empty() {
            "This field is required."
        } else {
            "Enter a whole number."
        };
        errors
            .fields
            .insert("round_instance".into(), message.into());
    }
    let entered = field("ballot_code").trim();
    if judge.is_some()
        && let Some(message) = code_field_error(entered)
    {
        errors.fields.insert("ballot_code".into(), message);
    }
    if prefix.is_empty()
        && rfd_access(settings, debate.number).allowed
        && field("rfd").contains('\0')
    {
        errors
            .fields
            .insert("rfd".into(), "Null characters are not allowed.".into());
    }
    if !errors.fields.is_empty() {
        return Err(errors);
    }

    let mut order = [0, 1, 2, 3];
    order.sort_by_key(|&index| scores[index].rank);
    if order.map(|index| scores[index].rank) != [1, 2, 3, 4] {
        for role in ROLES {
            errors
                .fields
                .insert(format!("{role}_ranks"), "Ranks must be different".into());
        }
    }
    for pair in order.windows(2) {
        if scores[pair[0]].speaks < scores[pair[1]].speaks {
            let role = ROLES[pair[1]];
            errors.fields.insert(
                format!("{role}_speaks"),
                "These speaks are too high for the rank".into(),
            );
        }
    }
    for (role, score) in ROLES.into_iter().zip(scores) {
        if score.debater == -1 {
            errors.fields.insert(
                format!("{role}_debater"),
                "You need to pick a debater".into(),
            );
        }
    }
    if winner == 0 {
        errors
            .fields
            .insert("winner".into(), "Someone has to win!".into());
    }
    if !errors.fields.is_empty() {
        return Err(errors);
    }

    let eligible = debate.teams.each_ref().map(|team| {
        team.speakers
            .iter()
            .map(|speaker| speaker.id)
            .collect::<Vec<_>>()
    });
    let ballot = Ballot { winner, scores };
    if let Err(message) = ballot.validate(policy(settings), [&eligible[0], &eligible[1]]) {
        // Outcome, roster, increments, and rank order were already checked above.
        errors
            .fields
            .insert("winner".into(), "Low Point Win!!".into());
        debug_assert_eq!(
            message,
            "The selected winner received fewer speaker points or lower ranks."
        );
        return Err(errors);
    }

    if let Some(judge) = judge {
        if !entered.eq_ignore_ascii_case(judge.code) {
            errors.fields.insert(
                "ballot_code".into(),
                "Incorrect judge code. Enter again.".into(),
            );
        }
        if !matches!(winner, 1 | 2) {
            errors.fields.insert(
                "winner".into(),
                "Go to tab to submit a result other than a win or loss.".into(),
            );
        }
        let bounds = judge.min.saturating_mul(10_000)..=judge.max.saturating_mul(10_000);
        for (role, score) in ROLES.into_iter().zip(scores) {
            if !bounds.contains(&score.speaks) {
                errors.fields.insert(
                    format!("{role}_speaks"),
                    "Speaks must be justified to tab.".into(),
                );
            }
        }
    }
    if !errors.fields.is_empty() {
        return Err(errors);
    }
    Ok(ballot)
}

fn code_field_error(value: &str) -> Option<String> {
    let length = value.chars().count();
    if value.is_empty() {
        return Some("This field is required.".into());
    }
    if length > 30 {
        return Some(format!(
            "Ensure this value has at most 30 characters (it has {length})."
        ));
    }
    value
        .contains('\0')
        .then(|| "Null characters are not allowed.".into())
}

fn rfd_field(access: RfdAccess, value: &str, error: Option<&String>) -> Value {
    let help_text = if access.open {
        format!(
            "Optional. You may submit the ballot without an RFD and edit this text later until {}.",
            access.deadline
        )
    } else if access.deadline.is_empty() {
        "Tab has not configured the written RFD deadline yet.".into()
    } else {
        format!("The written RFD deadline was {}.", access.deadline)
    };
    let label_tag =
        Value::from_safe_string(r#"<label for="id_rfd">Reason for decision:</label>"#.into());
    let help_id = "id_rfd_helptext";
    let error_id = error.map(|_| "id_rfd_error");
    let class = if error.is_some() {
        "form-control form-control is-invalid"
    } else {
        "form-control form-control"
    };
    let readonly = if access.open {
        ""
    } else {
        r#" readonly="readonly""#
    };
    let invalid = if error.is_some() {
        r#" aria-invalid="true""#
    } else {
        ""
    };
    let description = if error.is_some() {
        "id_rfd_helptext id_rfd_error"
    } else {
        help_id
    };
    let input = format!(
        r#"<textarea name="rfd" cols="40" rows="6" class="{class}"{readonly}{invalid} aria-describedby="{description}" id="id_rfd">
{}</textarea>"#,
        escape(value),
    );
    let errors: Vec<_> = error.into_iter().collect();
    context! { label_tag, input, help_id, help_text, error_id, errors }
}

pub fn judge_context(
    debate: &Debate,
    settings: &Settings,
    warnings: &Settings,
    code: &str,
    action: &str,
    return_path: &str,
    bound: Option<(&Submitted, &BallotErrors)>,
) -> Value {
    let form = ballot_fields(debate, settings, "", Some(code), bound);
    let portal_url = format!("/public/e-ballots/{code}/");
    let previous_ballots_url = format!("{portal_url}previous/");
    let page = ballot_context(debate, warnings, return_path, false);
    context! { form, ballot_code => code, action, portal_url, previous_ballots_url, ..page }
}

fn ballot_fields(
    debate: &Debate,
    settings: &Settings,
    prefix: &str,
    code: Option<&str>,
    bound: Option<(&Submitted, &BallotErrors)>,
) -> Value {
    let field = |name: &str, initial: &str| {
        bound.map_or_else(
            || initial.to_owned(),
            |(submitted, _)| ballot_value(submitted, prefix, name).to_owned(),
        )
    };
    let error = |name: &str| bound.and_then(|(_, errors)| errors.fields.get(name));
    let name = |key: &str| format!("{prefix}{key}");
    let prefill = prefix.is_empty() && debate.winner != 0;
    let winner = if prefill {
        debate.winner.to_string()
    } else {
        String::new()
    };
    let mut form = HashMap::from([(
        "winner".into(),
        select(
            &name("winner"),
            "Which team won the round?",
            &django_choices(&DJANGO_OUTCOMES, 0),
            &field("winner", &winner),
            false,
            false,
            error("winner").cloned(),
        ),
    )]);
    if let Some(code) = code {
        form.insert(
            "ballot_code".into(),
            input(
                "ballot_code",
                "Judge code:",
                "text",
                &field("ballot_code", code),
                r#" maxlength="30" minlength="0""#,
                true,
                error("ballot_code").cloned(),
            ),
        );
    }
    let ranks = django_choices(&["1", "2", "3", "4"], 1);
    let teams = [speaker_choices(debate, 0), speaker_choices(debate, 1)];
    let step = format!(r#" step="{}""#, score_step(settings).0);
    for (index, (role, label)) in ROLES.into_iter().zip(ROLE_NAMES).enumerate() {
        let prior = prefill.then(|| debate.scores.get(role)).flatten();
        let debater = format!("{role}_debater");
        let initial = prior.map_or_else(String::new, |(id, _, _)| id.to_string());
        form.insert(
            debater.clone(),
            select(
                &name(&debater),
                &format!("Who was {label}?"),
                &teams[index / 2],
                &field(&debater, &initial),
                false,
                false,
                error(&debater).cloned(),
            ),
        );
        let speaks = format!("{role}_speaks");
        let value = field(&speaks, prior.map_or("", |(_, speaks, _)| speaks));
        form.insert(
            speaks.clone(),
            input(
                &name(&speaks),
                &format!("{label} Speaks:"),
                "number",
                if value.len() <= MAX_SCORE_TEXT_LEN {
                    &value
                } else {
                    ""
                },
                &step,
                true,
                error(&speaks).cloned(),
            ),
        );
        let rank = format!("{role}_ranks");
        let initial = prior.map_or_else(String::new, |(_, _, rank)| rank.to_string());
        form.insert(
            rank.clone(),
            select(
                &name(&rank),
                &format!("{label} Rank:"),
                &ranks,
                &field(&rank, &initial),
                false,
                false,
                error(&rank).cloned(),
            ),
        );
    }
    if prefix.is_empty() {
        let rfd = rfd_access(settings, debate.number);
        if rfd.allowed {
            form.insert(
                "rfd".into(),
                rfd_field(rfd, &field("rfd", &debate.rfd), error("rfd")),
            );
        }
    }
    let round_name = name("round_instance");
    let id = debate.id.to_string();
    let hidden_value = match bound {
        Some((submitted, _)) => submitted.get(&round_name).map(String::as_str),
        None => Some(id.as_str()),
    };
    let value = hidden_value.map_or_else(String::new, |value| {
        format!(r#" value="{}""#, escape(value))
    });
    let hidden =
        format!(r#"<input type="hidden" name="{round_name}"{value} id="id_{round_name}">"#);
    form.insert("hidden_fields".into(), Value::from_serialize([hidden]));
    if let Some((_, errors)) = bound {
        form.insert(
            "errors".into(),
            Value::from_serialize(vec![true; errors.fields.len()]),
        );
    }
    Value::from_serialize(form)
}

fn ballot_context(debate: &Debate, warnings: &Settings, return_path: &str, panel: bool) -> Value {
    let [gov, opp] = debate.teams.each_ref().map(|team| team.name.as_str());
    let ballots = if panel { "Ballots" } else { "Ballot" };
    let title = format!(
        "Entering {ballots} for Round {} between {gov} and {opp}",
        debate.number
    );
    let warn_judges_about_speaks = match warnings.value("warn_judges_about_speaks") {
        Some(crate::settings::SettingValue::Number(value)) => value.to_string(),
        Some(crate::settings::SettingValue::Text(value)) if value.eq_ignore_ascii_case("false") => {
            "false".into()
        }
        _ => "true".into(),
    };
    context! {
        title, return_path,
        gov_team => context! { display => gov }, opp_team => context! { display => opp },
        warn_judges_about_speaks,
        low_speak_warning_threshold => warnings.integer("low_speak_warning_threshold", 25),
        high_speak_warning_threshold => warnings.integer("high_speak_warning_threshold", 34),
    }
}

pub async fn single_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path(round): Path<i32>,
) -> Response {
    staff_response(&app, actor, &headers, &uri, round, None, None).await
}

pub async fn panel_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path((round, count)): Path<(i32, usize)>,
) -> Response {
    staff_response(&app, actor, &headers, &uri, round, Some(count.max(1)), None).await
}

pub async fn single(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(round): Path<i32>,
    request: Request,
) -> Response {
    submit(&app, actor, round, None, request).await
}

pub async fn panel(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((round, count)): Path<(i32, usize)>,
    request: Request,
) -> Response {
    submit(&app, actor, round, Some(count.max(1)), request).await
}

async fn submit(
    app: &App,
    actor: Actor,
    id: i32,
    count: Option<usize>,
    request: Request,
) -> Response {
    let headers = request.headers().clone();
    let uri = request.uri().clone();
    let fields = match submitted_fields(app, request).await {
        Ok(fields) => fields,
        Err(error) => return *error,
    };
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    staff_response(app, actor, &headers, &uri, id, count, Some(fields)).await
}

async fn staff_response(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    uri: &Uri,
    id: i32,
    count: Option<usize>,
    fields: Option<Fields>,
) -> Response {
    if count.is_some_and(|count| count > 100) {
        return private((
            StatusCode::BAD_REQUEST,
            "Submit at most 100 ballots in one panel.",
        ));
    }
    let path = uri.path();
    let return_path = form_return_path(uri, headers, fields.as_ref());
    let result = async {
        let mut db = app.db.begin().await?;
        let actor = if fields.is_some() {
            lock_round(&mut db).await?;
            sqlx::query("SELECT id FROM tab_round WHERE id = ? FOR UPDATE")
                .bind(id)
                .fetch_optional(&mut *db)
                .await?;
            match access::code_portal_actor(&mut db, app, actor, headers).await {
                Ok(actor) => actor,
                Err(error) => {
                    db.rollback().await?;
                    return Ok(error.response(app, headers).await);
                }
            }
        } else {
            actor
        };
        let Actor::User {
            id: user,
            superuser,
            staff,
            board: false,
            ..
        } = actor
        else {
            return Ok(access::access_denied());
        };
        if fields.is_some()
            && !superuser
            && (staff
                || !(access::legacy_entry_user(&mut *db, user).await?
                    || access::user_permission(&mut *db, user, "tab_settings.can_change").await?))
        {
            return Ok(access::access_denied());
        }
        let settings = Settings::load(&mut db, FORM_SETTINGS).await?;
        let Some(debate) = load(&mut db, id).await? else {
            return Ok(private((StatusCode::NOT_FOUND, "Ballot not found.")));
        };
        let panel = count.is_some();
        let prefixes: Vec<_> = (1..=count.unwrap_or(1))
            .map(|index| {
                if panel {
                    format!("{index}-")
                } else {
                    String::new()
                }
            })
            .collect();
        let mut errors = Vec::new();
        let mut submitted = None;
        if let Some(fields) = fields {
            let target = if panel {
                form_return_target(uri, headers, Some(&fields)).unwrap_or_else(|| "/".into())
            } else {
                "/pairings/status/".into()
            };
            let form: Submitted = fields.into_last_values().collect();
            let rfd_only = !panel
                && debate.has_scores
                && form.contains_key("rfd")
                && !form.contains_key("winner");
            let rfd_open = !panel && rfd_access(&settings, debate.number).open;
            let wrong_debate = if rfd_only {
                form.get("round_instance")
                    .is_some_and(|value| integer_input(value.trim()).ok() != Some(id))
            } else {
                prefixes
                    .iter()
                    .filter_map(|prefix| {
                        integer_input(ballot_value(&form, prefix, "round_instance").trim()).ok()
                    })
                    .any(|posted| posted != id)
            };
            if wrong_debate {
                return Ok(private((
                    StatusCode::UNPROCESSABLE_ENTITY,
                    "This ballot belongs to a different debate.",
                )));
            }
            if rfd_only && !rfd_open {
                return Ok(staff_roster_notice::failed(
                    app,
                    headers,
                    path,
                    "Written RFDs cannot be edited for this ballot.",
                ));
            }
            let scores = if rfd_only {
                Ok(WriteBallot::Rfd)
            } else {
                staff_ballot(&debate, &settings, &form, &prefixes, panel)
            };
            match scores {
                Ok(ballot) => {
                    let before = round_audit::capture_one(&mut db, debate.number, id).await?;
                    save(&mut db, &debate, ballot).await?;
                    if rfd_open && let Some(reason) = form.get("rfd") {
                        sqlx::query("UPDATE tab_round SET rfd = ? WHERE id = ?")
                            .bind(reason.trim())
                            .bind(id)
                            .execute(&mut *db)
                            .await?;
                    }
                    let after = round_audit::capture_one(&mut db, debate.number, id).await?;
                    let source = if panel {
                        "panel ballot entry"
                    } else {
                        "ballot entry"
                    };
                    round_audit::record(&mut db, &before, &after, user, source).await?;
                    db.commit().await?;
                    let (destination, message) = if rfd_only {
                        (path, "Written RFD saved.")
                    } else if panel {
                        (target.as_str(), "Round entered successfully")
                    } else {
                        (target.as_str(), "Result entered successfully")
                    };
                    return Ok(staff_roster_notice::saved(
                        app,
                        headers,
                        destination,
                        message,
                    ));
                }
                Err(invalid) => {
                    submitted = Some(form);
                    errors = invalid;
                }
            }
        }

        let warnings = Settings::load(&mut db, SPEAK_WARNING_SETTINGS).await?;
        let shell = legacy_html::shell(&mut db, actor, path).await?;
        db.commit().await?;
        let csrf = TemplateCsrf::new(headers).map_err(|_| ChangeError::Storage)?;
        let page = ballot_context(&debate, &warnings, &return_path, panel);
        let mut forms = prefixes.iter().enumerate().map(|(index, prefix)| {
            let bound = submitted.as_ref().zip(errors.get(index));
            ballot_fields(&debate, &settings, prefix, None, bound)
        });
        let (template, page) = if panel {
            (
                "ballots/round_entry_multiple.html",
                context! { forms => forms.collect::<Vec<_>>(), ..page },
            )
        } else {
            (
                "ballots/round_entry.html",
                context! { form => forms.next(), action => path, ..page },
            )
        };
        let mut response = staff_roster_notice::render(
            app,
            headers,
            path,
            template,
            shell,
            context! { csrf_input => csrf.input(), ..page },
            if submitted.is_some() {
                StatusCode::UNPROCESSABLE_ENTITY
            } else {
                StatusCode::OK
            },
        );
        csrf.set_cookie(&mut response, headers);
        Ok::<_, ChangeError>(response)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

pub enum WriteBallot {
    Rfd,
    Scores(i32, Vec<(usize, i32, String, i32)>),
}

pub fn simulate(
    debate: &Debate,
    random: &mut mittab_tabulation::legacy_random::LegacyRandom,
) -> Result<WriteBallot, &'static str> {
    let team = random.below(2);
    let mut points = [0; 4];
    for value in &mut points {
        *value = random.below(20) + 15;
    }
    points.sort_unstable_by(|left, right| right.cmp(left));
    let mut positions = Vec::new();
    for (offset, side) in [team, 1 - team].into_iter().enumerate() {
        let speakers = &debate.teams[side].speakers;
        let Some(first) = speakers.first() else {
            return Err("Every team needs at least one debater to simulate this round.");
        };
        let last = speakers.last().unwrap_or(first);
        for (index, (speaker, role)) in [(first, side * 2 + 1), (last, side * 2)]
            .into_iter()
            .enumerate()
        {
            let rank = offset * 2 + index;
            positions.push((role, speaker.id, points[rank].to_string(), rank as i32 + 1));
        }
    }
    Ok(WriteBallot::Scores(team as i32 + 1, positions))
}

fn staff_ballot(
    debate: &Debate,
    settings: &Settings,
    form: &Submitted,
    prefixes: &[String],
    panel: bool,
) -> Result<WriteBallot, Vec<BallotErrors>> {
    let mut ballots = Vec::new();
    let mut errors = Vec::new();
    for prefix in prefixes {
        match validate_ballot(debate, settings, form, prefix, None) {
            Ok(ballot) => {
                ballots.push(ballot);
                errors.push(BallotErrors::default());
            }
            Err(error) => errors.push(error),
        }
    }
    if ballots.len() != prefixes.len() {
        return Err(errors);
    }
    if !panel {
        return Ok(scored_ballot(&ballots[0]));
    }
    match score_panel(&ballots, form.contains_key("discard_minority")) {
        Ok(result) => Ok(WriteBallot::Scores(
            result.outcome,
            result
                .scores
                .into_iter()
                .map(|score| {
                    (
                        score.position,
                        score.debater,
                        format!("{:.4}", score.speaks),
                        score.rank as i32,
                    )
                })
                .collect(),
        )),
        Err(_) => {
            errors[0].fields.insert(
                "winner".into(),
                "Inconsistent Panel results, please check yourself".into(),
            );
            Err(errors)
        }
    }
}

fn scored_ballot(ballot: &Ballot) -> WriteBallot {
    WriteBallot::Scores(
        ballot.winner,
        ballot
            .scores
            .iter()
            .enumerate()
            .map(|(position, score)| {
                (
                    position,
                    score.debater,
                    format!("{:.4}", f64::from(score.speaks) / 10_000.0),
                    score.rank,
                )
            })
            .collect(),
    )
}

pub async fn save(
    db: &mut MySqlConnection,
    debate: &Debate,
    ballot: WriteBallot,
) -> Result<(), sqlx::Error> {
    if let WriteBallot::Scores(winner, scores) = ballot {
        sqlx::query("DELETE FROM tab_roundstats WHERE round_id = ? AND debater_role IN ('pm', 'mg', 'lo', 'mo')")
            .bind(debate.id).execute(&mut *db).await?;
        for (position, debater, speaks, ranks) in scores {
            sqlx::query(
                "INSERT INTO tab_roundstats (round_id, debater_id, debater_role, speaks, ranks)
                         VALUES (?, ?, ?, ?, ?)",
            )
            .bind(debate.id)
            .bind(debater)
            .bind(ROLES[position])
            .bind(speaks)
            .bind(ranks)
            .execute(&mut *db)
            .await?;
        }
        sqlx::query("UPDATE tab_round SET result_entered_at = IF(victor = 0, UTC_TIMESTAMP(6), result_entered_at),
                                         victor = ? WHERE id = ?")
            .bind(winner).bind(debate.id).execute(&mut *db).await?;
        sqlx::query("DELETE FROM tab_noshow WHERE round_number = ? AND no_show_team_id IN (?, ?)")
            .bind(debate.number)
            .bind(debate.teams[0].id)
            .bind(debate.teams[1].id)
            .execute(db)
            .await?;
    }
    Ok(())
}
