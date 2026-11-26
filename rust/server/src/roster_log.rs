use sqlx::MySqlConnection;

use crate::settings::Settings;

pub async fn enabled(db: &mut MySqlConnection) -> Result<bool, sqlx::Error> {
    let settings = Settings::load(db, &["teams_public"]).await?;
    if settings.flag("teams_public", false) {
        sqlx::query(
            "INSERT INTO tab_publicrosterlogstate (id, first_published_at)
             VALUES (1, UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE id = id",
        )
        .execute(&mut *db)
        .await?;
    }
    let closed = sqlx::query_scalar::<_, bool>(
        "SELECT NOT allow_new_registrations OR (
            new_registrations_close_at IS NOT NULL AND new_registrations_close_at <= UTC_TIMESTAMP(6))
         FROM registration_registrationconfig ORDER BY id LIMIT 1",
    ).fetch_optional(&mut *db).await?.unwrap_or(true);
    if !closed {
        return Ok(false);
    }
    sqlx::query("UPDATE tab_publicrosterlogstate SET started_at = UTC_TIMESTAMP(6) WHERE id = 1 AND started_at IS NULL")
        .execute(&mut *db).await?;
    sqlx::query_scalar::<_, bool>(
        "SELECT started_at IS NOT NULL FROM tab_publicrosterlogstate WHERE id = 1",
    )
    .fetch_optional(db)
    .await
    .map(|state| state.unwrap_or(false))
}

pub async fn availability(
    db: &mut MySqlConnection,
    name: &str,
    round: i32,
    available: bool,
) -> Result<(), sqlx::Error> {
    let label = if round == 0 {
        "Outrounds".into()
    } else {
        format!("Round {round}")
    };
    let status = if available {
        "available"
    } else {
        "unavailable"
    };
    let message = format!("Changed availability for “{name}”: {label} {status}.");
    record(db, "judges", &message, false).await
}

pub async fn record(
    db: &mut MySqlConnection,
    kind: &'static str,
    message: &str,
    involves_debaters: bool,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO tab_publicrosterchange (list_kind, message, involves_debaters, created_at)
         VALUES (?, ?, ?, UTC_TIMESTAMP(6))",
    )
    .bind(kind)
    .bind(message)
    .bind(involves_debaters)
    .execute(db)
    .await?;
    Ok(())
}
