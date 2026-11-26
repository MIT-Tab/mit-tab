use std::collections::{BTreeMap, BTreeSet};

use chrono::NaiveDateTime;
use serde::Serialize;
use serde_json::{Map, Value, json};
use sqlx::{MySqlConnection, MySqlPool, Row};

use crate::{
    codes::{self, Credential},
    entity_store::{self, Entity},
    registration_form::{Judge, Member, Registration, SchoolField, Team, length},
    roster_log,
    settings::Settings,
    staff_pairing::lock_round,
};

#[derive(Serialize)]
pub struct Config {
    pub can_create: bool,
    pub can_edit: bool,
    pub housing: bool,
    pub last_round: i32,
}

pub async fn config(db: &mut MySqlConnection) -> Result<Config, sqlx::Error> {
    let entry = sqlx::query_as::<_, (bool, bool, bool)>(
        "SELECT allow_new_registrations
            AND (new_registrations_open_at IS NULL OR new_registrations_open_at <= UTC_TIMESTAMP(6))
            AND (new_registrations_close_at IS NULL OR new_registrations_close_at > UTC_TIMESTAMP(6)),
         allow_registration_edits
            AND (registration_edits_open_at IS NULL OR registration_edits_open_at <= UTC_TIMESTAMP(6))
            AND (registration_edits_close_at IS NULL OR registration_edits_close_at > UTC_TIMESTAMP(6)),
         crash_housing_enabled FROM registration_registrationconfig WHERE id = 1",
    ).fetch_optional(&mut *db).await?;
    let (can_create, can_edit, housing) = entry.unwrap_or((true, true, false));
    let settings = Settings::load(db, &["tot_rounds"]).await?;
    Ok(Config {
        can_create,
        can_edit,
        housing,
        last_round: settings.integer("tot_rounds", 6).clamp(0, 50),
    })
}

pub async fn lock_config(db: &mut MySqlConnection) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO registration_registrationconfig
            (id, allow_new_registrations, allow_registration_edits, updated_at, disc_scratch_quantity,
             disc_scratches_open, team_name_changes_allowed, crash_housing_enabled)
         VALUES (1, TRUE, TRUE, UTC_TIMESTAMP(6), 0, FALSE, FALSE, FALSE)
         ON DUPLICATE KEY UPDATE id = 1",
    ).execute(db).await?;
    Ok(())
}

#[derive(Clone, Serialize)]
struct School {
    id: i32,
    name: String,
    apda_id: Option<i32>,
}

impl School {
    fn field(&self) -> SchoolField {
        SchoolField {
            value: self
                .apda_id
                .filter(|&id| id > 0)
                .map_or_else(|| format!("pk:{}", self.id), |id| format!("apda:{id}")),
            name: self.name.clone(),
        }
    }
}

pub struct Entry {
    pub form: Registration,
    snapshot: Value,
}

pub async fn existing_code(
    db: &mut MySqlConnection,
    code: &str,
) -> Result<Option<i32>, sqlx::Error> {
    sqlx::query_scalar("SELECT id FROM registration_registration WHERE herokunator_code = ?")
        .bind(code)
        .fetch_optional(db)
        .await
}

async fn schools(db: &mut MySqlConnection) -> Result<BTreeMap<i32, School>, sqlx::Error> {
    let rows =
        sqlx::query_as::<_, (i32, String, Option<i32>)>("SELECT id, name, apda_id FROM tab_school")
            .fetch_all(db)
            .await?;
    Ok(rows
        .into_iter()
        .map(|(id, name, apda_id)| (id, School { id, name, apda_id }))
        .collect())
}

pub async fn entry(db: &mut MySqlConnection, id: i32) -> Result<Option<Entry>, sqlx::Error> {
    let Some(row) = sqlx::query("SELECT * FROM registration_registration WHERE id = ?")
        .bind(id)
        .fetch_optional(&mut *db)
        .await?
    else {
        return Ok(None);
    };
    let available_schools = schools(db).await?;
    let school = available_schools
        .get(&row.try_get::<i32, _>("school_id")?)
        .ok_or_else(|| sqlx::Error::Protocol("Registration school missing".into()))?;
    let revision = row
        .try_get::<NaiveDateTime, _>("updated_at")?
        .format("%Y-%m-%dT%H:%M:%S%.6f")
        .to_string();
    let mut form = Registration {
        id: Some(id),
        revision,
        school: school.field(),
        email: row.try_get("email")?,
        dietary_requests: row.try_get("dietary_requests")?,
        housing_requests: row.try_get("housing_requests")?,
        ..Registration::default()
    };

    let mut debaters: BTreeMap<i32, Vec<(Member, Option<i32>)>> = BTreeMap::new();
    let rows = sqlx::query(
        "SELECT d.id, d.name, d.email, d.apda_id, d.novice_status, d.needs_crash_housing,
            d.requests_wgm_housing, d.qualified, d.school_id, td.team_id, p.debater_id IS NOT NULL AS private_email
         FROM tab_team_debaters td JOIN tab_team t ON t.id = td.team_id
         JOIN tab_debater d ON td.debater_id = d.id
         LEFT JOIN mittab_registration_private_email p ON p.debater_id = d.id
         WHERE t.registration_id = ? ORDER BY td.team_id, d.id",
    ).bind(id).fetch_all(&mut *db).await?;
    for row in rows {
        let private: bool = row.try_get("private_email")?;
        let school_id: Option<i32> = row.try_get("school_id")?;
        let member = Member {
            id: Some(row.try_get("id")?),
            name: row.try_get("name")?,
            email: if private {
                String::new()
            } else {
                row.try_get::<Option<String>, _>("email")?
                    .unwrap_or_default()
            },
            use_private_email: private,
            apda_id: row.try_get("apda_id")?,
            novice_status: row.try_get("novice_status")?,
            needs_crash_housing: row.try_get("needs_crash_housing")?,
            requests_wgm_housing: row.try_get("requests_wgm_housing")?,
            qualified: row.try_get("qualified")?,
            school: school_id
                .and_then(|id| available_schools.get(&id))
                .map(School::field)
                .unwrap_or_default(),
        };
        debaters
            .entry(row.try_get("team_id")?)
            .or_default()
            .push((member, school_id));
    }
    let mut team_snapshots = Vec::new();
    for (team_id, name, seed, primary, hybrid) in sqlx::query_as::<_, (i32, String, i32, i32, Option<i32>)>(
        "SELECT id, name, seed, school_id, hybrid_school_id FROM tab_team WHERE registration_id = ? ORDER BY name, id",
    ).bind(id).fetch_all(&mut *db).await? {
        let members = debaters.remove(&team_id).unwrap_or_default();
        let second_primary = members.get(1).is_some_and(|(_, school)| *school == Some(primary));
        let member_snapshots: Vec<_> = members.iter().map(|(member, school_id)| json!({
            "id": member.id, "name": member.name, "email": (!member.email.is_empty()).then_some(&member.email),
            "apda_id": member.apda_id, "novice_status": member.novice_status,
            "needs_crash_housing": member.needs_crash_housing,
            "requests_wgm_housing": member.requests_wgm_housing,
            "qualified": member.qualified, "school": school_id.and_then(|id| available_schools.get(&id))
        })).collect();
        team_snapshots.push(json!({"id": team_id, "name": name, "seed": seed,
            "school": available_schools.get(&primary), "hybrid_school": hybrid.and_then(|id| available_schools.get(&id)),
            "debaters": member_snapshots}));
        let mut members = members.into_iter().map(|(member, _)| member);
        form.teams.push(Team { id: Some(team_id), name, seed, second_primary,
            members: [members.next().unwrap_or_default(), members.next().unwrap_or_default()] });
    }

    let affiliation_rows: Vec<(i32, i32)> = sqlx::query_as(
        "SELECT js.judge_id, js.school_id FROM tab_judge_schools js JOIN tab_judge j ON js.judge_id = j.id
         JOIN tab_school s ON js.school_id = s.id WHERE j.registration_id = ? ORDER BY s.name, s.id",
    ).bind(id).fetch_all(&mut *db).await?;
    let mut affiliations: BTreeMap<i32, Vec<&School>> = BTreeMap::new();
    for (judge, school) in affiliation_rows {
        if let Some(school) = available_schools.get(&school) {
            affiliations.entry(judge).or_default().push(school);
        }
    }
    let availability_rows: Vec<(i32, i32)> = sqlx::query_as(
        "SELECT c.judge_id, c.round_number FROM tab_judgeexpectedcheckin c JOIN tab_judge j ON c.judge_id = j.id
         WHERE j.registration_id = ? ORDER BY c.round_number",
    ).bind(id).fetch_all(&mut *db).await?;
    let mut availability: BTreeMap<i32, Vec<i32>> = BTreeMap::new();
    for (judge, round) in availability_rows {
        availability.entry(judge).or_default().push(round);
    }
    let mut judge_snapshots = Vec::new();
    for row in sqlx::query(
        "SELECT id, name, email, reported_experience, CAST(`rank` AS CHAR) AS calibration,
            CAST(`rank` AS SIGNED) AS experience, needs_crash_housing, requests_wgm_housing
         FROM tab_judge WHERE registration_id = ? ORDER BY name, id",
    )
    .bind(id)
    .fetch_all(db)
    .await?
    {
        let judge_id: i32 = row.try_get("id")?;
        let reported_experience: Option<u16> = row.try_get("reported_experience")?;
        let rank: i64 = row.try_get("experience")?;
        let judge_schools = affiliations.remove(&judge_id).unwrap_or_default();
        let judge = Judge {
            id: Some(judge_id),
            name: row.try_get("name")?,
            email: row
                .try_get::<Option<String>, _>("email")?
                .unwrap_or_default(),
            experience: reported_experience.unwrap_or(rank.clamp(0, 10) as u16),
            schools: judge_schools.iter().map(|school| school.field()).collect(),
            needs_crash_housing: row.try_get("needs_crash_housing")?,
            requests_wgm_housing: row.try_get("requests_wgm_housing")?,
            rounds: availability.remove(&judge_id).unwrap_or_default(),
        };
        judge_snapshots.push(json!({"id": judge_id, "name": judge.name,
            "calibration": row.try_get::<String, _>("calibration")?, "reported_experience": reported_experience,
            "needs_crash_housing": judge.needs_crash_housing, "requests_wgm_housing": judge.requests_wgm_housing,
            "schools": judge_schools, "availability_rounds": judge.rounds}));
        form.judges.push(judge);
    }
    let snapshot = json!({"id": id, "code": row.try_get::<String, _>("herokunator_code")?,
        "school": school, "email": form.email,
        "dietary_requests": form.dietary_requests, "housing_requests": form.housing_requests,
        "teams": team_snapshots, "judges": judge_snapshots});
    Ok(Some(Entry { form, snapshot }))
}

#[derive(Debug)]
pub enum Error {
    Invalid(&'static str),
    Closed,
    NotFound,
    Database,
}

impl From<sqlx::Error> for Error {
    fn from(_: sqlx::Error) -> Self {
        Self::Database
    }
}

pub async fn resolve_school(
    db: &mut MySqlConnection,
    field: &SchoolField,
    choices: &std::collections::HashMap<&str, &str>,
) -> Result<i32, Error> {
    let (kind, value) = field
        .value
        .split_once(':')
        .unwrap_or(("custom", field.value.as_str()));
    if kind == "pk" {
        let id = value
            .parse::<i32>()
            .map_err(|_| Error::Invalid("Unknown school"))?;
        return sqlx::query_scalar("SELECT id FROM tab_school WHERE id = ?")
            .bind(id)
            .fetch_optional(db)
            .await?
            .ok_or(Error::Invalid("Unknown school"));
    }
    let apda_id = if kind == "apda" {
        Some(
            value
                .parse::<i32>()
                .ok()
                .filter(|&id| id > 0)
                .ok_or(Error::Invalid("Unknown school"))?,
        )
    } else if kind == "custom" {
        None
    } else {
        return Err(Error::Invalid("Unknown school"));
    };
    let name = if let Some(apda_id) = apda_id {
        if let Some(id) =
            sqlx::query_scalar("SELECT id FROM tab_school WHERE apda_id = ? ORDER BY id LIMIT 1")
                .bind(apda_id)
                .fetch_optional(&mut *db)
                .await?
        {
            return Ok(id);
        }
        choices
            .get(field.value.as_str())
            .ok_or(Error::Invalid("Unknown school"))?
            .to_string()
    } else if !field.name.trim().is_empty() {
        field.name.trim().into()
    } else if value == "-1" {
        String::new()
    } else {
        let escaped = value.replace('+', "%2B");
        let encoded = format!("name={escaped}");
        url::form_urlencoded::parse(encoded.as_bytes())
            .next()
            .map(|(_, value)| value.into_owned())
            .unwrap_or_default()
    };
    if name.is_empty() || !length(&name, 50) || name.chars().any(char::is_control) {
        return Err(Error::Invalid(
            "Enter a school name of 50 characters or fewer",
        ));
    }
    if let Some((id, old_apda)) =
        sqlx::query_as::<_, (i32, Option<i32>)>("SELECT id, apda_id FROM tab_school WHERE name = ?")
            .bind(&name)
            .fetch_optional(&mut *db)
            .await?
    {
        if apda_id.is_some() && old_apda.is_none_or(|id| id == -1) {
            sqlx::query("UPDATE tab_school SET apda_id = ? WHERE id = ?")
                .bind(apda_id)
                .bind(id)
                .execute(&mut *db)
                .await?;
        }
        return Ok(id);
    }
    let change = sqlx::query("INSERT INTO tab_school (name, apda_id) VALUES (?, ?)")
        .bind(name)
        .bind(apda_id.or(Some(-1)))
        .execute(db)
        .await?;
    Ok(change.last_insert_id() as i32)
}

async fn save_debater(
    db: &mut MySqlConnection,
    member: &Member,
    school: i32,
    housing: bool,
    resolved_email: Option<&str>,
) -> Result<i32, Error> {
    let name = entity_store::unique_name(db, Entity::Debater, &member.name, member.id).await?;
    let stored = if member.email.is_empty() && resolved_email.is_none() {
        sqlx::query_scalar::<_, Option<String>>(
            "SELECT d.email FROM tab_debater d JOIN mittab_registration_private_email p ON p.debater_id = d.id
             WHERE d.id = ? AND d.apda_id = ? AND d.school_id = ?",
        ).bind(member.id).bind(member.apda_id).bind(school).fetch_optional(&mut *db).await?.flatten()
    } else {
        None
    };
    let email = if member.email.is_empty() {
        resolved_email
            .or(stored.as_deref())
            .ok_or(Error::Invalid("Each debater needs an email"))?
    } else {
        member.email.as_str()
    };
    let private = member.email.is_empty();
    let apda_id = member.apda_id.filter(|&id| id != 0).unwrap_or(-1);
    let id = if let Some(id) = member.id {
        sqlx::query(
            "UPDATE tab_debater SET name = ?, email = ?, novice_status = ?, qualified = ?, apda_id = ?, school_id = ?,
                needs_crash_housing = IF(?, ?, needs_crash_housing), requests_wgm_housing = IF(?, ?, requests_wgm_housing) WHERE id = ?",
        ).bind(&name).bind(email).bind(member.novice_status).bind(member.qualified).bind(apda_id).bind(school)
            .bind(housing).bind(member.needs_crash_housing).bind(housing).bind(member.requests_wgm_housing)
            .bind(id).execute(&mut *db).await?;
        id
    } else {
        let coin = codes::coin(db, false).await?;
        sqlx::query(
            "INSERT INTO tab_debater (name, email, novice_status, qualified, apda_id, school_id,
                needs_crash_housing, requests_wgm_housing, tiebreaker) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ).bind(name).bind(email).bind(member.novice_status).bind(member.qualified).bind(apda_id).bind(school)
            .bind(housing && member.needs_crash_housing).bind(housing && member.requests_wgm_housing)
            .bind(coin).execute(&mut *db).await?.last_insert_id() as i32
    };
    let marker = if private {
        "INSERT INTO mittab_registration_private_email (debater_id) VALUES (?) ON DUPLICATE KEY UPDATE debater_id = debater_id"
    } else {
        "DELETE FROM mittab_registration_private_email WHERE debater_id = ?"
    };
    sqlx::query(marker).bind(id).execute(db).await?;
    Ok(id)
}

async fn save_team(
    db: &mut MySqlConnection,
    team: &Team,
    registration: i32,
    primary: i32,
    secondary: i32,
) -> Result<i32, Error> {
    let name = entity_store::unique_name(db, Entity::Team, &team.name, team.id).await?;
    let id = if let Some(id) = team.id {
        sqlx::query("UPDATE tab_team SET name = ?, seed = ?, school_id = ?, break_preference = 0, checked_in = FALSE WHERE id = ?")
            .bind(name).bind(team.seed).bind(primary).bind(id).execute(db).await?;
        id
    } else {
        let coin = codes::coin(db, true).await?;
        let code = codes::unique(db, Credential::Team).await?;
        sqlx::query(
            "INSERT INTO tab_team (name, seed, checked_in, school_id, hybrid_school_id, tiebreaker,
                team_code, break_preference, ranking_public, created_at, registration_id)
             VALUES (?, ?, FALSE, ?, ?, ?, ?, 0, TRUE, UTC_TIMESTAMP(6), ?)",
        )
        .bind(name)
        .bind(team.seed)
        .bind(primary)
        .bind((primary != secondary).then_some(secondary))
        .bind(coin)
        .bind(code)
        .bind(registration)
        .execute(db)
        .await?
        .last_insert_id() as i32
    };
    Ok(id)
}

async fn save_judge(
    db: &mut MySqlConnection,
    judge: &Judge,
    registration: i32,
    housing: bool,
) -> Result<i32, Error> {
    let name = entity_store::unique_name(db, Entity::Judge, &judge.name, judge.id).await?;
    let id = if let Some(id) = judge.id {
        sqlx::query(
            "UPDATE tab_judge SET name = ?, email = ?, reported_experience = ?,
                needs_crash_housing = IF(?, ?, needs_crash_housing), requests_wgm_housing = IF(?, ?, requests_wgm_housing) WHERE id = ?",
        ).bind(name).bind(&judge.email).bind(judge.experience)
            .bind(housing).bind(judge.needs_crash_housing).bind(housing).bind(judge.requests_wgm_housing)
            .bind(id).execute(db).await?;
        id
    } else {
        let code = codes::unique(db, Credential::Judge).await?;
        sqlx::query(
            "INSERT INTO tab_judge (name, email, reported_experience, `rank`, ballot_code, wing_only, is_dino, request_wing,
               no_wings, needs_crash_housing, requests_wgm_housing, created_at, registration_id)
             VALUES (?, ?, ?, 0, ?, FALSE, FALSE, FALSE, FALSE, ?, ?, UTC_TIMESTAMP(6), ?)",
        ).bind(name).bind(&judge.email).bind(judge.experience).bind(code)
            .bind(housing && judge.needs_crash_housing).bind(housing && judge.requests_wgm_housing)
            .bind(registration).execute(db).await?.last_insert_id() as i32
    };
    Ok(id)
}

async fn set_team_members(
    db: &mut MySqlConnection,
    id: i32,
    members: [i32; 2],
) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM tab_team_debaters WHERE team_id = ? AND debater_id NOT IN (?, ?)")
        .bind(id)
        .bind(members[0])
        .bind(members[1])
        .execute(&mut *db)
        .await?;
    for member in members {
        sqlx::query("INSERT INTO tab_team_debaters (team_id, debater_id) VALUES (?, ?) ON DUPLICATE KEY UPDATE team_id = team_id")
            .bind(id).bind(member).execute(&mut *db).await?;
    }
    Ok(())
}

async fn set_judge_schools(
    db: &mut MySqlConnection,
    id: i32,
    schools: BTreeSet<i32>,
) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM tab_judge_schools WHERE judge_id = ?")
        .bind(id)
        .execute(&mut *db)
        .await?;
    for school in schools {
        sqlx::query("INSERT INTO tab_judge_schools (judge_id, school_id) VALUES (?, ?)")
            .bind(id)
            .bind(school)
            .execute(&mut *db)
            .await?;
    }
    Ok(())
}

async fn set_judge_availability(
    db: &mut MySqlConnection,
    id: i32,
    rounds: &[i32],
    name: &str,
    log: bool,
    last_round: i32,
) -> Result<(), sqlx::Error> {
    let existing: BTreeSet<i32> =
        sqlx::query_scalar("SELECT round_number FROM tab_judgeexpectedcheckin WHERE judge_id = ?")
            .bind(id)
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .collect();
    let desired: BTreeSet<i32> = rounds
        .iter()
        .copied()
        .filter(|&round| round <= last_round)
        .collect();
    for round in existing.difference(&desired) {
        sqlx::query("DELETE FROM tab_judgeexpectedcheckin WHERE judge_id = ? AND round_number = ?")
            .bind(id)
            .bind(round)
            .execute(&mut *db)
            .await?;
        if log {
            roster_log::availability(&mut *db, name, *round, false).await?;
        }
    }
    for round in desired.difference(&existing) {
        sqlx::query("INSERT INTO tab_judgeexpectedcheckin (judge_id, round_number) VALUES (?, ?)")
            .bind(id)
            .bind(round)
            .execute(&mut *db)
            .await?;
        if log {
            roster_log::availability(&mut *db, name, *round, true).await?;
        }
    }
    Ok(())
}

fn items<'a>(snapshot: &'a Value, key: &str) -> BTreeMap<i32, &'a Value> {
    snapshot[key]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| {
            i32::try_from(item["id"].as_i64()?)
                .ok()
                .map(|id| (id, item))
        })
        .collect()
}

fn changes(before: &Value, after: &Value) -> (Value, String) {
    let mut output = Map::new();
    let mut summary = Vec::new();
    for key in ["school", "email", "dietary_requests", "housing_requests"] {
        if before[key] != after[key] {
            output.insert(
                key.into(),
                json!({"before": before[key], "after": after[key]}),
            );
            let label = after[key]
                .as_str()
                .or_else(|| after[key]["name"].as_str())
                .unwrap_or_default();
            summary.push(format!("changed {key} to {label}"));
        }
    }
    for (key, label) in [("teams", "team"), ("judges", "judge")] {
        let previous = items(before, key);
        let current = items(after, key);
        let added: Vec<_> = current
            .iter()
            .filter(|(id, _)| !previous.contains_key(id))
            .map(|(_, &value)| value.clone())
            .collect();
        let removed: Vec<_> = previous
            .iter()
            .filter(|(id, _)| !current.contains_key(id))
            .map(|(_, &value)| value.clone())
            .collect();
        let updated: Vec<Value> = current
            .iter()
            .filter_map(|(id, &after)| {
                let before = *previous.get(id)?;
                (before != after).then(|| json!({"before": before, "after": after}))
            })
            .collect();
        let mut group = Map::new();
        for (action, rows) in [("added", added), ("removed", removed), ("updated", updated)] {
            if !rows.is_empty() {
                let count = rows.len();
                let plural = if count == 1 { "" } else { "s" };
                summary.push(format!("{action} {count} {label}{plural}"));
                group.insert(action.into(), Value::Array(rows));
            }
        }
        if !group.is_empty() {
            output.insert(key.into(), Value::Object(group));
        }
    }
    (Value::Object(output), summary.join(", "))
}

async fn log_change(
    db: &mut MySqlConnection,
    id: Option<i32>,
    action: &'static str,
    changes: Value,
    summary: &str,
    snapshot: &Value,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO registration_registrationchangelog
            (registration_id, registration_code, school_name, email, action, summary, changes, snapshot, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, UTC_TIMESTAMP(6))",
    ).bind(id).bind(snapshot["code"].as_str().unwrap_or_default())
        .bind(snapshot["school"]["name"].as_str().unwrap_or_default()).bind(snapshot["email"].as_str().unwrap_or_default())
        .bind(action).bind(summary).bind(changes.to_string()).bind(snapshot.to_string())
        .execute(db).await?;
    Ok(())
}

pub async fn save(
    pool: &MySqlPool,
    code: Option<&str>,
    input: &Registration,
    choices: &std::collections::HashMap<&str, &str>,
    emails: &BTreeMap<(i32, i32), String>,
) -> Result<(i32, String), Error> {
    input.validate().map_err(Error::Invalid)?;
    let mut tx = pool.begin().await?;
    lock_round(&mut tx).await?;
    lock_config(&mut tx).await?;
    let config = config(&mut tx).await?;
    if !if code.is_some() {
        config.can_edit
    } else {
        config.can_create
    } {
        return Err(Error::Closed);
    }
    let before = if let Some(code) = code {
        let id = existing_code(&mut tx, code).await?.ok_or(Error::NotFound)?;
        Some(entry(&mut tx, id).await?.ok_or(Error::NotFound)?)
    } else {
        None
    };
    if let Some(before) = &before
        && !input.revision.is_empty()
        && before.form.revision != input.revision
    {
        return Err(Error::Invalid(
            "This registration changed. Reload it before submitting again.",
        ));
    }
    let previous = before
        .as_ref()
        .map_or(&Value::Null, |entry| &entry.snapshot);
    let old_teams = items(previous, "teams");
    let old_judges = items(previous, "judges");
    let old_members: BTreeSet<i32> = old_teams
        .values()
        .flat_map(|team| items(team, "debaters").into_keys())
        .collect();
    let stale = input.teams.iter().any(|team| {
        team.id.is_some_and(|id| !old_teams.contains_key(&id))
            || team
                .members
                .iter()
                .any(|member| member.id.is_some_and(|id| !old_members.contains(&id)))
    }) || input
        .judges
        .iter()
        .any(|judge| judge.id.is_some_and(|id| !old_judges.contains_key(&id)));
    if stale {
        return Err(Error::Invalid(
            "A team, debater or judge does not belong to this registration.",
        ));
    }

    let school = resolve_school(&mut tx, &input.school, choices).await?;
    let (registration, code) = if let Some(before) = &before {
        let id = before.form.id.ok_or(Error::NotFound)?;
        sqlx::query(
            "UPDATE registration_registration SET school_id = ?, email = ?, dietary_requests = ?,
             housing_requests = IF(?, ?, housing_requests), updated_at = UTC_TIMESTAMP(6) WHERE id = ?",
        ).bind(school).bind(&input.email).bind(&input.dietary_requests).bind(config.housing)
            .bind(&input.housing_requests).bind(id).execute(&mut *tx).await?;
        (id, code.ok_or(Error::NotFound)?.to_string())
    } else {
        let code = codes::unique(&mut tx, Credential::Registration).await?;
        let id = sqlx::query(
            "INSERT INTO registration_registration (school_id, email, dietary_requests, housing_requests,
             herokunator_code, created_at, updated_at) VALUES (?, ?, ?, ?, ?, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
        ).bind(school).bind(&input.email).bind(&input.dietary_requests)
            .bind(if config.housing { input.housing_requests.as_str() } else { "" }).bind(&code)
            .execute(&mut *tx).await?.last_insert_id() as i32;
        (id, code)
    };

    let log = roster_log::enabled(&mut tx).await?;
    let mut saved_teams = BTreeSet::new();
    for team in &input.teams {
        let [first, second] = &team.members;
        let first_school = resolve_school(&mut tx, &first.school, choices).await?;
        let second_school = resolve_school(&mut tx, &second.school, choices).await?;
        let (primary, secondary) = if team.second_primary {
            (second_school, first_school)
        } else {
            (first_school, second_school)
        };
        if primary != school {
            return Err(Error::Invalid(
                "The primary debater must represent the registration school",
            ));
        }
        let id = save_team(&mut tx, team, registration, primary, secondary).await?;
        let mut members = [0; 2];
        for (index, (member, school)) in [(first, first_school), (second, second_school)]
            .into_iter()
            .enumerate()
        {
            let email = member
                .school
                .value
                .strip_prefix("apda:")
                .and_then(|school| school.parse::<i32>().ok())
                .zip(member.apda_id)
                .and_then(|key| emails.get(&key))
                .map(String::as_str);
            members[index] = save_debater(&mut tx, member, school, config.housing, email).await?;
        }
        set_team_members(&mut tx, id, members).await?;
        saved_teams.insert(id);
    }
    for id in old_teams.keys().filter(|id| !saved_teams.contains(*id)) {
        entity_store::delete_team(&mut tx, *id).await?;
    }

    let mut saved_judges = BTreeSet::new();
    for judge in &input.judges {
        let id = save_judge(&mut tx, judge, registration, config.housing).await?;
        let mut affiliations = BTreeSet::new();
        for selection in &judge.schools {
            affiliations.insert(resolve_school(&mut tx, selection, choices).await?);
        }
        if affiliations.is_empty() {
            affiliations.insert(school);
        }
        set_judge_schools(&mut tx, id, affiliations).await?;
        let name: String = sqlx::query_scalar("SELECT name FROM tab_judge WHERE id = ?")
            .bind(id)
            .fetch_one(&mut *tx)
            .await?;
        set_judge_availability(&mut tx, id, &judge.rounds, &name, log, config.last_round).await?;
        saved_judges.insert(id);
    }
    for id in old_judges.keys().filter(|id| !saved_judges.contains(*id)) {
        entity_store::delete_judge(&mut tx, *id).await?;
    }

    let after = entry(&mut tx, registration)
        .await?
        .ok_or(Error::NotFound)?
        .snapshot;
    if before.is_some() {
        let (fields, summary) = changes(previous, &after);
        if !summary.is_empty() {
            log_change(
                &mut tx,
                Some(registration),
                "updated",
                fields,
                &summary,
                &after,
            )
            .await?;
        }
    } else {
        log_change(
            &mut tx,
            Some(registration),
            "created",
            json!({"created": after}),
            "created registration",
            &after,
        )
        .await?;
    }
    if log {
        log_roster(&mut tx, previous, &after).await?;
    }
    tx.commit().await?;
    Ok((registration, code))
}

async fn log_roster(
    db: &mut MySqlConnection,
    before: &Value,
    after: &Value,
) -> Result<(), sqlx::Error> {
    for (key, label) in [("teams", "team"), ("judges", "judge")] {
        let previous = items(before, key);
        let current = items(after, key);
        for (id, row) in &current {
            let name = row["name"].as_str().unwrap_or_default();
            if let Some(old) = previous.get(id) {
                if old["name"] != row["name"] {
                    let old_name = old["name"].as_str().unwrap_or_default();
                    roster_log::record(
                        db,
                        key,
                        &format!("Renamed {label} “{old_name}” to “{name}”."),
                        false,
                    )
                    .await?;
                }
                if key == "teams" {
                    let member_names = |team: &Value| {
                        items(team, "debaters")
                            .into_iter()
                            .map(|(id, m)| (id, m["name"].clone()))
                            .collect::<BTreeMap<_, _>>()
                    };
                    if member_names(old) != member_names(row) {
                        roster_log::record(
                            db,
                            key,
                            &format!("Updated debaters for “{name}”."),
                            true,
                        )
                        .await?;
                    }
                }
            } else {
                roster_log::record(db, key, &format!("Added {label} “{name}”."), false).await?;
            }
        }
        for (id, row) in &previous {
            if !current.contains_key(id) {
                let name = row["name"].as_str().unwrap_or_default();
                roster_log::record(db, key, &format!("Removed {label} “{name}”."), false).await?;
            }
        }
    }
    Ok(())
}

pub async fn delete(pool: &MySqlPool, id: i32) -> Result<(), Error> {
    let mut tx = pool.begin().await?;
    lock_round(&mut tx).await?;
    lock_config(&mut tx).await?;
    let before = entry(&mut tx, id).await?.ok_or(Error::NotFound)?.snapshot;
    log_change(
        &mut tx,
        None,
        "deleted",
        json!({"deleted": before}),
        "deleted registration",
        &before,
    )
    .await?;
    let mut members = BTreeSet::new();
    for (id, team) in items(&before, "teams") {
        members.extend(items(team, "debaters").into_keys());
        entity_store::delete_team(&mut tx, id).await?;
    }
    for id in items(&before, "judges").into_keys() {
        entity_store::delete_judge(&mut tx, id).await?;
    }
    for id in members {
        let linked: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM tab_team_debaters WHERE debater_id = ?)",
        )
        .bind(id)
        .fetch_one(&mut *tx)
        .await?;
        if !linked {
            entity_store::delete_debater(&mut tx, id).await?;
        }
    }
    sqlx::query("UPDATE registration_registrationchangelog SET registration_id = NULL WHERE registration_id = ?")
        .bind(id).execute(&mut *tx).await?;
    sqlx::query(
        "DELETE FROM registration_registrationconfirmationemaillog WHERE registration_id = ?",
    )
    .bind(id)
    .execute(&mut *tx)
    .await?;
    sqlx::query("DELETE FROM registration_registration WHERE id = ?")
        .bind(id)
        .execute(&mut *tx)
        .await?;
    if roster_log::enabled(&mut tx).await? {
        log_roster(&mut tx, &before, &Value::Null).await?;
    }
    tx.commit().await?;
    Ok(())
}
