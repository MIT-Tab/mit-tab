use std::{collections::BTreeSet, env};

use aws_sigv4::http_request::SignableBody;
use axum::body::Bytes;
use reqwest::{Method, Response, StatusCode, Url};
use serde::Deserialize;

use crate::{
    aws_http::{self, AwsHttp},
    backups::{BackupError, MAX_BACKUP_BYTES, SUFFIX, valid_key},
};

const LIST_RESPONSE_LIMIT: usize = 8 * 1024 * 1024;

pub struct ObjectStore {
    aws: AwsHttp,
    bucket: Url,
    prefix: String,
}

pub enum Put {
    Created,
    Exists,
}

fn setting(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.trim().is_empty())
}

impl ObjectStore {
    pub fn from_env(prefix: &str) -> Result<Self, BackupError> {
        let prefix = prefix.trim_matches('/');
        let invalid_path = prefix.is_empty()
            || prefix.contains('\\')
            || prefix.chars().any(char::is_control)
            || prefix
                .split('/')
                .any(|part| matches!(part, "" | "." | ".."));
        if invalid_path {
            return Err(BackupError::Storage);
        }
        let bucket = setting("BACKUP_BUCKET").ok_or(BackupError::Storage)?;
        if !(3..=63).contains(&bucket.len())
            || !bucket.bytes().all(|byte| {
                byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'-' | b'.')
            })
        {
            return Err(BackupError::Storage);
        }
        let region = setting("BACKUP_S3_REGION")
            .or_else(|| setting("AWS_REGION"))
            .or_else(|| setting("AWS_DEFAULT_REGION"))
            .unwrap_or_else(|| "us-east-1".into());
        if !region
            .bytes()
            .all(|value| value.is_ascii_alphanumeric() || value == b'-')
        {
            return Err(BackupError::Storage);
        }
        let key = setting("AWS_ACCESS_KEY_ID").ok_or(BackupError::Storage)?;
        let secret = setting("AWS_SECRET_ACCESS_KEY").ok_or(BackupError::Storage)?;
        let aws = AwsHttp::new(
            key,
            secret,
            setting("AWS_SESSION_TOKEN"),
            region.clone(),
            "s3",
        )
        .ok_or(BackupError::Storage)?;
        let bucket_url = if let Some(address) = setting("BACKUP_S3_ENDPOINT") {
            let mut endpoint = aws_http::https_endpoint(&address).ok_or(BackupError::Storage)?;
            let host = endpoint.host_str().ok_or(BackupError::Storage)?.to_owned();
            if host.ends_with(".digitaloceanspaces.com")
                && !host.starts_with(&format!("{bucket}."))
                && !bucket.contains('.')
            {
                endpoint
                    .set_host(Some(&format!("{bucket}.{host}")))
                    .map_err(|_| BackupError::Storage)?;
            } else if !host.starts_with(&format!("{bucket}.")) {
                endpoint
                    .path_segments_mut()
                    .map_err(|_| BackupError::Storage)?
                    .pop_if_empty()
                    .push(&bucket);
            }
            endpoint
        } else {
            aws_http::https_endpoint(&format!("https://{bucket}.s3.{region}.amazonaws.com"))
                .ok_or(BackupError::Storage)?
        };
        Ok(Self {
            aws,
            bucket: bucket_url,
            prefix: prefix.into(),
        })
    }

    fn object(&self, key: &str) -> Result<Url, BackupError> {
        if !valid_key(key) {
            return Err(BackupError::InvalidName);
        }
        let mut url = self.bucket.clone();
        url.path_segments_mut()
            .map_err(|_| BackupError::Storage)?
            .pop_if_empty()
            .extend(self.prefix.split('/'))
            .push(&format!("{key}{SUFFIX}"));
        Ok(url)
    }

    pub async fn put(&self, key: &str, bytes: Bytes) -> Result<Put, BackupError> {
        let object = self.object(key)?;
        let response = self
            .aws
            .request(
                Method::PUT,
                object,
                &[("content-type", "application/sql"), ("if-none-match", "*")],
                SignableBody::Bytes(&bytes),
            )
            .map_err(|_| BackupError::Storage)?
            .body(bytes)
            .send()
            .await
            .map_err(|_| BackupError::Storage)?;
        match response.status() {
            status if status.is_success() => Ok(Put::Created),
            StatusCode::PRECONDITION_FAILED | StatusCode::CONFLICT => Ok(Put::Exists),
            _ => Err(BackupError::Storage),
        }
    }

    pub async fn get(&self, key: &str) -> Result<Vec<u8>, BackupError> {
        let upload = self.object(key)?;
        let response = self
            .aws
            .request(Method::GET, upload, &[], SignableBody::Bytes(&[]))
            .map_err(|_| BackupError::Storage)?
            .send()
            .await
            .map_err(|_| BackupError::Storage)?;
        read(response, MAX_BACKUP_BYTES).await
    }

    pub async fn list(&self) -> Result<Vec<String>, BackupError> {
        let mut keys = BTreeSet::new();
        let mut token = None;
        let mut marker = None;
        let prefix = format!("{}/", self.prefix);
        for _ in 0..1_000 {
            let mut url = self.bucket.clone();
            {
                let mut query = url.query_pairs_mut();
                query
                    .append_pair("prefix", &prefix)
                    .append_pair("max-keys", "1000");
                if let Some(marker) = marker.as_deref() {
                    query.append_pair("marker", marker);
                } else {
                    query.append_pair("list-type", "2");
                    if let Some(token) = token.as_deref() {
                        query.append_pair("continuation-token", token);
                    }
                }
            }
            let response = self
                .aws
                .request(Method::GET, url, &[], SignableBody::Bytes(&[]))
                .map_err(|_| BackupError::Storage)?
                .send()
                .await
                .map_err(|_| BackupError::Storage)?;
            let data = read(response, LIST_RESPONSE_LIMIT).await?;
            let xml = std::str::from_utf8(&data).map_err(|_| BackupError::Storage)?;
            let page: Page = quick_xml::de::from_str(xml).map_err(|_| BackupError::Storage)?;
            for object in &page.contents {
                if let Some(key) = object
                    .key
                    .strip_prefix(&prefix)
                    .and_then(|name| name.strip_suffix(SUFFIX))
                    .filter(|key| valid_key(key))
                {
                    keys.insert(key.to_string());
                }
            }
            if !page.is_truncated {
                return Ok(keys.into_iter().collect());
            }
            if let Some(next) = page
                .next_continuation_token
                .filter(|value| !value.is_empty())
            {
                if token.as_ref() == Some(&next) {
                    return Err(BackupError::Storage);
                }
                token = Some(next);
            } else {
                // Some S3-compatible services omit v2 continuation tokens.
                let next = page
                    .next_marker
                    .or_else(|| page.contents.last().map(|item| item.key.clone()))
                    .ok_or(BackupError::Storage)?;
                if marker.as_ref() == Some(&next) {
                    return Err(BackupError::Storage);
                }
                marker = Some(next);
            }
        }
        Err(BackupError::Storage)
    }
}

#[derive(Deserialize, Default)]
#[serde(default, rename_all = "PascalCase")]
struct Page {
    contents: Vec<Object>,
    is_truncated: bool,
    next_continuation_token: Option<String>,
    next_marker: Option<String>,
}

#[derive(Deserialize)]
struct Object {
    #[serde(rename = "Key")]
    key: String,
}

async fn read(mut response: Response, limit: usize) -> Result<Vec<u8>, BackupError> {
    match response.status() {
        StatusCode::NOT_FOUND => return Err(BackupError::Missing),
        status if status.is_success() => {}
        _ => return Err(BackupError::Storage),
    }
    if response
        .content_length()
        .is_some_and(|size| size > limit as u64)
    {
        return Err(BackupError::TooLarge);
    }
    let mut data = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| BackupError::Storage)? {
        if data.len() + chunk.len() > limit {
            return Err(BackupError::TooLarge);
        }
        data.extend_from_slice(&chunk);
    }
    Ok(data)
}
