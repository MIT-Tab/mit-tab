use std::{env, sync::LazyLock};

use aws_sigv4::http_request::SignableBody;
use axum::http::{HeaderMap, header};
use reqwest::{Method, StatusCode, Url};
use serde::Serialize;
use serde_json::{Value, json};
use tokio::sync::OnceCell;

use crate::{
    aws_http::{self, AwsHttp},
    login,
    registration_form::valid_email,
};

#[derive(Clone, Serialize)]
pub struct Email {
    pub to_address: String,
    pub subject: String,
    pub text_body: String,
    pub html_body: Option<String>,
    pub reply_to: Option<Vec<String>>,
    pub from_address: Option<String>,
}

impl Email {
    pub fn simple(to_address: String, subject: String, text_body: String) -> Self {
        let html_body = Some(format!(
            "<div style=\"white-space:pre-wrap;font-family:system-ui,sans-serif\">{}</div>",
            escape(&text_body)
        ));
        Self {
            to_address,
            subject,
            text_body,
            html_body,
            reply_to: None,
            from_address: None,
        }
    }

    pub fn with_link(mut self, url: &str, label: &str) -> Self {
        if let Some(html) = &mut self.html_body {
            html.push_str(&format!(
                "<p><a href=\"{}\">{}</a></p>",
                escape(url),
                escape(label)
            ));
        }
        self
    }

    pub fn snapshot(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| "{}".into())
    }
}

fn escape(content: &str) -> String {
    content
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

pub struct Mailer {
    aws: AwsHttp,
    endpoint: Url,
    configuration_set: Option<String>,
    address_list: Option<String>,
    address_list_ready: OnceCell<()>,
    from: String,
    reply_to: Option<String>,
}

fn env_value(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.trim().is_empty())
}

static SERVICE: LazyLock<Option<Mailer>> = LazyLock::new(Mailer::from_env);

pub fn service() -> Option<&'static Mailer> {
    SERVICE.as_ref()
}

impl Mailer {
    fn from_env() -> Option<Self> {
        let region = env::var("AWS_SES_REGION").unwrap_or_else(|_| "us-east-1".into());
        if !region
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        {
            return None;
        }
        let key = env_value("AWS_SES_ACCESS_KEY_ID").or_else(|| env_value("AWS_ACCESS_KEY_ID"))?;
        let secret = env_value("AWS_SES_SECRET_ACCESS_KEY")
            .or_else(|| env_value("AWS_SECRET_ACCESS_KEY"))?;
        let aws = AwsHttp::new(
            key,
            secret,
            env_value("AWS_SESSION_TOKEN"),
            region.clone(),
            "ses",
        )?;
        let endpoint = env_value("MITTAB_SES_ENDPOINT")
            .unwrap_or_else(|| format!("https://email.{region}.amazonaws.com"));
        let endpoint = aws_http::https_endpoint(&endpoint)?;
        let address =
            env_value("EMAIL_FROM_ADDRESS").unwrap_or_else(|| "no-reply@example.com".into());
        if !valid_email(&address) {
            return None;
        }
        let name = env_value("EMAIL_FROM_NAME").unwrap_or_else(|| "MIT-TAB".into());
        let name = name.replace(['\r', '\n', '<', '>'], "");
        let from = format!("{name} <{address}>");
        let reply_to = env::var("EMAIL_REPLY_TO").unwrap_or(address);
        Some(Self {
            aws,
            endpoint,
            from,
            reply_to: valid_email(&reply_to).then_some(reply_to),
            configuration_set: env_value("AWS_SES_CONFIGURATION_SET"),
            address_list: env_value("AWS_MAILMANAGER_ADDRESS_LIST"),
            address_list_ready: OnceCell::new(),
        })
    }

    fn url(&self, segments: &[&str]) -> Result<Url, &'static str> {
        let mut url = self.endpoint.clone();
        url.path_segments_mut()
            .map_err(|_| "Invalid email service endpoint.")?
            .pop_if_empty()
            .extend(segments);
        Ok(url)
    }

    async fn request(
        &self,
        method: Method,
        segments: &[&str],
        payload: Option<Value>,
    ) -> Result<StatusCode, &'static str> {
        let url = self.url(segments)?;
        let body = payload.map_or_else(String::new, |payload| payload.to_string());
        let headers = if body.is_empty() {
            &[][..]
        } else {
            &[("content-type", "application/json")][..]
        };
        self.aws
            .request(method, url, headers, SignableBody::Bytes(body.as_bytes()))?
            .body(body)
            .send()
            .await
            .map(|response| response.status())
            .map_err(|_| "The email delivery service could not be reached.")
    }

    async fn ensure_address_list(&self, name: &str) -> Result<(), &'static str> {
        self.address_list_ready
            .get_or_try_init(|| async {
                let root = ["v2", "email", "contact-lists"];
                let status = self
                    .request(Method::GET, &[root[0], root[1], root[2], name], None)
                    .await?;
                if status.is_success() {
                    return Ok(());
                }
                if status == StatusCode::NOT_FOUND {
                    let created = self
                        .request(Method::POST, &root, Some(json!({"ContactListName": name})))
                        .await?;
                    if created.is_success() || created == StatusCode::CONFLICT {
                        return Ok(());
                    }
                }
                Err("The email contact list could not be prepared.")
            })
            .await
            .map(|_| ())
    }

    pub async fn send(&self, email: &Email) -> Result<(), &'static str> {
        if !valid_email(&email.to_address) {
            return Err("Invalid email recipient.");
        }
        let mut body = json!({"Text": {"Data": email.text_body, "Charset": "UTF-8"}});
        if let Some(html) = &email.html_body {
            body["Html"] = json!({"Data": html, "Charset": "UTF-8"});
        }
        let replies = email
            .reply_to
            .clone()
            .unwrap_or_else(|| self.reply_to.iter().cloned().collect());
        let mut payload = json!({
            "FromEmailAddress": email.from_address.as_deref().unwrap_or(&self.from),
            "Destination": {"ToAddresses": [email.to_address]},
            "ReplyToAddresses": replies,
            "Content": {"Simple": {"Subject": {"Data": email.subject, "Charset": "UTF-8"}, "Body": body}},
        });
        if let Some(name) = &self.configuration_set {
            payload["ConfigurationSetName"] = json!(name);
        }
        if let Some(name) = &self.address_list {
            self.ensure_address_list(name).await?;
            payload["ListManagementOptions"] = json!({"ContactListName": name});
        }
        let status = self
            .request(
                Method::POST,
                &["v2", "email", "outbound-emails"],
                Some(payload),
            )
            .await?;
        if status.is_success() {
            Ok(())
        } else {
            Err("The email delivery service rejected the message.")
        }
    }
}

pub fn origin(headers: &HeaderMap) -> Option<String> {
    if let Some(origin) = env_value("MITTAB_PUBLIC_URL") {
        let url = aws_http::https_endpoint(&origin)?;
        if url.path() != "/" {
            return None;
        }
        return Some(url.origin().ascii_serialization());
    }
    let scheme = if login::secure_request(headers) {
        "https"
    } else {
        "http"
    };
    let host = headers.get(header::HOST)?.to_str().ok()?;
    let host = host.parse::<axum::http::uri::Authority>().ok()?;
    if host.as_str().contains('@') {
        return None;
    }
    let url = aws_http::https_endpoint(&format!("{scheme}://{host}"))?;
    Some(url.origin().ascii_serialization())
}
