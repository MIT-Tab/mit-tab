use std::collections::HashMap;

use axum::{
    Extension, Form,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::{FixedOffset, NaiveDateTime, Utc};
use minijinja::context;
use mittab_tabulation::ballot_entry::{Ballot, ROLE_NAMES, ROLES, Score, SpeakPolicy, score_panel};
use serde::Serialize;
use sqlx::{MySqlConnection, Row};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private, round_audit,
    settings::Settings,
    staff_pairing::{ChangeError, lock_round},
    unavailable,
};

const FORM_SETTINGS: &[&str] = &[
    "decimal_speaks",
    "min_speak",
    "max_speak",
    "written_rfd_first_round",
    "written_rfd_deadline",
    "motions_enabled",
];

pub const OUTCOMES: [(i32, &str); 6] = [
    (1, "Government"),
    (2, "Opposition"),
    (3, "Government by forfeit"),
    (4, "Opposition by forfeit"),
    (5, "Both lose"),
    (6, "Both win"),
];

type Submitted = HashMap<String, String>;

#[derive(Serialize)]
struct Team {
    id: i32,
    name: String,
    speakers: Vec<Speaker>,
}

#[derive(Serialize)]
struct Speaker {
    id: i32,
    name: String,
}

struct Debate {
    id: i32,
    number: i32,
    winner: i32,
    rfd: String,
    teams: [Team; 2],
    scores: HashMap<String, (i32, String, i32)>,
}

#[derive(Serialize)]
struct Position<'a> {
    code: &'static str,
    label: &'static str,
    team: &'a Team,
    selected: Option<i32>,
    speaks: String,
    ranks: Option<i32>,
}

#[derive(Serialize)]
struct FormValues<'a> {
    prefix: String,
    positions: Vec<Position<'a>>,
    outcome: Option<i32>,
}

struct RfdAccess {
    allowed: bool,
    open: bool,
    deadline: String,
}

fn rfd_access(settings: &Settings, round: i32) -> RfdAccess {
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

async fn load(db: &mut MySqlConnection, id: i32) -> Result<Option<Debate>, sqlx::Error> {
    let Some(row) = sqlx::query(
        "SELECT r.round_number, r.victor, r.rfd, g.id gov, g.name government, o.id opp, o.name opposition
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
    let scores = sqlx::query_as::<_, (String, i32, String, i32)>(
        "SELECT debater_role, debater_id, CAST(speaks AS CHAR), CAST(ROUND(ranks, 0) AS SIGNED)
         FROM tab_roundstats WHERE round_id = ? AND debater_role IN ('pm','mg','lo','mo') ORDER BY id",
    ).bind(id).fetch_all(db).await?.into_iter().map(|(role, id, speaks, ranks)| {
        (role, (id, speaks, ranks))
    }).collect();
    Ok(Some(Debate {
        id,
        number: row.try_get("round_number")?,
        winner: row.try_get("victor")?,
        rfd: row.try_get("rfd")?,
        teams,
        scores,
    }))
}

pub async fn single_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(round): Path<i32>,
) -> Response {
    show(&app, actor, &headers, round, None).await
}

pub async fn panel_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path((round, count)): Path<(i32, usize)>,
) -> Response {
    show(&app, actor, &headers, round, Some(count.max(1))).await
}

async fn show(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: i32,
    count: Option<usize>,
) -> Response {
    if count.is_some_and(|count| count > 100) {
        return private((
            StatusCode::BAD_REQUEST,
            "Submit at most 100 ballots in one panel.",
        ));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, FORM_SETTINGS).await?;
        let debate = load(&mut db, id).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>((debate, settings))
    }
    .await;
    match result {
        Ok((Some(debate), settings)) => page(actor, headers, &debate, &settings, count, None),
        Ok((None, _)) => not_found(),
        Err(_) => unavailable(),
    }
}

fn not_found() -> Response {
    private((StatusCode::NOT_FOUND, "Ballot not found."))
}

fn page(
    actor: Actor,
    headers: &HeaderMap,
    debate: &Debate,
    settings: &Settings,
    count: Option<usize>,
    invalid: Option<(&Submitted, &'static str)>,
) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let submitted = invalid.map(|(submitted, _)| submitted);
    let forms: Vec<_> = (1..=count.unwrap_or(1))
        .map(|index| {
            let prefix = count.map_or_else(String::new, |_| format!("{index}-"));
            let field =
                |name: &str| submitted.and_then(|values| values.get(&format!("{prefix}{name}")));
            let positions = ROLES
                .iter()
                .enumerate()
                .map(|(index, &role)| {
                    let previous = if count.is_none() {
                        debate.scores.get(role)
                    } else {
                        None
                    };
                    Position {
                        code: role,
                        label: ROLE_NAMES[index],
                        team: &debate.teams[index / 2],
                        selected: field(&format!("{role}_debater"))
                            .and_then(|raw| raw.parse().ok())
                            .or_else(|| previous.map(|(debater, _, _)| *debater)),
                        speaks: field(&format!("{role}_speaks"))
                            .cloned()
                            .or_else(|| previous.map(|(_, speaks, _)| speaks.clone()))
                            .unwrap_or_default(),
                        ranks: field(&format!("{role}_ranks"))
                            .and_then(|raw| raw.parse().ok())
                            .or_else(|| previous.map(|(_, _, ranks)| *ranks)),
                    }
                })
                .collect();
            let outcome = field("winner")
                .and_then(|raw| raw.parse().ok())
                .or_else(|| count.is_none().then_some(debate.winner));
            FormValues {
                prefix,
                positions,
                outcome,
            }
        })
        .collect();
    let rfd = rfd_access(settings, debate.number);
    let title = format!("Round {} Ballot", debate.number);
    let response = html::render(
        "ballot_entry.html",
        context! {
            title, forms, panel => count.is_some(), id => debate.id,
            gov => debate.teams[0].name, opp => debate.teams[1].name,
            csrf, outcomes => OUTCOMES, step => format!("{:.2}", f64::from(policy(settings).step) / 10_000.0),
            rfd_allowed => count.is_none() && rfd.allowed, rfd_open => rfd.open,
            deadline => rfd.deadline, rfd => submitted.and_then(|form| form.get("rfd")).unwrap_or(&debate.rfd),
            discard_minority => submitted.is_some_and(|form| form.contains_key("discard_minority")),
            submitted => !debate.scores.is_empty(),
            message => invalid.map(|(_, error)| error), has_error => invalid.is_some(),
            nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        },
        if invalid.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

pub async fn single(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(round): Path<i32>,
    Form(form): Form<Submitted>,
) -> Response {
    submit(&app, actor, &headers, round, None, form).await
}

pub async fn panel(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path((round, count)): Path<(i32, usize)>,
    Form(form): Form<Submitted>,
) -> Response {
    submit(&app, actor, &headers, round, Some(count.max(1)), form).await
}

async fn submit(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: i32,
    count: Option<usize>,
    form: Submitted,
) -> Response {
    let Actor::User {
        id: user,
        board: false,
        ..
    } = actor
    else {
        return access::access_denied();
    };
    if !login::valid_csrf(
        headers,
        form.get("csrfmiddlewaretoken").map_or("", String::as_str),
    ) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    if count.is_some_and(|count| count > 100) {
        return private((
            StatusCode::BAD_REQUEST,
            "Submit at most 100 ballots in one panel.",
        ));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        sqlx::query("SELECT id FROM tab_round WHERE id = ? FOR UPDATE")
            .bind(id)
            .fetch_optional(&mut *db)
            .await?;
        let settings = Settings::load(&mut db, FORM_SETTINGS).await?;
        let Some(debate) = load(&mut db, id).await? else {
            return Ok(not_found());
        };
        let rfd_only = count.is_none() && form.contains_key("rfd") && !form.contains_key("winner");
        if rfd_only && (debate.scores.is_empty() || !rfd_access(&settings, debate.number).open) {
            return Ok(page(
                actor,
                headers,
                &debate,
                &settings,
                count,
                Some((&form, "Written reasons cannot be edited for this ballot.")),
            ));
        }
        let result = if rfd_only {
            WriteBallot::Rfd
        } else {
            match extract(&debate, &settings, &form, count) {
                Ok(ballot) => ballot,
                Err(error) => {
                    return Ok(page(
                        actor,
                        headers,
                        &debate,
                        &settings,
                        count,
                        Some((&form, error)),
                    ));
                }
            }
        };
        let before = round_audit::capture_one(&mut db, debate.number, id).await?;
        save(&mut db, &debate, result).await?;
        if count.is_none()
            && rfd_access(&settings, debate.number).open
            && let Some(rfd) = form.get("rfd")
        {
            sqlx::query("UPDATE tab_round SET rfd = ? WHERE id = ?")
                .bind(rfd.trim())
                .bind(id)
                .execute(&mut *db)
                .await?;
        }
        let after = round_audit::capture_one(&mut db, debate.number, id).await?;
        let source = if count.is_some() {
            "panel ballot entry"
        } else {
            "ballot entry"
        };
        round_audit::record(&mut db, &before, &after, user, source).await?;
        db.commit().await?;
        Ok::<_, ChangeError>(if rfd_only {
            access::found(format!("/round/{id}/result/"))
        } else {
            access::found("/pairings/status/")
        })
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

enum WriteBallot {
    Rfd,
    Scores(i32, Vec<(usize, i32, String, i32)>),
}

fn extract(
    debate: &Debate,
    settings: &Settings,
    form: &Submitted,
    count: Option<usize>,
) -> Result<WriteBallot, &'static str> {
    let eligible = debate.teams.each_ref().map(|team| {
        team.speakers
            .iter()
            .map(|speaker| speaker.id)
            .collect::<Vec<_>>()
    });
    let mut ballots = Vec::new();
    for index in 1..=count.unwrap_or(1) {
        let prefix = count.map_or_else(String::new, |_| format!("{index}-"));
        let field = |name: &str| {
            form.get(&format!("{prefix}{name}"))
                .map_or("", String::as_str)
        };
        if field("round_instance").parse::<i32>().ok() != Some(debate.id) {
            return Err("This ballot belongs to a different debate.");
        }
        let winner = field("winner").parse().unwrap_or(0);
        let mut scores = [Score {
            debater: 0,
            speaks: 0,
            rank: 0,
        }; 4];
        for (index, role) in ROLES.iter().enumerate() {
            scores[index] = Score {
                debater: field(&format!("{role}_debater")).parse().unwrap_or(0),
                speaks: decimal(field(&format!("{role}_speaks")))
                    .ok_or("Enter a valid score for each speaker.")?,
                rank: field(&format!("{role}_ranks")).parse().unwrap_or(0),
            };
        }
        let ballot = Ballot { winner, scores };
        ballot.validate(policy(settings), [&eligible[0], &eligible[1]])?;
        ballots.push(ballot);
    }
    if count.is_some() {
        let result = score_panel(&ballots, form.contains_key("discard_minority"))?;
        return Ok(WriteBallot::Scores(
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
        ));
    }
    let ballot = &ballots[0];
    Ok(WriteBallot::Scores(
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
    ))
}

fn decimal(raw: &str) -> Option<i32> {
    let raw = raw.trim();
    let (negative, digits) = if let Some(value) = raw.strip_prefix('-') {
        (true, value)
    } else {
        (false, raw.strip_prefix('+').unwrap_or(raw))
    };
    let (whole, fraction) = digits.split_once('.').unwrap_or((digits, ""));
    if digits.is_empty()
        || (whole.is_empty() && fraction.is_empty())
        || !whole
            .bytes()
            .chain(fraction.bytes())
            .all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    let fraction = fraction.trim_end_matches('0');
    if fraction.len() > 4 {
        return None;
    }
    let value: i32 = if whole.is_empty() {
        0
    } else {
        whole.parse().ok()?
    };
    let fraction_value: i32 = fraction.parse().unwrap_or(0);
    let value = value
        .checked_mul(10_000)?
        .checked_add(fraction_value * 10_i32.pow(4 - fraction.len() as u32))?;
    Some(if negative { -value } else { value })
}

async fn save(
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
    }
    sqlx::query("DELETE FROM tab_noshow WHERE round_number = ? AND no_show_team_id IN (?, ?)")
        .bind(debate.number)
        .bind(debate.teams[0].id)
        .bind(debate.teams[1].id)
        .execute(db)
        .await?;
    Ok(())
}
