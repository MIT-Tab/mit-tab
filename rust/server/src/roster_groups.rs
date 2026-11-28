use std::collections::BTreeSet;

use axum::{
    Extension, Router,
    extract::{Path, Request, State},
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
    routing::get,
};
use minijinja::{Value, context};
use serde::Serialize;
use sqlx::{AssertSqlSafe, MySqlConnection, mysql::MySqlQueryResult};

use crate::{
    App, access, legacy_html,
    legacy_session::{self, Actor, SessionError},
    login::{self, TemplateCsrf},
    registration_form::Fields,
    staff_pairing::lock_round_if_configured,
    staff_roster::{form_return_path, form_return_target, submitted_fields},
    staff_roster_fields::{input, multi_select, rank_input},
    staff_roster_notice, unavailable,
};

#[derive(Clone, Copy)]
enum GroupKind {
    RoomTag,
    RankingGroup,
}

use GroupKind::{RankingGroup, RoomTag};

struct MemberKind {
    label: &'static str,
    field: &'static str,
    roster: &'static str,
    relation: &'static str,
    member_id: &'static str,
}

const TAG_MEMBERS: &[MemberKind] = &[
    MemberKind {
        label: "Teams",
        field: "teams",
        roster: "tab_team",
        relation: "tab_team_required_room_tags",
        member_id: "team_id",
    },
    MemberKind {
        label: "Judges",
        field: "judges",
        roster: "tab_judge",
        relation: "tab_judge_required_room_tags",
        member_id: "judge_id",
    },
    MemberKind {
        label: "Rooms",
        field: "rooms",
        roster: "tab_room",
        relation: "tab_room_tags",
        member_id: "room_id",
    },
];

const RANK_MEMBERS: &[MemberKind] = &[
    MemberKind {
        label: "Teams",
        field: "teams",
        roster: "tab_team",
        relation: "tab_rankinggroup_teams",
        member_id: "team_id",
    },
    MemberKind {
        label: "Debaters",
        field: "debaters",
        roster: "tab_debater",
        relation: "tab_rankinggroup_debaters",
        member_id: "debater_id",
    },
];

impl GroupKind {
    fn list(self) -> &'static str {
        match self {
            RoomTag => "/manage-room-tags",
            RankingGroup => "/manage-ranking-groups",
        }
    }

    fn prefix(self) -> &'static str {
        match self {
            RoomTag => "/room-tag/",
            RankingGroup => "/ranking-group/",
        }
    }

    fn detail_title(self, record: Option<&Record>) -> String {
        match (self, record) {
            (RoomTag, Some(record)) => format!("Viewing Tag: {}", record.name),
            (RankingGroup, Some(record)) => format!("Viewing Ranking Group: {}", record.name),
            (RoomTag, None) => "Create New Tag".into(),
            (RankingGroup, None) => "Create Ranking Group".into(),
        }
    }

    fn noun(self) -> &'static str {
        match self {
            RoomTag => "Tag",
            RankingGroup => "Ranking group",
        }
    }

    fn template(self) -> &'static str {
        match self {
            RoomTag => "pairing/manage_room_tags.html",
            RankingGroup => "pairing/manage_ranking_groups.html",
        }
    }

    fn field(self) -> (&'static str, &'static str) {
        match self {
            RoomTag => ("tag", "Tag:"),
            RankingGroup => ("name", "Name:"),
        }
    }

    fn members(self) -> &'static [MemberKind] {
        match self {
            RoomTag => TAG_MEMBERS,
            RankingGroup => RANK_MEMBERS,
        }
    }

    fn id_column(self) -> &'static str {
        match self {
            RoomTag => "roomtag_id",
            RankingGroup => "rankinggroup_id",
        }
    }
}

pub fn routes() -> Router<App> {
    Router::new()
        .merge(group_routes(RoomTag))
        .merge(group_routes(RankingGroup))
}

fn group_routes(kind: GroupKind) -> Router<App> {
    Router::new()
        .route(kind.list(), get(list).post(create))
        .route(kind.prefix(), get(new).post(create))
        .route(
            &format!("{}{{id}}/", kind.prefix()),
            get(detail).post(update),
        )
        .layer(Extension(kind))
}

#[derive(Serialize)]
struct Record {
    id: i32,
    name: String,
    priority: String,
    team_count: i64,
    debater_count: i64,
}

async fn records(
    db: &mut MySqlConnection,
    kind: GroupKind,
    id: Option<i32>,
) -> Result<Vec<Record>, sqlx::Error> {
    let query = match kind {
        RoomTag => {
            "SELECT id, tag AS name, CAST(t.priority AS CHAR) AS priority,
                    0 AS team_count, 0 AS debater_count
             FROM tab_roomtag t WHERE (? IS NULL OR t.id = ?) ORDER BY t.priority DESC, t.id"
        }
        RankingGroup => {
            "SELECT g.id, g.name, '' AS priority,
                    (SELECT COUNT(*) FROM tab_rankinggroup_teams WHERE rankinggroup_id = g.id) AS team_count,
                    (SELECT COUNT(*) FROM tab_rankinggroup_debaters WHERE rankinggroup_id = g.id) AS debater_count
             FROM tab_rankinggroup g WHERE (? IS NULL OR g.id = ?) ORDER BY g.name, g.id"
        }
    };
    let rows = sqlx::query_as::<_, (i32, String, String, i64, i64)>(query)
        .bind(id)
        .bind(id)
        .fetch_all(db)
        .await?;
    Ok(rows
        .into_iter()
        .map(|(id, name, priority, team_count, debater_count)| Record {
            id,
            name,
            priority,
            team_count,
            debater_count,
        })
        .collect())
}

async fn members(
    db: &mut MySqlConnection,
    kind: GroupKind,
    id: Option<i32>,
) -> Result<Vec<Value>, sqlx::Error> {
    let mut sections = Vec::new();
    for member in kind.members() {
        let query = format!(
            "SELECT roster.id, roster.name, EXISTS(SELECT 1 FROM {} m
            WHERE m.{} = ? AND m.{} = roster.id) AS selected
            FROM {} roster ORDER BY roster.name, roster.id",
            member.relation,
            kind.id_column(),
            member.member_id,
            member.roster,
        );
        let rows = sqlx::query_as::<_, (i32, String, bool)>(AssertSqlSafe(query))
            .bind(id)
            .fetch_all(&mut *db)
            .await?;
        let mut options = Vec::with_capacity(rows.len());
        let mut selected = Vec::new();
        for (id, name, included) in rows {
            let id = id.to_string();
            if included {
                selected.push(id.clone());
            }
            options.push((id, name));
        }
        sections.push(multi_select(
            member.field,
            &format!("{}:", member.label),
            &options,
            &selected,
            None,
        ));
    }
    Ok(sections)
}

fn input_fields(kind: GroupKind, record: Option<&Record>) -> Vec<Value> {
    let (name, label) = kind.field();
    let value = record.map_or("", |record| record.name.as_str());
    let mut fields = vec![input(
        name,
        label,
        "text",
        value,
        r#" maxlength="255""#,
        true,
        None,
    )];
    if matches!(kind, RoomTag) {
        fields.push(input(
            "priority",
            "Priority:",
            "number",
            record.map_or("", |record| record.priority.as_str()),
            r#" step="0.01""#,
            true,
            None,
        ));
    }
    fields
}

enum Screen {
    List,
    Form(Option<i32>),
}

async fn list(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    page(&app, actor, kind, Screen::List, &uri, &headers).await
}

async fn new(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    page(&app, actor, kind, Screen::Form(None), &uri, &headers).await
}

async fn detail(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    Path(id): Path<i32>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    page(&app, actor, kind, Screen::Form(Some(id)), &uri, &headers).await
}

async fn page(
    app: &App,
    actor: Actor,
    kind: GroupKind,
    screen: Screen,
    uri: &Uri,
    headers: &HeaderMap,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let result = async {
        let mut db = app.db.begin().await?;
        let shell = legacy_html::shell(&mut db, actor, uri.path()).await?;
        let return_path = form_return_path(uri, headers, None);
        let (template, page, fields) = match screen {
            Screen::List => {
                let groups = records(&mut db, kind, None).await?;
                let page = match kind {
                    RoomTag => context! { room_tags => groups },
                    RankingGroup => context! { ranking_groups => groups },
                };
                (kind.template(), page, input_fields(kind, None))
            }
            Screen::Form(id) => {
                let record = match id {
                    Some(id) => records(&mut db, kind, Some(id)).await?.pop(),
                    None => None,
                };
                let title = kind.detail_title(record.as_ref());
                let mut fields = input_fields(kind, record.as_ref());
                fields.extend(members(&mut db, kind, record.map(|record| record.id)).await?);
                ("common/data_entry.html", context! { title }, fields)
            }
        };
        db.commit().await?;
        let mut response = staff_roster_notice::render(
            app,
            headers,
            uri.path(),
            template,
            shell,
            context! {
                form => context! { visible_fields => fields },
                csrf_input => csrf.input(),
                return_path,
                ..page
            },
            StatusCode::OK,
        );
        csrf.set_cookie(&mut response, headers);
        Ok::<_, sqlx::Error>(response)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn create(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    request: Request,
) -> Response {
    save(&app, actor, kind, None, request).await
}

async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    Path(id): Path<i32>,
    request: Request,
) -> Response {
    save(&app, actor, kind, Some(id), request).await
}

enum Change {
    Saved(String),
    Deleted,
}

enum SaveError {
    Invalid,
    PriorityRange,
    Missing,
    Forbidden,
    Session(SessionError),
    Database(sqlx::Error),
}

impl From<sqlx::Error> for SaveError {
    fn from(error: sqlx::Error) -> Self {
        Self::Database(error)
    }
}

async fn save(
    app: &App,
    actor: Actor,
    kind: GroupKind,
    id: Option<i32>,
    request: Request,
) -> Response {
    let headers = request.headers().clone();
    let uri = request.uri().clone();
    let fields = match submitted_fields(app, request).await {
        Ok(fields) => fields,
        Err(error) => return *error,
    };
    if !login::valid_csrf(&headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    let deleting = fields
        .all("_method")
        .last()
        .is_some_and(|value| value == "DELETE");
    let target = form_return_target(&uri, &headers, Some(&fields));
    let return_to = target.as_deref().unwrap_or(kind.list());
    let noun = kind.noun();
    let invalid = |text: &str| staff_roster_notice::failed(app, &headers, return_to, text);
    let error = match kind {
        RoomTag => "Error saving tag.",
        RankingGroup => "Error saving ranking group.",
    };
    match change(app, actor, &headers, kind, id, &fields, deleting).await {
        Ok(Change::Saved(name)) => {
            let verb = if id.is_some() { "updated" } else { "created" };
            staff_roster_notice::saved(
                app,
                &headers,
                kind.list(),
                &format!("{noun} {name} {verb} successfully"),
            )
        }
        Ok(Change::Deleted) => staff_roster_notice::saved(
            app,
            &headers,
            return_to,
            &format!("{noun} deleted successfully"),
        ),
        Err(SaveError::Missing) => invalid(&format!("{noun} does not exist")),
        Err(SaveError::Invalid) => invalid(error),
        Err(SaveError::PriorityRange) => invalid("Priority must be between 0 and 100."),
        Err(SaveError::Forbidden) => access::access_denied(),
        Err(SaveError::Session(error)) => error.response(),
        Err(SaveError::Database(sqlx::Error::Database(database_error)))
            if database_error.is_unique_violation() =>
        {
            invalid(error)
        }
        Err(SaveError::Database(_)) => unavailable(),
    }
}

async fn change(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    kind: GroupKind,
    id: Option<i32>,
    fields: &Fields,
    deleting: bool,
) -> Result<Change, SaveError> {
    let mut db = app.db.begin().await?;
    lock_round_if_configured(&mut db).await?;
    let exists = if let Some(id) = id {
        let query = match kind {
            RoomTag => "SELECT id FROM tab_roomtag WHERE id = ? FOR UPDATE",
            RankingGroup => "SELECT id FROM tab_rankinggroup WHERE id = ? FOR UPDATE",
        };
        sqlx::query_scalar::<_, i32>(query)
            .bind(id)
            .fetch_optional(&mut *db)
            .await?
            .is_some()
    } else {
        !deleting
    };
    confirm_writer(app, &mut db, headers, actor).await?;
    if !exists {
        return Err(SaveError::Missing);
    }
    if deleting {
        let id = id.ok_or(SaveError::Missing)?;
        for member in kind.members() {
            clear(&mut db, kind, member, id).await?;
        }
        let query = match kind {
            RoomTag => "DELETE FROM tab_roomtag WHERE id = ?",
            RankingGroup => "DELETE FROM tab_rankinggroup WHERE id = ?",
        };
        sqlx::query(query).bind(id).execute(&mut *db).await?;
        db.commit().await?;
        return Ok(Change::Deleted);
    }

    let name = fields.get(kind.field().0);
    if name.is_empty() || name.chars().count() > 255 || name.contains('\0') {
        return Err(SaveError::Invalid);
    }
    let mut invalid_priority = false;
    let id = match kind {
        RoomTag => {
            let value = rank_input(fields.get("priority")).map_err(|_| SaveError::Invalid)?;
            invalid_priority = value.starts_with('-');
            let result = if let Some(id) = id {
                sqlx::query("UPDATE tab_roomtag SET tag = ?, priority = ? WHERE id = ?")
                    .bind(name)
                    .bind(value)
                    .bind(id)
                    .execute(&mut *db)
                    .await?
            } else {
                sqlx::query("INSERT INTO tab_roomtag (tag, priority) VALUES (?, ?)")
                    .bind(name)
                    .bind(value)
                    .execute(&mut *db)
                    .await?
            };
            saved_id(id, result)?
        }
        RankingGroup => {
            let result = if let Some(id) = id {
                sqlx::query("UPDATE tab_rankinggroup SET name = ? WHERE id = ?")
                    .bind(name)
                    .bind(id)
                    .execute(&mut *db)
                    .await?
            } else {
                sqlx::query("INSERT INTO tab_rankinggroup (name) VALUES (?)")
                    .bind(name)
                    .execute(&mut *db)
                    .await?
            };
            saved_id(id, result)?
        }
    };
    for member in kind.members() {
        let ids: BTreeSet<i32> = fields
            .all(member.field)
            .iter()
            .map(|value| {
                value
                    .parse::<i32>()
                    .ok()
                    .filter(|id| id.to_string() == *value)
                    .ok_or(SaveError::Invalid)
            })
            .collect::<Result<_, _>>()?;
        set_members(&mut db, kind, member, id, &ids).await?;
    }
    if invalid_priority {
        return Err(SaveError::PriorityRange);
    }
    db.commit().await?;
    Ok(Change::Saved(name.into()))
}

async fn confirm_writer(
    app: &App,
    db: &mut MySqlConnection,
    headers: &HeaderMap,
    actor: Actor,
) -> Result<(), SaveError> {
    let Actor::User { id: requested, .. } = actor else {
        return Err(SaveError::Forbidden);
    };
    let session = legacy_session::cookie(headers, "sessionid").ok_or(SaveError::Forbidden)?;
    let current = legacy_session::actor_on(db, session, app.django_secret.as_deref())
        .await
        .map_err(SaveError::Session)?;
    let allowed = match current {
        Actor::User {
            id,
            board: false,
            superuser: true,
            ..
        } => id == requested,
        Actor::User {
            id,
            board: false,
            staff: false,
            ..
        } if id == requested => access::user_permission(db, id, "tab_settings.can_change").await?,
        _ => false,
    };
    if allowed {
        Ok(())
    } else {
        Err(SaveError::Forbidden)
    }
}

fn saved_id(id: Option<i32>, result: MySqlQueryResult) -> Result<i32, SaveError> {
    if let Some(id) = id {
        Ok(id)
    } else {
        i32::try_from(result.last_insert_id()).map_err(|_| SaveError::Invalid)
    }
}

async fn set_members(
    db: &mut MySqlConnection,
    kind: GroupKind,
    member: &MemberKind,
    id: i32,
    selected: &BTreeSet<i32>,
) -> Result<(), SaveError> {
    let MemberKind {
        member_id,
        relation,
        roster,
        ..
    } = member;
    let group_id = kind.id_column();
    let current: BTreeSet<_> = sqlx::query_scalar::<_, i32>(AssertSqlSafe(format!(
        "SELECT {member_id} FROM {relation} WHERE {group_id} = ?",
    )))
    .bind(id)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    let delete = format!("DELETE FROM {relation} WHERE {group_id} = ? AND {member_id} = ?");
    for removed in current.difference(selected) {
        sqlx::query(AssertSqlSafe(delete.as_str()))
            .bind(id)
            .bind(removed)
            .execute(&mut *db)
            .await?;
    }
    let insert = format!(
        "INSERT INTO {relation} ({group_id}, {member_id}) SELECT ?, id FROM {roster} WHERE id = ?",
    );
    for added in selected.difference(&current) {
        let result = sqlx::query(AssertSqlSafe(insert.as_str()))
            .bind(id)
            .bind(added)
            .execute(&mut *db)
            .await?;
        if result.rows_affected() != 1 {
            return Err(SaveError::Invalid);
        }
    }
    Ok(())
}

async fn clear(
    db: &mut MySqlConnection,
    kind: GroupKind,
    member: &MemberKind,
    id: i32,
) -> Result<(), sqlx::Error> {
    let query = format!(
        "DELETE FROM {} WHERE {} = ?",
        member.relation,
        kind.id_column()
    );
    sqlx::query(AssertSqlSafe(query))
        .bind(id)
        .execute(db)
        .await?;
    Ok(())
}
