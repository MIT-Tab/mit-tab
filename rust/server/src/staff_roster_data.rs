use std::{collections::BTreeSet, ops::RangeInclusive};

use chrono::NaiveDateTime;
use serde_json::json;
use sqlx::{AssertSqlSafe, FromRow, MySqlConnection, QueryBuilder, Row, mysql::MySqlRow};

use crate::{
    codes::{self, Credential},
    entity_store, judge_portal,
    registration_form::{Fields, integer_input, length, valid_email},
    roster_log,
    settings::Settings,
    staff_roster_fields::{checkbox_value, email_errors, rank_input},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    School,
    Room,
    Debater,
    Judge,
    Team,
}

impl Kind {
    pub const ALL: [Self; 5] = [
        Self::School,
        Self::Room,
        Self::Debater,
        Self::Judge,
        Self::Team,
    ];

    pub fn model(self) -> &'static str {
        match self {
            Self::School => "school",
            Self::Room => "room",
            Self::Debater => "debater",
            Self::Judge => "judge",
            Self::Team => "team",
        }
    }

    pub fn singular(self) -> &'static str {
        match self {
            Self::School => "School",
            Self::Room => "Room",
            Self::Debater => "Debater",
            Self::Judge => "Judge",
            Self::Team => "Team",
        }
    }

    pub fn table(self) -> &'static str {
        match self {
            Self::School => "tab_school",
            Self::Room => "tab_room",
            Self::Debater => "tab_debater",
            Self::Judge => "tab_judge",
            Self::Team => "tab_team",
        }
    }

    pub fn parse(path: &str) -> Option<Self> {
        let root = path.split('/').nth(1)?;
        Self::ALL.into_iter().find(|kind| {
            root == kind.model()
                || root.strip_prefix("enter_") == Some(kind.model())
                || root
                    .strip_prefix("view_")
                    .and_then(|word| word.strip_suffix('s'))
                    == Some(kind.model())
        })
    }

    pub fn limit(self) -> usize {
        match self {
            Self::School => 50,
            Self::Team => 35,
            _ => 30,
        }
    }
}

#[derive(Debug)]
pub enum Error {
    Invalid(&'static str),
    Missing,
    Database,
}

impl From<sqlx::Error> for Error {
    fn from(error: sqlx::Error) -> Self {
        if error
            .as_database_error()
            .is_some_and(|error| error.is_unique_violation())
        {
            Self::Invalid("That name or code already exists.")
        } else {
            Self::Database
        }
    }
}

pub async fn name(
    db: &mut MySqlConnection,
    kind: Kind,
    id: i32,
) -> Result<Option<String>, sqlx::Error> {
    let sql = format!("SELECT name FROM {} WHERE id = ?", kind.table());
    sqlx::query_scalar(AssertSqlSafe(sql))
        .bind(id)
        .fetch_optional(db)
        .await
}

fn valid_name(kind: Kind, fields: &Fields) -> Result<&str, Error> {
    let name = fields.get("name");
    if name.is_empty() || !length(name, kind.limit()) || name.chars().any(char::is_control) {
        return Err(Error::Invalid(
            "Enter a valid name within the length limit.",
        ));
    }
    Ok(name)
}

fn integer(fields: &Fields, key: &str) -> Result<Option<i32>, Error> {
    let value = fields.get(key);
    if value.is_empty() {
        return Ok(None);
    }
    value
        .parse()
        .map(Some)
        .map_err(|_| Error::Invalid("Enter a valid number."))
}

async fn foreign(
    db: &mut MySqlConnection,
    fields: &Fields,
    key: &str,
    table: &'static str,
) -> Result<Option<i32>, Error> {
    let Some(id) = integer(fields, key)? else {
        return Ok(None);
    };
    require_existing(db, table, [id]).await?;
    Ok(Some(id))
}

fn decimal<'a>(fields: &'a Fields, key: &str) -> Result<&'a str, Error> {
    let input = fields.get(key);
    let digits = input.strip_prefix(['-', '+']).unwrap_or(input);
    let (whole, fraction) = digits.split_once('.').unwrap_or((digits, ""));
    let valid = !digits.is_empty()
        && (whole.len() + fraction.len() > 0)
        && whole.trim_start_matches('0').len() <= 2
        && fraction.len() <= 2
        && whole
            .bytes()
            .chain(fraction.bytes())
            .all(|digit| digit.is_ascii_digit());
    if !valid {
        return Err(Error::Invalid(
            "Enter a rank with up to two digits before and after the decimal point.",
        ));
    }
    Ok(input)
}

fn enum_value(fields: &Fields, key: &str, max: i32, default: i32) -> Result<i32, Error> {
    let value = integer(fields, key)?.unwrap_or(default);
    if (0..=max).contains(&value) {
        Ok(value)
    } else {
        Err(Error::Invalid("Choose a valid option."))
    }
}

fn optional_email(fields: &Fields) -> Result<Option<&str>, Error> {
    let email = fields.get("email");
    if email.is_empty() {
        return Ok(None);
    }
    if valid_email(email) {
        Ok(Some(email))
    } else {
        Err(Error::Invalid("Enter a valid email address."))
    }
}

pub fn ids(fields: &Fields, key: &str) -> Result<BTreeSet<i32>, Error> {
    fields
        .all(key)
        .iter()
        .filter(|value| !value.trim().is_empty())
        .map(|value| {
            value
                .trim()
                .parse::<i32>()
                .ok()
                .filter(|&id| id > 0)
                .ok_or(Error::Invalid("Choose a valid roster entry."))
        })
        .collect()
}

async fn require_existing(
    db: &mut MySqlConnection,
    table: &'static str,
    ids: impl IntoIterator<Item = i32>,
) -> Result<(), Error> {
    let ids: BTreeSet<_> = ids.into_iter().collect();
    if ids.is_empty() {
        return Ok(());
    }
    let mut query = QueryBuilder::new("SELECT COUNT(*) FROM ");
    query.push(table).push(" WHERE id IN (");
    let mut values = query.separated(", ");
    for &id in &ids {
        values.push_bind(id);
    }
    let count: i64 = query.push(")").build_query_scalar().fetch_one(db).await?;
    if count as usize == ids.len() {
        Ok(())
    } else {
        Err(Error::Invalid(
            "A selected entry was removed. Reload the page and try again.",
        ))
    }
}

async fn set_links(
    db: &mut MySqlConnection,
    table: &'static str,
    parent: &'static str,
    child: &'static str,
    id: i32,
    values: BTreeSet<i32>,
) -> Result<(), sqlx::Error> {
    let delete = format!("DELETE FROM {table} WHERE {parent} = ?");
    sqlx::query(AssertSqlSafe(delete))
        .bind(id)
        .execute(&mut *db)
        .await?;
    if !values.is_empty() {
        let mut insert = QueryBuilder::new("INSERT INTO ");
        insert
            .push(table)
            .push(" (")
            .push(parent)
            .push(", ")
            .push(child)
            .push(") ");
        insert.push_values(values, |mut row, value| {
            row.push_bind(id).push_bind(value);
        });
        insert.build().execute(db).await?;
    }
    Ok(())
}

pub struct Edit<'a> {
    pub id: Option<i32>,
    pub user: i32,
    pub checkins: bool,
    pub public_log: bool,
    pub fields: &'a Fields,
}

pub async fn save_on(db: &mut MySqlConnection, kind: Kind, edit: Edit<'_>) -> Result<i32, Error> {
    let new_name = valid_name(kind, edit.fields)?;
    let original = if let Some(id) = edit.id {
        Some(name(db, kind, id).await?.ok_or(Error::Missing)?)
    } else {
        None
    };
    let mut team_audit = None;
    let id = match kind {
        Kind::School => save_school(db, &edit, new_name).await?,
        Kind::Room => save_room(db, &edit, new_name).await?,
        Kind::Debater => save_debater(db, &edit, new_name).await?,
        Kind::Judge => save_judge(db, &edit, new_name).await?,
        Kind::Team => {
            let (id, changes) = save_team(db, &edit, new_name).await?;
            team_audit = Some(changes);
            id
        }
    };
    if matches!(kind, Kind::Team | Kind::Judge) {
        let changes = match (team_audit, original.as_deref()) {
            (Some(fields), Some(_)) => json!({ "fields": fields }),
            (None, Some(previous)) if previous != new_name => {
                json!({"fields": ["name"], "name": {"before": previous, "after": new_name}})
            }
            _ => json!({}),
        };
        audit(
            db,
            kind,
            id,
            new_name,
            edit.user,
            edit.id.is_none(),
            changes,
        )
        .await?;
        if kind == Kind::Judge && edit.public_log && roster_log::enabled(db).await? {
            let message = match original {
                None => Some(format!("Added {} “{new_name}”.", kind.model())),
                Some(old) if old != new_name => {
                    Some(format!("Renamed {} “{old}” to “{new_name}”.", kind.model()))
                }
                _ => None,
            };
            if let Some(message) = message {
                roster_log::record(db, "judges", &message, false).await?;
            }
        }
    }
    Ok(id)
}

async fn save_school(db: &mut MySqlConnection, edit: &Edit<'_>, name: &str) -> Result<i32, Error> {
    let apda_id = apda_id(edit.fields)?;
    if let Some(id) = edit.id {
        sqlx::query("UPDATE tab_school SET name = ?, apda_id = ? WHERE id = ?")
            .bind(name)
            .bind(apda_id)
            .bind(id)
            .execute(db)
            .await?;
        return Ok(id);
    }
    let result = sqlx::query("INSERT INTO tab_school (name, apda_id) VALUES (?, ?)")
        .bind(name)
        .bind(apda_id)
        .execute(db)
        .await?;
    Ok(result.last_insert_id() as i32)
}

fn apda_id(fields: &Fields) -> Result<Option<i32>, Error> {
    let value = fields.get("apda_id");
    if value.is_empty() {
        Ok(None)
    } else {
        integer_input(value)
            .map(Some)
            .map_err(|_| Error::Invalid("Enter a valid number."))
    }
}

async fn save_room(db: &mut MySqlConnection, edit: &Edit<'_>, name: &str) -> Result<i32, Error> {
    let rank = rank_input(edit.fields.get("rank")).map_err(Error::Invalid)?;
    let tags = edit
        .fields
        .all("tags")
        .iter()
        .map(|value| {
            value
                .parse::<i32>()
                .ok()
                .filter(|id| id.to_string() == *value)
                .ok_or(Error::Invalid("Choose a valid roster entry."))
        })
        .collect::<Result<BTreeSet<_>, _>>()?;
    require_existing(db, "tab_roomtag", tags.iter().copied()).await?;
    let id = if let Some(id) = edit.id {
        sqlx::query("UPDATE tab_room SET name = ?, `rank` = ? WHERE id = ?")
            .bind(name)
            .bind(&rank)
            .bind(id)
            .execute(&mut *db)
            .await?;
        id
    } else {
        sqlx::query("INSERT INTO tab_room (name, `rank`) VALUES (?, ?)")
            .bind(name)
            .bind(&rank)
            .execute(&mut *db)
            .await?
            .last_insert_id() as i32
    };
    set_links(db, "tab_room_tags", "room_id", "roomtag_id", id, tags).await?;
    if edit.checkins && edit.id.is_some() {
        let snapshot = room_checkins(db, id, true).await?;
        if snapshot.revision != edit.fields.get("mittab_room_checkin_revision") {
            return Err(Error::Invalid(ROOM_CHECKIN_CONFLICT));
        }
        checkins(db, edit.fields, "tab_roomcheckin", "room_id", id).await?;
    }
    Ok(id)
}

async fn save_debater(db: &mut MySqlConnection, edit: &Edit<'_>, name: &str) -> Result<i32, Error> {
    let fields = edit.fields;
    let school = foreign(db, fields, "school", "tab_school").await?;
    let email = fields.get("email");
    if !email_errors(email).is_empty() {
        return Err(Error::Invalid("Enter a valid email address."));
    }
    let email = (!email.is_empty()).then_some(email);
    let apda_id = apda_id(fields)?;
    let status = match fields.all("novice_status").last().map(String::as_str) {
        Some("0") => 0,
        Some("1") => 1,
        _ => return Err(Error::Invalid("Choose a valid novice status.")),
    };
    let qualified = fields.flag("qualified");
    let crash = fields.flag("needs_crash_housing");
    let wgm = fields.flag("requests_wgm_housing");
    let coin = codes::coin(db, false).await?;
    if let Some(id) = edit.id {
        sqlx::query(
            "UPDATE tab_debater SET name = ?, school_id = ?, email = ?, apda_id = ?, novice_status = ?,
             qualified = ?, needs_crash_housing = ?, requests_wgm_housing = ?, tiebreaker = ? WHERE id = ?",
        ).bind(name).bind(school).bind(email).bind(apda_id).bind(status).bind(qualified)
            .bind(crash).bind(wgm).bind(coin).bind(id).execute(db).await?;
        return Ok(id);
    }
    let result = sqlx::query(
        "INSERT INTO tab_debater (name, school_id, email, apda_id, novice_status, qualified,
         needs_crash_housing, requests_wgm_housing, tiebreaker) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(name)
    .bind(school)
    .bind(email)
    .bind(apda_id)
    .bind(status)
    .bind(qualified)
    .bind(crash)
    .bind(wgm)
    .bind(coin)
    .execute(db)
    .await?;
    Ok(result.last_insert_id() as i32)
}

async fn save_judge(db: &mut MySqlConnection, edit: &Edit<'_>, name: &str) -> Result<i32, Error> {
    let fields = edit.fields;
    let rank = decimal(fields, "rank")?;
    let email = optional_email(fields)?;
    let experience = integer(fields, "reported_experience")?;
    if experience.is_some_and(|experience| !(0..=10).contains(&experience)) {
        return Err(Error::Invalid(
            "Reported years of experience must be between 0 and 10.",
        ));
    }
    let registration = foreign(db, fields, "registration", "registration_registration").await?;
    let code = fields.get("ballot_code");
    let code = if code.is_empty() {
        let old: Option<String> =
            sqlx::query_scalar("SELECT ballot_code FROM tab_judge WHERE id = ?")
                .bind(edit.id)
                .fetch_optional(&mut *db)
                .await?
                .flatten();
        match old {
            Some(old) if !old.is_empty() => old,
            _ => codes::unique(db, Credential::Judge).await?,
        }
    } else if judge_portal::valid_code(code) {
        code.into()
    } else {
        return Err(Error::Invalid(
            "Enter a valid judge ballot code of at most 30 characters.",
        ));
    };
    let schools = ids(fields, "schools")?;
    let tags = ids(fields, "required_room_tags")?;
    require_existing(db, "tab_school", schools.iter().copied()).await?;
    require_existing(db, "tab_roomtag", tags.iter().copied()).await?;
    let request_wing = fields.flag("request_wing");
    let no_wings = fields.flag("no_wings");
    if request_wing && no_wings {
        return Err(Error::Invalid(
            "A judge cannot both request a wing and request no wings.",
        ));
    }
    let assignments = "name = ?, `rank` = ?, email = ?, reported_experience = ?, registration_id = ?, ballot_code = ?,
       is_dino = ?, wing_only = ?, request_wing = ?, no_wings = ?, needs_crash_housing = ?, requests_wgm_housing = ?";
    let statement = if edit.id.is_some() {
        format!("UPDATE tab_judge SET {assignments} WHERE id = ?")
    } else {
        format!(
            "INSERT INTO tab_judge SET {assignments}, created_by_id = ?, created_at = UTC_TIMESTAMP(6)"
        )
    };
    let result = sqlx::query(AssertSqlSafe(statement))
        .bind(name)
        .bind(rank)
        .bind(email)
        .bind(experience)
        .bind(registration)
        .bind(code)
        .bind(fields.flag("is_dino"))
        .bind(fields.flag("wing_only"))
        .bind(request_wing)
        .bind(no_wings)
        .bind(fields.flag("needs_crash_housing"))
        .bind(fields.flag("requests_wgm_housing"))
        .bind(edit.id.unwrap_or(edit.user))
        .execute(&mut *db)
        .await?;
    let id = edit.id.unwrap_or(result.last_insert_id() as i32);
    set_links(
        db,
        "tab_judge_schools",
        "judge_id",
        "school_id",
        id,
        schools,
    )
    .await?;
    set_links(
        db,
        "tab_judge_required_room_tags",
        "judge_id",
        "roomtag_id",
        id,
        tags,
    )
    .await?;
    if edit.checkins && edit.id.is_some() {
        checkins(db, fields, "tab_checkin", "judge_id", id).await?;
    }
    Ok(id)
}

pub struct TeamRow {
    pub name: String,
    pub school: i32,
    pub hybrid: Option<i32>,
    pub registration: Option<i32>,
    pub seed: i32,
    pub division: i32,
    pub ranking_public: bool,
    pub checked_in: bool,
    pub creator: String,
    pub created: Option<NaiveDateTime>,
}

impl FromRow<'_, MySqlRow> for TeamRow {
    fn from_row(row: &MySqlRow) -> Result<Self, sqlx::Error> {
        Ok(Self {
            name: row.try_get("name")?,
            school: row.try_get("school")?,
            hybrid: row.try_get("hybrid")?,
            registration: row.try_get("registration")?,
            seed: row.try_get("seed")?,
            division: row.try_get("division")?,
            ranking_public: row.try_get("ranking_public")?,
            checked_in: row.try_get::<i64, _>("checked_in")? != 0,
            creator: row.try_get("creator")?,
            created: row.try_get("created")?,
        })
    }
}

impl TeamRow {
    pub async fn load(db: &mut MySqlConnection, id: i32, checkins: bool) -> Result<Self, Error> {
        sqlx::query_as(
            "SELECT t.name, t.school_id AS school, t.hybrid_school_id AS hybrid,
                    t.registration_id AS registration, t.seed, t.break_preference AS division,
                    t.ranking_public, CAST(IF(?, t.checked_in, FALSE) AS SIGNED) AS checked_in,
                    COALESCE(u.username, 'Unknown') AS creator, t.created_at AS created
             FROM tab_team t LEFT JOIN auth_user u ON t.created_by_id = u.id WHERE t.id = ?",
        )
        .bind(checkins)
        .bind(id)
        .fetch_optional(db)
        .await?
        .ok_or(Error::Missing)
    }
}

pub async fn team_members(
    db: &mut MySqlConnection,
    id: i32,
) -> Result<Vec<(i32, String)>, sqlx::Error> {
    sqlx::query_as(
        "SELECT d.id, d.name FROM tab_debater d JOIN tab_team_debaters t ON d.id = t.debater_id
         WHERE t.team_id = ? ORDER BY d.name",
    )
    .bind(id)
    .fetch_all(db)
    .await
}

pub async fn team_tags(db: &mut MySqlConnection, team: i32) -> Result<BTreeSet<i32>, sqlx::Error> {
    Ok(
        sqlx::query_scalar("SELECT roomtag_id FROM tab_team_required_room_tags WHERE team_id = ?")
            .bind(team)
            .fetch_all(db)
            .await?
            .into_iter()
            .collect(),
    )
}

pub async fn team_members_conflict(
    db: &mut MySqlConnection,
    team: Option<i32>,
    members: &BTreeSet<i32>,
) -> Result<bool, sqlx::Error> {
    if members.is_empty() {
        return Ok(false);
    }
    let mut query =
        QueryBuilder::new("SELECT EXISTS(SELECT 1 FROM tab_team_debaters WHERE debater_id IN (");
    let mut ids = query.separated(", ");
    for &id in members {
        ids.push_bind(id);
    }
    ids.push_unseparated(")");
    if let Some(team) = team {
        query.push(" AND team_id <> ").push_bind(team);
    }
    query.push(")").build_query_scalar().fetch_one(db).await
}

pub const TEAM_CHECKIN_CONFLICT: &str =
    "Team check-in changed. Review the current check-in before saving again.";

pub fn team_checkin_initial(fields: &Fields) -> Option<bool> {
    match fields.get("mittab_team_checkin_initial") {
        "0" => Some(false),
        "1" => Some(true),
        _ => None,
    }
}

async fn save_team(
    db: &mut MySqlConnection,
    edit: &Edit<'_>,
    name: &str,
) -> Result<(i32, Vec<&'static str>), Error> {
    let fields = edit.fields;
    let school = foreign(db, fields, "school", "tab_school")
        .await?
        .ok_or(Error::Invalid("Choose a school."))?;
    let hybrid = foreign(db, fields, "hybrid_school", "tab_school").await?;
    let registration = foreign(db, fields, "registration", "registration_registration").await?;
    let seed = enum_value(fields, "seed", 3, 0)?;
    let division = enum_value(fields, "break_preference", 1, 0)?;
    let tags = ids(fields, "required_room_tags")?;
    let members = ids(fields, "debaters")?;
    require_existing(db, "tab_roomtag", tags.iter().copied()).await?;
    require_existing(db, "tab_debater", members.iter().copied()).await?;
    if edit.id.is_some() && !(1..=2).contains(&members.len()) {
        return Err(Error::Invalid("Choose one or two debaters."));
    }
    if team_members_conflict(db, edit.id, &members).await? {
        return Err(Error::Invalid(
            "A selected debater is already on another team.",
        ));
    }
    let checked_in = checkbox_value(fields, "checked_in");
    let ranking_public = checkbox_value(fields, "ranking_public");
    let coin = codes::coin(db, true).await?;
    let mut changes = Vec::new();
    let mut previous_members = BTreeSet::new();
    let mut previous_tags = BTreeSet::new();
    let mut affiliations = Vec::new();
    let id = if let Some(id) = edit.id {
        let before = TeamRow::load(db, id, edit.checkins).await?;
        let edit_checkin = if edit.checkins {
            match team_checkin_initial(fields) {
                Some(initial) => initial != checked_in,
                None if before.checked_in != checked_in => {
                    return Err(Error::Invalid(TEAM_CHECKIN_CONFLICT));
                }
                None => false,
            }
        } else {
            false
        };
        affiliations = [(Some(before.school), Some(school)), (before.hybrid, hybrid)]
            .into_iter()
            .filter(|(old, new)| old != new)
            .collect();
        previous_members = team_members(db, id)
            .await?
            .into_iter()
            .map(|(id, _)| id)
            .collect();
        previous_tags = team_tags(db, id).await?;
        changes.extend(
            [
                ("name", before.name != name),
                ("school", before.school != school),
                ("hybrid_school", before.hybrid != hybrid),
                ("debaters", previous_members != members),
                ("registration", before.registration != registration),
                ("seed", before.seed != seed),
                (
                    "checked_in",
                    edit_checkin && before.checked_in != checked_in,
                ),
                ("required_room_tags", previous_tags != tags),
                ("break_preference", before.division != division),
                ("ranking_public", before.ranking_public != ranking_public),
            ]
            .into_iter()
            .filter_map(|(field, changed)| changed.then_some(field)),
        );
        let missing_code: bool = sqlx::query_scalar(
            "SELECT team_code IS NULL OR team_code = '' FROM tab_team WHERE id = ?",
        )
        .bind(id)
        .fetch_one(&mut *db)
        .await?;
        let code = if missing_code {
            Some(codes::unique(db, Credential::Team).await?)
        } else {
            None
        };
        sqlx::query(
            "UPDATE tab_team SET name = ?, school_id = ?, hybrid_school_id = ?, registration_id = ?, seed = ?,
             break_preference = ?, ranking_public = ?, checked_in = IF(?, ?, checked_in),
             team_code = COALESCE(?, team_code), tiebreaker = ? WHERE id = ?",
        ).bind(name).bind(school).bind(hybrid).bind(registration).bind(seed).bind(division)
            .bind(ranking_public).bind(edit_checkin).bind(checked_in).bind(code).bind(coin).bind(id)
            .execute(&mut *db).await?;
        id
    } else {
        let code = codes::unique(db, Credential::Team).await?;
        sqlx::query(
            "INSERT INTO tab_team (name, school_id, hybrid_school_id, registration_id, seed, break_preference,
             ranking_public, checked_in, tiebreaker, team_code, created_by_id, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, UTC_TIMESTAMP(6))",
        ).bind(name).bind(school).bind(hybrid).bind(registration).bind(seed).bind(division)
            .bind(ranking_public).bind(!edit.checkins || checked_in)
            .bind(coin).bind(code).bind(edit.user).execute(&mut *db).await?.last_insert_id() as i32
    };
    if edit.public_log && roster_log::enabled(db).await? {
        if edit.id.is_none() {
            roster_log::record(db, "teams", &format!("Added team “{name}”."), false).await?;
        }
        for (old, new) in affiliations {
            let old_id = old.map(|id| id.to_string());
            let new_id = new.map(|id| id.to_string());
            let (old, new) =
                roster_log::affiliation_names(db, old_id.as_deref(), new_id.as_deref()).await?;
            roster_log::record(
                db,
                "teams",
                &format!("Changed affiliation for “{name}”: {old} → {new}."),
                false,
            )
            .await?;
        }
        for (verb, prep, ids) in [
            ("Removed", "from", previous_members.difference(&members)),
            ("Added", "on", members.difference(&previous_members)),
        ] {
            for &debater_id in ids {
                let debater = self::name(db, Kind::Debater, debater_id)
                    .await?
                    .ok_or(Error::Missing)?;
                roster_log::record(
                    db,
                    "teams",
                    &format!("{verb} debater “{debater}” {prep} team “{name}”."),
                    true,
                )
                .await?;
            }
        }
    }
    change_team_links(
        db,
        "tab_team_debaters",
        "debater_id",
        id,
        &previous_members,
        &members,
    )
    .await?;
    change_team_links(
        db,
        "tab_team_required_room_tags",
        "roomtag_id",
        id,
        &previous_tags,
        &tags,
    )
    .await?;
    Ok((id, changes))
}

async fn change_team_links(
    db: &mut MySqlConnection,
    table: &'static str,
    related: &'static str,
    team: i32,
    before: &BTreeSet<i32>,
    after: &BTreeSet<i32>,
) -> Result<(), sqlx::Error> {
    let mut removed = before.difference(after).peekable();
    if removed.peek().is_some() {
        let mut delete = QueryBuilder::new("DELETE FROM ");
        delete
            .push(table)
            .push(" WHERE team_id = ")
            .push_bind(team)
            .push(" AND ")
            .push(related)
            .push(" IN (");
        let mut ids = delete.separated(", ");
        for &id in removed {
            ids.push_bind(id);
        }
        ids.push_unseparated(")");
        delete.build().execute(&mut *db).await?;
    }
    let mut added = after.difference(before).peekable();
    if added.peek().is_some() {
        let mut insert = QueryBuilder::new("INSERT INTO ");
        insert
            .push(table)
            .push(" (team_id, ")
            .push(related)
            .push(") ");
        insert.push_values(added, |mut row, &id| {
            row.push_bind(team).push_bind(id);
        });
        insert.build().execute(db).await?;
    }
    Ok(())
}

async fn checkins(
    db: &mut MySqlConnection,
    fields: &Fields,
    table: &'static str,
    column: &'static str,
    id: i32,
) -> Result<(), sqlx::Error> {
    for round in checkin_rounds(db).await? {
        if fields.flag(&format!("checkin_{}", round - 1)) {
            let query = format!(
                "INSERT INTO {table} ({column}, round_number) VALUES (?, ?)
              ON DUPLICATE KEY UPDATE round_number = round_number"
            );
            sqlx::query(AssertSqlSafe(query))
                .bind(id)
                .bind(round)
                .execute(&mut *db)
                .await?;
        } else {
            let query = format!("DELETE FROM {table} WHERE {column} = ? AND round_number = ?");
            sqlx::query(AssertSqlSafe(query))
                .bind(id)
                .bind(round)
                .execute(&mut *db)
                .await?;
        }
    }
    Ok(())
}

pub async fn checkin_rounds(db: &mut MySqlConnection) -> Result<RangeInclusive<i32>, sqlx::Error> {
    let settings = Settings::load(db, &["tot_rounds"]).await?;
    Ok(0..=settings.integer("tot_rounds", 6).clamp(0, 50))
}

pub const ROOM_CHECKIN_CONFLICT: &str =
    "Room check-ins changed. Review the current check-ins before saving again.";

pub struct RoomCheckins {
    pub rounds: RangeInclusive<i32>,
    pub checked: Vec<i32>,
    pub revision: String,
}

pub async fn room_checkins(
    db: &mut MySqlConnection,
    room: i32,
    locked: bool,
) -> Result<RoomCheckins, sqlx::Error> {
    use ring::digest::{Context, SHA256};
    let rounds = checkin_rounds(db).await?;
    let query = if locked {
        "SELECT round_number FROM tab_roomcheckin WHERE room_id = ? AND round_number BETWEEN 0 AND ? ORDER BY round_number FOR UPDATE"
    } else {
        "SELECT round_number FROM tab_roomcheckin WHERE room_id = ? AND round_number BETWEEN 0 AND ? ORDER BY round_number"
    };
    let checked = sqlx::query_scalar::<_, i32>(query)
        .bind(room)
        .bind(*rounds.end())
        .fetch_all(db)
        .await?;
    let mut stamp = Context::new(&SHA256);
    stamp.update(b"room check-ins v1\0");
    stamp.update(&room.to_be_bytes());
    stamp.update(&rounds.end().to_be_bytes());
    for round in &checked {
        stamp.update(&round.to_be_bytes());
    }
    let revision = hex::encode(stamp.finish());
    Ok(RoomCheckins {
        rounds,
        checked,
        revision,
    })
}

async fn audit(
    db: &mut MySqlConnection,
    kind: Kind,
    id: i32,
    name: &str,
    user: i32,
    created: bool,
    changes: serde_json::Value,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO tab_auditevent (object_id, object_repr, event_type, created_at, changes, note, content_type_id, user_id)
         VALUES (?, ?, ?, UTC_TIMESTAMP(6), ?, '',
           (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = ?), ?)",
    ).bind(id).bind(name).bind(if created { "create" } else { "edit" }).bind(changes.to_string())
        .bind(kind.model()).bind(user).execute(db).await?;
    Ok(())
}

pub async fn delete_on(db: &mut MySqlConnection, kind: Kind, id: i32) -> Result<(), Error> {
    let name = name(db, kind, id).await?.ok_or(Error::Missing)?;
    match kind {
        Kind::Team => entity_store::delete_team(db, id).await?,
        Kind::Judge => entity_store::delete_judge(db, id).await?,
        Kind::Debater => {
            if referenced(
                db,
                "SELECT EXISTS(SELECT 1 FROM tab_team_debaters WHERE debater_id = ?)",
                id,
            )
            .await?
            {
                return Err(Error::Invalid(
                    "Remove this debater from their team before deleting the debater.",
                ));
            }
            entity_store::delete_debater(db, id).await?;
        }
        Kind::Room => {
            if referenced(
                db,
                "SELECT EXISTS(SELECT 1 FROM tab_round WHERE room_id = ?)",
                id,
            )
            .await?
            {
                return Err(Error::Invalid(
                    "This room is assigned to a preliminary round.",
                ));
            }
            let outrounds: Vec<i32> =
                sqlx::query_scalar("SELECT id FROM tab_outround WHERE room_id = ?")
                    .bind(id)
                    .fetch_all(&mut *db)
                    .await?;
            entity_store::delete_outrounds(db, &outrounds).await?;
            entity_store::delete_links(
                db,
                &[id],
                &[
                    ("tab_roomcheckin", "room_id"),
                    ("tab_room_tags", "room_id"),
                    ("tab_room", "id"),
                ],
            )
            .await?;
        }
        Kind::School => {
            let teams = referenced(
                db,
                "SELECT EXISTS(SELECT 1 FROM tab_team WHERE school_id = ?)",
                id,
            )
            .await?;
            let judges = referenced(
                db,
                "SELECT EXISTS(SELECT 1 FROM tab_judge_schools WHERE school_id = ?)",
                id,
            )
            .await?;
            if teams || judges {
                return Err(Error::Invalid(
                    "This school has teams or affiliated judges.",
                ));
            }
            let registrations = referenced(
                db,
                "SELECT EXISTS(SELECT 1 FROM registration_registration WHERE school_id = ?)",
                id,
            )
            .await?;
            if registrations {
                return Err(Error::Invalid(
                    "Remove the school’s registration before deleting the school.",
                ));
            }
            sqlx::query("UPDATE tab_team SET hybrid_school_id = NULL WHERE hybrid_school_id = ?")
                .bind(id)
                .execute(&mut *db)
                .await?;
            sqlx::query("UPDATE tab_debater SET school_id = NULL WHERE school_id = ?")
                .bind(id)
                .execute(&mut *db)
                .await?;
            sqlx::query("DELETE FROM tab_school WHERE id = ?")
                .bind(id)
                .execute(&mut *db)
                .await?;
        }
    }
    if matches!(kind, Kind::Team | Kind::Judge) && roster_log::enabled(db).await? {
        roster_log::record(
            db,
            if kind == Kind::Team {
                "teams"
            } else {
                "judges"
            },
            &format!("Removed {} “{name}”.", kind.model()),
            false,
        )
        .await?;
    }
    Ok(())
}

async fn referenced(
    db: &mut MySqlConnection,
    query: &'static str,
    id: i32,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(query).bind(id).fetch_one(db).await
}
