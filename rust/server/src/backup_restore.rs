use std::collections::BTreeSet;

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

    let delete_always = [
        "django_session",
        "mittab_rust_session",
        "mittab_staff_invite_token",
        "tab_bulkcheckinchange",
        "mittab_registration_private_email",
    ];
    let mut clear: BTreeSet<_> = dump
        .tables
        .into_iter()
        .filter(|&table| !schema::temporary_backup_table(table))
        .chain(delete_always)
        .filter(|table| existing.contains(*table))
        .collect();
    // Legacy backups never contained an uploaded Rust logo. Leaving a later
    // logo attached to restored branding would cross tournament boundaries.
    if existing.contains("mittab_public_logo") {
        clear.insert("mittab_public_logo");
    }
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
    validate_foreign_keys(db, &existing).await?;
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

fn name(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

async fn validate_foreign_keys(
    db: &mut MySqlConnection,
    existing: &BTreeSet<String>,
) -> Result<(), RestoreError> {
    let pairs = sqlx::query_as::<_, (String, String, String, String)>(
        "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
         FROM information_schema.KEY_COLUMN_USAGE
         WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_SCHEMA = DATABASE()
         AND REFERENCED_TABLE_NAME IS NOT NULL",
    )
    .fetch_all(&mut *db)
    .await?;
    for (table, column, reference, referred) in pairs {
        if !existing.contains(&table)
            || !schema::backup_table(&table)
            || !existing.contains(&reference)
            || !schema::backup_table(&reference)
        {
            continue;
        }
        if [&table, &column, &reference, &referred]
            .into_iter()
            .any(|value| !name(value))
        {
            return Err(RestoreError::Database);
        }
        let statement = format!(
            "SELECT EXISTS(SELECT 1 FROM `{table}` child LEFT JOIN `{reference}` parent
                           ON parent.`{referred}` = child.`{column}`
                           WHERE child.`{column}` IS NOT NULL AND parent.`{referred}` IS NULL)"
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
