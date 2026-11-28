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

pub async fn school_renamed(
    db: &mut MySqlConnection,
    id: i32,
    old: &str,
    new: &str,
) -> Result<(), sqlx::Error> {
    let (teams, debaters, judges): (bool, bool, bool) = sqlx::query_as(
        "SELECT EXISTS(SELECT 1 FROM tab_team WHERE school_id = ? OR hybrid_school_id = ?),
                EXISTS(SELECT 1 FROM tab_debater d JOIN tab_team_debaters t ON t.debater_id = d.id WHERE d.school_id = ?),
                EXISTS(SELECT 1 FROM tab_judge_schools WHERE school_id = ?)",
    )
    .bind(id).bind(id).bind(id).bind(id).fetch_one(&mut *db).await?;
    let affiliation = format!("Changed affiliation name: {old} → {new}.");
    if teams {
        record(db, "teams", &affiliation, false).await?;
    }
    if debaters {
        let message = format!("Changed debater affiliation name: {old} → {new}.");
        record(db, "teams", &message, true).await?;
    }
    if judges {
        record(db, "judges", &affiliation, false).await?;
    }
    Ok(())
}

pub async fn debater_edited(
    db: &mut MySqlConnection,
    id: i32,
    old_name: &str,
    name: &str,
    old_school: Option<&str>,
    new_school: Option<&str>,
) -> Result<(), sqlx::Error> {
    if (old_name, old_school) == (name, new_school) {
        return Ok(());
    }
    let member: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM tab_team_debaters WHERE debater_id = ?)")
            .bind(id)
            .fetch_one(&mut *db)
            .await?;
    if !member {
        return Ok(());
    }
    if old_name != name {
        let message = format!("Changed debater name for “{name}”: {old_name} → {name}.");
        record(db, "teams", &message, true).await?;
    }
    if old_school != new_school {
        let (old, new) = affiliation_names(db, old_school, new_school).await?;
        let message = format!("Changed debater affiliation for “{name}”: {old} → {new}.");
        record(db, "teams", &message, true).await?;
    }
    Ok(())
}

pub async fn affiliation_names(
    db: &mut MySqlConnection,
    old_school: Option<&str>,
    new_school: Option<&str>,
) -> Result<(String, String), sqlx::Error> {
    let (old, new): (Option<String>, Option<String>) = sqlx::query_as(
        "SELECT (SELECT name FROM tab_school WHERE id = ?),
                (SELECT name FROM tab_school WHERE id = ?)",
    )
    .bind(old_school)
    .bind(new_school)
    .fetch_one(db)
    .await?;
    let name = |name: Option<String>| {
        name.filter(|name| !name.is_empty())
            .unwrap_or_else(|| "None".into())
    };
    Ok((name(old), name(new)))
}

pub async fn school_removed(
    db: &mut MySqlConnection,
    id: i32,
    name: &str,
) -> Result<(), sqlx::Error> {
    let teams: Vec<String> = sqlx::query_scalar(
        "SELECT name FROM tab_team WHERE hybrid_school_id = ? AND school_id <> ? ORDER BY id",
    )
    .bind(id)
    .bind(id)
    .fetch_all(&mut *db)
    .await?;
    for team in teams {
        let message = format!("Removed hybrid affiliation “{name}” from team “{team}”.");
        record(db, "teams", &message, false).await?;
    }
    let debaters: Vec<String> = sqlx::query_scalar(
        "SELECT d.name FROM tab_debater d WHERE d.school_id = ?
         AND EXISTS(SELECT 1 FROM tab_team_debaters t WHERE t.debater_id = d.id) ORDER BY d.name",
    )
    .bind(id)
    .fetch_all(&mut *db)
    .await?;
    for debater in debaters {
        let message = format!("Removed affiliation “{name}” from debater “{debater}”.");
        record(db, "teams", &message, true).await?;
    }
    Ok(())
}
