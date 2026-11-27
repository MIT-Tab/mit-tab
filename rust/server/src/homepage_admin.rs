use std::io::Cursor;

use axum::{
    Extension,
    extract::{Multipart, State},
    http::{HeaderMap, StatusCode, header},
    response::Response,
};
use image::{
    DynamicImage, ImageDecoder, ImageFormat, Limits,
    codecs::{jpeg::JpegDecoder, png::PngDecoder, webp::WebPDecoder},
};
use minijinja::context;

use crate::{
    App, access, home,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_admin::{Resource, resources},
    registration_form::length,
    settings::{self, SettingValue, Settings},
    staff_pairing::lock_round,
    unavailable,
};

const PATH: &str = "/settings/public-homepage/";
const DEFAULT_THEME: &str = "#00438A";
const MAX_LOGO_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_BODY: usize = MAX_LOGO_BYTES + 64 * 1024;
const IMAGE_ERROR: &str = "Upload a valid PNG, JPEG, or WebP image.";

fn is_hex_color(color: &str) -> bool {
    color.starts_with('#')
        && color.len() == 7
        && color.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
}

fn theme_color(candidate: &str) -> String {
    let candidate = candidate.trim();
    let color = if candidate.starts_with('#') {
        candidate.into()
    } else {
        format!("#{candidate}")
    };
    if is_hex_color(&color) {
        color.to_ascii_uppercase()
    } else {
        DEFAULT_THEME.into()
    }
}

async fn branding(app: &App) -> Result<(String, String), sqlx::Error> {
    let mut db = app.db.acquire().await?;
    let settings = Settings::load(&mut db, &["theme_color", "tournament_name"]).await?;
    Ok((
        settings.string("tournament_name", "New Tournament"),
        theme_color(&settings.string("theme_color", DEFAULT_THEME)),
    ))
}

pub async fn theme(State(app): State<App>) -> Response {
    let color = branding(&app)
        .await
        .map_or_else(|_| DEFAULT_THEME.into(), |(_, color)| color);
    private((
        [
            (header::CONTENT_TYPE, "text/css; charset=utf-8"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
        ],
        format!(":root {{ {} }}", theme_variables(&color)),
    ))
}

pub fn theme_variables(candidate: &str) -> String {
    let color = theme_color(candidate);
    let channel = |from, to| u32::from_str_radix(&color[from..to], 16).unwrap_or_default();
    let (red, green, blue) = (channel(1, 3), channel(3, 5), channel(5, 7));
    let (text, text_rgb) = if red * 299 + green * 587 + blue * 114 >= 128_000 {
        ("#1a1a1a", "26, 26, 26")
    } else {
        ("#ffffff", "255, 255, 255")
    };
    format!(
        "--theme-color: {color}; --theme-color-rgb: {red}, {green}, {blue}; --theme-on-color: {text}; --theme-on-color-rgb: {text_rgb};"
    )
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    render(&app, actor, &headers, None).await
}

async fn render(app: &App, actor: Actor, headers: &HeaderMap, error: Option<&str>) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let Ok((name, color)) = branding(app).await else {
        return unavailable();
    };
    let logo = home::logo_url(app).await;
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(info_links) = resources(&mut db, Resource::InfoLink).await else {
        return unavailable();
    };
    let Ok(info_text) = resources(&mut db, Resource::InfoText).await else {
        return unavailable();
    };
    let response = html::render(
        "homepage_admin.html",
        context! {
            title => "Public Homepage", nav => Navigation::new(actor, true), csrf, name, color, logo,
            info_links, info_text, error,
        },
        if error.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    html::form_response(response, headers, &csrf)
}

#[derive(Default)]
struct Upload {
    csrf: String,
    name: String,
    color: String,
    logo: Option<Vec<u8>>,
}

async fn fields(mut parts: Multipart) -> Result<Upload, &'static str> {
    let mut form = Upload::default();
    while let Some(field) = parts
        .next_field()
        .await
        .map_err(|_| "Invalid branding form.")?
    {
        match field.name() {
            Some("csrfmiddlewaretoken") => {
                form.csrf = field.text().await.map_err(|_| "Invalid branding form.")?
            }
            Some("tournament_name") => {
                form.name = field.text().await.map_err(|_| "Invalid tournament name.")?
            }
            Some("theme_color") => {
                form.color = field.text().await.map_err(|_| "Invalid theme color.")?
            }
            Some("tournament_logo") if field.file_name().is_some_and(|name| !name.is_empty()) => {
                let bytes = field
                    .bytes()
                    .await
                    .map_err(|_| "Logo must be 8 MB or smaller.")?;
                if bytes.len() > MAX_LOGO_BYTES {
                    return Err("Logo must be 8 MB or smaller.");
                }
                form.logo = Some(bytes.to_vec());
            }
            _ => {}
        }
    }
    Ok(form)
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    parts: Multipart,
) -> Response {
    let upload = match fields(parts).await {
        Ok(upload) => upload,
        Err(error) => return render(&app, actor, &headers, Some(error)).await,
    };
    if !login::valid_csrf(&headers, &upload.csrf) {
        return login::csrf_error();
    }
    let name = upload.name.trim();
    let color = upload.color.trim();
    if !length(name, 200) || !is_hex_color(color) {
        return render(
            &app,
            actor,
            &headers,
            Some("Enter a tournament name and a six-digit hex theme color."),
        )
        .await;
    }
    let logo = if let Some(bytes) = upload.logo {
        match tokio::task::spawn_blocking(move || decode_logo(&bytes)).await {
            Ok(Ok(bytes)) => Some(bytes),
            Ok(Err(error)) => return render(&app, actor, &headers, Some(error)).await,
            Err(_) => return unavailable(),
        }
    } else {
        None
    };
    match save(&app, name, color, logo).await {
        Ok(()) => access::found(PATH),
        Err(_) => unavailable(),
    }
}

async fn save(
    app: &App,
    name: &str,
    color: &str,
    logo: Option<Vec<u8>>,
) -> Result<(), sqlx::Error> {
    let mut tx = app.db.begin().await?;
    lock_round(&mut tx).await?;
    let name = if name.is_empty() {
        "New Tournament"
    } else {
        name
    };
    settings::put(&mut tx, "tournament_name", SettingValue::Text(name)).await?;
    settings::put(
        &mut tx,
        "theme_color",
        SettingValue::Text(&theme_color(color)),
    )
    .await?;
    if let Some(bytes) = logo {
        sqlx::query(
            "INSERT INTO mittab_public_logo (id, bytes, updated_at) VALUES (1, ?, UTC_TIMESTAMP(6))
             ON DUPLICATE KEY UPDATE bytes = ?, updated_at = UTC_TIMESTAMP(6)",
        )
        .bind(&bytes)
        .bind(&bytes)
        .execute(&mut *tx)
        .await?;
    }
    tx.commit().await
}

fn decode_logo(bytes: &[u8]) -> Result<Vec<u8>, &'static str> {
    let input = Cursor::new(bytes);
    let mut limits = Limits::default();
    limits.max_image_width = Some(3000);
    limits.max_image_height = Some(3000);
    limits.max_alloc = Some(80 * 1024 * 1024);

    let format = image::guess_format(bytes).map_err(|_| IMAGE_ERROR)?;
    let mut decoder: Box<dyn ImageDecoder> = match format {
        ImageFormat::Png => {
            let decoder =
                PngDecoder::with_limits(input, limits.clone()).map_err(|_| IMAGE_ERROR)?;
            if decoder.is_apng().map_err(|_| IMAGE_ERROR)? {
                return Err("Animated logos are not supported.");
            }
            Box::new(decoder)
        }
        ImageFormat::Jpeg => Box::new(JpegDecoder::new(input).map_err(|_| IMAGE_ERROR)?),
        ImageFormat::WebP => {
            let decoder = WebPDecoder::new(input).map_err(|_| IMAGE_ERROR)?;
            if decoder.has_animation() {
                return Err("Animated logos are not supported.");
            }
            Box::new(decoder)
        }
        _ => return Err(IMAGE_ERROR),
    };
    let (width, height) = decoder.dimensions();
    if width == 0 || height == 0 || width > 3000 || height > 3000 {
        return Err("Logo dimensions must be at most 3000 by 3000 pixels.");
    }
    if width * 2 < height || height * 2 < width {
        return Err("Logo aspect ratio must be between 1:2 and 2:1.");
    }
    decoder.set_limits(limits).map_err(|_| IMAGE_ERROR)?;
    let orientation = decoder.orientation().map_err(|_| IMAGE_ERROR)?;
    let mut image = DynamicImage::from_decoder(decoder).map_err(|_| IMAGE_ERROR)?;
    image.apply_orientation(orientation);
    let image = if image.color().has_alpha() {
        DynamicImage::ImageRgba8(image.into_rgba8())
    } else {
        DynamicImage::ImageRgb8(image.into_rgb8())
    };
    let mut png = Cursor::new(Vec::new());
    image
        .write_to(&mut png, ImageFormat::Png)
        .map_err(|_| IMAGE_ERROR)?;
    Ok(png.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn image_upload_decodes_png_jpeg_and_webp_and_rejects_invalid_dimensions() {
        let encode = |width, height, format| {
            let mut input = Cursor::new(Vec::new());
            DynamicImage::new_rgb8(width, height)
                .write_to(&mut input, format)
                .unwrap();
            input.into_inner()
        };
        for format in [ImageFormat::Png, ImageFormat::Jpeg, ImageFormat::WebP] {
            let png = decode_logo(&encode(32, 16, format)).unwrap();
            assert_eq!(image::load_from_memory(&png).unwrap().width(), 32);
            assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
        }
        assert!(decode_logo(&encode(33, 16, ImageFormat::Png)).is_err());
        assert!(decode_logo(&encode(3001, 1600, ImageFormat::Png)).is_err());
        assert!(decode_logo(b"\x89PNG\r\n\x1a\nnot a logo").is_err());
    }
}
