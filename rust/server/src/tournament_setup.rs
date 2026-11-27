use std::collections::HashMap;

use axum::{
    Extension, Form, Json,
    extract::{FromRequest, Multipart, Request, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use chrono::Utc;
use sqlx::MySqlConnection;
use url::Url;

use crate::{
    App, access, legacy_html,
    legacy_session::Actor,
    login, private,
    school_directory::{self, SchoolChoice},
    settings::{SettingValue, Settings, put},
    staff_invites,
    staff_pairing::lock_round,
    unavailable,
};

#[path = "tournament_setup_form.rs"]
mod ui;

const PATH: &str = "/setup/start-tournament/";
pub const CHECKLIST: &str = "/setup/tournament-todo/";
const NAME: &str = "New Tournament";
const COMPLETED: &str = "start_tournament_wizard_completed";
const TEXT_FIELDS: &[&str] = &[
    "tournament_name",
    "tournament_director",
    "tab_director",
    "equity_officers",
    "tournament_observer",
];
const SETTINGS: &[&str] = &[
    "tournament_name",
    "host_school",
    "host_school_name",
    "tournament_type",
    "sanctioned_apda_tournament",
    "open_speaks",
    "open_ranks",
    COMPLETED,
    staff_invites::INVITE_COUNT,
    "tournament_director",
    "tab_director",
    "equity_officers",
    "tournament_observer",
    "tournament_date",
];

const TOURNAMENT_TYPES: &[&str] = &[
    "Regular points tournament",
    "Brandeis IV",
    "Yale IV",
    "NorthAms",
    "Expansion",
    "Worlds",
    "NAUDC",
    "ProAms",
    "Nationals",
    "Novice",
    "Gender Minority",
    "Online (no points)",
    "Online points tournament",
    "Online ProAms",
    "Online Gender Minority",
    "BIPOC",
];

type FormFields = HashMap<String, String>;

struct Role {
    title: String,
    names: String,
}

struct Data {
    fields: FormFields,
    roles: Vec<Role>,
    schools: Vec<SchoolChoice>,
    complete: bool,
}

impl Data {
    async fn load(app: &App) -> Result<Self, sqlx::Error> {
        let (schools, data) = tokio::join!(school_directory::choices(&app.db), own_data(app));
        let (fields, roles, complete) = data?;
        let mut schools = schools?;
        if let (Some(value), Some(name)) =
            (fields.get("host_school"), fields.get("host_school_name"))
            && !value.is_empty()
            && value != "manual"
            && !name.is_empty()
            && !schools.iter().any(|school| school.value == *value)
        {
            schools.push(SchoolChoice {
                value: value.clone(),
                name: name.clone(),
            });
            schools.sort_by_key(|school| school.name.to_lowercase());
        }
        Ok(Self {
            fields,
            roles,
            schools,
            complete,
        })
    }
}

async fn own_data(app: &App) -> Result<(FormFields, Vec<Role>, bool), sqlx::Error> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let complete = ["1", "true", "yes", "on"]
        .contains(&settings.string(COMPLETED, "1").to_ascii_lowercase().trim());
    let mut fields: FormFields = SETTINGS
        .iter()
        .map(|&key| (key.to_owned(), settings.string(key, "")))
        .collect();
    if fields["tournament_name"] == NAME {
        fields.remove("tournament_name");
    }
    fields.entry("tournament_type".into()).and_modify(|value| {
        if value.is_empty() {
            *value = "0".into();
        }
    });
    fields.insert(
        "sanctioned".into(),
        if settings.integer("sanctioned_apda_tournament", 1) == 1 {
            "on"
        } else {
            ""
        }
        .into(),
    );
    let ga = sqlx::query_scalar::<_, String>(
        "SELECT text FROM registration_infotext WHERE title = 'GA' ORDER BY id LIMIT 1",
    )
    .fetch_optional(&mut *db)
    .await?
    .filter(|text| text != "TBD")
    .unwrap_or_default();
    fields.insert("ga_room".into(), ga);
    if complete {
        let url = sqlx::query_scalar::<_, String>(
            "SELECT url FROM registration_infolink WHERE title = 'Facebook' ORDER BY id LIMIT 1",
        )
        .fetch_optional(&mut *db)
        .await?
        .unwrap_or_default();
        fields.insert("facebook_url".into(), url);
    }
    let roles = sqlx::query_as::<_, (String, String)>(
        "SELECT title, names FROM tab_tournamentstaffrole ORDER BY sort_order, id",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(title, names)| Role { title, names })
    .collect();
    db.commit().await?;
    Ok((fields, roles, complete))
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    match Data::load(&app).await {
        Ok(data) => render(&app, actor, &headers, &data, None, None).await,
        Err(_) => unavailable(),
    }
}

async fn render(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    data: &Data,
    bound: Option<(&FormFields, &ui::Errors)>,
    invite_bound: Option<(&FormFields, &ui::Errors)>,
) -> Response {
    let Ok(csrf) = login::TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let Ok(shell) = legacy_html::shell(&mut db, actor, PATH).await else {
        return unavailable();
    };
    if db.commit().await.is_err() {
        return unavailable();
    }
    let status = if bound.is_some() || invite_bound.is_some() {
        StatusCode::UNPROCESSABLE_ENTITY
    } else {
        StatusCode::OK
    };
    let invite_enabled = !data.complete
        && matches!(
            actor,
            Actor::User {
                superuser: true,
                board: false,
                ..
            }
        );
    let page = ui::page(data, invite_enabled, bound, invite_bound);
    let mut response = legacy_html::render(
        "tab/start_tournament.html",
        shell,
        minijinja::context! {csrf_input => csrf.input(), ..page},
        status,
    );
    csrf.set_cookie(&mut response, headers);
    response
}

pub async fn save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    request: Request,
) -> Response {
    let fields = match parse_form(&app, request).await {
        Ok(fields) => fields,
        Err(response) => return *response,
    };
    if !login::valid_csrf(
        &headers,
        fields.get("csrfmiddlewaretoken").map_or("", String::as_str),
    ) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let Ok(data) = Data::load(&app).await else {
        return unavailable();
    };
    let action = if fields.contains_key("wizard_action") {
        "wizard_action"
    } else {
        "action"
    };
    if value(&fields, action) == "send_invite" {
        if data.complete {
            return private((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "ok": false, "message": "Tournament setup is already complete."
                })),
            ));
        }
        let invite = staff_invites::InviteForm::setup(
            value(&fields, "invite-email"),
            value(&fields, "invite-username"),
            value(&fields, "invite-permission_preset"),
        );
        let mut errors = ui::Errors::new();
        for key in ["invite-email", "invite-username"] {
            if value(&fields, key).is_empty() {
                errors.insert(key.into(), "This field is required.".into());
            }
        }
        if !invite.email.is_empty() && !crate::registration_form::valid_email(&invite.email) {
            errors.insert("invite-email".into(), "Enter a valid email address.".into());
        }
        if let Some(message) = length_error(&invite.username, 150) {
            errors.insert("invite-username".into(), message);
        } else if !invite
            .username
            .chars()
            .all(|ch| ch.is_alphanumeric() || "@.+-_".contains(ch))
        {
            errors.insert("invite-username".into(), "Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters.".into());
        }
        if !invite.permission_preset.is_empty()
            && !staff_invites::ROLES
                .iter()
                .any(|(key, _, _)| *key == invite.permission_preset)
        {
            errors.insert(
                "invite-permission_preset".into(),
                choice_error(&invite.permission_preset),
            );
        }
        let count = if errors.is_empty() {
            match staff_invites::send(&app, &headers, actor, &invite, true).await {
                Ok(count) => Some(count),
                Err(error) => {
                    errors.insert("__all__".into(), error.into());
                    None
                }
            }
        } else {
            None
        };
        if headers
            .get("x-requested-with")
            .is_some_and(|value| value == "XMLHttpRequest")
        {
            let (status, body) = match count {
                Some(count) => (
                    StatusCode::OK,
                    serde_json::json!({"ok": true, "invite_count": count, "message": format!(
                        "Invite sent to {}. You can send another invite or continue setup.", invite.email
                    )}),
                ),
                None => {
                    let errors: serde_json::Map<_, _> = errors.iter().map(|(key, message)| {
                        (key.trim_start_matches("invite-").to_string(),
                        serde_json::json!([{"message": message, "code": invite_error_code(key, message)}]))
                    }).collect();
                    (
                        StatusCode::BAD_REQUEST,
                        serde_json::json!({"ok": false, "errors": errors}),
                    )
                }
            };
            return private((status, Json(body)));
        }
        return match count {
            Some(_) => access::found(PATH),
            None => render(&app, actor, &headers, &data, None, Some((&fields, &errors))).await,
        };
    }
    let mut schools = school_directory::by_code(&data.schools);
    if value(&data.fields, "host_school") == "manual" {
        schools.insert("manual", value(&data.fields, "host_school_name"));
    }
    let form = validate(&fields, &schools);
    let (values, roles) = match form {
        Ok(form) => form,
        Err(errors) => {
            return render(&app, actor, &headers, &data, Some((&fields, &errors)), None).await;
        }
    };
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let existing = Settings::load(&mut db, &[COMPLETED, "tournament_date"]).await?;
        let existing_roles: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM tab_tournamentstaffrole")
                .fetch_one(&mut *db)
                .await?;
        if existing_roles != values.initial_roles {
            db.rollback().await?;
            return Ok(None);
        }
        let completed = ["1", "true", "yes", "on"]
            .contains(&existing.string(COMPLETED, "1").to_ascii_lowercase().trim());
        for &key in TEXT_FIELDS {
            put(&mut db, key, SettingValue::Text(value(&fields, key))).await?;
        }
        put(
            &mut db,
            "host_school",
            SettingValue::Text(value(&fields, "host_school")),
        )
        .await?;
        put(
            &mut db,
            "host_school_name",
            SettingValue::Text(values.school),
        )
        .await?;
        put(
            &mut db,
            "tournament_type",
            SettingValue::Number(values.kind),
        )
        .await?;
        put(
            &mut db,
            "sanctioned_apda_tournament",
            SettingValue::Number(i32::from(truthy(value(&fields, "sanctioned")))),
        )
        .await?;
        put(&mut db, "open_speaks", SettingValue::Number(values.speaks)).await?;
        put(&mut db, "open_ranks", SettingValue::Number(values.ranks)).await?;
        if existing.string("tournament_date", "").is_empty() {
            let today = Utc::now().date_naive().to_string();
            put(&mut db, "tournament_date", SettingValue::Text(&today)).await?;
        }
        update_resources(&mut db, value(&fields, "ga_room"), &values.link).await?;
        sqlx::query("DELETE FROM tab_tournamentstaffrole")
            .execute(&mut *db)
            .await?;
        for (index, role) in roles.into_iter().enumerate() {
            sqlx::query(
                "INSERT INTO tab_tournamentstaffrole (title, names, sort_order) VALUES (?, ?, ?)",
            )
            .bind(role.title)
            .bind(role.names)
            .bind(index as u16)
            .execute(&mut *db)
            .await?;
        }
        put(&mut db, COMPLETED, SettingValue::Number(1)).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(Some(access::found(if completed {
            PATH
        } else {
            CHECKLIST
        })))
    }
    .await;
    match result {
        Ok(Some(response)) => response,
        Ok(None) => {
            let errors = ui::Errors::from([(
                "custom_staff-__all__".into(),
                "Other staff roles changed. Reload the page before saving.".into(),
            )]);
            render(&app, actor, &headers, &data, Some((&fields, &errors)), None).await
        }
        Err(_) => unavailable(),
    }
}

async fn parse_form(app: &App, request: Request) -> Result<FormFields, Box<Response>> {
    let multipart = request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .is_some_and(|kind| kind.trim().eq_ignore_ascii_case("multipart/form-data"));
    if !multipart {
        return Form::from_request(request, app)
            .await
            .map(|Form(fields)| fields)
            .map_err(|error| Box::new(error.into_response()));
    }
    let mut form = Multipart::from_request(request, app)
        .await
        .map_err(|error| Box::new(error.into_response()))?;
    let mut fields = FormFields::new();
    while let Some(field) = form
        .next_field()
        .await
        .map_err(|error| Box::new(error.into_response()))?
    {
        let Some(name) = field
            .name()
            .filter(|_| field.file_name().is_none())
            .map(str::to_owned)
        else {
            return Err(Box::new(private((
                StatusCode::BAD_REQUEST,
                "Expected a named form field.",
            ))));
        };
        let value = field
            .text()
            .await
            .map_err(|error| Box::new(error.into_response()))?;
        fields.insert(name, value);
    }
    Ok(fields)
}

struct Values<'a> {
    school: &'a str,
    kind: i32,
    speaks: i32,
    ranks: i32,
    link: String,
    initial_roles: i64,
}

fn validate<'a>(
    fields: &'a FormFields,
    schools: &HashMap<&str, &'a str>,
) -> Result<(Values<'a>, Vec<Role>), ui::Errors> {
    let mut errors = ui::Errors::new();
    for (key, maximum) in TEXT_FIELDS
        .iter()
        .map(|&key| (key, 200))
        .chain([("ga_room", 500), ("facebook_url", 500)])
    {
        let text = value(fields, key);
        if text.is_empty() {
            errors.insert(key.into(), "This field is required.".into());
        } else if let Some(message) = length_error(text, maximum) {
            errors.insert(key.into(), message);
        }
    }
    let school_code = value(fields, "host_school");
    let school = match school_code {
        "manual" if !value(fields, "host_school_name").is_empty() => {
            Some(value(fields, "host_school_name"))
        }
        code => schools.get(code).copied(),
    }
    .filter(|name| !name.is_empty() && name.chars().count() <= 200);
    if school.is_none() {
        errors.insert("host_school".into(), choice_error(school_code));
    }
    let mut choice = |key, range: std::ops::Range<i32>| {
        let text = value(fields, key);
        let number = text
            .parse::<i32>()
            .ok()
            .filter(|number| range.contains(number) && number.to_string() == text);
        if number.is_none() {
            errors.insert(key.into(), choice_error(text));
        }
        number
    };
    let kind = choice("tournament_type", 0..TOURNAMENT_TYPES.len() as i32);
    let speaks = choice("open_speaks", 1..3);
    let ranks = choice("open_ranks", 1..3);
    let original = value(fields, "facebook_url");
    let url = Url::parse(original)
        .ok()
        .or_else(|| Url::parse(&format!("https://{original}")).ok())
        .filter(|url| {
            matches!(url.scheme(), "http" | "https")
                && url.host_str().is_some()
                && url.username().is_empty()
                && url.password().is_none()
                && url.as_str().len() <= 500
        });
    if url.is_none() {
        errors
            .entry("facebook_url".into())
            .or_insert_with(|| "Enter a valid URL.".into());
    }
    let custom = custom_roles(fields, &mut errors);
    let (
        Some(school),
        Some(kind),
        Some(speaks),
        Some(ranks),
        Some(url),
        Some((initial_roles, roles)),
    ) = (school, kind, speaks, ranks, url, custom)
    else {
        return Err(errors);
    };
    if !errors.is_empty() {
        return Err(errors);
    }
    Ok((
        Values {
            school,
            kind,
            speaks,
            ranks,
            link: url.into(),
            initial_roles,
        },
        roles,
    ))
}

fn custom_roles(fields: &FormFields, errors: &mut ui::Errors) -> Option<(i64, Vec<Role>)> {
    let [total, initial] = ["custom_staff-TOTAL_FORMS", "custom_staff-INITIAL_FORMS"].map(|name| {
        fields
            .get(name)
            .and_then(|value| value.parse::<usize>().ok())
    });
    let (Some(total), Some(initial)) = (total, initial) else {
        errors.insert(
            "custom_staff-__all__".into(),
            "Reload the page to restore the staff form fields before saving.".into(),
        );
        return None;
    };
    if total > 2000 || initial > total {
        errors.insert(
            "custom_staff-__all__".into(),
            "Check the number of staff forms before saving.".into(),
        );
        return None;
    }
    let mut roles = Vec::new();
    for index in 0..total {
        if truthy(value(fields, &format!("custom_staff-{index}-DELETE"))) {
            continue;
        }
        let title_key = format!("custom_staff-{index}-title");
        let names_key = format!("custom_staff-{index}-names");
        if index < initial && (!fields.contains_key(&title_key) || !fields.contains_key(&names_key))
        {
            errors.insert(
                "custom_staff-__all__".into(),
                "Reload the page to restore the staff form fields before saving.".into(),
            );
            return None;
        }
        let title = value(fields, &title_key);
        let names = value(fields, &names_key);
        if title.is_empty() && names.is_empty() {
            continue;
        }
        for (key, error) in [title_key, names_key]
            .into_iter()
            .zip(role_errors(title, names))
        {
            if let Some(error) = error {
                errors.insert(key, error);
            }
        }
        roles.push(Role {
            title: title.into(),
            names: names.into(),
        });
    }
    Some((initial as i64, roles))
}

fn role_errors(title: &str, names: &str) -> [Option<String>; 2] {
    [
        length_error(title, 100).or_else(|| {
            (title.is_empty() && !names.is_empty()).then(|| "Enter a title for this role.".into())
        }),
        length_error(names, 500).or_else(|| {
            (names.is_empty() && !title.is_empty())
                .then(|| "Enter at least one name for this role.".into())
        }),
    ]
}

pub fn truthy(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn length_error(value: &str, maximum: usize) -> Option<String> {
    let count = value.chars().count();
    (count > maximum)
        .then(|| format!("Ensure this value has at most {maximum} characters (it has {count})."))
}

fn choice_error(value: &str) -> String {
    if value.is_empty() {
        "This field is required.".into()
    } else {
        format!("Select a valid choice. {value} is not one of the available choices.")
    }
}

fn invite_error_code(key: &str, message: &str) -> &'static str {
    if key == "__all__" {
        ""
    } else if message == "This field is required." {
        "required"
    } else if message.starts_with("Select a valid choice.") {
        "invalid_choice"
    } else if message.starts_with("Ensure this value has") {
        "max_length"
    } else {
        "invalid"
    }
}

fn value<'a>(fields: &'a FormFields, key: &str) -> &'a str {
    fields.get(key).map_or("", |value| value.trim())
}

async fn update_resources(
    db: &mut MySqlConnection,
    ga: &str,
    link: &str,
) -> Result<(), sqlx::Error> {
    let assembly = sqlx::query_scalar::<_, i32>(
        "SELECT id FROM registration_infotext WHERE title = 'GA' ORDER BY id LIMIT 1",
    )
    .fetch_optional(&mut *db)
    .await?;
    if let Some(id) = assembly {
        sqlx::query("UPDATE registration_infotext SET text = ?, is_active = TRUE, updated_at = UTC_TIMESTAMP(6) WHERE id = ?")
            .bind(ga).bind(id).execute(&mut *db).await?;
    } else {
        sqlx::query(
            "INSERT INTO registration_infotext
            (title, text, is_active, display_order, created_at, updated_at)
            VALUES ('GA', ?, TRUE, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
        )
        .bind(ga)
        .execute(&mut *db)
        .await?;
    }
    let announcement = sqlx::query_scalar::<_, i32>(
        "SELECT id FROM registration_infolink WHERE title = 'Facebook' ORDER BY id LIMIT 1",
    )
    .fetch_optional(&mut *db)
    .await?;
    if let Some(id) = announcement {
        sqlx::query("UPDATE registration_infolink SET url = ?, description = 'Tournament updates and announcements.',
                     is_active = TRUE, updated_at = UTC_TIMESTAMP(6) WHERE id = ?")
            .bind(link).bind(id).execute(&mut *db).await?;
    } else {
        sqlx::query("INSERT INTO registration_infolink
            (title, url, description, is_active, display_order, created_at, updated_at)
            VALUES ('Facebook', ?, 'Tournament updates and announcements.', TRUE, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))")
            .bind(link).execute(db).await?;
    }
    Ok(())
}
