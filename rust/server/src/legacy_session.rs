use std::io::Read;

use axum::{
    http::{HeaderMap, StatusCode, header},
    response::Response,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use flate2::read::ZlibDecoder;
use ring::{digest, hmac};
use serde::Deserialize;
use sqlx::MySqlPool;

const AUTH_BACKEND: &str = "mittab.apps.tab.auth_backends.TabAuthenticationBackend";
const SIGNER_SALT: &str = "django.contrib.sessions.SessionStoresigner";
const AUTH_HASH_SALT: &str = "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash";
const MAX_SESSION_BYTES: usize = 64 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Actor {
    Anonymous,
    User {
        id: i32,
        board: bool,
        staff: bool,
        superuser: bool,
    },
}

pub enum SessionError {
    Reauthenticate,
    Database,
}

impl SessionError {
    pub fn response(self) -> Response {
        match self {
            Self::Reauthenticate => crate::private((
                StatusCode::SEE_OTHER,
                [(header::LOCATION, "/public/login/")],
            )),
            Self::Database => crate::unavailable(),
        }
    }
}

#[derive(Deserialize)]
struct Session {
    #[serde(rename = "_auth_user_id")]
    user_id: String,
    #[serde(rename = "_auth_user_backend")]
    backend: String,
    #[serde(rename = "_auth_user_hash")]
    auth_hash: String,
}

pub async fn actor(
    db: &MySqlPool,
    headers: &HeaderMap,
    secret: Option<&str>,
) -> Result<Actor, SessionError> {
    let Some(key) = cookie(headers, "sessionid") else {
        return Ok(Actor::Anonymous);
    };
    if crate::native_session::is_native_key(key) {
        return crate::native_session::actor(db, key)
            .await
            .map_err(|_| SessionError::Database);
    }
    if !is_legacy_key(key) {
        return Ok(Actor::Anonymous);
    };
    let Some(data) = sqlx::query_scalar::<_, String>(
        "SELECT session_data FROM django_session WHERE session_key = ? AND expire_date > UTC_TIMESTAMP(6)",
    )
    .bind(key)
    .fetch_optional(db)
    .await
    .map_err(|_| SessionError::Database)?
    else {
        return Ok(Actor::Anonymous);
    };
    let (signed, tag) = data.rsplit_once(':').ok_or(SessionError::Reauthenticate)?;
    let payload = decode_session_data(signed).ok_or(SessionError::Reauthenticate)?;
    // Django also creates database sessions for anonymous visitors. Only
    // explicit absence of authentication markers can take the anonymous path.
    if !["_auth_user_id", "_auth_user_backend", "_auth_user_hash"]
        .into_iter()
        .any(|field| payload.contains_key(field))
    {
        return Ok(Actor::Anonymous);
    }
    let secret = secret.ok_or(SessionError::Reauthenticate)?;
    let tag = URL_SAFE_NO_PAD
        .decode(tag)
        .map_err(|_| SessionError::Reauthenticate)?;
    if !verify(SIGNER_SALT, signed, secret, &tag) {
        return Err(SessionError::Reauthenticate);
    }
    let session: Session = serde_json::from_value(serde_json::Value::Object(payload))
        .map_err(|_| SessionError::Reauthenticate)?;
    let Ok(user_id) = session.user_id.parse::<i32>() else {
        return Ok(Actor::Anonymous);
    };
    if session.backend != AUTH_BACKEND {
        return Ok(Actor::Anonymous);
    }

    let Some((password, staff, superuser, board)) = sqlx::query_as::<_, (String, bool, bool, i64)>(
        "SELECT u.password, u.is_staff, u.is_superuser,
                EXISTS(SELECT 1
                       FROM auth_user_groups membership
                       JOIN auth_group g ON g.id = membership.group_id
                       WHERE membership.user_id = u.id AND g.name = 'APDA Board')
         FROM auth_user u
         WHERE u.id = ? AND u.is_active = TRUE",
    )
    .bind(user_id)
    .fetch_optional(db)
    .await
    .map_err(|_| SessionError::Database)?
    else {
        return Ok(Actor::Anonymous);
    };
    let auth_hash = hex::decode(session.auth_hash).ok();
    if !auth_hash.is_some_and(|tag| verify(AUTH_HASH_SALT, &password, secret, &tag)) {
        return Ok(Actor::Anonymous);
    }
    Ok(Actor::User {
        id: user_id,
        board: board != 0,
        staff,
        superuser,
    })
}

pub fn cookie<'a>(headers: &'a HeaderMap, target: &str) -> Option<&'a str> {
    // Django overwrites an earlier cookie if another cookie of the same name
    // appears later in the header.
    headers
        .get_all(header::COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|header| header.split(';'))
        .filter_map(|cookie| cookie.trim().split_once('='))
        .filter(|(name, _)| name.trim() == target)
        .map(|(_, value)| value.trim().trim_matches('"'))
        .next_back()
}

pub fn is_legacy_key(key: &str) -> bool {
    key.len() == 32
        && key
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
}

fn decode_session_data(signed: &str) -> Option<serde_json::Map<String, serde_json::Value>> {
    if signed.len() > MAX_SESSION_BYTES {
        return None;
    }
    let (payload, timestamp) = signed.rsplit_once(':')?;
    if timestamp.is_empty() || !timestamp.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
        return None;
    }
    if let Some(compressed) = payload.strip_prefix('.') {
        let bytes = URL_SAFE_NO_PAD.decode(compressed).ok()?;
        let mut decoded = Vec::new();
        ZlibDecoder::new(bytes.as_slice())
            .take((MAX_SESSION_BYTES + 1) as u64)
            .read_to_end(&mut decoded)
            .ok()?;
        if decoded.len() > MAX_SESSION_BYTES {
            return None;
        }
        serde_json::from_slice(&decoded).ok()
    } else {
        serde_json::from_slice(&URL_SAFE_NO_PAD.decode(payload).ok()?).ok()
    }
}

fn verify(salt: &str, value: &str, secret: &str, tag: &[u8]) -> bool {
    let mut digest = digest::Context::new(&digest::SHA256);
    digest.update(salt.as_bytes());
    digest.update(secret.as_bytes());
    let key = hmac::Key::new(hmac::HMAC_SHA256, digest.finish().as_ref());
    hmac::verify(&key, value.as_bytes(), tag).is_ok()
}
