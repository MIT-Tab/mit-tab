use std::{
    collections::{HashMap, HashSet},
    sync::LazyLock,
    time::{Duration, Instant},
};

use reqwest::Client;
use serde::{Deserialize, Serialize};
use sqlx::MySqlPool;
use tokio::sync::Mutex;

#[derive(Clone, Serialize)]
pub struct SchoolChoice {
    pub value: String,
    pub name: String,
}

#[derive(Deserialize)]
struct School {
    id: Option<i32>,
    apda_id: Option<i32>,
    name: Option<String>,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum DirectoryResponse {
    List(Vec<School>),
    Object { schools: Vec<School> },
}

type CachedDirectory = Option<(Instant, Vec<SchoolChoice>)>;

static DIRECTORY: LazyLock<Mutex<CachedDirectory>> = LazyLock::new(|| Mutex::new(None));
static CLIENT: LazyLock<Option<Client>> = LazyLock::new(|| {
    Client::builder()
        .timeout(Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .ok()
});

async fn remote() -> Vec<SchoolChoice> {
    let mut cached = DIRECTORY.lock().await;
    if let Some((expires, schools)) = &*cached
        && Instant::now() < *expires
    {
        return schools.clone();
    }
    let schools = request().await;
    let ttl = if schools.is_some() { 3600 } else { 30 };
    let schools = schools
        .or_else(|| cached.as_ref().map(|(_, schools)| schools.clone()))
        .unwrap_or_default();
    *cached = Some((Instant::now() + Duration::from_secs(ttl), schools.clone()));
    schools
}

async fn request() -> Option<Vec<SchoolChoice>> {
    let endpoint = std::env::var("MITTAB_SCHOOL_DIRECTORY_URL")
        .unwrap_or_else(|_| "https://results.apda.online/api/schools/all/".into());
    if endpoint.is_empty() {
        return Some(Vec::new());
    }
    let mut response = CLIENT
        .as_ref()?
        .get(endpoint)
        .send()
        .await
        .ok()?
        .error_for_status()
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.ok()? {
        if body.len() + chunk.len() > 2 * 1024 * 1024 {
            return None;
        }
        body.extend_from_slice(&chunk);
    }
    let schools = match serde_json::from_slice(&body).ok()? {
        DirectoryResponse::List(schools) | DirectoryResponse::Object { schools } => schools,
    };
    Some(
        schools
            .into_iter()
            .filter_map(|school| {
                let id = school.id.or(school.apda_id).filter(|&id| id > 0)?;
                let name = school
                    .name
                    .filter(|name| !name.trim().is_empty() && name.chars().count() <= 200)?;
                Some(SchoolChoice {
                    value: format!("apda:{id}"),
                    name,
                })
            })
            .collect(),
    )
}

pub async fn choices(db: &MySqlPool) -> Result<Vec<SchoolChoice>, sqlx::Error> {
    let (remote, local) = tokio::join!(
        remote(),
        sqlx::query_as::<_, (i32, Option<i32>, String)>(
            "SELECT id, apda_id, name FROM tab_school ORDER BY name, id",
        )
        .fetch_all(db)
    );
    let mut seen = HashSet::new();
    let local = local?.into_iter().map(|(id, apda_id, name)| SchoolChoice {
        value: match apda_id {
            Some(apda) if apda > 0 => format!("apda:{apda}"),
            _ => format!("pk:{id}"),
        },
        name,
    });
    let mut choices: Vec<_> = remote
        .into_iter()
        .chain(local)
        .filter(|choice| seen.insert(choice.value.clone()))
        .collect();
    choices.sort_by_key(|choice| choice.name.to_lowercase());
    Ok(choices)
}

pub fn by_code(choices: &[SchoolChoice]) -> HashMap<&str, &str> {
    choices
        .iter()
        .map(|choice| (choice.value.as_str(), choice.name.as_str()))
        .collect()
}
