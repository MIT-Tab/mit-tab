use std::num::NonZeroU32;

use base64::{
    Engine,
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
};
use ring::{
    pbkdf2::{self, PBKDF2_HMAC_SHA256},
    rand::{SecureRandom, SystemRandom},
};

const ITERATIONS: NonZeroU32 = NonZeroU32::new(600_000).unwrap();

pub fn random_secret() -> Result<String, &'static str> {
    let mut bytes = [0_u8; 32];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| "Could not generate credentials")?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

pub fn encode(password: &[u8]) -> Result<String, &'static str> {
    let salt = random_secret()?;
    let mut bytes = [0_u8; 32];
    pbkdf2::derive(
        PBKDF2_HMAC_SHA256,
        ITERATIONS,
        salt.as_bytes(),
        password,
        &mut bytes,
    );
    Ok(format!(
        "pbkdf2_sha256${ITERATIONS}${salt}${}",
        STANDARD.encode(bytes)
    ))
}

pub fn verify(password: &[u8], encoded: &str) -> bool {
    let mut fields = encoded.split('$');
    let (Some(algorithm), Some(iterations), Some(salt), Some(tag), None) = (
        fields.next(),
        fields.next(),
        fields.next(),
        fields.next(),
        fields.next(),
    ) else {
        return false;
    };
    let algorithm = match algorithm {
        "pbkdf2_sha256" => PBKDF2_HMAC_SHA256,
        "pbkdf2_sha1" => pbkdf2::PBKDF2_HMAC_SHA1,
        _ => return false,
    };
    let (Ok(iterations), Ok(tag)) = (iterations.parse::<NonZeroU32>(), STANDARD.decode(tag)) else {
        return false;
    };
    pbkdf2::verify(algorithm, iterations, salt.as_bytes(), password, &tag).is_ok()
}

pub fn dummy_verify(password: &[u8]) {
    let mut scratch = [0_u8; 32];
    pbkdf2::derive(
        PBKDF2_HMAC_SHA256,
        ITERATIONS,
        b"mittab-login",
        password,
        &mut scratch,
    );
}
