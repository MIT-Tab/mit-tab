use std::collections::{BTreeMap, BTreeSet};

use sqlx::{MySql, MySqlConnection, MySqlPool, QueryBuilder};

use crate::passwords;

const BASELINE: &str = include_str!("../schema/0001_initial.sql");
const LEGACY_BASELINE: [(&str, &str); 6] = [
    ("admin", "0003_logentry_add_action_flag_choices"),
    ("auth", "0012_alter_user_first_name_max_length"),
    ("contenttypes", "0002_remove_content_type_name"),
    ("registration", "0008_registration_schedule_eastern_time"),
    ("sessions", "0001_initial"),
    ("tab", "0059_bulkcheckinchange"),
];

pub async fn prepare(pool: &MySqlPool, initialize: bool) -> Result<(), &'static str> {
    let mut db = pool
        .acquire()
        .await
        .map_err(|_| "Could not open the database")?;
    let lock: String =
        sqlx::query_scalar("SELECT SHA2(CONCAT('mit-tab schema:', DATABASE()), 256)")
            .fetch_one(&mut *db)
            .await
            .map_err(|_| "Could not identify the database")?;
    let acquired = sqlx::query_scalar::<_, Option<i64>>("SELECT GET_LOCK(?, 15)")
        .bind(&lock)
        .fetch_one(&mut *db)
        .await
        .map_err(|_| "Could not lock the database")?;
    if acquired != Some(1) {
        return Err("Could not lock the database");
    }
    let result = prepare_locked(&mut db, initialize).await;
    // Close the dedicated connection if releasing the advisory lock fails.
    let released = sqlx::query_scalar::<_, Option<i64>>("SELECT RELEASE_LOCK(?)")
        .bind(&lock)
        .fetch_one(&mut *db)
        .await;
    if !matches!(released, Ok(Some(1))) {
        let _ = db.close().await;
        return result.and(Err("Could not release the database lock"));
    }
    result
}

async fn prepare_locked(db: &mut MySqlConnection, initialize: bool) -> Result<(), &'static str> {
    let tables: BTreeSet<String> = sqlx::query_scalar(
        "SELECT TABLE_NAME FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'",
    )
    .fetch_all(&mut *db)
    .await
    .map_err(|_| "Could not inspect the database schema")?
    .into_iter()
    .collect();
    let status = if tables.contains("mittab_rust_schema") {
        sqlx::query_scalar::<_, bool>("SELECT applied FROM mittab_rust_schema WHERE version = 1")
            .fetch_optional(&mut *db)
            .await
            .map_err(|_| "Could not read the Rust schema version")?
    } else {
        None
    };
    const VERSION_TABLE: &str = "
        CREATE TABLE IF NOT EXISTS mittab_rust_schema (
            version int NOT NULL PRIMARY KEY, applied boolean NOT NULL
        ) ENGINE=InnoDB
    ";

    match status {
        Some(true) => {}
        Some(false) => install_baseline(db).await?,
        None => {
            let empty = tables.iter().all(|name| name == "mittab_rust_schema");
            if !empty {
                check_legacy_baseline(db, tables.contains("django_migrations")).await?;
                validate_columns(db).await?;
            }
            sqlx::query(VERSION_TABLE)
                .execute(&mut *db)
                .await
                .map_err(|_| "Could not create the Rust schema version table")?;
            sqlx::query("INSERT INTO mittab_rust_schema (version, applied) VALUES (1, ?)")
                .bind(!empty)
                .execute(&mut *db)
                .await
                .map_err(|_| "Could not record the Rust schema version")?;
            if empty {
                install_baseline(db).await?;
            }
        }
    }

    install_sessions(db).await?;
    install_registration_email_privacy(db).await?;
    install_public_logo(db).await?;
    install_staff_invite_token(db).await?;
    sqlx::query(
        "INSERT INTO django_content_type (app_label, model) VALUES ('tab', 'round'), ('tab', 'outround'), ('tab', 'judge'), ('tab', 'breakingteam')
         ON DUPLICATE KEY UPDATE model = django_content_type.model",
    )
    .execute(&mut *db)
    .await
    .map_err(|_| "Could not prepare pairing audit types")?;
    if initialize {
        initialize_tournament(db).await?;
    }
    Ok(())
}

async fn install_sessions(db: &mut MySqlConnection) -> Result<(), &'static str> {
    let applied =
        sqlx::query_scalar::<_, bool>("SELECT applied FROM mittab_rust_schema WHERE version = 2")
            .fetch_optional(&mut *db)
            .await
            .map_err(|_| "Could not read the session schema version")?;
    if applied == Some(true) {
        return Ok(());
    }
    sqlx::query(
        "
        CREATE TABLE IF NOT EXISTS mittab_rust_session (
            token_hash binary(32) NOT NULL PRIMARY KEY,
            user_id int NOT NULL,
            password_hash binary(32) NOT NULL,
            expires_at datetime(6) NOT NULL,
            INDEX mittab_session_expiry (expires_at),
            CONSTRAINT mittab_session_user FOREIGN KEY (user_id) REFERENCES auth_user (id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB
    ",
    )
    .execute(&mut *db)
    .await
    .map_err(|_| "Could not create the Rust session table")?;
    sqlx::query(
        "
        INSERT INTO mittab_rust_schema (version, applied) VALUES (2, TRUE)
        ON DUPLICATE KEY UPDATE applied = TRUE
    ",
    )
    .execute(db)
    .await
    .map_err(|_| "Could not record the Rust session schema")?;
    Ok(())
}

async fn install_registration_email_privacy(db: &mut MySqlConnection) -> Result<(), &'static str> {
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS mittab_registration_private_email (
            debater_id int NOT NULL PRIMARY KEY,
            CONSTRAINT mittab_registration_email_debater FOREIGN KEY (debater_id)
                REFERENCES tab_debater (id) ON DELETE CASCADE
        ) ENGINE=InnoDB",
    )
    .execute(db)
    .await
    .map_err(|_| "Could not prepare registration email privacy")?;
    Ok(())
}

async fn install_public_logo(db: &mut MySqlConnection) -> Result<(), &'static str> {
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS mittab_public_logo (
            id int NOT NULL PRIMARY KEY, bytes longblob NOT NULL, updated_at datetime(6) NOT NULL
        ) ENGINE=InnoDB",
    )
    .execute(db)
    .await
    .map_err(|_| "Could not prepare tournament logos")?;
    Ok(())
}

async fn install_staff_invite_token(db: &mut MySqlConnection) -> Result<(), &'static str> {
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS mittab_staff_invite_token (
            user_id int NOT NULL PRIMARY KEY, token_hash binary(32) NOT NULL, expires_at datetime(6) NOT NULL,
            CONSTRAINT mittab_invite_user FOREIGN KEY (user_id) REFERENCES auth_user (id) ON DELETE CASCADE
        ) ENGINE=InnoDB",
    )
    .execute(db)
    .await
    .map_err(|_| "Could not prepare staff invitations")?;
    Ok(())
}

async fn install_baseline(db: &mut MySqlConnection) -> Result<(), &'static str> {
    let enabled: i64 = sqlx::query_scalar("SELECT CAST(@@FOREIGN_KEY_CHECKS AS SIGNED)")
        .fetch_one(&mut *db)
        .await
        .map_err(|_| "Could not read foreign key enforcement")?;
    sqlx::query("SET FOREIGN_KEY_CHECKS = 0")
        .execute(&mut *db)
        .await
        .map_err(|_| "Could not prepare the schema install")?;
    let installation = sqlx::raw_sql(BASELINE).execute(&mut *db).await;
    let restore = if enabled == 0 {
        "SET FOREIGN_KEY_CHECKS = 0"
    } else {
        "SET FOREIGN_KEY_CHECKS = 1"
    };
    sqlx::query(restore)
        .execute(&mut *db)
        .await
        .map_err(|_| "Could not restore foreign key enforcement")?;
    installation.map_err(|_| "Could not install the MIT-Tab MySQL schema")?;
    validate_columns(db).await?;
    sqlx::query("UPDATE mittab_rust_schema SET applied = TRUE WHERE version = 1")
        .execute(db)
        .await
        .map_err(|_| "Could not mark the schema as installed")?;
    Ok(())
}

async fn check_legacy_baseline(
    db: &mut MySqlConnection,
    has_migration_table: bool,
) -> Result<(), &'static str> {
    if !has_migration_table {
        return Err("The existing database is missing the MIT-Tab migration history");
    }
    let applied: BTreeSet<_> =
        sqlx::query_as::<_, (String, String)>("SELECT app, name FROM django_migrations")
            .fetch_all(db)
            .await
            .map_err(|_| "Could not read the legacy schema version")?
            .into_iter()
            .collect();
    if LEGACY_BASELINE
        .iter()
        .all(|(app, migration)| applied.contains(&(app.to_string(), migration.to_string())))
    {
        Ok(())
    } else {
        Err("The existing database predates the supported MIT-Tab schema baseline")
    }
}

async fn validate_columns(db: &mut MySqlConnection) -> Result<(), &'static str> {
    let mut columns: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (table, column) in sqlx::query_as::<_, (String, String)>(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()",
    ).fetch_all(db).await.map_err(|_| "Could not verify the MIT-Tab schema")? {
        columns.entry(table).or_default().insert(column);
    }

    // The same SQL that creates the schema is the source for required columns.
    // Additional columns remain available to older versions and integrations.
    let mut table = None;
    for line in BASELINE.lines() {
        if let Some(create) = line.strip_prefix("CREATE TABLE IF NOT EXISTS `") {
            table = create.split_once('`').map(|(name, _)| name);
        } else if let Some((column, _)) = line
            .strip_prefix("  `")
            .and_then(|field| field.split_once('`'))
            && !table
                .and_then(|name| columns.get(name))
                .is_some_and(|fields| fields.contains(column))
        {
            return Err("The database is missing required MIT-Tab schema columns");
        }
    }
    Ok(())
}

async fn initialize_tournament(db: &mut MySqlConnection) -> Result<(), &'static str> {
    use sqlx::Connection;

    let existing: i64 = sqlx::query_scalar(
        "
        SELECT EXISTS(SELECT 1 FROM auth_user)
            OR EXISTS(SELECT 1 FROM tab_tabsettings)
            OR EXISTS(SELECT 1 FROM tab_team)
            OR EXISTS(SELECT 1 FROM tab_school)
            OR EXISTS(SELECT 1 FROM tab_debater)
            OR EXISTS(SELECT 1 FROM tab_judge)
    ",
    )
    .fetch_one(&mut *db)
    .await
    .map_err(|_| "Could not check tournament initialization")?;
    if existing != 0 {
        return Ok(());
    }

    let tab = std::env::var("TAB_PASSWORD")
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or("TAB_PASSWORD is required to initialize an empty tournament")?;
    let entry = password_or_random("ENTRY_PASSWORD")?;
    let board = password_or_random("BOARD_PASSWORD")?;
    let credentials = [
        ("tab", passwords::encode(tab.as_bytes())?, true, true),
        ("entry", passwords::encode(entry.as_bytes())?, false, false),
        ("board", passwords::encode(board.as_bytes())?, false, false),
    ];
    let mut tx = db
        .begin()
        .await
        .map_err(|_| "Could not start tournament initialization")?;
    for (username, password, staff, superuser) in credentials {
        sqlx::query(
            "
            INSERT INTO auth_user (username, password, first_name, last_name, email, is_active,
                                   is_staff, is_superuser, date_joined)
            VALUES (?, ?, '', '', '', TRUE, ?, ?, UTC_TIMESTAMP(6))
        ",
        )
        .bind(username)
        .bind(password)
        .bind(staff)
        .bind(superuser)
        .execute(&mut *tx)
        .await
        .map_err(|_| "Could not create tournament users")?;
    }
    sqlx::query(
        "INSERT INTO auth_group (name) VALUES ('APDA Board') ON DUPLICATE KEY UPDATE id = id",
    )
    .execute(&mut *tx)
    .await
    .map_err(|_| "Could not create the APDA Board group")?;
    sqlx::query(
        "
        INSERT INTO auth_user_groups (user_id, group_id)
        SELECT u.id, g.id FROM auth_user u
        JOIN auth_group g ON g.name = 'APDA Board' WHERE u.username = 'board'
    ",
    )
    .execute(&mut *tx)
    .await
    .map_err(|_| "Could not configure the APDA Board account")?;

    let defaults = [
        ("tot_rounds", Some(5), None),
        ("cur_round", Some(1), None),
        ("lenient_late", Some(0), None),
        ("tournament_name", None, Some("New Tournament")),
        ("theme_color", None, Some("#00438A")),
        ("start_tournament_wizard_completed", Some(0), None),
        ("start_tournament_staff_invites_sent", Some(0), None),
    ];
    QueryBuilder::<MySql>::new("INSERT INTO tab_tabsettings (`key`, value, value_string) ")
        .push_values(defaults, |mut row, (key, value, text)| {
            row.push_bind(key).push_bind(value).push_bind(text);
        })
        .build()
        .execute(&mut *tx)
        .await
        .map_err(|_| "Could not create tournament settings")?;
    sqlx::query(
        "
        INSERT INTO registration_registrationconfig (
            id, allow_new_registrations, allow_registration_edits, crash_housing_enabled,
            team_name_changes_allowed, disc_scratches_open, disc_scratch_quantity, updated_at
        ) VALUES (1, FALSE, TRUE, FALSE, FALSE, FALSE, 0, UTC_TIMESTAMP(6))
    ",
    )
    .execute(&mut *tx)
    .await
    .map_err(|_| "Could not initialize registration settings")?;
    sqlx::query("
        INSERT INTO registration_infotext (title, text, is_active, display_order, created_at, updated_at)
        VALUES ('GA', 'TBD', TRUE, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
    ").execute(&mut *tx).await.map_err(|_| "Could not initialize tournament information")?;
    tx.commit()
        .await
        .map_err(|_| "Could not finish tournament initialization")
}

fn password_or_random(name: &str) -> Result<String, &'static str> {
    match std::env::var(name) {
        Ok(password) if !password.is_empty() => Ok(password),
        _ => passwords::random_secret(),
    }
}
