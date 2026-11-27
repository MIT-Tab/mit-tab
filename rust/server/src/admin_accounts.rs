use std::collections::BTreeSet;

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::{AssertSqlSafe, MySqlConnection, mysql::MySqlQueryResult};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, passwords, private,
    registration_form::Fields,
    unavailable,
};

pub fn routes() -> Router<App> {
    Router::new()
        .route("/admin/auth/user/", get(users))
        .route("/admin/auth/user/add/", get(user_add).post(user_create))
        .route(
            "/admin/auth/user/{id}/change/",
            get(user_edit).post(user_update),
        )
        .route(
            "/admin/auth/user/{id}/password/",
            get(user_password).post(user_set_password),
        )
        .route(
            "/admin/auth/user/{id}/delete/",
            get(user_confirm_delete).post(user_delete),
        )
        .route("/admin/auth/group/", get(groups))
        .route("/admin/auth/group/add/", get(group_add).post(group_create))
        .route(
            "/admin/auth/group/{id}/change/",
            get(group_edit).post(group_update),
        )
        .route(
            "/admin/auth/group/{id}/delete/",
            get(group_confirm_delete).post(group_delete),
        )
}

const USERS: &str = "/admin/auth/user/";
const GROUPS: &str = "/admin/auth/group/";

#[derive(Deserialize)]
struct Filter {
    q: Option<String>,
    page: Option<u64>,
}

#[derive(Serialize)]
struct Row {
    id: i32,
    name: String,
    detail: String,
    elevated: bool,
    active: bool,
}

async fn users(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Query(filter): Query<Filter>,
) -> Response {
    listing(&app, actor, true, filter).await
}

async fn groups(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Query(filter): Query<Filter>,
) -> Response {
    listing(&app, actor, false, filter).await
}

async fn listing(app: &App, actor: Actor, users: bool, filter: Filter) -> Response {
    let query = filter.q.as_deref().unwrap_or_default().trim();
    let page = filter.page.unwrap_or(1).clamp(1, 1_000_000);
    let statement = if users {
        "SELECT id, username, email, is_superuser, is_active FROM auth_user
         WHERE LOCATE(?, username) > 0 OR LOCATE(?, email) > 0
         ORDER BY username, id LIMIT 51 OFFSET ?"
    } else {
        "SELECT id, name, '', FALSE, TRUE FROM auth_group
         WHERE LOCATE(?, name) > 0 OR LOCATE(?, name) > 0
         ORDER BY name, id LIMIT 51 OFFSET ?"
    };
    let fetched = sqlx::query_as::<_, (i32, String, String, bool, bool)>(statement)
        .bind(query)
        .bind(query)
        .bind((page - 1) * 50)
        .fetch_all(&app.db)
        .await;
    let Ok(fetched) = fetched else {
        return unavailable();
    };
    let next = (fetched.len() > 50).then_some(page + 1);
    let previous = (page > 1).then_some(page - 1);
    let rows: Vec<_> = fetched
        .into_iter()
        .take(50)
        .map(|(id, name, detail, elevated, active)| Row {
            id,
            name,
            detail,
            elevated,
            active,
        })
        .collect();
    let root = if users { USERS } else { GROUPS };
    let query_string: String = url::form_urlencoded::Serializer::new(String::new())
        .append_pair("q", query)
        .finish();
    html::render(
        "admin_accounts.html",
        context! {
            title => if users { "Users" } else { "Groups" }, nav => Navigation::new(actor, true),
            list => true, users, rows, root, query, query_string, next, previous, page,
        },
        StatusCode::OK,
    )
}

type UserRow = (i32, String, String, String, String, bool, bool, bool);

#[derive(Serialize)]
struct User {
    id: i32,
    name: String,
    first: String,
    last: String,
    email: String,
    staff: bool,
    active: bool,
    elevated: bool,
}

impl From<UserRow> for User {
    fn from((id, name, first, last, email, staff, active, elevated): UserRow) -> Self {
        Self {
            id,
            name,
            first,
            last,
            email,
            staff,
            active,
            elevated,
        }
    }
}

#[derive(Serialize)]
struct OptionRow {
    id: i32,
    label: String,
    selected: bool,
}

async fn permissions(
    db: &mut MySqlConnection,
    user: bool,
    id: Option<i32>,
) -> Result<Vec<OptionRow>, sqlx::Error> {
    let membership = if user {
        "auth_user_user_permissions"
    } else {
        "auth_group_permissions"
    };
    let column = if user { "user_id" } else { "group_id" };
    let query = format!(
        "SELECT p.id, CONCAT(c.app_label, ' · ', p.name), EXISTS(
            SELECT 1 FROM {membership} m WHERE m.{column} = ? AND m.permission_id = p.id
         ) FROM auth_permission p JOIN django_content_type c ON c.id = p.content_type_id
         ORDER BY c.app_label, c.model, p.codename, p.id",
    );
    Ok(
        sqlx::query_as::<_, (i32, String, bool)>(AssertSqlSafe(query))
            .bind(id)
            .fetch_all(db)
            .await?
            .into_iter()
            .map(|(id, label, selected)| OptionRow {
                id,
                label,
                selected,
            })
            .collect(),
    )
}

async fn user_add(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    user_form(&app, actor, &headers, None, "edit").await
}

async fn user_edit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    user_form(&app, actor, &headers, Some(id), "edit").await
}

async fn user_password(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    user_form(&app, actor, &headers, Some(id), "password").await
}

async fn user_confirm_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    user_form(&app, actor, &headers, Some(id), "delete").await
}

async fn user_form(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: Option<i32>,
    action: &str,
) -> Response {
    let result = async {
        let mut db = app.db.acquire().await?;
        let user = match id {
            Some(id) => {
                let row = sqlx::query_as::<_, UserRow>(
                    "SELECT id, username, first_name, last_name, email, is_staff, is_active, is_superuser
                     FROM auth_user WHERE id = ?",
                ).bind(id).fetch_optional(&mut *db).await?;
                let Some(row) = row else { return Ok(missing()) };
                Some(User::from(row))
            }
            None => None,
        };
        let groups = if action == "edit" {
            sqlx::query_as::<_, (i32, String, bool)>(
                "SELECT g.id, g.name, EXISTS(SELECT 1 FROM auth_user_groups m WHERE m.user_id = ? AND m.group_id = g.id)
                 FROM auth_group g ORDER BY g.name, g.id",
            ).bind(id).fetch_all(&mut *db).await?.into_iter()
                .map(|(id, label, selected)| OptionRow { id, label, selected }).collect()
        } else { Vec::new() };
        let permissions = if action == "edit" { permissions(&mut db, true, id).await? } else { Vec::new() };
        let Ok(csrf) = html::page_csrf(headers) else { return Ok(unavailable()) };
        let title = match action {
            "password" => "Change Password",
            "delete" => "Delete User",
            _ if id.is_some() => "Edit User",
            _ => "Add User",
        };
        let page = html::render("admin_accounts.html", context! {
            title, nav => Navigation::new(actor, true), csrf, user, users => true, root => USERS,
            memberships => groups, permissions, action,
            self_edit => matches!(actor, Actor::User { id: signed_in, .. } if Some(signed_in) == id),
        }, StatusCode::OK);
        Ok::<_, sqlx::Error>(html::form_response(page, headers, &csrf))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

async fn group_add(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    group_form(&app, actor, &headers, None, false).await
}

async fn group_edit(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    group_form(&app, actor, &headers, Some(id), false).await
}

async fn group_confirm_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    group_form(&app, actor, &headers, Some(id), true).await
}

async fn group_form(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: Option<i32>,
    delete: bool,
) -> Response {
    let result = async {
        let mut db = app.db.acquire().await?;
        let name = if let Some(id) = id {
            let name = sqlx::query_scalar::<_, String>("SELECT name FROM auth_group WHERE id = ?")
                .bind(id).fetch_optional(&mut *db).await?;
            let Some(name) = name else { return Ok(missing()) };
            name
        } else { String::new() };
        let permissions = if delete { Vec::new() } else { permissions(&mut db, false, id).await? };
        let Ok(csrf) = html::page_csrf(headers) else { return Ok(unavailable()) };
        let title = if delete { "Delete Group" } else if id.is_some() { "Edit Group" } else { "Add Group" };
        let response = html::render("admin_accounts.html", context! {
            title, nav => Navigation::new(actor, true), csrf, group => id, name, root => GROUPS, permissions,
            action => if delete { "delete" } else { "edit" }, protected_group => protected_group(&name),
        }, StatusCode::OK);
        Ok::<_, sqlx::Error>(html::form_response(response, headers, &csrf))
    }.await;
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

async fn user_create(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, &body, ChangeUser, None).await
}
async fn user_update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, &body, ChangeUser, Some(id)).await
}
async fn user_set_password(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, &body, Password, Some(id)).await
}
async fn user_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, &body, DeleteUser, Some(id)).await
}
async fn group_create(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, &body, Group, None).await
}
async fn group_update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, &body, Group, Some(id)).await
}
async fn group_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    submit(&app, actor, &headers, &body, DeleteGroup, Some(id)).await
}

enum Error {
    Invalid(&'static str),
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
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    body: &[u8],
    change: Change,
    id: Option<i32>,
) -> Response {
    let fields = Fields::parse(body);
    if !login::valid_csrf(headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let Actor::User { id: actor, .. } = actor else {
        return missing();
    };
    match store(app, actor, change, id, &fields).await {
        Ok(id) if change == Password => access::found(format!("{USERS}{id}/change/")),
        Ok(_) => access::found(if change.is_user() { USERS } else { GROUPS }),
        Err(Error::Missing) => missing(),
        Err(Error::Invalid(message)) => private((StatusCode::UNPROCESSABLE_ENTITY, message)),
        Err(Error::Database(sqlx::Error::Database(error))) if error.is_unique_violation() => {
            private((
                StatusCode::UNPROCESSABLE_ENTITY,
                "That name is already in use.",
            ))
        }
        Err(Error::Database(_)) => unavailable(),
    }
}

async fn store(
    app: &App,
    actor: i32,
    change: Change,
    id: Option<i32>,
    fields: &Fields,
) -> Result<i32, Error> {
    if matches!(change, DeleteGroup | DeleteUser) && !fields.flag("confirm") {
        return Err(Error::Invalid("Confirm deletion before continuing."));
    }
    let password = if change == Password || (change == ChangeUser && id.is_none()) {
        let password = fields.get("password");
        if !(8..=256).contains(&password.chars().count())
            || password != fields.get("confirmation")
            || password.chars().all(|character| character.is_ascii_digit())
        {
            return Err(Error::Invalid(
                "Enter a matching password of at least eight characters.",
            ));
        }
        let password = password.as_bytes().to_owned();
        Some(
            tokio::task::spawn_blocking(move || passwords::encode(&password))
                .await
                .ok()
                .and_then(Result::ok)
                .ok_or(Error::Invalid("Could not save the password."))?,
        )
    } else {
        None
    };

    let mut db = app.db.begin().await?;
    let owner = if change.is_user() {
        change_user(&mut db, actor, change, id, fields, password.as_deref()).await?
    } else {
        change_group(&mut db, change, id, fields).await?
    };
    db.commit().await?;
    Ok(owner)
}

async fn change_user(
    db: &mut MySqlConnection,
    actor: i32,
    change: Change,
    id: Option<i32>,
    fields: &Fields,
    password: Option<&str>,
) -> Result<i32, Error> {
    // Serialize account administration against other changes made by admins.
    sqlx::query_scalar::<_, i32>(
        "SELECT id FROM auth_user WHERE is_superuser = TRUE ORDER BY id FOR UPDATE",
    )
    .fetch_all(&mut *db)
    .await?;
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

    let username = fields.get("username").trim();
    if username.is_empty()
        || username.chars().count() > 150
        || !username
            .chars()
            .all(|c| c.is_alphanumeric() || "_@.+-".contains(c))
    {
        return Err(Error::Invalid(
            "Enter a valid username of up to 150 characters.",
        ));
    }
    let first = fields.get("first_name").trim();
    let last = fields.get("last_name").trim();
    let email = fields.get("email").trim();
    if first.chars().count() > 150
        || last.chars().count() > 150
        || email.chars().count() > 254
        || (!email.is_empty()
            && !email.split_once('@').is_some_and(|(local, domain)| {
                !local.is_empty() && !domain.is_empty() && !domain.contains('@')
            }))
        || email.contains(char::is_whitespace)
    {
        return Err(Error::Invalid("Enter a valid name and email address."));
    }
    let active = fields.flag("active");
    let staff = fields.flag("staff");
    let elevated = fields.flag("elevated");
    let groups = selected(fields, "groups")?;
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
            .bind(username).bind(first).bind(last).bind(email).bind(staff).bind(active).bind(elevated).bind(id)
            .execute(&mut *db).await?
    } else {
        sqlx::query("INSERT INTO auth_user (username, first_name, last_name, email, is_staff, is_active, is_superuser, password, date_joined)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, UTC_TIMESTAMP(6))")
            .bind(username).bind(first).bind(last).bind(email).bind(staff).bind(active).bind(elevated).bind(password)
            .execute(&mut *db).await?
    };
    let id = saved_id(id, result)?;
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
        selected(fields, "permissions")?,
    )
    .await?;
    Ok(id)
}

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
    for table in [
        "tab_judge",
        "tab_team",
        "tab_scratch",
        "tab_manualjudgeassignment",
    ] {
        let query = format!("UPDATE {table} SET created_by_id = NULL WHERE created_by_id = ?");
        sqlx::query(AssertSqlSafe(query))
            .bind(id)
            .execute(&mut *db)
            .await?;
    }
    sqlx::query("UPDATE tab_auditevent SET user_id = NULL WHERE user_id = ?")
        .bind(id)
        .execute(&mut *db)
        .await?;
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
    let previous = if let Some(id) = id {
        let name =
            sqlx::query_scalar::<_, String>("SELECT name FROM auth_group WHERE id = ? FOR UPDATE")
                .bind(id)
                .fetch_optional(&mut *db)
                .await?
                .ok_or(Error::Missing)?;
        Some(name)
    } else {
        None
    };
    if change == DeleteGroup {
        if previous.as_deref().is_some_and(protected_group) {
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
        .as_deref()
        .is_some_and(|old| protected_group(old) && old != name)
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

fn protected_group(name: &str) -> bool {
    name == "APDA Board" || name.starts_with("MIT-TAB: ")
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
    let clear = format!("DELETE FROM {table} WHERE {owner} = ?");
    sqlx::query(AssertSqlSafe(clear))
        .bind(id)
        .execute(&mut *db)
        .await?;
    let insert =
        format!("INSERT INTO {table} ({owner}, {member}) SELECT ?, id FROM {source} WHERE id = ?");
    for member in selected {
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
