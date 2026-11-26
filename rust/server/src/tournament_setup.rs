use std::collections::HashMap;

use axum::{
    Extension, Form,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::Utc;
use minijinja::context;
use serde::Serialize;
use sqlx::MySqlConnection;
use url::Url;

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    school_directory::{self, SchoolChoice},
    settings::{SettingValue, Settings, put},
    staff_pairing::lock_round,
    unavailable,
};

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

#[derive(Clone, Serialize)]
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
        let (mut fields, roles, complete) = data?;
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
        }
        if fields
            .get("host_school")
            .is_none_or(|value| value != "manual")
        {
            fields.remove("host_school_name");
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
        Ok(data) => render(actor, &headers, &data, None),
        Err(_) => unavailable(),
    }
}

fn render(actor: Actor, headers: &HeaderMap, data: &Data, error: Option<&'static str>) -> Response {
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let types: Vec<_> = TOURNAMENT_TYPES
        .iter()
        .enumerate()
        .map(|(index, &label)| (index.to_string(), label))
        .collect();
    let status = if error.is_some() {
        StatusCode::UNPROCESSABLE_ENTITY
    } else {
        StatusCode::OK
    };
    let title = if data.complete {
        "Tournament Basics"
    } else {
        "Start Tournament"
    };
    let response = html::render(
        "tournament_setup.html",
        context! {
            title, csrf, fields => data.fields, roles => data.roles, schools => data.schools, types,
            message => error, has_error => error.is_some(),
            nav => Navigation::new(actor, false),
        },
        status,
    );
    html::form_response(response, headers, &csrf)
}

pub async fn save(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(fields): Form<FormFields>,
) -> Response {
    if !login::valid_csrf(
        &headers,
        fields.get("csrfmiddlewaretoken").map_or("", String::as_str),
    ) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let Ok(mut data) = Data::load(&app).await else {
        return unavailable();
    };
    let schools = school_directory::by_code(&data.schools);
    let form = validate(&fields, &schools);
    let (values, roles) = match form {
        Ok(form) => form,
        Err(message) => {
            data.roles = custom_roles(&fields).unwrap_or_default();
            data.fields = fields;
            return render(actor, &headers, &data, Some(message));
        }
    };
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let existing = Settings::load(&mut db, &[COMPLETED, "tournament_date"]).await?;
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
        Ok::<_, sqlx::Error>(access::found(if completed { PATH } else { CHECKLIST }))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

struct Values<'a> {
    school: &'a str,
    kind: i32,
    speaks: i32,
    ranks: i32,
    link: String,
}

fn validate<'a>(
    fields: &'a FormFields,
    schools: &HashMap<&str, &'a str>,
) -> Result<(Values<'a>, Vec<Role>), &'static str> {
    if value(fields, "action") == "send_invite" {
        return Err("Send staff invitations from the Invite Staff page.");
    }
    for &key in TEXT_FIELDS {
        if !valid_length(value(fields, key), 200) {
            return Err(
                "Enter a tournament name and all four staff roles using at most 200 characters each.",
            );
        }
    }
    let school = match value(fields, "host_school") {
        "manual" => Some(value(fields, "host_school_name")),
        code => schools.get(code).copied(),
    }
    .filter(|name| valid_length(name, 200))
    .ok_or("Choose a host school or enter its name.")?;
    let kind = value(fields, "tournament_type")
        .parse::<i32>()
        .ok()
        .filter(|&index| (0..TOURNAMENT_TYPES.len() as i32).contains(&index))
        .ok_or("Choose a tournament type.")?;
    let choice = |key| {
        value(fields, key)
            .parse::<i32>()
            .ok()
            .filter(|&choice| matches!(choice, 1 | 2))
    };
    let speaks = choice("open_speaks").ok_or("Choose a speaker points policy.")?;
    let ranks = choice("open_ranks").ok_or("Choose a speaker ranks policy.")?;
    if !valid_length(value(fields, "ga_room"), 500) {
        return Err("Enter the general assembly room or link.");
    }
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
        })
        .ok_or("Enter a valid Facebook announcement URL.")?;
    let roles = custom_roles(fields)?;
    Ok((
        Values {
            school,
            kind,
            speaks,
            ranks,
            link: url.into(),
        },
        roles,
    ))
}

fn custom_roles(fields: &FormFields) -> Result<Vec<Role>, &'static str> {
    let total = match fields.get("custom_staff-TOTAL_FORMS") {
        Some(value) => value
            .parse::<usize>()
            .ok()
            .filter(|&count| count <= 100)
            .ok_or("Too many additional staff roles.")?,
        None => 0,
    };
    let mut roles = Vec::new();
    for index in 0..total {
        if truthy(value(fields, &format!("custom_staff-{index}-DELETE"))) {
            continue;
        }
        let title = value(fields, &format!("custom_staff-{index}-title"));
        let names = value(fields, &format!("custom_staff-{index}-names"));
        if title.is_empty() && names.is_empty() {
            continue;
        }
        if !valid_length(title, 100) || !valid_length(names, 500) {
            return Err("Enter a title and at least one name for each additional staff role.");
        }
        roles.push(Role {
            title: title.into(),
            names: names.into(),
        });
    }
    Ok(roles)
}

pub fn truthy(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn valid_length(value: &str, maximum: usize) -> bool {
    !value.is_empty() && value.chars().count() <= maximum
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
