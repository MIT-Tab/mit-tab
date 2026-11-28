use std::collections::BTreeMap;

use chrono::{NaiveDateTime, Utc};
use minijinja::{Value, context};
use serde::Serialize;
use sqlx::{MySqlConnection, Row};

use crate::{
    legacy_html::escape, registration_admin::date_input, registration_form::Fields,
    settings::Settings,
};

pub(super) const FLAGS: [&str; 5] = [
    "allow_new_registrations",
    "allow_registration_edits",
    "crash_housing_enabled",
    "team_name_changes_allowed",
    "disc_scratches_open",
];
pub(super) const TIMES: [&str; 4] = [
    "new_registrations_open_at",
    "new_registrations_close_at",
    "registration_edits_open_at",
    "registration_edits_close_at",
];

pub(super) type Problems = BTreeMap<&'static str, String>;

#[derive(Serialize)]
pub(super) struct Schedule {
    pub flags: [bool; 5],
    pub dates: [Option<NaiveDateTime>; 4],
    pub limit: u16,
    pub judges_public: bool,
}

pub(super) async fn schedule(db: &mut MySqlConnection) -> Result<Schedule, sqlx::Error> {
    let mut schedule = Schedule {
        flags: [true, true, false, false, false],
        dates: [None; 4],
        limit: 0,
        judges_public: Settings::load(db, &["judges_public"])
            .await?
            .flag("judges_public", false),
    };
    if let Some(row) = sqlx::query(
        "SELECT allow_new_registrations, allow_registration_edits, crash_housing_enabled,
                team_name_changes_allowed, disc_scratches_open, new_registrations_open_at,
                new_registrations_close_at, registration_edits_open_at, registration_edits_close_at,
                disc_scratch_quantity
         FROM registration_registrationconfig WHERE id = 1",
    )
    .fetch_optional(db)
    .await?
    {
        for (flag, key) in schedule.flags.iter_mut().zip(FLAGS) {
            *flag = row.try_get(key)?;
        }
        for (date, key) in schedule.dates.iter_mut().zip(TIMES) {
            *date = row.try_get(key)?;
        }
        schedule.limit = row.try_get("disc_scratch_quantity")?;
    }
    Ok(schedule)
}

impl Schedule {
    pub(super) fn context(&self) -> Value {
        let now = Utc::now().naive_utc();
        let is_open =
            |enabled: bool, opens: Option<NaiveDateTime>, closes: Option<NaiveDateTime>| {
                enabled
                    && opens.is_none_or(|opens| opens <= now)
                    && closes.is_none_or(|closes| closes > now)
            };
        context! {
            can_create => is_open(self.flags[0], self.dates[0], self.dates[1]),
            can_modify => is_open(self.flags[1], self.dates[2], self.dates[3]),
            crash_housing_enabled => self.flags[2],
        }
    }
}

pub(super) fn hidden(name: &str, value: &str) -> String {
    input("hidden", name, value, "")
}

fn input(kind: &str, name: &str, value: &str, attrs: &str) -> String {
    let value = if value.is_empty() {
        String::new()
    } else {
        format!(r#" value="{}""#, escape(value))
    };
    format!(r#"<input type="{kind}" name="{name}"{value}{attrs} id="id_{name}">"#)
}

pub(super) fn settings_form(
    schedule: &Schedule,
    posted: Option<&Fields>,
    problems: &Problems,
) -> Value {
    let make = |name: &str, input: String| {
        let errors = problems.get(name).map_or_else(String::new, |message| {
            format!(r#"<ul class="errorlist"><li>{}</li></ul>"#, escape(message))
        });
        context! { input => Value::from_safe_string(input), errors => Value::from_safe_string(errors),
        id_for_label => format!("id_{name}") }
    };
    let value = |key: &str, initial: String| {
        posted.map_or(initial, |fields| {
            fields.all(key).last().cloned().unwrap_or_default()
        })
    };
    let mut widgets = BTreeMap::new();
    for (key, stored) in FLAGS.into_iter().zip(schedule.flags) {
        let checked = if posted.map_or(stored, |fields| fields.flag(key)) {
            " checked"
        } else {
            ""
        };
        let markup = format!(
            r#"<input type="checkbox" name="{key}" class="custom-control-input" id="id_{key}"{checked}>"#
        );
        widgets.insert(key, make(key, markup));
    }
    for (key, stored) in TIMES.into_iter().zip(schedule.dates) {
        widgets.insert(
            key,
            make(
                key,
                input(
                    "datetime-local",
                    key,
                    &value(key, date_input(stored)),
                    r#" class="form-control form-control-sm""#,
                ),
            ),
        );
    }
    let quantity = "disc_scratch_quantity";
    widgets.insert(
        quantity,
        make(
            quantity,
            input(
                "number",
                quantity,
                &value(quantity, schedule.limit.to_string()),
                r#" class="form-control form-control-sm" min="0""#,
            ),
        ),
    );
    let confirm = "confirm_judge_list_publication";
    widgets.insert(
        confirm,
        make(confirm, hidden(confirm, &value(confirm, String::new()))),
    );
    context! { judge_list_is_public => schedule.judges_public, errors => !problems.is_empty(),
    ..Value::from_serialize(widgets) }
}

#[derive(Serialize)]
pub(super) struct RegistrationRow {
    pub id: i32,
    pub herokunator_code: String,
    school: School,
    email: String,
    dietary_requests: String,
    housing_requests: String,
    created_at: NaiveDateTime,
    updated_at: NaiveDateTime,
    teams: Vec<Team>,
    judges: Vec<Judge>,
}

#[derive(Serialize)]
struct School {
    id: i32,
    name: String,
}

#[derive(Serialize)]
struct Team {
    id: i32,
    name: String,
    debaters: Vec<Debater>,
}

#[derive(Serialize)]
struct Debater {
    id: i32,
    name: String,
    needs_crash_housing: bool,
    requests_wgm_housing: bool,
}

#[derive(Serialize)]
struct Judge {
    id: i32,
    name: String,
    reported_experience: Option<u16>,
    rank: String,
    needs_crash_housing: bool,
    requests_wgm_housing: bool,
    schools: Vec<School>,
}

impl RegistrationRow {
    pub(super) fn context(&self, delete_field: String, logs: &[Value]) -> Value {
        let edit_url = if crate::registration_views::valid_code(&self.herokunator_code) {
            format!("/registration/{}/", self.herokunator_code)
        } else {
            "/404/".into()
        };
        context! {
            delete_field => Value::from_safe_string(delete_field),
            edit_url,
            team_count => self.teams.len(),
            judge_count => self.judges.len(),
            created_display => self.created_at.format("%b %-d, %Y, %H:%M").to_string(),
            updated_display => self.updated_at.format("%b %-d, %Y, %H:%M").to_string(),
            change_logs => logs, ..Value::from_serialize(self)
        }
    }
}

pub(super) async fn registrations(
    db: &mut MySqlConnection,
    only: Option<i32>,
) -> Result<Vec<RegistrationRow>, sqlx::Error> {
    let mut members: BTreeMap<i32, Vec<Debater>> = BTreeMap::new();
    for (team, id, name, needs_crash_housing, requests_wgm_housing) in
        sqlx::query_as::<_, (i32, i32, String, bool, bool)>(
            "SELECT t.id, d.id, d.name, d.needs_crash_housing, d.requests_wgm_housing
             FROM tab_team t JOIN tab_team_debaters td ON td.team_id = t.id
             JOIN tab_debater d ON d.id = td.debater_id
             WHERE t.registration_id IS NOT NULL AND (? IS NULL OR t.registration_id = ?)
             ORDER BY d.name, d.id",
        )
        .bind(only)
        .bind(only)
        .fetch_all(&mut *db)
        .await?
    {
        members.entry(team).or_default().push(Debater {
            id,
            name,
            needs_crash_housing,
            requests_wgm_housing,
        });
    }
    let mut teams: BTreeMap<i32, Vec<Team>> = BTreeMap::new();
    for (registration, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
        "SELECT registration_id, id, name FROM tab_team
         WHERE registration_id IS NOT NULL AND (? IS NULL OR registration_id = ?) ORDER BY name, id",
    ).bind(only).bind(only).fetch_all(&mut *db).await? {
        teams.entry(registration).or_default().push(Team {
            id, name, debaters: members.remove(&id).unwrap_or_default(),
        });
    }
    let mut schools: BTreeMap<i32, Vec<School>> = BTreeMap::new();
    for (judge, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
        "SELECT j.id, s.id, s.name FROM tab_judge j JOIN tab_judge_schools js ON js.judge_id = j.id
         JOIN tab_school s ON s.id = js.school_id
         WHERE j.registration_id IS NOT NULL AND (? IS NULL OR j.registration_id = ?) ORDER BY s.name, s.id",
    ).bind(only).bind(only).fetch_all(&mut *db).await? {
        schools.entry(judge).or_default().push(School { id, name });
    }
    let mut judges: BTreeMap<i32, Vec<Judge>> = BTreeMap::new();
    for row in sqlx::query(
        "SELECT registration_id, id, name, reported_experience, CAST(`rank` AS CHAR) rank_text,
                needs_crash_housing, requests_wgm_housing
         FROM tab_judge WHERE registration_id IS NOT NULL AND (? IS NULL OR registration_id = ?)
         ORDER BY name, id",
    )
    .bind(only)
    .bind(only)
    .fetch_all(&mut *db)
    .await?
    {
        let id: i32 = row.try_get("id")?;
        judges
            .entry(row.try_get("registration_id")?)
            .or_default()
            .push(Judge {
                id,
                name: row.try_get("name")?,
                reported_experience: row.try_get("reported_experience")?,
                rank: row.try_get("rank_text")?,
                needs_crash_housing: row.try_get("needs_crash_housing")?,
                requests_wgm_housing: row.try_get("requests_wgm_housing")?,
                schools: schools.remove(&id).unwrap_or_default(),
            });
    }
    sqlx::query(
        "SELECT r.id, r.herokunator_code, s.id school_id, s.name school_name, r.email,
                r.dietary_requests, r.housing_requests, r.created_at, r.updated_at
         FROM registration_registration r JOIN tab_school s ON s.id = r.school_id
         WHERE (? IS NULL OR r.id = ?) ORDER BY r.created_at DESC, r.id DESC",
    )
    .bind(only)
    .bind(only)
    .fetch_all(db)
    .await?
    .into_iter()
    .map(|row| {
        let id: i32 = row.try_get("id")?;
        Ok(RegistrationRow {
            id,
            herokunator_code: row.try_get("herokunator_code")?,
            school: School {
                id: row.try_get("school_id")?,
                name: row.try_get("school_name")?,
            },
            email: row.try_get("email")?,
            dietary_requests: row.try_get("dietary_requests")?,
            housing_requests: row.try_get("housing_requests")?,
            created_at: row.try_get("created_at")?,
            updated_at: row.try_get("updated_at")?,
            teams: teams.remove(&id).unwrap_or_default(),
            judges: judges.remove(&id).unwrap_or_default(),
        })
    })
    .collect()
}

type Activity = (
    Option<i32>,
    String,
    String,
    String,
    String,
    String,
    NaiveDateTime,
);

fn activity(
    (_, registration_code, school_name, email, action, summary, created_at): Activity,
) -> Value {
    let get_action_display = match action.as_str() {
        "created" => "Created",
        "updated" => "Updated",
        "deleted" => "Deleted",
        _ => &action,
    };
    context! {
        registration_code, school_name, email, get_action_display, summary,
        created_display => created_at.format("%b %-d, %H:%M").to_string(),
    }
}

pub(super) async fn activities(
    db: &mut MySqlConnection,
) -> Result<(Vec<Value>, BTreeMap<i32, Vec<Value>>), sqlx::Error> {
    let recent = sqlx::query_as::<_, Activity>(
        "SELECT registration_id, registration_code, school_name, email, action, summary, created_at
         FROM registration_registrationchangelog ORDER BY created_at DESC, id DESC LIMIT 25",
    )
    .fetch_all(&mut *db)
    .await?;
    let mut attached: BTreeMap<i32, Vec<Value>> = BTreeMap::new();
    for row in sqlx::query_as::<_, Activity>(
        "SELECT registration_id, registration_code, school_name, email, action, summary, created_at
         FROM (
           SELECT registration_id, registration_code, school_name, email, action, summary, created_at,
                  ROW_NUMBER() OVER (PARTITION BY registration_id ORDER BY created_at DESC, id DESC) AS ordinal
           FROM registration_registrationchangelog WHERE registration_id IS NOT NULL
         ) recent WHERE ordinal <= 3 ORDER BY registration_id, ordinal",
    ).fetch_all(db).await? {
        if let Some(id) = row.0 {
            attached.entry(id).or_default().push(activity(row));
        }
    }
    Ok((recent.into_iter().map(activity).collect(), attached))
}
