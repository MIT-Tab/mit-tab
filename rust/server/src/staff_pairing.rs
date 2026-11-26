use std::collections::HashMap;

use axum::{
    Extension, Form,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use mittab_tabulation::{
    pairing::{PairingWeights, required_bye_count},
    prelim::Policy,
};
use serde::{Deserialize, Serialize};
use sqlx::{MySqlConnection, QueryBuilder};

use crate::{
    App, access,
    backups::Trigger,
    html::{self, Navigation},
    judge_assignment,
    legacy_session::Actor,
    login, private, room_assignment, round_audit,
    settings::Settings,
    tournament_data::TournamentData,
    unavailable,
};

const STATUS: &str = "/pairings/status/";
const SETTINGS: &[&str] = &[
    "cur_round",
    "tot_rounds",
    "min_byes",
    "fair_bye",
    "r2_random_within_brackets",
    "power_pairing_multiple",
    "high_opp_penalty",
    "higher_opp_penalty",
    "high_gov_penalty",
    "same_school_penalty",
    "hit_pull_up_before",
    "hit_team_before",
    "lenient_late",
    "enable_room_seeding",
    "judge_pairing_mode",
    "pair_wings",
    "wing_pairing_mode",
    "inround_round_priority",
    "allow_rejudges",
    "rejudge_penalty",
    "repeat_wing_penalty",
    "motions_enabled",
    "pairing_released",
];

#[derive(Deserialize)]
pub struct Action {
    #[serde(default)]
    csrfmiddlewaretoken: String,
    #[serde(default)]
    round: i32,
}

#[derive(Debug)]
pub enum ChangeError {
    Conflict(&'static str),
    Storage,
}

impl From<sqlx::Error> for ChangeError {
    fn from(_: sqlx::Error) -> Self {
        Self::Storage
    }
}

#[derive(Serialize)]
struct Readiness {
    rooms: i64,
    judges: i64,
    needed: usize,
    previous_error: Option<&'static str>,
}

impl Readiness {
    fn issue(&self) -> Result<(), ChangeError> {
        if self.judges < self.needed as i64 {
            return Err(ChangeError::Conflict(
                "Check in more judges before pairing this round.",
            ));
        }
        if self.rooms < self.needed as i64 {
            return Err(ChangeError::Conflict(
                "Check in more rooms before pairing this round.",
            ));
        }
        if let Some(message) = self.previous_error {
            return Err(ChangeError::Conflict(message));
        }
        Ok(())
    }
}

async fn readiness(
    db: &mut MySqlConnection,
    settings: &Settings,
    round: i32,
) -> Result<Readiness, ChangeError> {
    let (checked, judges, rooms) = sqlx::query_as::<_, (i64, i64, i64)>(
        "SELECT (SELECT COUNT(*) FROM tab_team WHERE checked_in = TRUE),
                (SELECT COUNT(*) FROM tab_checkin WHERE round_number = ?),
                (SELECT COUNT(*) FROM tab_roomcheckin WHERE round_number = ?)",
    )
    .bind(round)
    .bind(round)
    .fetch_one(&mut *db)
    .await?;
    let minimum = settings.integer("min_byes", 0);
    let bye_count = required_bye_count(checked as usize, minimum).map_err(|_| {
        ChangeError::Conflict(
            "Minimum byes must be between zero and the number of checked-in teams.",
        )
    })?;
    let prior = round - 1;
    let (pending, bye, no_show) = sqlx::query_as::<_, (i64, i64, i64)>(
        "SELECT EXISTS(SELECT 1 FROM tab_round WHERE round_number = ? AND victor = 0),
                EXISTS(SELECT 1 FROM tab_round r JOIN tab_bye b ON r.round_number = b.round_number
                       AND b.bye_team_id IN (r.gov_team_id, r.opp_team_id) WHERE r.round_number = ?),
                EXISTS(SELECT 1 FROM tab_round r JOIN tab_noshow n ON r.round_number = n.round_number
                       AND n.no_show_team_id IN (r.gov_team_id, r.opp_team_id) WHERE r.round_number = ?)",
    ).bind(prior).bind(prior).bind(prior).fetch_one(db).await?;
    let previous_error = if pending != 0 {
        Some("Enter the previous round’s results before pairing.")
    } else if bye != 0 {
        Some("A team both debated and received a bye in the previous round.")
    } else if no_show != 0 {
        Some("A team both debated and was recorded absent in the previous round.")
    } else {
        None
    };
    Ok(Readiness {
        rooms,
        judges,
        needed: (checked as usize - bye_count) / 2,
        previous_error,
    })
}

fn error(actor: Actor, result: ChangeError) -> Response {
    match result {
        ChangeError::Storage => unavailable(),
        ChangeError::Conflict(message) => html::render(
            "error.html",
            context! {title => "Pairing unavailable", heading => message, nav => Navigation::new(actor, false), back => STATUS},
            StatusCode::CONFLICT,
        ),
    }
}

pub async fn pair_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    match preview(&app, actor, &headers, false).await {
        Ok(response) => response,
        Err(issue) => error(actor, issue),
    }
}

pub async fn repair_page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    match preview(&app, actor, &headers, true).await {
        Ok(response) => response,
        Err(issue) => error(actor, issue),
    }
}

async fn preview(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    repair: bool,
) -> Result<Response, ChangeError> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let round = target(&settings, repair)?;
    let check = readiness(&mut db, &settings, round).await?;
    let judges = sqlx::query_scalar::<_, String>(
        "SELECT j.name FROM tab_judge j JOIN tab_checkin c ON c.judge_id = j.id
         WHERE c.round_number = ? ORDER BY j.name, j.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?;
    db.commit().await?;
    let csrf = html::page_csrf(headers).map_err(|_| ChangeError::Storage)?;
    let title = if repair {
        format!("Re-pair Round {round}")
    } else {
        format!("Pairing Round {round}")
    };
    let response = html::render(
        "pair_prelim.html",
        context! {
            title, round, repair, check, judges, ready => check.issue().is_ok(), csrf,
            nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        },
        StatusCode::OK,
    );
    Ok(html::form_response(response, headers, &csrf))
}

fn target(settings: &Settings, repair: bool) -> Result<i32, ChangeError> {
    let round = settings
        .integer("cur_round", 1)
        .saturating_sub(i32::from(repair));
    if round < 1 {
        return Err(ChangeError::Conflict("No round has been paired yet."));
    }
    if round > settings.integer("tot_rounds", 5) {
        return Err(ChangeError::Conflict(
            "All scheduled preliminary rounds have been paired.",
        ));
    }
    Ok(round)
}

pub async fn pair(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Action>,
) -> Response {
    change(&app, actor, &headers, form, Trigger::Pair).await
}

pub async fn repair(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Action>,
) -> Response {
    change(&app, actor, &headers, form, Trigger::RePair).await
}

pub async fn rooms(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Action>,
) -> Response {
    change(&app, actor, &headers, form, Trigger::Rooms).await
}

pub async fn judges(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Action>,
) -> Response {
    change(&app, actor, &headers, form, Trigger::Judges).await
}

pub async fn back_to_status() -> Response {
    access::found(STATUS)
}

async fn change(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    form: Action,
    trigger: Trigger,
) -> Response {
    let Actor::User {
        id, board: false, ..
    } = actor
    else {
        return access::access_denied();
    };
    if !login::valid_csrf(headers, &form.csrfmiddlewaretoken) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    match apply(app, id, form.round, trigger).await {
        Ok(()) => access::found(STATUS),
        Err(issue) => error(actor, issue),
    }
}

async fn apply(app: &App, user: i32, requested: i32, trigger: Trigger) -> Result<(), ChangeError> {
    let mut db = app.db.begin().await?;
    let setting_id = lock_round(&mut db).await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let round = target(&settings, !matches!(trigger, Trigger::Pair))?;
    if requested != round {
        return Err(ChangeError::Conflict(
            "The current round changed. Reload this page and try again.",
        ));
    }
    if matches!(trigger, Trigger::Pair) {
        let existing = sqlx::query_scalar::<_, i64>(
            "SELECT EXISTS(SELECT 1 FROM tab_round WHERE round_number = ?)
                  + EXISTS(SELECT 1 FROM tab_bye WHERE round_number = ?)
                  + EXISTS(SELECT 1 FROM tab_noshow WHERE round_number = ?)",
        )
        .bind(round)
        .bind(round)
        .bind(round)
        .fetch_one(&mut *db)
        .await?;
        if existing != 0 {
            return Err(ChangeError::Conflict(
                "This round already has pairings or attendance records.",
            ));
        }
    }
    let before = round_audit::capture(&mut db, round).await?;
    let source = match trigger {
        Trigger::Pair | Trigger::RePair => {
            readiness(&mut db, &settings, round).await?.issue()?;
            app.backups
                .pre_pairing(round, trigger)
                .await
                .map_err(ChangeError::Conflict)?;
            if matches!(trigger, Trigger::RePair) {
                clear(&mut db, round).await?;
            }
            let participants = TournamentData::load(&mut db).await?;
            let policy = policy(&settings, round);
            let plan = participants
                .pairing_plan(&policy, settings.integer("lenient_late", 0))
                .map_err(ChangeError::Conflict)?;
            for team in participants.teams.iter().filter(|team| !team.checked_in) {
                sqlx::query("INSERT INTO tab_noshow (round_number, no_show_team_id) VALUES (?, ?)")
                    .bind(round)
                    .bind(team.id)
                    .execute(&mut *db)
                    .await?;
            }
            for bye in plan.byes {
                sqlx::query("INSERT INTO tab_bye (round_number, bye_team_id) VALUES (?, ?)")
                    .bind(round)
                    .bind(bye)
                    .execute(&mut *db)
                    .await?;
            }
            for debate in plan.debates {
                let pullup = match debate.pulled_up {
                    Some(team) if team == debate.government => 1,
                    Some(_) => 2,
                    None => 0,
                };
                sqlx::query("INSERT INTO tab_round (round_number, pullup, victor, gov_team_id, opp_team_id, rfd)
                             VALUES (?, ?, 0, ?, ?, '')")
                    .bind(round).bind(pullup).bind(debate.government).bind(debate.opposition)
                    .execute(&mut *db).await?;
            }
            sqlx::query("UPDATE tab_tabsettings SET value = ?, value_string = NULL WHERE id = ?")
                .bind(round + 1)
                .bind(setting_id)
                .execute(&mut *db)
                .await?;
            write_release(&mut db, false).await?;
            if matches!(trigger, Trigger::Pair) {
                "staff pairing"
            } else {
                "staff re-pairing"
            }
        }
        Trigger::Judges => {
            let panels = judge_assignment::plan(&mut db, &settings, round).await?;
            app.backups
                .pre_pairing(round, trigger)
                .await
                .map_err(ChangeError::Conflict)?;
            judge_assignment::save(&mut db, round, panels).await?;
            "staff judge assignment"
        }
        Trigger::Rooms => {
            let assignments = room_assignment::plan(&mut db, &settings, round).await?;
            app.backups
                .pre_pairing(round, trigger)
                .await
                .map_err(ChangeError::Conflict)?;
            for (pairing, room) in assignments {
                sqlx::query("UPDATE tab_round SET room_id = ? WHERE id = ?")
                    .bind(room)
                    .bind(pairing)
                    .execute(&mut *db)
                    .await?;
            }
            "staff room assignment"
        }
    };
    let after = round_audit::capture(&mut db, round).await?;
    round_audit::record(&mut db, &before, &after, user, source).await?;
    db.commit().await?;
    Ok(())
}

pub async fn lock_round(db: &mut MySqlConnection) -> Result<i32, sqlx::Error> {
    sqlx::query_scalar::<_, i32>(
        "SELECT id FROM tab_tabsettings WHERE `key` = 'cur_round' ORDER BY id LIMIT 1 FOR UPDATE",
    )
    .fetch_one(db)
    .await
}

async fn write_release(db: &mut MySqlConnection, released: bool) -> Result<(), sqlx::Error> {
    let id = sqlx::query_scalar::<_, i32>(
        "SELECT id FROM tab_tabsettings WHERE `key` = 'pairing_released' ORDER BY id LIMIT 1 FOR UPDATE",
    ).fetch_optional(&mut *db).await?;
    match id {
        Some(id) => {
            sqlx::query("UPDATE tab_tabsettings SET value = ?, value_string = NULL WHERE id = ?")
                .bind(i32::from(released))
                .bind(id)
                .execute(db)
                .await?
        }
        None => {
            sqlx::query("INSERT INTO tab_tabsettings (`key`, value) VALUES ('pairing_released', ?)")
                .bind(i32::from(released))
                .execute(db)
                .await?
        }
    };
    Ok(())
}

async fn clear(db: &mut MySqlConnection, round: i32) -> Result<(), sqlx::Error> {
    for table in [
        "tab_round_judges",
        "tab_roundstats",
        "tab_manualjudgeassignment",
        "tab_writtenrfdemaillog",
    ] {
        let mut query = QueryBuilder::new("DELETE d FROM ");
        query
            .push(table)
            .push(" d JOIN tab_round r ON r.id = d.round_id WHERE r.round_number = ")
            .push_bind(round)
            .build()
            .execute(&mut *db)
            .await?;
    }
    for table in ["tab_bye", "tab_noshow", "tab_round"] {
        let mut query = QueryBuilder::new("DELETE FROM ");
        query
            .push(table)
            .push(" WHERE round_number = ")
            .push_bind(round)
            .build()
            .execute(&mut *db)
            .await?;
    }
    Ok(())
}

fn policy(settings: &Settings, round: i32) -> Policy {
    Policy {
        current_round: round as u32,
        total_rounds: settings.integer("tot_rounds", 5),
        minimum_byes: settings.integer("min_byes", 0),
        fair_bye: settings.integer("fair_bye", 1),
        random_second_round: settings.integer("r2_random_within_brackets", 0) == 1,
        weights: PairingWeights {
            power_pairing_multiple: settings.integer("power_pairing_multiple", -1),
            high_opp_penalty: settings.integer("high_opp_penalty", 0),
            higher_opp_penalty: settings.integer("higher_opp_penalty", -10),
            high_gov_penalty: settings.integer("high_gov_penalty", -100),
            same_school_penalty: settings.integer("same_school_penalty", -1000),
            hit_pull_up_before: settings.integer("hit_pull_up_before", -10000),
            hit_team_before: settings.integer("hit_team_before", -100000),
        },
    }
}

#[derive(Serialize)]
struct Pairing {
    id: i32,
    government: String,
    opposition: String,
    room: Option<String>,
    judges: Vec<Judge>,
    victor: i32,
}

#[derive(Serialize)]
struct Judge {
    name: String,
    chair: bool,
}

pub async fn status(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    match status_page(&app, actor, &headers, None).await {
        Ok(response) => response,
        Err(issue) => error(actor, issue),
    }
}

pub async fn previous(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(round): Path<i32>,
) -> Response {
    match status_page(&app, actor, &headers, Some(round)).await {
        Ok(response) => response,
        Err(issue) => error(actor, issue),
    }
}

async fn status_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    selected: Option<i32>,
) -> Result<Response, ChangeError> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let current = settings.integer("cur_round", 1).saturating_sub(1);
    let round = selected.unwrap_or(current);
    if selected.is_some() && !(1..=current).contains(&round) {
        return Ok(private((StatusCode::NOT_FOUND, "Round not found.")));
    }
    let mut judges: HashMap<i32, Vec<Judge>> = HashMap::new();
    for (pairing, name, chair) in sqlx::query_as::<_, (i32, String, bool)>(
        "SELECT r.id, j.name, COALESCE(r.chair_id = j.id, FALSE) FROM tab_round r
         JOIN tab_round_judges x ON x.round_id = r.id JOIN tab_judge j ON j.id = x.judge_id
         WHERE r.round_number = ? ORDER BY COALESCE(r.chair_id = j.id, FALSE) DESC, j.name, j.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?
    {
        judges
            .entry(pairing)
            .or_default()
            .push(Judge { name, chair });
    }
    let pairings: Vec<_> = sqlx::query_as::<_, (i32, String, String, Option<String>, i32)>(
        "SELECT r.id, g.name, o.name, m.name, r.victor FROM tab_round r
         JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_room m ON m.id = r.room_id WHERE r.round_number = ? ORDER BY r.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, government, opposition, room, victor)| Pairing {
        id,
        government,
        opposition,
        room,
        judges: judges.remove(&id).unwrap_or_default(),
        victor,
    })
    .collect();
    let byes = sqlx::query_scalar::<_, String>(
        "SELECT t.name FROM tab_bye b JOIN tab_team t ON t.id = b.bye_team_id WHERE b.round_number = ? ORDER BY b.id",
    ).bind(round).fetch_all(&mut *db).await?;
    let no_shows = sqlx::query_scalar::<_, String>(
        "SELECT t.name FROM tab_noshow n JOIN tab_team t ON t.id = n.no_show_team_id WHERE n.round_number = ? ORDER BY n.id",
    ).bind(round).fetch_all(&mut *db).await?;
    db.commit().await?;
    let csrf = html::page_csrf(headers).map_err(|_| ChangeError::Storage)?;
    let title = if round > 0 {
        format!("Round {round} Pairings")
    } else {
        "Preliminary Pairings".into()
    };
    let response = html::render(
        "staff_pairings.html",
        context! {
            title, round, current, csrf, pairings, byes, no_shows,
            next_round => current < settings.integer("tot_rounds", 5),
            released => settings.integer("pairing_released", 0) == 1,
            nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        },
        StatusCode::OK,
    );
    Ok(html::form_response(response, headers, &csrf))
}

pub async fn release(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Action>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let settings = Settings::load(&mut db, SETTINGS).await?;
        let round = target(&settings, true)?;
        if form.round != round {
            return Err(ChangeError::Conflict(
                "The current round changed. Reload this page and try again.",
            ));
        }
        write_release(&mut db, settings.integer("pairing_released", 0) != 1).await?;
        db.commit().await?;
        Ok(())
    }
    .await;
    match result {
        Ok(()) => access::found(STATUS),
        Err(issue) => error(actor, issue),
    }
}
