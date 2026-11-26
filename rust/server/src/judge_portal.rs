use std::collections::HashSet;

use axum::{
    Extension, Form,
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
};
use minijinja::context;
use mittab_tabulation::ballot_entry::{ROLE_NAMES, ROLES};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    ballot_entry::{self, Submitted, WriteBallot},
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    settings::Settings,
    staff_pairing::lock_round,
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

fn valid_code(code: &str) -> bool {
    if code.is_empty() || code.len() > 30 {
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

pub async fn search(Extension(actor): Extension<Actor>, headers: HeaderMap) -> Response {
    search_page(actor, &headers, None)
}

fn search_page(actor: Actor, headers: &HeaderMap, message: Option<&'static str>) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let response = html::render(
        "judge_search.html",
        context! {
            title => "Judge Portal and Ballots", csrf, message, has_error => message.is_some(),
            nav => Navigation::new(actor, true),
        },
        if message.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn search_submit(
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Submitted>,
) -> Response {
    if !csrf_valid(&headers, &form) {
        return csrf_error();
    }
    let code = form.get("ballot_code").map_or("", |code| code.trim());
    if !valid_code(code) {
        return search_page(
            actor,
            &headers,
            Some("Enter the judge code provided by tab."),
        );
    }
    access::found(target(code, ""))
}

async fn judge(
    db: &mut MySqlConnection,
    code: &str,
    lock: bool,
) -> Result<Option<(i32, String)>, sqlx::Error> {
    let sql = if lock {
        "SELECT id, name FROM tab_judge WHERE ballot_code = ? FOR UPDATE"
    } else {
        "SELECT id, name FROM tab_judge WHERE ballot_code = ?"
    };
    sqlx::query_as(sql).bind(code).fetch_optional(db).await
}

#[derive(Serialize)]
struct Availability {
    field: String,
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
    Path(code): Path<String>,
) -> Response {
    if !valid_code(&code) {
        return access::found(SEARCH);
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let Some((id, name)) = judge(&mut db, &code, false).await? else {
            return Ok(search_page(
                actor,
                &headers,
                Some("No judge was found for that code."),
            ));
        };
        let settings = Settings::load(&mut db, PORTAL_SETTINGS).await?;
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
                field: field(round),
                label: if round == 0 {
                    "Outrounds".into()
                } else {
                    format!("Round {round}")
                },
                checked: expected.contains(&round),
            })
            .collect();
        let Ok(csrf) = html::page_csrf(&headers) else {
            return Ok(unavailable());
        };
        let response = html::render(
            "judge_portal.html",
            context! {
                title => "Judge Portal and Ballots", name, code, csrf, rows,
                nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
            },
            StatusCode::OK,
        );
        Ok::<_, sqlx::Error>(html::form_response(response, &headers, &csrf))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

pub async fn availability(
    State(app): State<App>,
    headers: HeaderMap,
    Path(code): Path<String>,
    Form(form): Form<Submitted>,
) -> Response {
    if !csrf_valid(&headers, &form) {
        return csrf_error();
    }
    if !valid_code(&code) {
        return access::found(SEARCH);
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let Some((id, name)) = judge(&mut db, &code, true).await? else {
            return Ok(access::found(SEARCH));
        };
        let settings = Settings::load(&mut db, PORTAL_SETTINGS).await?;
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
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(target(&code, "")))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

type Assignment = (i32, i32, Option<i32>, bool);
type PortalResult = Result<Response, sqlx::Error>;

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
    Path(code): Path<String>,
) -> Response {
    current_response(&app, actor, &headers, &code, None).await
}

pub async fn submit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(code): Path<String>,
    Form(form): Form<Submitted>,
) -> Response {
    current_response(&app, actor, &headers, &code, Some(form)).await
}

async fn current_response(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    code: &str,
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
        let Some((judge_id, _)) = judge(&mut db, code, form.is_some()).await? else {
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
        if *submitted {
            return Ok(access::found(target(code, "submitted/")));
        }
        if *chair != Some(judge_id) {
            return Ok(private((
                StatusCode::FORBIDDEN,
                "Only the assigned chair can submit this ballot.",
            )));
        }
        let settings = Settings::load(&mut db, ballot_entry::FORM_SETTINGS).await?;
        let Some(debate) = ballot_entry::load(&mut db, *id).await? else {
            return Ok(access::found(target(code, "")));
        };
        let Some(form) = form else {
            return Ok(ballot_entry::page(
                actor,
                headers,
                &debate,
                &settings,
                None,
                None,
                Some(code),
            ));
        };
        let invalid = |message| {
            ballot_entry::page(
                actor,
                headers,
                &debate,
                &settings,
                None,
                Some((&form, message)),
                Some(code),
            )
        };
        let entered = form.get("ballot_code").map_or("", |value| value.trim());
        if !entered.eq_ignore_ascii_case(code) {
            return Ok(invalid("Enter the judge code provided by tab."));
        }
        let ballot = match ballot_entry::extract(&debate, &settings, &form, None) {
            Ok(WriteBallot::Scores(winner @ (1 | 2), scores)) => {
                WriteBallot::Scores(winner, scores)
            }
            Ok(_) => {
                return Ok(invalid(
                    "See tab to submit a result other than a win or loss.",
                ));
            }
            Err(error) => return Ok(invalid(error)),
        };
        let minimum = options
            .integer("min_eballot_speak", 15)
            .saturating_mul(10_000);
        let maximum = options
            .integer("max_eballot_speak", 35)
            .saturating_mul(10_000);
        if ROLES.iter().any(|role| {
            form.get(&format!("{role}_speaks"))
                .and_then(|value| ballot_entry::decimal(value))
                .is_none_or(|speaks| !(minimum..=maximum).contains(&speaks))
        }) {
            return Ok(invalid(
                "See tab to justify speaker points outside the e-ballot range.",
            ));
        }
        ballot_entry::save(&mut db, &debate, ballot).await?;
        if ballot_entry::rfd_access(&settings, number).open
            && let Some(rfd) = form.get("rfd")
        {
            save_reason(&mut db, *id, rfd).await?;
        }
        db.commit().await?;
        Ok(access::found(target(code, "submitted/")))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Serialize)]
struct Previous {
    number: i32,
    government: String,
    opposition: String,
    outcome: String,
    rfd_status: &'static str,
    url: String,
}

pub async fn previous(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(code): Path<String>,
) -> Response {
    if !valid_code(&code) {
        return access::found(SEARCH);
    }
    let result: PortalResult = async {
        let mut db = app.db.begin().await?;
        let Some((judge_id, name)) = judge(&mut db, &code, false).await? else {
            return Ok(access::found(SEARCH));
        };
        let settings = Settings::load(&mut db, PORTAL_SETTINGS).await?;
        let number = settings.integer("cur_round", 1).saturating_sub(1);
        let rfd_first = settings.integer("written_rfd_first_round", -1);
        let rows: Vec<_> = sqlx::query_as::<_, (i32, i32, String, String, i32, bool)>(
            "SELECT r.id, r.round_number, g.name, o.name, r.victor, CHAR_LENGTH(TRIM(r.rfd)) > 0
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
        .map(|(id, number, government, opposition, winner, written)| {
            let outcome = match winner {
                1 | 3 => government.clone(),
                2 | 4 => opposition.clone(),
                _ => outcome(winner).into(),
            };
            let rfd_status = if rfd_first < 1 || number < rfd_first {
                "Not required"
            } else if written {
                "Written"
            } else {
                "Blank"
            };
            Previous {
                number,
                government,
                opposition,
                outcome,
                rfd_status,
                url: target(&code, &format!("submitted/{id}/")),
            }
        })
        .collect();
        db.commit().await?;
        Ok(html::render(
            "judge_previous.html",
            context! {
                title => "Previous Ballots", name, code, rows,
                nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
            },
            StatusCode::OK,
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn outcome(victor: i32) -> &'static str {
    ballot_entry::OUTCOMES
        .iter()
        .find(|&&(code, _)| code == victor)
        .map_or("Pending", |&(_, label)| label)
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
    ranks: i32,
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
        return access::found(SEARCH);
    }
    let result: PortalResult = async {
        let mut db = app.db.begin().await?;
        if form.is_some() {
            lock_round(&mut db).await?;
        }
        let Some((judge_id, _)) = judge(&mut db, code, form.is_some()).await? else {
            return Ok(access::found(SEARCH));
        };
        let options = Settings::load(&mut db, PORTAL_SETTINGS).await?;
        let current = options.integer("cur_round", 1).saturating_sub(1);
        let Some((id, number, chair, victor, gov, opp, rfd)) =
            sqlx::query_as::<_, (i32, i32, Option<i32>, i32, String, String, String)>(
                "SELECT r.id, r.round_number, r.chair_id, r.victor, g.name, o.name, r.rfd
                 FROM tab_round r JOIN tab_round_judges j ON j.round_id = r.id
                 JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
                 WHERE j.judge_id = ? AND ((? IS NULL AND r.round_number = ?) OR r.id = ?)
                   AND EXISTS(SELECT 1 FROM tab_roundstats s WHERE s.round_id = r.id)
                 ORDER BY r.id LIMIT 1",
            ).bind(judge_id).bind(requested).bind(current).bind(requested).fetch_optional(&mut *db).await?
        else {
            return Ok(access::found(target(code, "previous/")));
        };
        let settings = Settings::load(&mut db, ballot_entry::FORM_SETTINGS).await?;
        let allowed = ballot_entry::rfd_access(&settings, number);
        let editable = allowed.open && chair == Some(judge_id);
        if let Some(form) = form {
            if !editable || !form.contains_key("rfd") {
                return Ok(private((StatusCode::FORBIDDEN, "Written reasons cannot be edited for this ballot.")));
            }
            save_reason(&mut db, id, &form["rfd"]).await?;
            db.commit().await?;
            let suffix = requested.map_or_else(|| "submitted/".into(), |id| format!("submitted/{id}/"));
            return Ok(access::found(target(code, &suffix)));
        }
        let mut scores: Vec<_> = sqlx::query_as::<_, (String, String, String, i32)>(
            "SELECT s.debater_role, d.name, CAST(s.speaks AS CHAR), CAST(ROUND(s.ranks, 0) AS SIGNED)
             FROM tab_roundstats s JOIN tab_debater d ON d.id = s.debater_id WHERE s.round_id = ?
             AND s.debater_role IN ('pm','mg','lo','mo') ORDER BY s.id",
        ).bind(id).fetch_all(&mut *db).await?.into_iter().map(|(role, name, speaks, ranks)| {
            let position = ROLES.iter().position(|&code| code == role).unwrap_or(0);
            (position, FrozenScore { role: ROLE_NAMES[position], name, speaks, ranks })
        }).collect();
        scores.sort_by_key(|(position, _)| *position);
        let scores: Vec<_> = scores.into_iter().map(|(_, score)| score).collect();
        db.commit().await?;
        let Ok(csrf) = html::page_csrf(headers) else { return Ok(unavailable()); };
        let response = html::render("judge_submitted.html", context! {
            title => format!("Round {number} Ballot Submitted"),
            code, gov, opp, rfd, editable, scores, csrf, outcome => outcome(victor),
            rfd_enabled => allowed.allowed, deadline => allowed.deadline,
            open_speaks => options.integer("open_speaks", 0), open_ranks => options.integer("open_ranks", 0),
            nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        }, StatusCode::OK);
        Ok(html::form_response(response, headers, &csrf))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

async fn save_reason(db: &mut MySqlConnection, id: i32, reason: &str) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE tab_round SET rfd = ? WHERE id = ?")
        .bind(reason.trim())
        .bind(id)
        .execute(db)
        .await?;
    Ok(())
}
