use std::collections::{BTreeMap, BTreeSet, HashSet};

use chrono::NaiveDateTime;
use ring::rand::{SecureRandom, SystemRandom};
use serde::Serialize;
use sqlx::{MySqlConnection, QueryBuilder};

use crate::roster_log;

#[derive(Clone, Copy, Eq, PartialEq)]
pub enum Entity {
    Team,
    Judge,
    Expected,
    Room,
}

impl Entity {
    pub const ALL: [Self; 4] = [Self::Team, Self::Judge, Self::Expected, Self::Room];

    pub fn parse(value: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|kind| kind.key() == value)
    }

    pub fn key(self) -> &'static str {
        match self {
            Self::Team => "team",
            Self::Judge => "judge",
            Self::Expected => "judge_expected",
            Self::Room => "room",
        }
    }

    pub fn title(self) -> &'static str {
        match self {
            Self::Team => "Teams",
            Self::Judge => "Judges",
            Self::Expected => "Expected Judges",
            Self::Room => "Rooms",
        }
    }

    pub fn fragment(self) -> &'static str {
        if self == Self::Expected {
            "judge-expected"
        } else {
            self.key()
        }
    }

    pub fn table(self) -> &'static str {
        match self {
            Self::Team => "tab_team",
            Self::Judge | Self::Expected => "tab_judge",
            Self::Room => "tab_room",
        }
    }

    fn marks(self) -> Option<(&'static str, &'static str)> {
        match self {
            Self::Team => None,
            Self::Judge => Some(("tab_checkin", "judge_id")),
            Self::Expected => Some(("tab_judgeexpectedcheckin", "judge_id")),
            Self::Room => Some(("tab_roomcheckin", "room_id")),
        }
    }
}

type Cell = (i32, Option<i32>);
type Cells = BTreeSet<Cell>;

pub struct Change {
    pub id: i32,
    pub token: String,
    pub checked: bool,
    pub cells: Cells,
    pub undone: bool,
    pub created: NaiveDateTime,
}

pub enum ChangeError {
    Conflict,
    Storage,
}

impl From<sqlx::Error> for ChangeError {
    fn from(_: sqlx::Error) -> Self {
        Self::Storage
    }
}

pub fn key(raw: &str) -> Option<String> {
    let normalized = raw.replace('-', "").to_ascii_lowercase();
    (normalized.len() == 32 && normalized.bytes().all(|byte| byte.is_ascii_hexdigit()))
        .then_some(normalized)
}

pub fn printable(token: &str) -> String {
    let Some(value) = key(token) else {
        return String::new();
    };
    format!(
        "{}-{}-{}-{}-{}",
        &value[..8],
        &value[8..12],
        &value[12..16],
        &value[16..20],
        &value[20..]
    )
}

fn new_key() -> Result<String, ChangeError> {
    let mut bytes = [0_u8; 16];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| ChangeError::Storage)?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(hex::encode(bytes))
}

type ChangeRow = (i32, String, bool, String, bool, NaiveDateTime);

fn convert((id, token, checked, cells, undone, created): ChangeRow) -> Result<Change, sqlx::Error> {
    let cells: Cells = serde_json::from_str(&cells)
        .map_err(|_| sqlx::Error::Protocol("Invalid check-in history cells".into()))?;
    Ok(Change {
        id,
        token,
        checked,
        cells,
        undone,
        created,
    })
}

pub async fn latest(
    db: &mut MySqlConnection,
    entity: Entity,
) -> Result<Option<Change>, sqlx::Error> {
    sqlx::query_as::<_, ChangeRow>(
        "SELECT id, token, checked, CAST(cells AS CHAR), undone, created_at
           FROM tab_bulkcheckinchange WHERE entity_type = ? AND undone = FALSE ORDER BY id DESC LIMIT 1",
    ).bind(entity.key()).fetch_optional(db).await?.map(convert).transpose()
}

pub async fn history(
    db: &mut MySqlConnection,
    entity: Entity,
    requested: i64,
) -> Result<(Vec<Change>, u32, u32), sqlx::Error> {
    let count = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM tab_bulkcheckinchange WHERE entity_type = ?",
    )
    .bind(entity.key())
    .fetch_one(&mut *db)
    .await?;
    let pages = (count as u64).div_ceil(25).max(1).min(u64::from(u32::MAX)) as u32;
    let page = match u32::try_from(requested) {
        Ok(page) if (1..=pages).contains(&page) => page,
        _ => pages,
    };
    let rows = sqlx::query_as::<_, ChangeRow>(
        "SELECT id, token, checked, CAST(cells AS CHAR), undone, created_at
           FROM tab_bulkcheckinchange WHERE entity_type = ? ORDER BY id DESC LIMIT 25 OFFSET ?",
    )
    .bind(entity.key())
    .bind(u64::from(page - 1) * 25)
    .fetch_all(db)
    .await?;
    Ok((
        rows.into_iter().map(convert).collect::<Result<_, _>>()?,
        page,
        pages,
    ))
}

pub async fn checked(db: &mut MySqlConnection, entity: Entity) -> Result<Cells, sqlx::Error> {
    checked_scope(db, entity, None).await
}

async fn checked_scope(
    db: &mut MySqlConnection,
    entity: Entity,
    ids: Option<&HashSet<i32>>,
) -> Result<Cells, sqlx::Error> {
    if ids.is_some_and(HashSet::is_empty) {
        return Ok(Cells::new());
    }
    let (column, mut query) = if let Some((table, column)) = entity.marks() {
        let mut query = QueryBuilder::new("SELECT ");
        query
            .push(column)
            .push(", round_number FROM ")
            .push(table)
            .push(" WHERE TRUE");
        (column, query)
    } else {
        (
            "id",
            QueryBuilder::new("SELECT id, NULL FROM tab_team WHERE checked_in = TRUE"),
        )
    };
    if let Some(ids) = ids {
        query.push(" AND ").push(column).push(" IN (");
        let mut separated = query.separated(", ");
        for id in ids {
            separated.push_bind(id);
        }
        query.push(") FOR UPDATE");
    }
    Ok(query
        .build_query_as::<(i32, Option<i32>)>()
        .fetch_all(db)
        .await?
        .into_iter()
        .collect())
}

async fn lock_entities(
    db: &mut MySqlConnection,
    entity: Entity,
    ids: &HashSet<i32>,
) -> Result<BTreeMap<i32, String>, sqlx::Error> {
    if ids.is_empty() {
        return Ok(BTreeMap::new());
    }
    let mut query = QueryBuilder::new("SELECT id, name FROM ");
    query.push(entity.table()).push(" WHERE id IN (");
    let mut separated = query.separated(", ");
    for &id in ids {
        separated.push_bind(id);
    }
    query.push(") ORDER BY id FOR UPDATE");
    Ok(query
        .build_query_as::<(i32, String)>()
        .fetch_all(db)
        .await?
        .into_iter()
        .collect())
}

async fn write(
    db: &mut MySqlConnection,
    entity: Entity,
    cells: &Cells,
    checked: bool,
    names: &BTreeMap<i32, String>,
) -> Result<(), sqlx::Error> {
    if cells.is_empty() {
        return Ok(());
    }
    let log = entity == Entity::Expected && roster_log::enabled(db).await?;
    if let Some((table, column)) = entity.marks() {
        for &(id, round) in cells {
            let Some(round) = round else {
                return Err(sqlx::Error::Protocol("Invalid check-in round".into()));
            };
            let mut query = if checked {
                let mut query = QueryBuilder::new("INSERT INTO ");
                query
                    .push(table)
                    .push(" (")
                    .push(column)
                    .push(", round_number) VALUES (")
                    .push_bind(id)
                    .push(", ")
                    .push_bind(round)
                    .push(")");
                query
            } else {
                let mut query = QueryBuilder::new("DELETE FROM ");
                query
                    .push(table)
                    .push(" WHERE ")
                    .push(column)
                    .push(" = ")
                    .push_bind(id)
                    .push(" AND round_number = ")
                    .push_bind(round);
                query
            };
            query.build().execute(&mut *db).await?;
            if log && let Some(name) = names.get(&id) {
                roster_log::availability(db, name, round, checked).await?;
            }
        }
    } else {
        let mut query = QueryBuilder::new("UPDATE tab_team SET checked_in = ");
        query.push_bind(checked).push(" WHERE id IN (");
        let mut values = query.separated(", ");
        for &(id, _) in cells {
            values.push_bind(id);
        }
        query.push(")").build().execute(db).await?;
    }
    Ok(())
}

pub async fn apply(
    db: &mut MySqlConnection,
    entity: Entity,
    ids: &HashSet<i32>,
    rounds: &BTreeSet<Option<i32>>,
    wanted: bool,
) -> Result<Option<Change>, ChangeError> {
    let names = lock_entities(db, entity, ids).await?;
    let mut changed = Cells::new();
    let state = checked_scope(db, entity, Some(ids)).await?;
    for id in names.keys() {
        for round in rounds {
            let cell = (*id, *round);
            if state.contains(&cell) != wanted {
                changed.insert(cell);
            }
        }
    }
    if !changed.is_empty() {
        write(db, entity, &changed, wanted, &names).await?;
        let token = new_key()?;
        let cells = serde_json::to_string(&changed).expect("serializable check-in cells");
        sqlx::query(
            "INSERT INTO tab_bulkcheckinchange (token, entity_type, checked, cells, undone, created_at)
             VALUES (?, ?, ?, ?, FALSE, UTC_TIMESTAMP(6))",
        ).bind(token).bind(entity.key()).bind(wanted).bind(cells).execute(&mut *db).await?;
    }
    Ok(latest(db, entity).await?)
}

#[derive(Serialize)]
pub struct Restore {
    pub head: String,
    pub before: NaiveDateTime,
    pub edits: usize,
    pub check_in: usize,
    pub check_out: usize,
}

pub async fn restore(
    db: &mut MySqlConnection,
    entity: Entity,
    target: &str,
    confirmed_head: Option<&str>,
) -> Result<Restore, ChangeError> {
    let target = key(target).ok_or(ChangeError::Conflict)?;
    let changes: Vec<_> = sqlx::query_as::<_, ChangeRow>(
        "SELECT id, token, checked, CAST(cells AS CHAR), undone, created_at FROM tab_bulkcheckinchange
         WHERE entity_type = ? AND undone = FALSE AND id >= (
             SELECT id FROM tab_bulkcheckinchange WHERE entity_type = ? AND token = ? AND undone = FALSE)
         ORDER BY id DESC",
    ).bind(entity.key()).bind(entity.key()).bind(&target).fetch_all(&mut *db).await?
        .into_iter().map(convert).collect::<Result<_, _>>()?;
    let head = changes.first().ok_or(ChangeError::Conflict)?;
    let confirmed = confirmed_head
        .map(|raw| key(raw).ok_or(ChangeError::Conflict))
        .transpose()?;
    if confirmed
        .as_ref()
        .is_some_and(|confirmed| *confirmed != head.token)
    {
        return Err(ChangeError::Conflict);
    }
    let earliest = changes.last().ok_or(ChangeError::Conflict)?;
    let affected: Cells = changes
        .iter()
        .flat_map(|change| change.cells.iter().copied())
        .collect();
    let ids: HashSet<_> = affected.iter().map(|&(id, _)| id).collect();
    let names = lock_entities(db, entity, &ids).await?;
    if names.len() != ids.len() {
        return Err(ChangeError::Conflict);
    }
    let current: Cells = checked_scope(db, entity, Some(&ids))
        .await?
        .intersection(&affected)
        .copied()
        .collect();
    let mut restored = current.clone();
    for change in &changes {
        for cell in &change.cells {
            if restored.contains(cell) != change.checked {
                return Err(ChangeError::Conflict);
            }
            if change.checked {
                restored.remove(cell);
            } else {
                restored.insert(*cell);
            }
        }
    }
    let check_in: Cells = restored.difference(&current).copied().collect();
    let check_out: Cells = current.difference(&restored).copied().collect();
    if confirmed.is_some() {
        write(db, entity, &check_in, true, &names).await?;
        write(db, entity, &check_out, false, &names).await?;
        sqlx::query("UPDATE tab_bulkcheckinchange SET undone = TRUE WHERE entity_type = ? AND id BETWEEN ? AND ?")
            .bind(entity.key()).bind(earliest.id).bind(head.id).execute(db).await?;
    }
    Ok(Restore {
        head: printable(&head.token),
        before: earliest.created,
        edits: changes.len(),
        check_in: check_in.len(),
        check_out: check_out.len(),
    })
}
