use std::collections::BTreeMap;

use axum::http::HeaderMap;
use serde_json::{Value, json};
use sqlx::{AssertSqlSafe, MySqlConnection};

use crate::{
    App, access,
    board_snapshot::{self, Identity, Pairings, Snapshot},
    legacy_session::Actor,
    round_audit,
    staff_pairing::lock_round,
    staff_roster_data::{self as roster, Kind},
};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Debate {
    Inround,
    Outround,
}

impl Debate {
    pub fn table(self) -> &'static str {
        match self {
            Self::Inround => "tab_round",
            Self::Outround => "tab_outround",
        }
    }

    pub fn panel(self) -> (&'static str, &'static str) {
        match self {
            Self::Inround => ("tab_round_judges", "round_id"),
            Self::Outround => ("tab_outround_judges", "outround_id"),
        }
    }

    pub fn model(self) -> &'static str {
        match self {
            Self::Inround => "round",
            Self::Outround => "outround",
        }
    }

    pub fn stage_column(self) -> &'static str {
        match self {
            Self::Inround => "round_number",
            Self::Outround => "num_teams",
        }
    }

    pub fn route(path: &str) -> Self {
        if path.starts_with("/outround/") {
            Self::Outround
        } else {
            Self::Inround
        }
    }
}

pub struct Detail {
    pub id: i32,
    pub stage: i32,
    pub snapshot: Snapshot,
}

impl Detail {
    pub fn team(&self, government: bool) -> Option<&Identity> {
        if government {
            self.snapshot.gov_team.as_ref()
        } else {
            self.snapshot.opp_team.as_ref()
        }
    }

    fn reference(&self, kind: Debate) -> String {
        format!(
            "{} {} between {} and {}",
            if kind == Debate::Inround {
                "Round"
            } else {
                "Outround"
            },
            self.stage,
            self.team(true).map_or("", |team| &team.name),
            self.team(false).map_or("", |team| &team.name)
        )
    }
}

pub async fn load(
    db: &mut MySqlConnection,
    kind: Debate,
    id: i32,
) -> Result<Option<Detail>, sqlx::Error> {
    let sql = format!(
        "SELECT {} FROM {} WHERE id = ?",
        kind.stage_column(),
        kind.table()
    );
    let stage = sqlx::query_scalar(AssertSqlSafe(sql))
        .bind(id)
        .fetch_optional(&mut *db)
        .await?;
    let Some(stage) = stage else {
        return Ok(None);
    };
    let scope = if kind == Debate::Inround {
        Pairings::Inround(stage)
    } else {
        Pairings::Outround
    };
    let snapshot = board_snapshot::capture(db, scope).await?.remove(&id);
    Ok(snapshot.map(|snapshot| Detail {
        id,
        stage,
        snapshot,
    }))
}

#[derive(Clone, Copy)]
pub enum Adjustment {
    Team { government: bool, team: i32 },
    SwapSides,
    Room(i32),
    Assign { judge: i32, replace: Option<i32> },
    Remove(i32),
    Chair(i32),
}

impl Adjustment {
    fn source(self) -> &'static str {
        match self {
            Self::Team { .. } => "staff team correction",
            Self::SwapSides => "staff side correction",
            Self::Room(_) => "staff room correction",
            Self::Assign { .. } | Self::Remove(_) | Self::Chair(_) => "staff judge correction",
        }
    }

    fn is_judge(self) -> bool {
        matches!(self, Self::Assign { .. } | Self::Remove(_) | Self::Chair(_))
    }
}

#[derive(Debug)]
pub enum Error {
    Missing,
    Invalid(&'static str),
    Denied,
    Database,
}

impl From<sqlx::Error> for Error {
    fn from(_: sqlx::Error) -> Self {
        Self::Database
    }
}

pub async fn adjust(
    app: &App,
    (actor, headers): (Actor, &HeaderMap),
    kind: Debate,
    id: i32,
    operation: Adjustment,
    revision: &str,
) -> Result<Value, Error> {
    let mut tx = app.db.begin().await?;
    lock_round(&mut tx).await?;
    let user = match access::settings_actor(&mut tx, app, actor, headers).await {
        Ok(Actor::User { id, .. }) => id,
        Err(access::PortalAccessError::Database) => return Err(Error::Database),
        _ => return Err(Error::Denied),
    };
    let before = load(&mut tx, kind, id).await?.ok_or(Error::Missing)?;
    if !revision.is_empty() && before.snapshot.revision() != revision {
        return Err(Error::Invalid(
            "This pairing changed. Reload it before saving.",
        ));
    }
    let audit_before = if kind == Debate::Inround && !operation.is_judge() {
        Some(round_audit::capture_one(&mut tx, before.stage, id).await?)
    } else {
        None
    };

    let mut result = match operation {
        Adjustment::Team { government, team } => {
            assign_team(&mut tx, kind, &before, government, team).await?
        }
        Adjustment::SwapSides => swap_sides(&mut tx, kind, &before).await?,
        Adjustment::Room(room) => assign_room(&mut tx, kind, id, room).await?,
        Adjustment::Assign { judge, replace } => {
            set_judge(&mut tx, kind, &before, user, judge, replace).await?
        }
        Adjustment::Remove(judge) => remove_judge(&mut tx, kind, &before, user, judge).await?,
        Adjustment::Chair(judge) => assign_chair(&mut tx, kind, &before, user, judge).await?,
    };
    if let Some(audit_before) = audit_before {
        let after = round_audit::capture_one(&mut tx, before.stage, id).await?;
        round_audit::record(&mut tx, &audit_before, &after, user, operation.source()).await?;
    } else if kind == Debate::Outround && (!operation.is_judge() || !revision.is_empty()) {
        let after = load(&mut tx, kind, id).await?.ok_or(Error::Missing)?;
        if !revision.is_empty() {
            result["revision"] = json!(after.snapshot.revision());
        }
        if !operation.is_judge() {
            board_snapshot::audit_pairings(
                &mut tx,
                &BTreeMap::from([(id, before.snapshot)]),
                &BTreeMap::from([(id, after.snapshot)]),
                user,
                operation.source(),
            )
            .await?;
        }
    }
    tx.commit().await?;
    Ok(result)
}

async fn assign_team(
    db: &mut MySqlConnection,
    kind: Debate,
    debate: &Detail,
    government: bool,
    team: i32,
) -> Result<Value, Error> {
    if debate
        .team(!government)
        .is_some_and(|opponent| opponent.id == team)
    {
        return Err(Error::Invalid("Choose a different team for each side."));
    }
    let name = roster::name(db, Kind::Team, team)
        .await?
        .ok_or(Error::Missing)?;
    let column = if government {
        "gov_team_id"
    } else {
        "opp_team_id"
    };
    let sql = format!("UPDATE {} SET {column} = ? WHERE id = ?", kind.table());
    sqlx::query(AssertSqlSafe(sql))
        .bind(team)
        .bind(debate.id)
        .execute(&mut *db)
        .await?;
    if kind == Debate::Inround {
        let opponent = debate.team(!government).ok_or(Error::Missing)?.id;
        sqlx::query("DELETE FROM tab_noshow WHERE round_number = ? AND no_show_team_id IN (?, ?)")
            .bind(debate.stage)
            .bind(team)
            .bind(opponent)
            .execute(&mut *db)
            .await?;
    }
    Ok(json!({"success": true, "team": {"id": team, "name": name}}))
}

async fn swap_sides(
    db: &mut MySqlConnection,
    kind: Debate,
    debate: &Detail,
) -> Result<Value, Error> {
    let gov = debate.team(true).ok_or(Error::Missing)?;
    let opp = debate.team(false).ok_or(Error::Missing)?;
    match kind {
        Debate::Inround => {
            sqlx::query(
                "UPDATE tab_round SET gov_team_id = ?, opp_team_id = ?, pullup = CASE pullup WHEN 1 THEN 2 WHEN 2 THEN 1 ELSE pullup END WHERE id = ?",
            ).bind(opp.id).bind(gov.id).bind(debate.id).execute(&mut *db).await?;
        }
        Debate::Outround => {
            sqlx::query("UPDATE tab_outround SET gov_team_id = ?, opp_team_id = ?, choice = CASE choice WHEN 1 THEN 2 WHEN 2 THEN 1 ELSE choice END WHERE id = ?")
                .bind(opp.id).bind(gov.id).bind(debate.id).execute(&mut *db).await?;
        }
    }
    Ok(json!({"success": true, "round_id": debate.id, "gov_team": opp, "opp_team": gov}))
}

async fn assign_room(
    db: &mut MySqlConnection,
    kind: Debate,
    id: i32,
    room: i32,
) -> Result<Value, Error> {
    let name = roster::name(db, Kind::Room, room)
        .await?
        .ok_or(Error::Missing)?;
    let sql = format!("UPDATE {} SET room_id = ? WHERE id = ?", kind.table());
    sqlx::query(AssertSqlSafe(sql))
        .bind(room)
        .bind(id)
        .execute(db)
        .await?;
    Ok(json!({"success": true, "room_id": room, "round_id": id, "room_name": name}))
}

async fn chair(
    db: &mut MySqlConnection,
    kind: Debate,
    id: i32,
    chair: Option<i32>,
) -> Result<(), sqlx::Error> {
    let sql = format!("UPDATE {} SET chair_id = ? WHERE id = ?", kind.table());
    sqlx::query(AssertSqlSafe(sql))
        .bind(chair)
        .bind(id)
        .execute(db)
        .await?;
    Ok(())
}

async fn highest(
    db: &mut MySqlConnection,
    kind: Debate,
    id: i32,
) -> Result<Option<i32>, sqlx::Error> {
    let (table, column) = kind.panel();
    let sql = format!(
        "SELECT j.id FROM {table} p JOIN tab_judge j ON p.judge_id = j.id WHERE p.{column} = ?
        ORDER BY j.`rank` DESC, j.name, j.id LIMIT 1"
    );
    sqlx::query_scalar(AssertSqlSafe(sql))
        .bind(id)
        .fetch_optional(db)
        .await
}

fn member(debate: &Detail, id: i32) -> Option<&Identity> {
    debate.snapshot.judges.iter().find(|judge| judge.id == id)
}

async fn clear_judge(
    db: &mut MySqlConnection,
    kind: Debate,
    id: i32,
    judge: i32,
) -> Result<(), sqlx::Error> {
    let (table, column) = kind.panel();
    let sql = format!("DELETE FROM {table} WHERE {column} = ? AND judge_id = ?");
    sqlx::query(AssertSqlSafe(sql))
        .bind(id)
        .bind(judge)
        .execute(&mut *db)
        .await?;
    if kind == Debate::Inround {
        sqlx::query("DELETE FROM tab_manualjudgeassignment WHERE round_id = ? AND judge_id = ?")
            .bind(id)
            .bind(judge)
            .execute(db)
            .await?;
    }
    Ok(())
}

async fn set_judge(
    db: &mut MySqlConnection,
    kind: Debate,
    debate: &Detail,
    user: i32,
    judge: i32,
    replace: Option<i32>,
) -> Result<Value, Error> {
    let (name, rank) = sqlx::query_as::<_, (String, String)>(
        "SELECT name, CAST(`rank` AS CHAR) FROM tab_judge WHERE id = ?",
    )
    .bind(judge)
    .fetch_optional(&mut *db)
    .await?
    .ok_or(Error::Missing)?;
    let removed = if let Some(replace) = replace {
        if replace == judge {
            return Err(Error::Invalid("Choose a different replacement judge."));
        }
        Some(member(debate, replace).ok_or(Error::Invalid(
            "The previous judge is no longer on this panel.",
        ))?)
    } else {
        None
    };
    if kind == Debate::Outround && member(debate, judge).is_some() {
        return Err(Error::Invalid("This judge is already on this panel."));
    }
    let (table, column) = kind.panel();
    let sql = format!(
        "INSERT INTO {table} ({column}, judge_id) VALUES (?, ?) ON DUPLICATE KEY UPDATE judge_id = judge_id"
    );
    sqlx::query(AssertSqlSafe(sql))
        .bind(debate.id)
        .bind(judge)
        .execute(&mut *db)
        .await?;
    if kind == Debate::Inround {
        sqlx::query(
            "INSERT INTO tab_manualjudgeassignment (round_id, judge_id, created_by_id, created_at)
             VALUES (?, ?, ?, UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE created_by_id = ?",
        )
        .bind(debate.id)
        .bind(judge)
        .bind(user)
        .bind(user)
        .execute(&mut *db)
        .await?;
    }
    let mut selected = debate.snapshot.chair;
    let (changes, note) = if let Some(removed) = removed {
        clear_judge(db, kind, debate.id, removed.id).await?;
        if selected == Some(removed.id) {
            selected = highest(db, kind, debate.id).await?;
        }
        (
            json!({"assigned_judge": name, "removed_judge": removed.name}),
            format!("Swapped {} for {name}", removed.name),
        )
    } else {
        (json!({"assigned_judge": name}), format!("Assigned {name}"))
    };
    let selected = selected.or(Some(judge));
    chair(db, kind, debate.id, selected).await?;
    manual_audit(db, kind, debate, user, "manual_judge_assign", changes, note).await?;
    Ok(
        json!({"success": true, "chair_id": selected, "round_id": debate.id,
        "judge_id": judge, "judge_name": name, "judge_rank": rank.parse::<f64>().unwrap_or_default()}),
    )
}

async fn remove_judge(
    db: &mut MySqlConnection,
    kind: Debate,
    debate: &Detail,
    user: i32,
    judge: i32,
) -> Result<Value, Error> {
    let removed = member(debate, judge).ok_or(Error::Invalid("Judge not found on this panel."))?;
    clear_judge(db, kind, debate.id, judge).await?;
    if debate.snapshot.chair == Some(judge) {
        let replacement = highest(db, kind, debate.id).await?;
        chair(db, kind, debate.id, replacement).await?;
    }
    manual_audit(
        db,
        kind,
        debate,
        user,
        "manual_judge_remove",
        json!({"removed_judge": removed.name}),
        format!("Removed {}", removed.name),
    )
    .await?;
    Ok(json!({"success": true}))
}

async fn assign_chair(
    db: &mut MySqlConnection,
    kind: Debate,
    debate: &Detail,
    user: i32,
    judge: i32,
) -> Result<Value, Error> {
    let new =
        member(debate, judge).ok_or(Error::Invalid("Choose a judge assigned to this panel."))?;
    let previous = debate
        .snapshot
        .chair
        .and_then(|judge| member(debate, judge))
        .map(|judge| &judge.name);
    chair(db, kind, debate.id, Some(judge)).await?;
    if debate.snapshot.chair != Some(judge) {
        manual_audit(
            db,
            kind,
            debate,
            user,
            "manual_chair_change",
            json!({"old_chair": previous, "new_chair": new.name}),
            format!("Assigned {} as chair", new.name),
        )
        .await?;
    }
    Ok(json!({"success": true}))
}

async fn manual_audit(
    db: &mut MySqlConnection,
    kind: Debate,
    debate: &Detail,
    user: i32,
    event: &'static str,
    changes: Value,
    note: String,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO tab_auditevent (object_id, event_type, created_at, object_repr, changes, note, content_type_id, user_id)
         VALUES (?, ?, UTC_TIMESTAMP(6), ?, ?, ?, (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = ?), ?)",
    ).bind(debate.id).bind(event).bind(debate.reference(kind)).bind(changes.to_string()).bind(note)
        .bind(kind.model()).bind(user).execute(db).await?;
    Ok(())
}
