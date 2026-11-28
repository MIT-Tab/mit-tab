use axum::{
    Extension, Form, Json,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use mittab_tabulation::{
    pairing::{PairingWeights, required_bye_count},
    prelim::Policy,
};
use serde::Deserialize;
use sqlx::{MySqlConnection, QueryBuilder};

use crate::{
    App, access,
    backups::Trigger,
    html::{self, Navigation},
    judge_assignment, legacy_html,
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
    let path = if repair {
        "/pairing/re_pair_round/"
    } else {
        "/pairing/pair_round/"
    };
    let shell = legacy_html::shell(&mut db, actor, path).await?;
    let csrf = login::TemplateCsrf::new(headers).map_err(|_| ChangeError::Storage)?;
    let round_input = format!("<input type=\"hidden\" name=\"round\" value=\"{round}\">");
    let (template, page) = if repair {
        (
            "pairing/confirm_re_pair.html",
            context! {
                round_number => round, csrf_input => csrf.input(), round_input,
            },
        )
    } else {
        let check = readiness(&mut db, &settings, round).await?;
        let checks = [
            (
                format!(
                    "Enough judges checked in for Round {round}? Need {}, have {}",
                    check.needed, check.judges
                ),
                check.judges >= check.needed as i64,
                "Judges are checked in".into(),
                "Not enough judges".into(),
            ),
            (
                format!(
                    "Enough rooms available Round {round}? Need {}, have {}",
                    check.needed, check.rooms
                ),
                check.rooms >= check.needed as i64,
                "Rooms are checked in".into(),
                "Not enough rooms".into(),
            ),
            (
                format!("All Rounds properly entered for Round {}", round - 1),
                check.previous_error.is_none(),
                "All rounds look good".into(),
                if check.previous_error.is_some() {
                    previous_notice(&mut db, round - 1).await?
                } else {
                    String::new()
                },
            ),
        ];
        let check_status = checks.map(|(message, valid, yes, no)| {
            (
                message,
                if valid { "Yes" } else { "No" },
                if valid { yes } else { no },
            )
        });
        let checked_in_judges = checked_in_judges(&mut db, round).await?;
        (
            "pairing/pair_round.html",
            context! {
                title => format!("Pairing Round {round}"),
                check_status, checked_in_judges, csrf_input => csrf.input(), round_input,
            },
        )
    };
    db.commit().await?;
    let mut response = legacy_html::render(template, shell, page, StatusCode::OK);
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}

async fn checked_in_judges(
    db: &mut MySqlConnection,
    round: i32,
) -> Result<Vec<minijinja::Value>, sqlx::Error> {
    let rows = sqlx::query_as::<_, (i32, String, Option<String>)>(
        "SELECT j.id, j.name, s.name FROM tab_judge j
         LEFT JOIN tab_judge_schools x ON x.judge_id = j.id LEFT JOIN tab_school s ON s.id = x.school_id
         WHERE EXISTS (SELECT 1 FROM tab_checkin c WHERE c.judge_id = j.id AND c.round_number = ?)
         ORDER BY j.name, j.id, s.name, s.id",
    ).bind(round).fetch_all(db).await?;
    let mut judges: Vec<(i32, String, Vec<String>)> = Vec::new();
    for (id, name, school) in rows {
        if judges.last().is_none_or(|(previous, ..)| *previous != id) {
            judges.push((id, name, Vec::new()));
        }
        if let Some(school) = school.filter(|school| !school.is_empty())
            && let Some((_, _, schools)) = judges.last_mut()
        {
            schools.push(school);
        }
    }
    Ok(judges
        .into_iter()
        .map(|(id, name, schools)| {
            context! {
                id, name, affiliations_display => schools.join(", "),
            }
        })
        .collect())
}

async fn previous_notice(db: &mut MySqlConnection, round: i32) -> Result<String, sqlx::Error> {
    type Row = (i32, String, bool, bool, String, bool, bool);
    for (victor, government, gov_bye, gov_absent, opposition, opp_bye, opp_absent) in sqlx::query_as::<_, Row>(
        "SELECT r.victor, g.name,
            EXISTS(SELECT 1 FROM tab_bye b WHERE b.round_number = r.round_number AND b.bye_team_id = g.id),
            EXISTS(SELECT 1 FROM tab_noshow n WHERE n.round_number = r.round_number AND n.no_show_team_id = g.id),
            o.name,
            EXISTS(SELECT 1 FROM tab_bye b WHERE b.round_number = r.round_number AND b.bye_team_id = o.id),
            EXISTS(SELECT 1 FROM tab_noshow n WHERE n.round_number = r.round_number AND n.no_show_team_id = o.id)
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id
         JOIN tab_team o ON o.id = r.opp_team_id WHERE r.round_number = ? ORDER BY r.id",
    ).bind(round).fetch_all(db).await? {
        if victor == 0 {
            return Ok("Not all rounds are entered. 'You have not entered all the results from the previous round.'".into());
        }
        for (name, bye, absent) in [(government, gov_bye, gov_absent), (opposition, opp_bye, opp_absent)] {
            if bye {
                return Ok(format!("You have a bye and results. {name} both had a bye and debated last round"));
            }
            if absent {
                return Ok(format!("You have a noshow and results. {name} both debated and had a no show"));
            }
        }
    }
    Ok(String::new())
}

fn target(settings: &Settings, repair: bool) -> Result<i32, ChangeError> {
    let round = settings
        .integer("cur_round", 1)
        .saturating_sub(i32::from(repair));
    if round < 1 {
        return Err(ChangeError::Conflict("No round has been paired yet."));
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
    lock_round_if_configured(db)
        .await?
        .ok_or(sqlx::Error::RowNotFound)
}

pub async fn lock_round_if_configured(
    db: &mut MySqlConnection,
) -> Result<Option<i32>, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT id FROM tab_tabsettings WHERE `key` = 'cur_round' ORDER BY id LIMIT 1 FOR UPDATE",
    )
    .fetch_optional(db)
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

pub async fn status(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    match crate::pairing_status::render(&app, actor, &headers, None).await {
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
    match crate::pairing_status::render(&app, actor, &headers, Some(round)).await {
        Ok(response) => response,
        Err(issue) => error(actor, issue),
    }
}

pub async fn release(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<Action>,
) -> Response {
    let token = headers
        .get("x-csrftoken")
        .and_then(|token| token.to_str().ok())
        .unwrap_or(&form.csrfmiddlewaretoken);
    if !login::valid_csrf(&headers, token) {
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
        let released = settings.integer("pairing_released", 0) != 1;
        write_release(&mut db, released).await?;
        db.commit().await?;
        Ok(released)
    }
    .await;
    match result {
        Ok(released) => private(Json(serde_json::json!({
            "success": true, "pairing_released": released,
        }))),
        Err(issue) => error(actor, issue),
    }
}
