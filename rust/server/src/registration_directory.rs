use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
    time::Duration,
};

use reqwest::{Client, Method};
use serde_json::{Value, json};

use crate::registration_form::valid_email;

static CLIENT: LazyLock<Option<Client>> = LazyLock::new(|| {
    Client::builder()
        .timeout(Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .ok()
});

async fn request(method: Method, path: &str, private: Option<(&str, Value)>) -> Result<Value, u16> {
    let base = std::env::var("BLACK_ROD_API_BASE_URL")
        .unwrap_or_else(|_| "https://results.apda.online".into());
    let mut request = CLIENT
        .as_ref()
        .ok_or(502_u16)?
        .request(method, format!("{}{path}", base.trim_end_matches('/')));
    if let Some((token, payload)) = private {
        request = request
            .bearer_auth(token)
            .header("Content-Type", "application/json")
            .body(payload.to_string());
    }
    let mut response = request.send().await.map_err(|_| 502_u16)?;
    if !response.status().is_success() {
        return Err(response.status().as_u16());
    }
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| 502_u16)? {
        if body.len() + chunk.len() > 2 * 1024 * 1024 {
            return Err(502);
        }
        body.extend_from_slice(&chunk);
    }
    serde_json::from_slice(&body).map_err(|_| 502_u16)
}

pub async fn debaters(school: i32) -> Result<Value, u16> {
    request(Method::GET, &format!("/api/debaters/{school}/"), None).await
}

pub fn positive_id(value: &Value) -> Option<i32> {
    let id = if let Some(text) = value.as_str() {
        text.parse().ok()?
    } else {
        i32::try_from(value.as_i64()?).ok()?
    };
    (id > 0).then_some(id)
}

pub fn includes(debaters: &Value, id: i32) -> bool {
    debaters
        .as_array()
        .or_else(|| debaters["debaters"].as_array())
        .into_iter()
        .flatten()
        .any(|member| {
            let effective = if member["apda_id"].is_null() {
                &member["id"]
            } else {
                &member["apda_id"]
            };
            positive_id(effective) == Some(id)
        })
}

pub async fn private_emails(ids: &BTreeSet<i32>) -> BTreeMap<i32, String> {
    let token = std::env::var("BLACK_ROD_PRIVATE_API_TOKEN").unwrap_or_default();
    if token.is_empty() || ids.is_empty() {
        return BTreeMap::new();
    }
    let Ok(payload) = request(
        Method::POST,
        "/api/private/debater-emails/",
        Some((&token, json!({"debater_ids": ids}))),
    )
    .await
    else {
        return BTreeMap::new();
    };
    payload["debaters"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| {
            let id = positive_id(&row["id"]).filter(|id| ids.contains(id))?;
            let email = row["email"].as_str()?.trim();
            valid_email(email).then_some((id, email.into()))
        })
        .collect()
}

pub fn mask(email: &str) -> String {
    email
        .rsplit_once('@')
        .map_or_else(String::new, |(local, host)| {
            let visible: String = local.chars().take(2).collect();
            format!("{visible}***@{host}")
        })
}
