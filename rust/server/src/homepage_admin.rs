use std::io::{self, Cursor, Read};

use axum::{
    Extension,
    body::Bytes,
    extract::{FromRequest, Multipart, Request, State},
    http::{HeaderMap, StatusCode, Uri, header},
    response::{IntoResponse, Response},
};
use flate2::read::ZlibDecoder;
use image::{
    DynamicImage, ImageDecoder, ImageEncoder, ImageFormat, Limits,
    codecs::{
        jpeg::JpegDecoder,
        png::{PngDecoder, PngEncoder},
        webp::WebPDecoder,
    },
};
use minijinja::{Value, context};

use crate::{
    App, access, home, legacy_html,
    legacy_session::Actor,
    login::{self, TemplateCsrf},
    private,
    registration_admin::{Resource, ResourceRow, resources},
    registration_form::Fields,
    settings::{self, SettingValue, Settings},
    staff_pairing::lock_round_if_configured,
    staff_roster::form_return_path,
    staff_roster_notice, unavailable,
};

pub(super) const PATH: &str = "/settings/public-homepage/";
const DEFAULT_THEME: &str = "#00438A";
const MAX_LOGO_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_BODY: usize = MAX_LOGO_BYTES + 64 * 1024;
const IMAGE_ERROR: &str =
    "Upload a valid image. The file you uploaded was either not an image or a corrupted image.";
// Django's ImageField checks the suffix using Pillow's registered extensions before the logo validator checks the format.
const IMAGE_EXTENSIONS: &str = "bmp, dib, gif, jfif, jpe, jpg, jpeg, pbm, pgm, ppm, pnm, pfm, png, apng, tif, tiff, avif, avifs, blp, bufr, cur, pcx, dcx, dds, ps, eps, fit, fits, fli, flc, ftc, ftu, gbr, grib, h5, hdf, jp2, j2k, jpc, jpf, jpx, j2c, icns, ico, im, iim, mpg, mpeg, mpo, msp, palm, pcd, pdf, pxr, psd, qoi, bw, rgb, rgba, sgi, ras, tga, icb, vda, vst, webp, wmf, emf, xbm, xpm";
const MAX_PNG_PROFILE_BYTES: u64 = 1024 * 1024;

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

async fn branding(db: &mut sqlx::MySqlConnection) -> Result<(String, String), sqlx::Error> {
    let settings = Settings::load(db, &["theme_color", "tournament_name"]).await?;
    Ok((
        settings.string("tournament_name", "New Tournament"),
        theme_color(&settings.string("theme_color", DEFAULT_THEME)),
    ))
}

pub async fn theme(State(app): State<App>) -> Response {
    let color = match app.db.acquire().await {
        Ok(mut db) => branding(&mut db)
            .await
            .map_or_else(|_| DEFAULT_THEME.into(), |(_, color)| color),
        Err(_) => DEFAULT_THEME.into(),
    };
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
    uri: Uri,
) -> Response {
    render(&app, actor, &headers, &uri, None, &Errors::default()).await
}

#[derive(Default)]
struct Errors {
    name: String,
    color: &'static str,
    logo: String,
}

fn form_context(name: &str, color: &str, errors: &Errors) -> Value {
    let value = |raw: &str| {
        if raw.is_empty() {
            String::new()
        } else {
            format!(r#" value="{}""#, legacy_html::escape(raw))
        }
    };
    let name_value = value(name);
    let color_value = value(color);
    context! {
        tournament_name => context! {
            id_for_label => "id_tournament_name", errors => errors.name,
            input => Value::from_safe_string(format!(r#"<input type="text" name="tournament_name"{name_value} class="form-control" maxlength="200" id="id_tournament_name">"#)),
        },
        theme_color => context! {
            id_for_label => "id_theme_color", value => color, errors => errors.color,
            input => Value::from_safe_string(format!(r##"<input type="text" name="theme_color"{color_value} class="form-control theme-color-input" placeholder="#00438A" pattern="^#[0-9a-fA-F]{{6}}$" autocomplete="off" style="max-width: 12rem;" required id="id_theme_color">"##)),
        },
        tournament_logo => context! {
            id_for_label => "id_tournament_logo", errors => errors.logo,
            input => Value::from_safe_string(r#"<input type="file" name="tournament_logo" class="form-control-file" accept="image/png,image/jpeg,image/webp" id="id_tournament_logo">"#.into()),
        },
    }
}

async fn render(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    uri: &Uri,
    posted: Option<&Fields>,
    errors: &Errors,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let actor = match access::settings_actor(&mut db, app, actor, headers).await {
        Ok(actor) => actor,
        Err(error) => {
            drop(db);
            return error.response(app, headers).await;
        }
    };
    let result = async {
        let (name, color) = branding(&mut db).await?;
        let form = if let Some(fields) = posted {
            let raw = |key| fields.all(key).last().map_or("", String::as_str);
            form_context(raw("tournament_name"), raw("theme_color"), errors)
        } else {
            form_context(&name, &color, errors)
        };
        let logo = home::logo_url(&mut db).await;
        let info_links: Vec<_> = resources(&mut db, Resource::InfoLink)
            .await?
            .into_iter()
            .map(ResourceRow::context)
            .collect();
        let info_texts: Vec<_> = resources(&mut db, Resource::InfoText)
            .await?
            .into_iter()
            .map(ResourceRow::context)
            .collect();
        let shell = legacy_html::shell(&mut db, actor, PATH).await?;
        db.commit().await?;
        let mut response = staff_roster_notice::render(
            app,
            headers,
            PATH,
            "tab/public_homepage.html",
            shell,
            context! { form, logo, info_links, info_texts, csrf_input => csrf.input(),
            return_path => form_return_path(uri, headers, posted), },
            StatusCode::OK,
        );
        csrf.set_cookie(&mut response, headers);
        Ok::<_, sqlx::Error>(response)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Default)]
struct Upload {
    fields: Fields,
    logo: Option<(String, Vec<u8>)>,
}

async fn submitted(app: &App, request: Request) -> Result<Upload, Box<Response>> {
    let multipart = request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.starts_with("multipart/form-data"));
    if !multipart {
        let body = Bytes::from_request(request, app)
            .await
            .map_err(|error| Box::new(private(error.into_response())))?;
        return Ok(Upload {
            fields: Fields::parse(&body),
            logo: None,
        });
    }
    let mut parts = Multipart::from_request(request, app)
        .await
        .map_err(|error| Box::new(private(error.into_response())))?;
    let mut form = Upload::default();
    while let Some(field) = parts
        .next_field()
        .await
        .map_err(|error| Box::new(private(error.into_response())))?
    {
        match (field.name(), field.file_name()) {
            (
                Some(
                    name
                    @ ("csrfmiddlewaretoken" | "tournament_name" | "theme_color" | "return_to"),
                ),
                None,
            ) => {
                let name = name.to_owned();
                let value = field
                    .text()
                    .await
                    .map_err(|error| Box::new(private(error.into_response())))?;
                form.fields.push(name, value);
            }
            (Some("tournament_logo"), Some(name)) if !name.is_empty() => {
                let name = name.to_owned();
                let bytes = field
                    .bytes()
                    .await
                    .map_err(|error| Box::new(private(error.into_response())))?;
                form.logo = Some((name, bytes.to_vec()));
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
    uri: Uri,
    request: Request,
) -> Response {
    let upload = match submitted(&app, request).await {
        Ok(upload) => upload,
        Err(error) => return *error,
    };
    let fields = &upload.fields;
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let name = fields.get("tournament_name");
    let color = fields.get("theme_color");
    let mut errors = Errors::default();
    let name_length = name.chars().count();
    if name_length > 200 {
        errors.name =
            format!("Ensure this value has at most 200 characters (it has {name_length}).");
    }
    if name.contains('\0') {
        errors.name.push_str("Null characters are not allowed.");
    }
    errors.color = if color.is_empty() {
        "This field is required."
    } else if !is_hex_color(color) {
        "Please use a valid hex color (for example: #00438A)."
    } else {
        ""
    };
    let logo = if let Some((filename, bytes)) = upload.logo {
        match tokio::task::spawn_blocking(move || validate_logo_upload(&filename, &bytes)).await {
            Ok(Ok(bytes)) => Some(bytes),
            Ok(Err(error)) => {
                errors.logo = error;
                None
            }
            Err(_) => return unavailable(),
        }
    } else {
        None
    };
    if !errors.name.is_empty() || !errors.color.is_empty() || !errors.logo.is_empty() {
        return render(&app, actor, &headers, &uri, Some(fields), &errors).await;
    }
    save(&app, actor, &headers, name, color, logo).await
}

async fn save(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    name: &str,
    color: &str,
    logo: Option<Vec<u8>>,
) -> Response {
    let Ok(mut tx) = app.db.begin().await else {
        return unavailable();
    };
    if lock_round_if_configured(&mut tx).await.is_err() {
        return unavailable();
    }
    if let Err(error) = access::settings_actor(&mut tx, app, actor, headers).await {
        drop(tx);
        return error.response(app, headers).await;
    }
    let name = if name.is_empty() {
        "New Tournament"
    } else {
        name
    };
    let result = async {
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
    .await;
    match result {
        Ok(()) => staff_roster_notice::saved(app, headers, PATH, "Public homepage updated!"),
        Err(_) => unavailable(),
    }
}

fn validate_logo_upload(filename: &str, bytes: &[u8]) -> Result<Vec<u8>, String> {
    let decoded = decode_logo(bytes).map_err(str::to_owned)?;
    let extension = filename
        .rsplit_once('.')
        .filter(|(stem, _)| stem.chars().any(|character| character != '.'))
        .map_or(String::new(), |(_, ext)| ext.to_lowercase());
    if !IMAGE_EXTENSIONS.split(", ").any(|ext| ext == extension) {
        return Err(format!(
            "File extension “{extension}” is not allowed. Allowed extensions are: {IMAGE_EXTENSIONS}."
        ));
    }
    Ok(decoded)
}

fn decode_logo(bytes: &[u8]) -> Result<Vec<u8>, &'static str> {
    if bytes.is_empty() {
        return Err("The submitted file is empty.");
    }
    if bytes.len() > MAX_LOGO_BYTES {
        return Err("Logo must be 8MB or smaller.");
    }
    let input = Cursor::new(bytes);
    let mut limits = Limits::default();
    limits.max_alloc = Some(80 * 1024 * 1024);

    let format = image::guess_format(bytes).map_err(|_| IMAGE_ERROR)?;
    let mut decoder: Box<dyn ImageDecoder> = match format {
        ImageFormat::Png => {
            let is_static = validate_png_metadata(bytes)?;
            let decoder =
                PngDecoder::with_limits(input, limits.clone()).map_err(|_| IMAGE_ERROR)?;
            validate_dimensions(decoder.dimensions())?;
            if decoder.is_apng().map_err(|_| IMAGE_ERROR)? && !is_static {
                return Err("Animated logos are not supported.");
            }
            Box::new(decoder)
        }
        ImageFormat::Jpeg => Box::new(JpegDecoder::new(input).map_err(|_| IMAGE_ERROR)?),
        ImageFormat::WebP => {
            let decoder = WebPDecoder::new(input).map_err(|_| IMAGE_ERROR)?;
            validate_dimensions(decoder.dimensions())?;
            if decoder.has_animation() {
                return Err("Animated logos are not supported.");
            }
            Box::new(decoder)
        }
        _ => return Err("Logo must be a real image file."),
    };
    validate_dimensions(decoder.dimensions())?;
    limits.max_image_width = Some(3000);
    limits.max_image_height = Some(3000);
    decoder.set_limits(limits).map_err(|_| IMAGE_ERROR)?;
    let profile = decoder.icc_profile().map_err(|_| IMAGE_ERROR)?;
    let image = DynamicImage::from_decoder(decoder).map_err(|_| IMAGE_ERROR)?;
    let image = if image.color().has_alpha() {
        DynamicImage::ImageRgba8(image.into_rgba8())
    } else {
        DynamicImage::ImageRgb8(image.into_rgb8())
    };
    let mut png = Vec::new();
    let mut encoder = PngEncoder::new(&mut png);
    if let Some(profile) = profile {
        encoder.set_icc_profile(profile).map_err(|_| IMAGE_ERROR)?;
    }
    image.write_with_encoder(encoder).map_err(|_| IMAGE_ERROR)?;
    Ok(png)
}

fn validate_png_metadata(bytes: &[u8]) -> Result<bool, &'static str> {
    // A PNG with one animation frame is static only if that frame is also the default image.
    let mut chunks = bytes.get(8..).ok_or(IMAGE_ERROR)?;
    let mut one_frame = false;
    let mut frame_precedes_image = false;
    while let Some(header) = chunks.get(..8) {
        let length = u32::from_be_bytes(header[..4].try_into().unwrap_or_default()) as usize;
        let end = length.checked_add(12).ok_or(IMAGE_ERROR)?;
        let chunk = chunks.get(..end).ok_or(IMAGE_ERROR)?;
        match &header[4..] {
            b"acTL" => one_frame = length == 8 && chunk[8..12] == 1u32.to_be_bytes(),
            b"fcTL" => frame_precedes_image = true,
            b"iCCP" => {
                let data = &chunk[8..end - 4];
                let name_end = data.iter().position(|byte| *byte == 0).ok_or(IMAGE_ERROR)?;
                let compressed = data[name_end + 1..].strip_prefix(&[0]).ok_or(IMAGE_ERROR)?;
                let mut plain = ZlibDecoder::new(compressed).take(MAX_PNG_PROFILE_BYTES + 1);
                if io::copy(&mut plain, &mut io::sink()).map_err(|_| IMAGE_ERROR)?
                    > MAX_PNG_PROFILE_BYTES
                {
                    return Err(IMAGE_ERROR);
                }
            }
            b"IDAT" => return Ok(one_frame && frame_precedes_image),
            _ => (),
        }
        chunks = &chunks[end..];
    }
    Err(IMAGE_ERROR)
}

fn validate_dimensions((width, height): (u32, u32)) -> Result<(), &'static str> {
    if width == 0 || height == 0 {
        return Err("Logo has invalid dimensions.");
    }
    if u64::from(width) * u64::from(height) > 9_000_000 {
        return Err("Logo must be a real image file.");
    }
    if width > 3000 || height > 3000 {
        return Err("Logo dimensions must be at most 3000x3000.");
    }
    if width * 2 < height || height * 2 < width {
        return Err("Logo aspect ratio must be between 1:2 and 2:1.");
    }
    Ok(())
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
