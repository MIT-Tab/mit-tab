use std::collections::{BTreeMap, BTreeSet};

use sqlx::{AssertSqlSafe, Connection, MySqlConnection};

use crate::{
    App, backup_dump, backups::BackupError, schema, settings::Settings, staff_pairing::lock_round,
};

#[derive(Debug)]
pub enum RestoreError {
    Backup(BackupError),
    Invalid(&'static str),
    Database,
}

impl From<sqlx::Error> for RestoreError {
    fn from(_: sqlx::Error) -> Self {
        Self::Database
    }
}

impl From<BackupError> for RestoreError {
    fn from(error: BackupError) -> Self {
        Self::Backup(error)
    }
}

pub async fn restore(app: &App, key: &str) -> Result<(), RestoreError> {
    let content = app.backups.content(key).await?;
    let dump = backup_dump::parse(&content).map_err(RestoreError::Invalid)?;
    let mut db = app.db.acquire().await?;
    // These connection settings must never be returned to the shared pool,
    // including while a previously submitted restore request is cancelled.
    db.close_on_drop();
    let mut transaction = db.begin().await?;
    lock_round(&mut transaction).await?;
    let round = Settings::load(&mut transaction, &["cur_round"])
        .await?
        .integer("cur_round", 1)
        .to_string();
    app.backups
        .create("Before Restoring Backup", 8, &round, true)
        .await?;
    apply(&mut transaction, dump).await?;
    transaction.commit().await?;
    Ok(())
}

async fn apply(db: &mut MySqlConnection, dump: backup_dump::Dump<'_>) -> Result<(), RestoreError> {
    let existing: BTreeSet<String> = sqlx::query_scalar(
        "SELECT TABLE_NAME FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    let original_scratches = if dump.tables.contains("tab_scratch") {
        None
    } else {
        Some(scratches(db).await?)
    };

    sqlx::query("SET SESSION FOREIGN_KEY_CHECKS = 0")
        .execute(&mut *db)
        .await?;
    sqlx::query(
        "SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_AUTO_VALUE_ON_ZERO,NO_ENGINE_SUBSTITUTION'",
    )
    .execute(&mut *db)
    .await?;

    // Tables introduced after an older archive start empty. The active
    // schema history and explicitly omitted scratches stay local.
    let clear: BTreeSet<&str> = existing
        .iter()
        .map(String::as_str)
        .filter(|&table| {
            schema::backup_table(table)
                && (!schema::temporary_backup_table(table)
                    || matches!(
                        table,
                        "django_session"
                            | "mittab_rust_session"
                            | "mittab_staff_invite_token"
                            | "tab_bulkcheckinchange"
                    ))
                && (table != "tab_scratch" || original_scratches.is_none())
        })
        .collect();
    for table in &clear {
        sqlx::query(AssertSqlSafe(format!("DELETE FROM `{table}`")))
            .execute(&mut *db)
            .await?;
    }
    for insert in dump.inserts {
        if !existing.contains(insert.table) {
            continue;
        }
        sqlx::query(AssertSqlSafe(insert.sql))
            .persistent(false)
            .execute(&mut *db)
            .await
            .map_err(|_| {
                RestoreError::Invalid(
                    "The backup is incompatible with the installed tournament schema.",
                )
            })?;
    }
    let current: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM tab_tabsettings WHERE `key` = 'cur_round')",
    )
    .fetch_one(&mut *db)
    .await?;
    if !current {
        return Err(RestoreError::Invalid(
            "The backup does not include the tournament's current round.",
        ));
    }
    if let Some(original) = original_scratches
        && original != scratches(db).await?
    {
        return Err(RestoreError::Invalid(
            "This scratch-free backup has a different roster. Restore a backup that includes scratches.",
        ));
    }
    validate_foreign_keys(db, &clear).await?;
    sqlx::query("SET SESSION FOREIGN_KEY_CHECKS = 1")
        .execute(db)
        .await?;
    Ok(())
}

type Scratch = (i32, String, i32, String);

async fn scratches(db: &mut MySqlConnection) -> Result<Vec<Scratch>, sqlx::Error> {
    sqlx::query_as(
        "SELECT x.team_id, COALESCE(t.name, ''), x.judge_id, COALESCE(j.name, '') FROM tab_scratch x
         LEFT JOIN tab_team t ON t.id = x.team_id LEFT JOIN tab_judge j ON j.id = x.judge_id
         ORDER BY x.team_id, x.judge_id",
    ).fetch_all(db).await
}

fn identifier(value: &str) -> String {
    format!("`{}`", value.replace('`', "``"))
}

async fn validate_foreign_keys(
    db: &mut MySqlConnection,
    changed: &BTreeSet<&str>,
) -> Result<(), RestoreError> {
    let rows = sqlx::query_as::<_, (String, String, String, String, String)>(
        "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
         FROM information_schema.KEY_COLUMN_USAGE
         WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_SCHEMA = DATABASE()
         AND REFERENCED_TABLE_NAME IS NOT NULL
         ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION",
    )
    .fetch_all(&mut *db)
    .await?;
    let mut constraints: BTreeMap<_, Vec<_>> = BTreeMap::new();
    for (table, constraint, column, reference, referred) in rows {
        if changed.contains(table.as_str()) || changed.contains(reference.as_str()) {
            constraints
                .entry((table, constraint, reference))
                .or_default()
                .push((column, referred));
        }
    }
    for ((table, _, reference), columns) in constraints {
        let join = columns
            .iter()
            .map(|(child, parent)| {
                format!(
                    "child.{} = parent.{}",
                    identifier(child),
                    identifier(parent)
                )
            })
            .collect::<Vec<_>>()
            .join(" AND ");
        let present = columns
            .iter()
            .map(|(child, _)| format!("child.{} IS NOT NULL", identifier(child)))
            .collect::<Vec<_>>()
            .join(" AND ");
        let statement = format!(
            "SELECT EXISTS(SELECT 1 FROM {} child LEFT JOIN {} parent ON {join}
                           WHERE {present} AND parent.{} IS NULL)",
            identifier(&table),
            identifier(&reference),
            identifier(&columns[0].1),
        );
        let invalid: bool = sqlx::query_scalar(AssertSqlSafe(statement))
            .fetch_one(&mut *db)
            .await?;
        if invalid {
            return Err(RestoreError::Invalid(
                "The backup contains a reference to a missing tournament record.",
            ));
        }
    }
    Ok(())
}
