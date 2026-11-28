use ring::digest;
use sqlx::{MySqlConnection, MySqlPool};

use crate::{
    legacy_session::{self, Actor},
    passwords,
};

pub const MAX_AGE: u32 = 14 * 24 * 60 * 60;
const PREFIX: &str = "r_";

pub fn is_native_key(token: &str) -> bool {
    token.strip_prefix(PREFIX).is_some_and(|key| {
        key.len() == 43
            && key
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    })
}

pub async fn create(db: &MySqlPool, user_id: i32, password: &str) -> Result<String, sqlx::Error> {
    let mut tx = db.begin().await?;
    sqlx::query(
        "DELETE FROM mittab_rust_session WHERE expires_at <= UTC_TIMESTAMP(6)
         ORDER BY expires_at LIMIT 100",
    )
    .execute(&mut *tx)
    .await?;
    let token = issue(&mut tx, user_id, password).await?;
    sqlx::query("UPDATE auth_user SET last_login = UTC_TIMESTAMP(6) WHERE id = ?")
        .bind(user_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(token)
}

pub(super) async fn issue(
    db: &mut MySqlConnection,
    user_id: i32,
    password: &str,
) -> Result<String, sqlx::Error> {
    let token = format!(
        "{PREFIX}{}",
        passwords::random_secret().map_err(|message| sqlx::Error::Protocol(message.into()))?
    );
    sqlx::query(
        "INSERT INTO mittab_rust_session (token_hash, user_id, password_hash, expires_at)
         VALUES (?, ?, ?, DATE_ADD(UTC_TIMESTAMP(6), INTERVAL ? SECOND))",
    )
    .bind(hash(&token).as_ref())
    .bind(user_id)
    .bind(hash(password).as_ref())
    .bind(MAX_AGE)
    .execute(db)
    .await?;
    Ok(token)
}

pub async fn actor(db: &mut MySqlConnection, token: &str) -> Result<Actor, sqlx::Error> {
    let row = sqlx::query_as::<_, (i32, String, Vec<u8>, bool, bool, i64)>(
        "SELECT u.id, u.password, s.password_hash, u.is_staff, u.is_superuser,
                EXISTS(SELECT 1 FROM auth_user_groups membership
                       JOIN auth_group g ON g.id = membership.group_id
                       WHERE membership.user_id = u.id AND g.name = 'APDA Board')
         FROM mittab_rust_session s JOIN auth_user u ON u.id = s.user_id
         WHERE s.token_hash = ? AND s.expires_at > UTC_TIMESTAMP(6) AND u.is_active = TRUE",
    )
    .bind(hash(token).as_ref())
    .fetch_optional(db)
    .await?;
    Ok(match row {
        Some((id, password, fingerprint, staff, superuser, board))
            if hash(&password).as_ref() == fingerprint =>
        {
            Actor::User {
                id,
                board: board != 0,
                staff,
                superuser,
            }
        }
        _ => Actor::Anonymous,
    })
}

pub(super) async fn lock_active(
    db: &mut MySqlConnection,
    token: &str,
) -> Result<bool, sqlx::Error> {
    let session = sqlx::query_scalar::<_, i32>(
        "SELECT user_id FROM mittab_rust_session
         WHERE token_hash = ? AND expires_at > UTC_TIMESTAMP(6) FOR UPDATE",
    )
    .bind(hash(token).as_ref())
    .fetch_optional(db)
    .await?;
    Ok(session.is_some())
}

pub async fn remove(db: &MySqlPool, token: &str) -> Result<(), sqlx::Error> {
    if is_native_key(token) {
        sqlx::query("DELETE FROM mittab_rust_session WHERE token_hash = ?")
            .bind(hash(token).as_ref())
            .execute(db)
            .await?;
    } else if legacy_session::is_legacy_key(token) {
        sqlx::query("DELETE FROM django_session WHERE session_key = ?")
            .bind(token)
            .execute(db)
            .await?;
    }
    Ok(())
}

fn hash(value: &str) -> digest::Digest {
    digest::digest(&digest::SHA256, value.as_bytes())
}
