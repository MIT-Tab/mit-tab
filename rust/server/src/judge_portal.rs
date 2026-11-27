use std::collections::HashSet;

use axum::{
    Extension, Form,
    extract::{Path, Request, State},
    http::{HeaderMap, HeaderValue, StatusCode, Uri, header},
    response::Response,
};
use minijinja::{Value, context};
use mittab_tabulation::ballot_entry::{ROLE_NAMES, ROLES};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    ballot_entry::{self, Submitted},
    legacy_html,
    legacy_session::Actor,
    login::{self, TemplateCsrf},
    private,
    settings::Settings,
    staff_pairing::{lock_round, lock_round_if_configured},
    staff_roster::{form_return_path, submitted_fields},
    staff_roster_notice::{self, Portal},
    unavailable,
};

const SEARCH: &str = "/public/e-ballots/";
const PORTAL_SETTINGS: &[&str] = &[
    "tot_rounds",
    "cur_round",
    "pairing_released",
    "min_eballot_speak",
    "max_eballot_speak",
    "motions_enabled",
    "open_speaks",
    "open_ranks",
    "written_rfd_first_round",
];

pub async fn protect_response(mut response: Response) -> Response {
    response.headers_mut().insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("same-origin"),
    );
    response
}

pub fn valid_code(code: &str) -> bool {
    code.len() <= 30 && code_format(code)
}

pub fn code_format(code: &str) -> bool {
    if code.is_empty() {
        return false;
    }
    let mut segments = code.split('-');
    let first = segments.next().unwrap_or_default();
    let Some(second) = segments.next() else {
        return first.bytes().all(|byte| byte.is_ascii_alphanumeric());
    };
    let alpha = |segment: &str| {
        !segment.is_empty() && segment.bytes().all(|byte| byte.is_ascii_alphabetic())
    };
    alpha(first)
        && alpha(second)
        && segments.next().is_none_or(|suffix| {
            !suffix.is_empty()
                && suffix.bytes().all(|byte| byte.is_ascii_alphanumeric())
                && segments.next().is_none()
        })
}

fn target(code: &str, suffix: &str) -> String {
    format!("{SEARCH}{code}/{suffix}")
}

fn csrf_valid(headers: &HeaderMap, form: &Submitted) -> bool {
    login::valid_csrf(
        headers,
        form.get("csrfmiddlewaretoken").map_or("", String::as_str),
    )
}

fn csrf_error() -> Response {
    private((StatusCode::FORBIDDEN, "CSRF verification failed."))
}

pub async fn search(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    search_page(&app, actor, &headers, None).await
}

async fn search_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    message: Option<&str>,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let actor = access::code_portal_actor(&mut db, app, actor, headers).await?;
        let shell = legacy_html::shell(&mut db, actor, SEARCH).await?;
        db.commit().await?;
        let messages = message.map(|text| [context! { text, tags => "error", level => 40 }]);
        let mut response = form_page(
            app,
            headers,
            SEARCH,
            "public/e_ballot_search.html",
            shell,
            context! { messages },
        );
        if message.is_some() && response.status().is_success() {
            *response.status_mut() = StatusCode::UNPROCESSABLE_ENTITY;
        }
        Ok(response)
    }
    .await;
    portal_response(app, headers, result).await
}

fn form_page(
    app: &App,
    headers: &HeaderMap,
    path: &str,
    template: &str,
    shell: Value,
    page: Value,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let mut response = staff_roster_notice::render_portal(
        app,
        headers,
        Portal::Judge,
        path,
        template,
        shell,
        context! { csrf_input => csrf.input(), ..page },
    );
    csrf.set_cookie(&mut response, headers);
    response
}

fn missing_judge(app: &App, headers: &HeaderMap) -> Response {
    staff_roster_notice::portal_redirect(
        app,
        headers,
        Portal::Judge,
        SEARCH,
        "No judge was found for that code. Try submitting again, or go to tab to resolve the issue.",
        true,
    )
}

pub async fn search_submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Submitted>,
) -> Response {
    if !csrf_valid(&headers, &form) {
        return csrf_error();
    }
    let code = form.get("ballot_code").map_or("", |code| code.trim());
    if code.is_empty() {
        return staff_roster_notice::portal_redirect(
            &app,
            &headers,
            Portal::Judge,
            SEARCH,
            "Please enter the judge code provided by tab.",
            true,
        );
    }
    if !valid_code(code) {
        return search_page(
            &app,
            actor,
            &headers,
            Some("Enter the judge code provided by tab."),
        )
        .await;
    }
    access::found(target(code, ""))
}

async fn judge(
    db: &mut MySqlConnection,
    code: &str,
    lock: bool,
) -> Result<Option<(i32, String)>, sqlx::Error> {
    let sql = if lock {
        "SELECT id, name, ballot_code FROM tab_judge WHERE ballot_code = ? FOR UPDATE"
    } else {
        "SELECT id, name, ballot_code FROM tab_judge WHERE ballot_code = ?"
    };
    let row = sqlx::query_as::<_, (i32, String, String)>(sql)
        .bind(code)
        .fetch_optional(db)
        .await?;
    Ok(row
        .filter(|(_, _, stored)| valid_code(stored) && stored.eq_ignore_ascii_case(code))
        .map(|(id, name, _)| (id, name)))
}

#[derive(Serialize)]
struct Availability {
    field_name: String,
    label: String,
    checked: bool,
}

fn field(round: i32) -> String {
    if round == 0 {
        "availability_outround".into()
    } else {
        format!("availability_round_{round}")
    }
}

pub async fn portal(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path(code): Path<String>,
) -> Response {
    if !valid_code(&code) {
        return missing_judge(&app, &headers);
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let actor = access::code_portal_actor(&mut db, &app, actor, &headers).await?;
        let Some((id, name)) = judge(&mut db, &code, false).await? else {
            return Ok(missing_judge(&app, &headers));
        };
        let settings = Settings::load(&mut db, &["tot_rounds"]).await?;
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        let expected: HashSet<_> = sqlx::query_scalar::<_, i32>(
            "SELECT round_number FROM tab_judgeexpectedcheckin WHERE judge_id = ?",
        )
        .bind(id)
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .collect();
        db.commit().await?;
        let rows: Vec<_> = (0..=settings.integer("tot_rounds", 0).max(0))
            .map(|round| Availability {
                field_name: field(round),
                label: if round == 0 {
                    "Outrounds".into()
                } else {
                    format!("Round {round}")
                },
                checked: expected.contains(&round),
            })
            .collect();
        Ok(form_page(
            &app,
            &headers,
            uri.path(),
            "public/judge_portal.html",
            shell,
            context! {
                judge => context! { name },
                ballot_code => &code,
                availability_rows => rows,
                current_ballot_url => target(&code, "current/"),
                previous_ballots_url => target(&code, "previous/"),
            },
        ))
    }
    .await;
    portal_response(&app, &headers, result).await
}

pub async fn availability(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(code): Path<String>,
    Form(form): Form<Submitted>,
) -> Response {
    if !csrf_valid(&headers, &form) {
        return csrf_error();
    }
    if !valid_code(&code) {
        return missing_judge(&app, &headers);
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round_if_configured(&mut db).await?;
        let row = judge(&mut db, &code, true).await?;
        access::code_portal_actor(&mut db, &app, actor, &headers).await?;
        let Some((id, name)) = row else {
            return Ok(missing_judge(&app, &headers));
        };
        let settings = Settings::load(&mut db, &["tot_rounds"]).await?;
        let rounds = settings.integer("tot_rounds", 0).max(0);
        let existing: HashSet<_> = sqlx::query_scalar::<_, i32>(
            "SELECT round_number FROM tab_judgeexpectedcheckin WHERE judge_id = ?",
        )
        .bind(id)
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .collect();
        let desired: HashSet<_> = (0..=rounds)
            .filter(|&round| {
                form.get(&field(round))
                    .is_some_and(|value| !value.is_empty())
            })
            .collect();
        let log_changes = existing != desired && crate::roster_log::enabled(&mut db).await?;
        let removed: std::collections::BTreeSet<_> =
            existing.difference(&desired).copied().collect();
        let added: std::collections::BTreeSet<_> = desired.difference(&existing).copied().collect();
        for round in added {
            sqlx::query(
                "INSERT INTO tab_judgeexpectedcheckin (judge_id, round_number) VALUES (?, ?)",
            )
            .bind(id)
            .bind(round)
            .execute(&mut *db)
            .await?;
            if log_changes {
                crate::roster_log::availability(&mut db, &name, round, true).await?;
            }
        }
        for round in removed {
            sqlx::query(
                "DELETE FROM tab_judgeexpectedcheckin WHERE judge_id = ? AND round_number = ?",
            )
            .bind(id)
            .bind(round)
            .execute(&mut *db)
            .await?;
            if log_changes {
                crate::roster_log::availability(&mut db, &name, round, false).await?;
            }
        }
        db.commit().await?;
        Ok(staff_roster_notice::portal_redirect(
            &app,
            &headers,
            Portal::Judge,
            &target(&code, ""),
            "Availability saved.",
            false,
        ))
    }
    .await;
    portal_response(&app, &headers, result).await
}

type Assignment = (i32, i32, Option<i32>, bool);
type PortalResult = Result<Response, PortalError>;

enum PortalError {
    Storage,
    Access(access::PortalAccessError),
}

impl From<sqlx::Error> for PortalError {
    fn from(_: sqlx::Error) -> Self {
        Self::Storage
    }
}

impl From<access::PortalAccessError> for PortalError {
    fn from(error: access::PortalAccessError) -> Self {
        Self::Access(error)
    }
}

async fn portal_response(app: &App, headers: &HeaderMap, result: PortalResult) -> Response {
    match result {
        Ok(response) => response,
        Err(PortalError::Access(error)) => error.response(app, headers).await,
        Err(PortalError::Storage) => unavailable(),
    }
}

async fn assignments(
    db: &mut MySqlConnection,
    judge: i32,
    round: i32,
) -> Result<Vec<Assignment>, sqlx::Error> {
    sqlx::query_as(
        "SELECT r.id, r.round_number, r.chair_id,
                EXISTS(SELECT 1 FROM tab_roundstats s WHERE s.round_id = r.id)
           FROM tab_round r JOIN tab_round_judges j ON j.round_id = r.id
           WHERE j.judge_id = ? AND r.round_number = ? ORDER BY r.id LIMIT 2",
    )
    .bind(judge)
    .bind(round)
    .fetch_all(db)
    .await
}

pub async fn current(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path(code): Path<String>,
) -> Response {
    let return_path = form_return_path(&uri, &headers, None);
    current_response(&app, actor, &headers, &code, &return_path, None).await
}

pub async fn submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(code): Path<String>,
    request: Request,
) -> Response {
    let headers = request.headers().clone();
    let uri = request.uri().clone();
    let fields = match submitted_fields(&app, request).await {
        Ok(fields) => fields,
        Err(error) => return *error,
    };
    let return_path = form_return_path(&uri, &headers, Some(&fields));
    let form = fields.into_last_values().collect();
    current_response(&app, actor, &headers, &code, &return_path, Some(form)).await
}

async fn current_response(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    code: &str,
    return_path: &str,
    form: Option<Submitted>,
) -> Response {
    if let Some(form) = &form
        && !csrf_valid(headers, form)
    {
        return csrf_error();
    }
    if !valid_code(code) {
        return access::found(SEARCH);
    }
    let result: PortalResult = async {
        let mut db = app.db.begin().await?;
        if form.is_some() {
            lock_round(&mut db).await?;
        }
        let row = judge(&mut db, code, form.is_some()).await?;
        let actor = access::code_portal_actor(&mut db, app, actor, headers).await?;
        let Some((judge_id, _)) = row else {
            return Ok(access::found(SEARCH));
        };
        let options = Settings::load(&mut db, PORTAL_SETTINGS).await?;
        let number = options.integer("cur_round", 1).saturating_sub(1);
        if options.integer("pairing_released", 0) != 1 || number < 1 {
            return Ok(access::found(target(code, "previous/")));
        }
        let assignments = assignments(&mut db, judge_id, number).await?;
        let [(id, _, chair, submitted)] = assignments.as_slice() else {
            return Ok(access::found(target(code, "")));
        };
        if *submitted && (form.is_some() || *chair != Some(judge_id)) {
            return Ok(access::found(target(code, "submitted/")));
        }
        if *chair != Some(judge_id) {
            return Ok(private((
                StatusCode::FORBIDDEN,
                "Only the assigned chair can submit this ballot.",
            )));
        }
        if *submitted {
            let Some(ballot) = submitted_ballot(&mut db, judge_id, number, Some(*id)).await? else {
                return Ok(access::found(target(code, "previous/")));
            };
            let path = target(code, "current/");
            let view = FrozenView {
                code,
                path: &path,
                actor,
                judge: judge_id,
                explicit: false,
            };
            return Ok(frozen_page(app, &mut db, headers, &view, &ballot, &options).await?);
        }
        if let Some(form) = &form
            && form.get("round_instance").and_then(|value| value.trim().parse::<i32>().ok()) != Some(*id)
        {
            return Ok(private((
                StatusCode::CONFLICT,
                "This ballot belongs to a different debate. Reopen the current ballot from the Judge Portal.",
            )));
        }
        let settings = Settings::load(&mut db, ballot_entry::FORM_SETTINGS).await?;
        let Some(debate) = ballot_entry::load(&mut db, *id).await? else {
            return Ok(access::found(target(code, "")));
        };
        let validation = form.as_ref().map(|form| {
            ballot_entry::judge_ballot(
                &debate,
                &settings,
                options.integer("min_eballot_speak", 15),
                options.integer("max_eballot_speak", 35),
                code,
                form,
            )
        });
        let ballot = match (form.as_ref(), validation) {
            (Some(_), Some(Ok(ballot))) => ballot,
            (form, validation) => {
                let errors = validation.and_then(Result::err);
                let path = target(code, "current/");
                let warnings =
                    Settings::load(&mut db, ballot_entry::SPEAK_WARNING_SETTINGS).await?;
                let shell = legacy_html::shell(&mut db, actor, &path).await?;
                let context = ballot_entry::judge_context(
                    &debate,
                    &settings,
                    &warnings,
                    code,
                    &path,
                    return_path,
                    form.zip(errors.as_ref()),
                );
                let mut response = form_page(
                    app,
                    headers,
                    &path,
                    "ballots/round_entry.html",
                    shell,
                    context,
                );
                if errors.is_some() && response.status().is_success() {
                    *response.status_mut() = StatusCode::UNPROCESSABLE_ENTITY;
                }
                return Ok(response);
            }
        };
        ballot_entry::save(&mut db, &debate, ballot).await?;
        if ballot_entry::rfd_access(&settings, number).open
            && let Some(rfd) = form.as_ref().and_then(|data| data.get("rfd"))
        {
            save_reason(&mut db, *id, rfd).await?;
        }
        db.commit().await?;
        Ok(access::found(target(code, "submitted/")))
    }
    .await;
    portal_response(app, headers, result).await
}

#[derive(Serialize)]
struct Previous {
    number: i32,
    government: String,
    opposition: String,
    outcome: String,
    rfd_enabled: bool,
    rfd_status: &'static str,
    url: String,
}

pub async fn previous(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path(code): Path<String>,
) -> Response {
    if !valid_code(&code) {
        return missing_judge(&app, &headers);
    }
    let result: PortalResult = async {
        let mut db = app.db.begin().await?;
        let actor = access::code_portal_actor(&mut db, &app, actor, &headers).await?;
        let Some((judge_id, name)) = judge(&mut db, &code, false).await? else {
            return Ok(missing_judge(&app, &headers));
        };
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        let settings = Settings::load(&mut db, &["cur_round", "written_rfd_first_round"]).await?;
        let number = settings.integer("cur_round", 1).saturating_sub(1);
        let rfd_first = settings.integer("written_rfd_first_round", -1);
        let rows: Vec<_> = sqlx::query_as::<_, (i32, i32, String, String, i32, String)>(
            "SELECT r.id, r.round_number, g.name, o.name, r.victor, r.rfd
             FROM tab_round r JOIN tab_round_judges j ON j.round_id = r.id
             JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
             WHERE j.judge_id = ? AND r.round_number <= ?
             AND EXISTS(SELECT 1 FROM tab_roundstats s WHERE s.round_id = r.id)
             ORDER BY r.round_number DESC, g.name, o.name, r.id",
        )
        .bind(judge_id)
        .bind(number)
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(|(id, number, government, opposition, winner, rfd)| {
            let outcome = match winner {
                1 | 3 => government.clone(),
                2 | 4 => opposition.clone(),
                0 => "UNKNOWN".into(),
                5 => "ALL DROP".into(),
                6 => "ALL WIN".into(),
                _ => winner.to_string(),
            };
            let rfd_status = if rfd
                .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
                .is_empty()
            {
                "Blank"
            } else {
                "Written"
            };
            Previous {
                number,
                government,
                opposition,
                outcome,
                rfd_enabled: rfd_first >= 1 && number >= rfd_first,
                rfd_status,
                url: target(&code, &format!("submitted/{id}/")),
            }
        })
        .collect();
        db.commit().await?;
        Ok(legacy_html::render(
            "ballots/previous_ballots.html",
            shell,
            context! {
                judge => context! { name },
                ballot_rows => rows,
                portal_url => target(&code, ""),
                current_ballot_url => target(&code, "current/"),
            },
            StatusCode::OK,
        ))
    }
    .await;
    portal_response(&app, &headers, result).await
}

pub async fn submitted(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(code): Path<String>,
) -> Response {
    frozen(&app, actor, &headers, &code, None, None).await
}

pub async fn submitted_one(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path((code, id)): Path<(String, i32)>,
) -> Response {
    frozen(&app, actor, &headers, &code, Some(id), None).await
}

pub async fn edit_reason(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(code): Path<String>,
    Form(form): Form<Submitted>,
) -> Response {
    frozen(&app, actor, &headers, &code, None, Some(form)).await
}

pub async fn edit_reason_one(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path((code, id)): Path<(String, i32)>,
    Form(form): Form<Submitted>,
) -> Response {
    frozen(&app, actor, &headers, &code, Some(id), Some(form)).await
}

#[derive(Serialize)]
struct FrozenScore {
    role: &'static str,
    name: String,
    speaks: String,
    ranks: String,
}

struct SubmittedBallot {
    id: i32,
    number: i32,
    chair: Option<i32>,
    victor: i32,
    gov: String,
    opp: String,
    rfd: String,
}

async fn submitted_ballot(
    db: &mut MySqlConnection,
    judge: i32,
    current: i32,
    requested: Option<i32>,
) -> Result<Option<SubmittedBallot>, sqlx::Error> {
    type Row = (i32, i32, Option<i32>, i32, String, String, String);
    let ballot = sqlx::query_as::<_, Row>(
        "SELECT r.id, r.round_number, r.chair_id, r.victor, g.name, o.name, r.rfd
         FROM tab_round r JOIN tab_round_judges j ON j.round_id = r.id
         JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         WHERE j.judge_id = ? AND r.round_number <= ?
           AND ((? IS NULL AND r.round_number = ?) OR r.id = ?)
           AND EXISTS(SELECT 1 FROM tab_roundstats s WHERE s.round_id = r.id)
         ORDER BY r.id LIMIT 1",
    )
    .bind(judge)
    .bind(current)
    .bind(requested)
    .bind(current)
    .bind(requested)
    .fetch_optional(db)
    .await?;
    Ok(ballot.map(
        |(id, number, chair, victor, gov, opp, rfd)| SubmittedBallot {
            id,
            number,
            chair,
            victor,
            gov,
            opp,
            rfd,
        },
    ))
}

fn disclosure_message(speaks: i32, ranks: i32) -> Option<&'static str> {
    match (speaks, ranks) {
        (0, 0) => None,
        (1, 0) => Some(
            "This tournament is open speaks. You are required to disclose speaks to debaters if they opt-in to hearing them.",
        ),
        (_, 0) => {
            Some("This tournament is closed speaks. You should not tell debaters their speaks.")
        }
        (0, 1) => Some(
            "This tournament is open ranks. You are required to disclose ranks to debaters if they opt-in to hearing them.",
        ),
        (0, _) => {
            Some("This tournament is closed ranks. You should not tell debaters their ranks.")
        }
        (1, 1) => Some(
            "This tournament is open speaks and open ranks. You are required to disclose both speaks and ranks to debaters if they opt-in to hearing them.",
        ),
        (1, 2) => Some(
            "This tournament is open speaks but closed ranks. You are required to disclose speaks to debaters if they opt-in, but you should not tell debaters their ranks.",
        ),
        (2, 1) => Some(
            "This tournament is closed speaks but open ranks. You should not tell speaks to debaters, but are required to disclose ranks if debaters opt-in to hearing them.",
        ),
        (_, _) => Some(
            "This tournament is closed speaks and closed ranks. You should not tell debaters their speaks or ranks.",
        ),
    }
}

struct FrozenView<'a> {
    code: &'a str,
    path: &'a str,
    actor: Actor,
    judge: i32,
    explicit: bool,
}

async fn frozen_page(
    app: &App,
    db: &mut MySqlConnection,
    headers: &HeaderMap,
    view: &FrozenView<'_>,
    ballot: &SubmittedBallot,
    options: &Settings,
) -> Result<Response, sqlx::Error> {
    let settings = Settings::load(db, ballot_entry::FORM_SETTINGS).await?;
    let allowed = ballot_entry::rfd_access(&settings, ballot.number);
    let editable = allowed.open && ballot.chair == Some(view.judge);
    let shell = legacy_html::shell(db, view.actor, view.path).await?;
    let mut stats: std::collections::HashMap<_, _> =
        sqlx::query_as::<_, (String, String, String, String)>(
            "SELECT s.debater_role, d.name, CAST(s.speaks AS CHAR), CAST(s.ranks AS CHAR)
         FROM tab_roundstats s JOIN tab_debater d ON d.id = s.debater_id
         WHERE s.round_id = ? AND s.debater_role IN ('pm','mg','lo','mo') ORDER BY s.id",
        )
        .bind(ballot.id)
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(role, name, speaks, ranks)| (role, (name, speaks, ranks)))
        .collect();
    let scores = ROLES.map(|role| stats.remove(role));
    let rows: Vec<_> = ROLE_NAMES
        .into_iter()
        .zip(scores)
        .map(|(role, score)| {
            let (name, speaks, ranks) = score.map_or_else(
                || ("—".into(), "—".into(), "—".into()),
                |(name, speaks, ranks)| {
                    let rounded = ranks
                        .parse::<f64>()
                        .expect("stored decimal rank")
                        .round_ties_even() as i32;
                    (name, speaks, rounded.to_string())
                },
            );
            FrozenScore {
                role,
                name,
                speaks,
                ranks,
            }
        })
        .collect();
    let winner_display = usize::try_from(ballot.victor)
        .ok()
        .and_then(|index| ballot_entry::DJANGO_OUTCOMES.get(index))
        .copied()
        .unwrap_or("Unknown");
    let rfd_action = if view.explicit {
        String::new()
    } else {
        target(view.code, &format!("submitted/{}/", ballot.id))
    };
    let page = context! {
        portal_url => target(view.code, ""),
        previous_ballots_url => target(view.code, "previous/"),
        ballot_code => view.code,
        gov_team => context! { display => &ballot.gov },
        opp_team => context! { display => &ballot.opp },
        gov_debaters => &rows[..2],
        opp_debaters => &rows[2..],
        winner_display,
        disclosure_message => disclosure_message(options.integer("open_speaks", 0), options.integer("open_ranks", 0)),
        rfd_text => &ballot.rfd,
        rfd_enabled => allowed.allowed,
        rfd_editable => editable,
        rfd_deadline_configured => !allowed.deadline.is_empty(),
        rfd_deadline_display => allowed.deadline,
        rfd_action,
    };
    if editable {
        Ok(form_page(
            app,
            headers,
            view.path,
            "ballots/ballot_submitted.html",
            shell,
            page,
        ))
    } else {
        Ok(staff_roster_notice::render_portal(
            app,
            headers,
            Portal::Judge,
            view.path,
            "ballots/ballot_submitted.html",
            shell,
            page,
        ))
    }
}

async fn frozen(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    code: &str,
    requested: Option<i32>,
    form: Option<Submitted>,
) -> Response {
    if let Some(form) = &form
        && !csrf_valid(headers, form)
    {
        return csrf_error();
    }
    if !valid_code(code) {
        return missing_judge(app, headers);
    }
    let result: PortalResult = async {
        let mut db = app.db.begin().await?;
        if form.is_some() {
            lock_round(&mut db).await?;
        }
        let row = judge(&mut db, code, form.is_some()).await?;
        let actor = access::code_portal_actor(&mut db, app, actor, headers).await?;
        let Some((judge_id, _)) = row else {
            return Ok(missing_judge(app, headers));
        };
        if form.is_some() && requested.is_none() {
            return Ok(private((
                StatusCode::CONFLICT,
                "Reopen the submitted ballot before editing its written RFD.",
            )));
        }
        let options = Settings::load(&mut db, PORTAL_SETTINGS).await?;
        let current = options.integer("cur_round", 1).saturating_sub(1);
        let Some(ballot) = submitted_ballot(&mut db, judge_id, current, requested).await? else {
            return Ok(access::found(target(code, "previous/")));
        };
        let suffix = requested.map_or_else(|| "submitted/".into(), |id| format!("submitted/{id}/"));
        let path = target(code, &suffix);
        if let Some(form) = form {
            let settings = Settings::load(&mut db, ballot_entry::FORM_SETTINGS).await?;
            let editable = ballot_entry::rfd_access(&settings, ballot.number).open
                && ballot.chair == Some(judge_id);
            if !editable || !form.contains_key("rfd") {
                return Ok(private((
                    StatusCode::FORBIDDEN,
                    "Written reasons cannot be edited for this ballot.",
                )));
            }
            save_reason(&mut db, ballot.id, &form["rfd"]).await?;
            db.commit().await?;
            return Ok(staff_roster_notice::portal_redirect(
                app,
                headers,
                Portal::Judge,
                &path,
                "Written RFD saved.",
                false,
            ));
        }
        let view = FrozenView {
            actor,
            code,
            path: &path,
            judge: judge_id,
            explicit: requested.is_some(),
        };
        Ok(frozen_page(app, &mut db, headers, &view, &ballot, &options).await?)
    }
    .await;
    portal_response(app, headers, result).await
}

async fn save_reason(db: &mut MySqlConnection, id: i32, reason: &str) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE tab_round SET rfd = ? WHERE id = ?")
        .bind(reason.trim())
        .bind(id)
        .execute(db)
        .await?;
    Ok(())
}
