use std::collections::BTreeSet;

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{RawQuery, State},
    http::{HeaderMap, Method, StatusCode, Uri},
    response::Response,
    routing::get,
};
use chrono::{NaiveDate, NaiveDateTime, NaiveTime};
use minijinja::context;
use sqlx::{AssertSqlSafe, MySqlConnection, QueryBuilder, mysql::MySqlQueryResult};
use unicode_normalization::UnicodeNormalization;

use crate::{
    App, access,
    admin_auth_pages::{self as pages, AuthPage, Errors},
    admin_cascade::Cascade,
    admin_changelist::Query,
    admin_html,
    legacy_html::escape,
    legacy_session::{self, Actor, SessionError, cookie},
    login, native_session, passwords, private,
    registration_form::Fields,
    unavailable,
};

pub fn routes() -> Router<App> {
    let mut router = Router::new();
    for root in [USERS, GROUPS] {
        router = router.route(root, get(entry).post(entry));
        router = router.route(&format!("{root}add/"), get(entry).post(entry));
        for action in ["change", "delete"] {
            router = router.route(&format!("{root}{{id}}/{action}/"), get(entry).post(entry));
        }
        router = router.route(&format!("{root}{{id}}/history/"), get(entry));
    }
    router.route("/admin/auth/user/{id}/password/", get(entry).post(entry))
}

const USERS: &str = "/admin/auth/user/";
const GROUPS: &str = "/admin/auth/group/";

async fn entry(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    uri: Uri,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
    method: Method,
    body: Bytes,
) -> Response {
    let Actor::User {
        id: actor_id,
        board: false,
        staff: true,
        superuser: true,
    } = actor
    else {
        return access::access_denied();
    };
    let path = uri.path();
    let user = path.starts_with(USERS);
    let rest = path
        .strip_prefix(if user { USERS } else { GROUPS })
        .unwrap_or_default();
    let page = AuthPage {
        app: &app,
        actor,
        headers: &headers,
        path,
        query: Query::new(raw),
        user,
    };
    let (id, view) = if let Some((id, view)) = rest.trim_end_matches('/').split_once('/') {
        let Ok(id) = id.parse::<i32>() else {
            return missing();
        };
        (Some(id), view)
    } else {
        (None, rest.trim_end_matches('/'))
    };
    let post = (method == Method::POST).then(|| Fields::parse(&body));
    if let Some(post) = post.as_ref()
        && !login::valid_csrf(&headers, post.get("csrfmiddlewaretoken"))
    {
        return login::csrf_error();
    }
    if page
        .query
        .get("_to_field")
        .is_some_and(|field| !matches!(field, "" | "id"))
        || post
            .as_ref()
            .is_some_and(|post| !matches!(post.get("_to_field"), "" | "id"))
    {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }

    let result = match (view, id, post.as_ref()) {
        ("", None, None) => pages::listing(&page).await,
        ("", None, Some(post)) => delete(&page, actor_id, None, Some(post)).await,
        ("history", Some(id), None) => pages::history(&page, id).await,
        ("delete", Some(id), post) => delete(&page, actor_id, Some(id), post).await,
        ("add" | "change" | "password", _, None) => {
            pages::editor(&page, id, view == "password", None, &Errors::new()).await
        }
        ("add" | "change" | "password", _, Some(post)) => {
            let action = if view == "password" {
                Password
            } else if user {
                ChangeUser
            } else {
                Group
            };
            return submit(&page, actor_id, post, action, id).await;
        }
        _ => return missing(),
    };
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Clone, Copy, PartialEq)]
enum Change {
    User,
    Password,
    DeleteUser,
    Group,
    DeleteGroup,
}

impl Change {
    fn is_user(self) -> bool {
        matches!(self, Self::User | Self::Password | Self::DeleteUser)
    }
}

use Change::{DeleteGroup, DeleteUser, Group, Password, User as ChangeUser};

enum Error {
    Invalid(&'static str),
    Forbidden,
    Missing,
    Database(sqlx::Error),
}

impl From<sqlx::Error> for Error {
    fn from(error: sqlx::Error) -> Self {
        Self::Database(error)
    }
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Account or group not found."))
}

async fn submit(
    page: &AuthPage<'_>,
    actor: i32,
    fields: &Fields,
    change: Change,
    id: Option<i32>,
) -> Response {
    let canonical_add =
        matches!(change, ChangeUser) && id.is_none() && !fields.all("password1").is_empty();
    let password_form = change == Password || change == ChangeUser && id.is_none();
    let errors = validate_form(change, id, fields, canonical_add);
    if !errors.is_empty() {
        return pages::editor(page, id, change == Password, Some(fields), &errors)
            .await
            .unwrap_or_else(|_| unavailable());
    }
    let canonical = canonical_add || change == Password && !fields.all("password1").is_empty();
    let password = if password_form {
        let raw = fields
            .all(if canonical { "password1" } else { "password" })
            .last()
            .map_or("", String::as_str)
            .as_bytes()
            .to_owned();
        match tokio::task::spawn_blocking(move || passwords::encode(&raw)).await {
            Ok(Ok(hash)) => Some(hash),
            _ => return unavailable(),
        }
    } else {
        None
    };
    let result = store(
        page,
        actor,
        change,
        id,
        fields,
        canonical_add,
        password.as_deref(),
    )
    .await;
    match result {
        Ok((saved, log, session)) => {
            let mut response = if change == Password {
                admin_html::redirect_with_notice_for_session(
                    page.app,
                    page.headers,
                    format!("{USERS}{saved}/change/"),
                    log,
                    "password",
                    session
                        .as_deref()
                        .or_else(|| cookie(page.headers, "sessionid")),
                )
            } else {
                redirect(page, id, saved, log, fields, canonical_add)
            };
            if let Some(session) = session {
                login::set_cookie(
                    &mut response,
                    "sessionid",
                    &session,
                    native_session::MAX_AGE,
                    login::secure_request(page.headers),
                    true,
                );
            }
            response
        }
        Err(Error::Missing) => missing(),
        Err(Error::Forbidden) => access::access_denied(),
        Err(Error::Invalid(message)) => private((StatusCode::UNPROCESSABLE_ENTITY, message)),
        Err(Error::Database(sqlx::Error::Database(error))) if error.is_unique_violation() => {
            let errors = if page.user {
                Errors::from([(
                    "username",
                    "A user with that username already exists.".into(),
                )])
            } else {
                Errors::from([("name", "Group with this Name already exists.".into())])
            };
            pages::editor(page, id, change == Password, Some(fields), &errors)
                .await
                .unwrap_or_else(|_| unavailable())
        }
        Err(Error::Database(_)) => unavailable(),
    }
}

fn validate_form(change: Change, id: Option<i32>, post: &Fields, canonical_add: bool) -> Errors {
    let mut errors = Errors::new();
    let check = |value: &str, max: usize, required: bool| {
        let len = value.chars().count();
        if required && value.is_empty() {
            Some("This field is required.".into())
        } else if len > max {
            Some(format!(
                "Ensure this value has at most {max} characters (it has {len})."
            ))
        } else if value.contains('\0') {
            Some("Null characters are not allowed.".into())
        } else {
            None
        }
    };
    if matches!(change, ChangeUser | Group) {
        let (field, value) = if change == ChangeUser {
            ("username", username(post))
        } else {
            ("name", post.get("name").to_owned())
        };
        if let Some(error) = check(&value, 150, true) {
            errors.insert(field, error);
        } else if change == ChangeUser
            && !value
                .chars()
                .all(|c| c.is_alphanumeric() || "_@.+-".contains(c))
        {
            errors.insert(field, "Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters.".into());
        }
    }
    if change == ChangeUser && !canonical_add {
        for (field, limit) in [("first_name", 150), ("last_name", 150), ("email", 254)] {
            let value = post.get(field);
            if let Some(error) = check(value, limit, false) {
                errors.insert(field, error);
            } else if field == "email"
                && !value.is_empty()
                && !crate::admin_model_data::valid_email(value)
            {
                errors.insert(field, "Enter a valid email address.".into());
            }
        }
        if id.is_some() {
            for (field, required) in [("last_login", false), ("date_joined", true)] {
                if let Err(message) = submitted_date(post, field, required) {
                    errors.insert(field, message.into());
                }
            }
        }
    }
    if change == Password || change == ChangeUser && id.is_none() {
        let canonical =
            canonical_add || !post.all("password1").is_empty() || !post.all("password2").is_empty();
        let raw = |field| post.all(field).last().map_or("", String::as_str);
        let password1 = raw(if canonical { "password1" } else { "password" });
        let password2 = raw(if canonical {
            "password2"
        } else {
            "confirmation"
        });
        if password1.is_empty() {
            errors.insert("password1", "This field is required.".into());
        }
        if password2.is_empty() {
            errors.insert("password2", "This field is required.".into());
        } else if !password1.is_empty() && password1 != password2 {
            errors.insert("password2", "The two password fields didn’t match.".into());
        } else if password2.chars().count() < 8 {
            errors.insert(
                "password2",
                "This password is too short. It must contain at least 8 characters.".into(),
            );
        } else if password2
            .chars()
            .all(|character| character.is_ascii_digit())
        {
            errors.insert("password2", "This password is entirely numeric.".into());
        } else if password2.chars().count() > 256 || password2.contains('\0') {
            errors.insert(
                "password2",
                "Enter a password of at most 256 characters without null characters.".into(),
            );
        }
    }
    errors
}

type DateSubmission = Option<Option<NaiveDateTime>>;
fn submitted_date(
    post: &Fields,
    field: &str,
    required: bool,
) -> Result<DateSubmission, &'static str> {
    let date_field = format!("{field}_0");
    let time_field = format!("{field}_1");
    let date = post.get(&date_field);
    let time = post.get(&time_field);
    if post.all(&date_field).is_empty() && post.all(&time_field).is_empty() {
        return Ok(None);
    }
    if date.is_empty() && time.is_empty() {
        return if required {
            Err("This field is required.")
        } else {
            Ok(Some(None))
        };
    }
    let date = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]
        .iter()
        .find_map(|format| NaiveDate::parse_from_str(date, format).ok())
        .ok_or("Enter a valid date.")?;
    let time = ["%H:%M:%S", "%H:%M", "%H:%M:%S%.f"]
        .iter()
        .find_map(|format| NaiveTime::parse_from_str(time, format).ok())
        .ok_or("Enter a valid time.")?;
    Ok(Some(Some(date.and_time(time))))
}

fn username(post: &Fields) -> String {
    post.get("username").nfkc().collect()
}

fn redirect(
    page: &AuthPage<'_>,
    id: Option<i32>,
    saved: i32,
    log: u64,
    fields: &Fields,
    canonical_add: bool,
) -> Response {
    let root = page.model().url();
    let filters = page.query.get("_changelist_filters").unwrap_or_default();
    let suffix = admin_html::preserved_filters(filters);
    let list_suffix = url::form_urlencoded::Serializer::new(String::new())
        .extend_pairs(url::form_urlencoded::parse(filters.as_bytes()))
        .finish();
    if !fields.all("_popup").is_empty() {
        let description = if page.user {
            username(fields)
        } else {
            fields.get("name").to_owned()
        };
        return crate::admin_model_forms::popup_response(
            id.map(|_| "change"),
            saved,
            Some(&description),
        );
    }
    let (destination, notice) = if !fields.all("_addanother").is_empty() {
        (format!("{root}add/{suffix}"), "another")
    } else if canonical_add || !fields.all("_continue").is_empty() {
        (format!("{root}{saved}/change/{suffix}"), "continue")
    } else if list_suffix.is_empty() {
        (root, "save")
    } else {
        (format!("{root}?{list_suffix}"), "save")
    };
    admin_html::redirect_with_notice(page.app, page.headers, destination, log, notice)
}

async fn admin_lock(
    db: &mut MySqlConnection,
    page: &AuthPage<'_>,
    actor: i32,
) -> Result<(), Error> {
    // Account writes serialize against revocation, privilege edits, and password changes.
    sqlx::query_scalar::<_, i32>(
        "SELECT id FROM auth_user WHERE is_superuser = TRUE ORDER BY id FOR UPDATE",
    )
    .fetch_all(&mut *db)
    .await?;
    let session = cookie(page.headers, "sessionid").ok_or(Error::Forbidden)?;
    let current = legacy_session::actor_on(db, session, page.app.django_secret.as_deref()).await;
    match current {
        Ok(Actor::User {
            id,
            board: false,
            staff: true,
            superuser: true,
        }) if id == actor => Ok(()),
        Err(SessionError::Database) => {
            Err(sqlx::Error::Protocol("Could not validate the administrator session".into()).into())
        }
        _ => Err(Error::Forbidden),
    }
}

async fn store(
    page: &AuthPage<'_>,
    actor: i32,
    change: Change,
    id: Option<i32>,
    fields: &Fields,
    canonical_add: bool,
    password: Option<&str>,
) -> Result<(i32, u64, Option<String>), Error> {
    let mut db = page.app.db.begin().await?;
    admin_lock(&mut db, page, actor).await?;
    let old = match id {
        Some(id) if change != Password => editable_values(&mut db, page.user, id).await?,
        _ => Vec::new(),
    };
    let owner = if change.is_user() {
        change_user(&mut db, actor, change, id, fields, password, canonical_add).await?
    } else {
        change_group(&mut db, change, id, fields).await?
    };
    let current = editable_values(&mut db, page.user, owner).await?;
    let description = &current[0];
    let (flag, message) = if change == Password {
        (
            2,
            serde_json::json!([{"changed": {"fields": ["password"]}}]).to_string(),
        )
    } else if id.is_none() {
        (1, r#"[{"added": {}}]"#.into())
    } else {
        let labels: &[&str] = if page.user {
            &[
                "Username",
                "First name",
                "Last name",
                "Email address",
                "Active",
                "Staff status",
                "Superuser status",
                "Last login",
                "Date joined",
                "Groups",
                "User permissions",
            ]
        } else {
            &["Name", "Permissions"]
        };
        let names: Vec<_> = old
            .iter()
            .zip(&current)
            .zip(labels)
            .filter_map(|((before, after), label)| (before != after).then_some(label))
            .collect();
        let message = if names.is_empty() {
            "[]".into()
        } else {
            serde_json::json!([{"changed": {"fields": names}}]).to_string()
        };
        (2, message)
    };
    let log = admin_html::log_action(
        &mut db,
        page.model().name,
        actor,
        owner,
        description,
        flag,
        message,
    )
    .await?;
    let session = if change == Password && actor == owner {
        Some(native_session::issue(&mut db, actor, password.expect("validated password")).await?)
    } else {
        None
    };
    db.commit().await?;
    Ok((owner, log, session))
}

async fn editable_values(
    db: &mut MySqlConnection,
    user: bool,
    id: i32,
) -> Result<Vec<String>, Error> {
    let mut values = if user {
        let Some(row) = pages::load_user(db, id).await? else {
            return Err(Error::Missing);
        };
        vec![
            row.1,
            row.2,
            row.3,
            row.4,
            row.6.to_string(),
            row.5.to_string(),
            row.7.to_string(),
            row.9.map(|date| date.to_string()).unwrap_or_default(),
            row.10.to_string(),
        ]
    } else {
        let name: String = sqlx::query_scalar("SELECT name FROM auth_group WHERE id = ?")
            .bind(id)
            .fetch_optional(&mut *db)
            .await?
            .ok_or(Error::Missing)?;
        vec![name]
    };
    let memberships: &[(&str, &str, &str)] = if user {
        &[
            ("auth_user_groups", "user_id", "group_id"),
            ("auth_user_user_permissions", "user_id", "permission_id"),
        ]
    } else {
        &[("auth_group_permissions", "group_id", "permission_id")]
    };
    for &(table, owner, member) in memberships {
        let ids: Vec<i32> = sqlx::query_scalar(AssertSqlSafe(format!(
            "SELECT {member} FROM {table} WHERE {owner} = ? ORDER BY {member}"
        )))
        .bind(id)
        .fetch_all(&mut *db)
        .await?;
        values.push(serde_json::to_string(&ids).expect("numeric ids"));
    }
    Ok(values)
}

async fn change_user(
    db: &mut MySqlConnection,
    actor: i32,
    change: Change,
    id: Option<i32>,
    fields: &Fields,
    password: Option<&str>,
    canonical_add: bool,
) -> Result<i32, Error> {
    if let Some(id) = id {
        let exists =
            sqlx::query_scalar::<_, i32>("SELECT id FROM auth_user WHERE id = ? FOR UPDATE")
                .bind(id)
                .fetch_optional(&mut *db)
                .await?;
        if exists.is_none() {
            return Err(Error::Missing);
        }
        if change == Password {
            sqlx::query("UPDATE auth_user SET password = ? WHERE id = ?")
                .bind(password)
                .bind(id)
                .execute(&mut *db)
                .await?;
            sqlx::query("DELETE FROM mittab_rust_session WHERE user_id = ?")
                .bind(id)
                .execute(db)
                .await?;
            return Ok(id);
        }
        if change == DeleteUser {
            if id == actor {
                return Err(Error::Invalid(
                    "Use a different administrator account to delete this user.",
                ));
            }
            delete_user(db, id).await?;
            return Ok(id);
        }
    }

    let username = username(fields);
    let first = if canonical_add {
        ""
    } else {
        fields.get("first_name")
    };
    let last = if canonical_add {
        ""
    } else {
        fields.get("last_name")
    };
    let email = if canonical_add {
        ""
    } else {
        fields.get("email")
    };
    let active = canonical_add || account_flag(fields, "is_active", "active");
    let staff = !canonical_add && account_flag(fields, "is_staff", "staff");
    let elevated = !canonical_add && account_flag(fields, "is_superuser", "elevated");
    let groups = if canonical_add {
        BTreeSet::new()
    } else {
        selected(fields, "groups")?
    };
    if id == Some(actor) {
        let board =
            sqlx::query_scalar::<_, i32>("SELECT id FROM auth_group WHERE name = 'APDA Board'")
                .fetch_optional(&mut *db)
                .await?;
        if !active || !staff || !elevated || board.is_some_and(|group| groups.contains(&group)) {
            return Err(Error::Invalid(
                "Use a different administrator account to change your access.",
            ));
        }
    }
    let result = if let Some(id) = id {
        sqlx::query("UPDATE auth_user SET username = ?, first_name = ?, last_name = ?, email = ?, is_staff = ?, is_active = ?, is_superuser = ? WHERE id = ?")
            .bind(&username).bind(first).bind(last).bind(email).bind(staff).bind(active).bind(elevated).bind(id)
            .execute(&mut *db).await?
    } else {
        sqlx::query("INSERT INTO auth_user (username, first_name, last_name, email, is_staff, is_active, is_superuser, password, date_joined)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, UTC_TIMESTAMP(6))")
            .bind(username).bind(first).bind(last).bind(email).bind(staff).bind(active).bind(elevated).bind(password)
            .execute(&mut *db).await?
    };
    let saved = saved_id(id, result)?;
    if id.is_some() {
        for (field, required) in [("last_login", false), ("date_joined", true)] {
            if let Some(date) = submitted_date(fields, field, required).map_err(Error::Invalid)? {
                sqlx::query(AssertSqlSafe(format!(
                    "UPDATE auth_user SET {field} = ? WHERE id = ?"
                )))
                .bind(date)
                .bind(saved)
                .execute(&mut *db)
                .await?;
            }
        }
    }
    let id = saved;
    apply_membership(
        db,
        "auth_user_groups",
        "user_id",
        "group_id",
        "auth_group",
        id,
        groups,
    )
    .await?;
    apply_membership(
        db,
        "auth_user_user_permissions",
        "user_id",
        "permission_id",
        "auth_permission",
        id,
        if canonical_add {
            BTreeSet::new()
        } else if !fields.all("user_permissions").is_empty() {
            selected(fields, "user_permissions")?
        } else if ["active", "staff", "elevated", "password"]
            .iter()
            .any(|name| !fields.all(name).is_empty())
        {
            selected(fields, "permissions")?
        } else {
            BTreeSet::new()
        },
    )
    .await?;
    Ok(id)
}

fn account_flag(fields: &Fields, canonical: &str, legacy: &str) -> bool {
    fields.flag(canonical) || fields.flag(legacy)
}

const AUTH_ATTRIBUTIONS: &[(&str, &str)] = &[
    ("tab_judge", "created_by_id"),
    ("tab_team", "created_by_id"),
    ("tab_scratch", "created_by_id"),
    ("tab_manualjudgeassignment", "created_by_id"),
    ("tab_auditevent", "user_id"),
];

const PROTECTED_GROUP_SQL: &str = "(name = 'APDA Board' OR name LIKE 'MIT-TAB: %')";

async fn delete_user(db: &mut MySqlConnection, id: i32) -> Result<(), sqlx::Error> {
    for table in [
        "auth_user_groups",
        "auth_user_user_permissions",
        "django_admin_log",
        "tab_usertournamentsetuppreference",
    ] {
        let query = format!("DELETE FROM {table} WHERE user_id = ?");
        sqlx::query(AssertSqlSafe(query))
            .bind(id)
            .execute(&mut *db)
            .await?;
    }
    for &(table, owner) in AUTH_ATTRIBUTIONS {
        let query = format!("UPDATE {table} SET {owner} = NULL WHERE {owner} = ?");
        sqlx::query(AssertSqlSafe(query))
            .bind(id)
            .execute(&mut *db)
            .await?;
    }
    sqlx::query("DELETE FROM auth_user WHERE id = ?")
        .bind(id)
        .execute(db)
        .await?;
    Ok(())
}

async fn change_group(
    db: &mut MySqlConnection,
    change: Change,
    id: Option<i32>,
    fields: &Fields,
) -> Result<i32, Error> {
    let previous: Option<(String, bool)> = if let Some(id) = id {
        let current = sqlx::query_as(AssertSqlSafe(format!(
            "SELECT name, {PROTECTED_GROUP_SQL} FROM auth_group WHERE id = ? FOR UPDATE"
        )))
        .bind(id)
        .fetch_optional(&mut *db)
        .await?
        .ok_or(Error::Missing)?;
        Some(current)
    } else {
        None
    };
    if change == DeleteGroup {
        if previous.as_ref().is_some_and(|(_, protected)| *protected) {
            return Err(Error::Invalid(
                "This group is used by MIT-Tab’s access rules.",
            ));
        }
        let id = id.ok_or(Error::Missing)?;
        sqlx::query("DELETE FROM auth_user_groups WHERE group_id = ?")
            .bind(id)
            .execute(&mut *db)
            .await?;
        sqlx::query("DELETE FROM auth_group_permissions WHERE group_id = ?")
            .bind(id)
            .execute(&mut *db)
            .await?;
        sqlx::query("DELETE FROM auth_group WHERE id = ?")
            .bind(id)
            .execute(db)
            .await?;
        return Ok(id);
    }
    let name = fields.get("name").trim();
    if name.is_empty() || name.chars().count() > 150 {
        return Err(Error::Invalid(
            "Enter a group name of up to 150 characters.",
        ));
    }
    if previous
        .as_ref()
        .is_some_and(|(old, protected)| *protected && old != name)
    {
        return Err(Error::Invalid(
            "This group’s name is used by MIT-Tab’s access rules.",
        ));
    }
    let result = if let Some(id) = id {
        sqlx::query("UPDATE auth_group SET name = ? WHERE id = ?")
            .bind(name)
            .bind(id)
            .execute(&mut *db)
            .await?
    } else {
        sqlx::query("INSERT INTO auth_group (name) VALUES (?)")
            .bind(name)
            .execute(&mut *db)
            .await?
    };
    let id = saved_id(id, result)?;
    apply_membership(
        db,
        "auth_group_permissions",
        "group_id",
        "permission_id",
        "auth_permission",
        id,
        selected(fields, "permissions")?,
    )
    .await?;
    Ok(id)
}

fn selected(fields: &Fields, name: &str) -> Result<BTreeSet<i32>, Error> {
    fields
        .all(name)
        .iter()
        .map(|value| {
            value
                .parse::<i32>()
                .map_err(|_| Error::Invalid("An invalid group or permission was selected."))
        })
        .collect()
}

fn saved_id(id: Option<i32>, result: MySqlQueryResult) -> Result<i32, Error> {
    match id {
        Some(id) => Ok(id),
        None => i32::try_from(result.last_insert_id())
            .map_err(|_| Error::Invalid("No more records can be added.")),
    }
}

async fn apply_membership(
    db: &mut MySqlConnection,
    table: &str,
    owner: &str,
    member: &str,
    source: &str,
    id: i32,
    selected: BTreeSet<i32>,
) -> Result<(), Error> {
    let before: BTreeSet<i32> = sqlx::query_scalar(AssertSqlSafe(format!(
        "SELECT {member} FROM {table} WHERE {owner} = ?"
    )))
    .bind(id)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    let clear = format!("DELETE FROM {table} WHERE {owner} = ? AND {member} = ?");
    for id_to_remove in before.difference(&selected) {
        sqlx::query(AssertSqlSafe(clear.clone()))
            .bind(id)
            .bind(id_to_remove)
            .execute(&mut *db)
            .await?;
    }
    let insert =
        format!("INSERT INTO {table} ({owner}, {member}) SELECT ?, id FROM {source} WHERE id = ?");
    for member in selected.difference(&before) {
        let saved = sqlx::query(AssertSqlSafe(insert.clone()))
            .bind(id)
            .bind(member)
            .execute(&mut *db)
            .await?;
        if saved.rows_affected() != 1 {
            return Err(Error::Invalid(
                "A selected group or permission no longer exists.",
            ));
        }
    }
    Ok(())
}

async fn delete(
    page: &AuthPage<'_>,
    actor: i32,
    id: Option<i32>,
    post: Option<&Fields>,
) -> Result<Response, sqlx::Error> {
    let model = page.model();
    let root = model.url();
    let bulk = id.is_none();
    if bulk && post.is_none_or(|post| post.get("action") != "delete_selected") {
        return Ok(access::found(root));
    }
    let confirmed =
        post.is_some_and(|post| post.get("post") == "yes" || !bulk && post.flag("confirm"));
    if !bulk && post.is_some() && !confirmed {
        return Ok(private((
            StatusCode::UNPROCESSABLE_ENTITY,
            "Confirm deletion before continuing.",
        )));
    }
    let mut db = page.app.db.begin().await?;
    if confirmed {
        match admin_lock(&mut db, page, actor).await {
            Ok(()) => {}
            Err(Error::Forbidden) => return Ok(access::access_denied()),
            Err(Error::Database(error)) => return Err(error),
            Err(_) => return Ok(unavailable()),
        }
    }
    let rows = if let Some(id) = id {
        let lock = if confirmed { " FOR UPDATE" } else { "" };
        sqlx::query_as(AssertSqlSafe(format!(
            "SELECT id, {} FROM {} WHERE id = ?{lock}",
            page.name_column(),
            page.table()
        )))
        .bind(id)
        .fetch_all(&mut *db)
        .await?
    } else {
        let post = post.expect("bulk form");
        if pages::Selection::read(page).is_none() {
            return Ok(access::found(format!("{root}?e=1")));
        }
        let Ok(ids) = selected(post, "_selected_action") else {
            return Ok(private((
                StatusCode::BAD_REQUEST,
                "Invalid record selection.",
            )));
        };
        pages::objects(
            &mut db,
            page,
            (confirmed || !post.flag("select_across")).then_some(&ids),
            confirmed,
        )
        .await?
    };
    if rows.is_empty() {
        return Ok(access::found(if bulk { &root } else { "/admin/" }));
    }
    let ids: Vec<_> = rows.iter().map(|(id, _)| *id).collect();
    let denied = if page.user {
        ids.contains(&actor)
            .then_some("Use a different administrator account to delete this user.")
    } else {
        let mut protected = QueryBuilder::new(format!(
            "SELECT EXISTS(SELECT 1 FROM auth_group WHERE {PROTECTED_GROUP_SQL} AND id IN ("
        ));
        let mut selected = protected.separated(", ");
        for id in &ids {
            selected.push_bind(*id);
        }
        let exists: bool = protected
            .push("))")
            .build_query_scalar()
            .fetch_one(&mut *db)
            .await?;
        exists.then_some("This group is used by MIT-Tab’s access rules.")
    };
    if confirmed && let Some(message) = denied {
        return Ok(private((StatusCode::UNPROCESSABLE_ENTITY, message)));
    }
    let cascade = Cascade::read(&mut db, page.table(), &ids, confirmed).await?;
    let mut attribution = Vec::new();
    if page.user {
        for &(table, owner) in AUTH_ATTRIBUTIONS {
            let mut query = QueryBuilder::new(format!(
                "SELECT id, {owner} FROM {table} WHERE {owner} IN ("
            ));
            let mut selected = query.separated(", ");
            for id in &ids {
                selected.push_bind(*id);
            }
            query.push(") ORDER BY id");
            if confirmed {
                query.push(" FOR UPDATE");
            }
            let records: Vec<(i32, i32)> = query.build_query_as().fetch_all(&mut *db).await?;
            attribution.push((table, records));
        }
    }
    let proof = cookie(page.headers, "sessionid").map(|session| {
        serde_json::to_vec(&(
            "admin account deletion",
            session,
            page.path,
            &rows,
            cascade.scope(),
            attribution,
        ))
        .expect("serializable selection")
    });
    let stale = confirmed
        && proof.as_ref().is_none_or(|proof| {
            let submitted = post.and_then(|post| hex::decode(post.get("mittab_delete_scope")).ok());
            submitted.is_none_or(|tag| {
                ring::hmac::verify(&page.app.admin_notice_key, proof, &tag).is_err()
            })
        });
    if confirmed && !stale {
        // A logout can revoke the session while this request waits on a dependent row.
        let session = cookie(page.headers, "sessionid").unwrap_or_default();
        if !legacy_session::lock_active(&mut db, session).await? {
            return Ok(access::access_denied());
        }
        let empty = Fields::default();
        let mut log = 0;
        for (id, name) in &rows {
            log = admin_html::log_action(&mut db, model.name, actor, *id, name, 3, String::new())
                .await?;
            let result = if page.user {
                change_user(&mut db, actor, DeleteUser, Some(*id), &empty, None, false).await
            } else {
                change_group(&mut db, DeleteGroup, Some(*id), &empty).await
            };
            match result {
                Ok(_) => {}
                Err(Error::Invalid(message)) => {
                    return Ok(private((StatusCode::UNPROCESSABLE_ENTITY, message)));
                }
                Err(Error::Database(error)) => return Err(error),
                _ => return Ok(unavailable()),
            }
        }
        db.commit().await?;
        if let Some(id) = id
            && post.is_some_and(|post| !post.all("_popup").is_empty())
        {
            return Ok(crate::admin_model_forms::popup_response(
                Some("delete"),
                id,
                None,
            ));
        }
        let filters = if bulk {
            String::new()
        } else {
            url::form_urlencoded::Serializer::new(String::new())
                .extend_pairs(url::form_urlencoded::parse(
                    page.query
                        .get("_changelist_filters")
                        .unwrap_or_default()
                        .as_bytes(),
                ))
                .finish()
        };
        let destination = if bulk && !page.query.params.is_empty() {
            format!("{root}{}", Query::encode(page.query.params.clone()))
        } else if filters.is_empty() {
            root
        } else {
            format!("{root}?{filters}")
        };
        let notice = if bulk {
            format!("bulk-{}", rows.len())
        } else {
            "delete".into()
        };
        return Ok(admin_html::redirect_with_notice(
            page.app,
            page.headers,
            destination,
            log,
            &notice,
        ));
    }
    db.commit().await?;
    let mut counts = vec![(model.plural, rows.len())];
    counts.extend(cascade.counts());
    let mut seen = BTreeSet::new();
    let title_case = admin_html::capitalized(model.singular);
    let summary = rows
        .iter()
        .map(|(id, name)| {
            let label = format!(
                "{title_case}: <a href=\"{root}{id}/change/\">{}</a>",
                escape(name)
            );
            cascade.rendered(page.table(), *id, &label, &mut seen)
        })
        .collect::<Vec<_>>()
        .join("\n");
    let item = if rows.len() == 1 {
        model.singular
    } else {
        model.plural
    };
    let protected = denied.map(|message| format!("\t<li>{}</li>", escape(message)));
    let token = proof
        .filter(|_| denied.is_none())
        .map(|proof| hex::encode(ring::hmac::sign(&page.app.admin_notice_key, &proof).as_ref()));
    let title = if stale {
        "Review updated deletion".into()
    } else if denied.is_some() {
        format!("Cannot delete {item}")
    } else {
        "Are you sure?".into()
    };
    let template = if bulk {
        "admin/delete_selected_confirmation.html"
    } else {
        "admin/delete_confirmation.html"
    };
    let mut response = page.render(template, context! {
        title, protected, delete_scope => token, media => admin_html::scripts(),
        model_count => counts, objects_name => item, object_name => model.singular,
        deleted_objects => &summary, deletable_objects => [&summary], action_checkbox_name => "_selected_action",
        queryset => ids.iter().map(|id| context! {pk => id}).collect::<Vec<_>>(),
        object => &rows[0].1, object_url => format!("{root}{}/", rows[0].0),
        is_popup => page.popup(post), is_popup_var => "_popup",
        to_field => page.query.get("_to_field"), to_field_var => "_to_field",
    }).await;
    if stale && response.status().is_success() {
        *response.status_mut() = StatusCode::CONFLICT;
    }
    Ok(response)
}
