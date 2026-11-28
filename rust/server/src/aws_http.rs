use std::{
    sync::LazyLock,
    time::{Duration, SystemTime},
};

use aws_credential_types::Credentials;
use aws_sigv4::{
    http_request::{
        PayloadChecksumKind, PercentEncodingMode, SignableBody, SignableRequest, SigningSettings,
        UriPathNormalizationMode, sign,
    },
    sign::v4,
};
use reqwest::{Client, Method, RequestBuilder, Url};

static CLIENT: LazyLock<Option<Client>> = LazyLock::new(|| {
    Client::builder()
        .timeout(Duration::from_secs(30))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .ok()
});

#[derive(Clone)]
pub struct AwsHttp {
    credentials: Credentials,
    region: String,
    service: &'static str,
}

impl AwsHttp {
    pub fn new(
        key: String,
        secret: String,
        token: Option<String>,
        region: String,
        service: &'static str,
    ) -> Option<Self> {
        if key.is_empty() || secret.is_empty() || region.is_empty() {
            return None;
        }
        Some(Self {
            credentials: Credentials::new(key, secret, token, None, "MIT-Tab"),
            region,
            service,
        })
    }

    pub fn request(
        &self,
        method: Method,
        url: Url,
        headers: &[(&str, &str)],
        body: SignableBody<'_>,
    ) -> Result<RequestBuilder, &'static str> {
        const ERROR: &str = "Unable to sign the service request.";
        let mut settings = SigningSettings::default();
        if self.service == "s3" {
            settings.payload_checksum_kind = PayloadChecksumKind::XAmzSha256;
            settings.percent_encoding_mode = PercentEncodingMode::Single;
            settings.uri_path_normalization_mode = UriPathNormalizationMode::Disabled;
        }
        let identity = self.credentials.clone().into();
        let params = v4::SigningParams::builder()
            .identity(&identity)
            .region(&self.region)
            .name(self.service)
            .time(SystemTime::now())
            .settings(settings)
            .build()
            .map_err(|_| ERROR)?
            .into();
        let signable =
            SignableRequest::new(method.as_str(), url.as_str(), headers.iter().copied(), body)
                .map_err(|_| ERROR)?;
        let (signature, _) = sign(signable, &params).map_err(|_| ERROR)?.into_parts();
        let mut request = CLIENT.as_ref().ok_or(ERROR)?.request(method, url);
        for &(name, value) in headers {
            request = request.header(name, value);
        }
        for (name, value) in signature.headers() {
            request = request.header(name, value);
        }
        Ok(request)
    }
}

pub fn https_endpoint(value: &str) -> Option<Url> {
    let parsed = Url::parse(value).ok()?;
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return None;
    }
    match (parsed.scheme(), parsed.host()?) {
        ("https", _) => Some(parsed),
        ("http", url::Host::Ipv4(address)) if address.is_loopback() => Some(parsed),
        ("http", url::Host::Ipv6(address)) if address.is_loopback() => Some(parsed),
        ("http", url::Host::Domain("localhost")) => Some(parsed),
        _ => None,
    }
}
