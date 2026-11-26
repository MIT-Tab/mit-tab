use std::collections::BTreeSet;

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use minijinja::context;
use serde::Serialize;
use sqlx::{AssertSqlSafe, MySqlConnection, mysql::MySqlQueryResult};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private,
    registration_form::Fields,
    staff_pairing::lock_round,
    unavailable,
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

    fn label(self) -> &'static str {
        match self {
            RoomTag => "Room Tag",
            RankingGroup => "Ranking Group",
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
}

async fn records(db: &mut MySqlConnection, kind: GroupKind) -> Result<Vec<Record>, sqlx::Error> {
    let query = match kind {
        RoomTag => {
            "SELECT id, tag AS name, CAST(priority AS CHAR) AS priority FROM tab_roomtag ORDER BY priority DESC, tag, id"
        }
        RankingGroup => "SELECT id, name, '' AS priority FROM tab_rankinggroup ORDER BY name, id",
    };
    let rows = sqlx::query_as::<_, (i32, String, String)>(query)
        .fetch_all(db)
        .await?;
    Ok(rows
        .into_iter()
        .map(|(id, name, priority)| Record { id, name, priority })
        .collect())
}

#[derive(Serialize)]
struct MemberList {
    field: &'static str,
    label: &'static str,
    options: Vec<Member>,
}

#[derive(Serialize)]
struct Member {
    id: i32,
    name: String,
    selected: bool,
}

async fn members(
    db: &mut MySqlConnection,
    kind: GroupKind,
    id: Option<i32>,
) -> Result<Vec<MemberList>, sqlx::Error> {
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
        sections.push(MemberList {
            field: member.field,
            label: member.label,
            options: rows
                .into_iter()
                .map(|(id, name, selected)| Member { id, name, selected })
                .collect(),
        });
    }
    Ok(sections)
}

async fn list(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    headers: HeaderMap,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let Ok(records) = records(&mut db, kind).await else {
        return unavailable();
    };
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    let response = html::render(
        "roster_groups.html",
        context! {
            title => format!("Manage {}s", kind.label()), nav => Navigation::new(actor, true),
            label => kind.label(), priority => matches!(kind, RoomTag), prefix => kind.prefix(),
            back => kind.list(), records, csrf, list => true,
        },
        StatusCode::OK,
    );
    html::form_response(response, &headers, &csrf)
}

async fn new(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    headers: HeaderMap,
) -> Response {
    page(&app, actor, kind, None, &headers).await
}

async fn detail(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Extension(kind): Extension<GroupKind>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    page(&app, actor, kind, Some(id), &headers).await
}

async fn page(
    app: &App,
    actor: Actor,
    kind: GroupKind,
    id: Option<i32>,
    headers: &HeaderMap,
) -> Response {
    let Ok(mut db) = app.db.acquire().await else {
        return unavailable();
    };
    let record = if let Some(id) = id {
        let Ok(records) = records(&mut db, kind).await else {
            return unavailable();
        };
        let Some(record) = records.into_iter().find(|entry| entry.id == id) else {
            return not_found();
        };
        Some(record)
    } else {
        None
    };
    let Ok(members) = members(&mut db, kind, id).await else {
        return unavailable();
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let title = match &record {
        Some(record) => format!("Edit {}: {}", kind.label(), record.name),
        None => format!("Create {}", kind.label()),
    };
    let response = html::render(
        "roster_groups.html",
        context! {
            title, nav => Navigation::new(actor, true), label => kind.label(), record, members,
            priority => matches!(kind, RoomTag), prefix => kind.prefix(), back => kind.list(), csrf,
        },
        StatusCode::OK,
    );
    html::form_response(response, headers, &csrf)
}

async fn create(
    State(app): State<App>,
    Extension(kind): Extension<GroupKind>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    save(&app, kind, None, &headers, &body).await
}

async fn update(
    State(app): State<App>,
    Extension(kind): Extension<GroupKind>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    save(&app, kind, Some(id), &headers, &body).await
}

enum SaveError {
    Invalid(&'static str),
    Missing,
    Database(sqlx::Error),
}

impl From<sqlx::Error> for SaveError {
    fn from(error: sqlx::Error) -> Self {
        Self::Database(error)
    }
}

async fn save(
    app: &App,
    kind: GroupKind,
    id: Option<i32>,
    headers: &HeaderMap,
    body: &[u8],
) -> Response {
    let fields = Fields::parse(body);
    if !login::valid_csrf(headers, fields.get("csrfmiddlewaretoken")) {
        return login::csrf_error();
    }
    match change(app, kind, id, &fields).await {
        Ok(()) => access::found(kind.list()),
        Err(SaveError::Missing) => not_found(),
        Err(SaveError::Invalid(message)) => private((StatusCode::UNPROCESSABLE_ENTITY, message)),
        Err(SaveError::Database(sqlx::Error::Database(error))) if error.is_unique_violation() => {
            private((
                StatusCode::UNPROCESSABLE_ENTITY,
                "A ranking group already has that name.",
            ))
        }
        Err(SaveError::Database(_)) => unavailable(),
    }
}

fn not_found() -> Response {
    private((StatusCode::NOT_FOUND, "Group not found."))
}

async fn change(
    app: &App,
    kind: GroupKind,
    id: Option<i32>,
    fields: &Fields,
) -> Result<(), SaveError> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    if let Some(id) = id
        && !records(&mut db, kind)
            .await?
            .iter()
            .any(|record| record.id == id)
    {
        return Err(SaveError::Missing);
    }
    if fields.get("_method") == "DELETE" {
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
        return Ok(());
    }

    let name = fields
        .get(match kind {
            RoomTag => "tag",
            RankingGroup => "name",
        })
        .trim();
    if name.is_empty() || name.chars().count() > 255 {
        return Err(SaveError::Invalid("Enter a name up to 255 characters."));
    }
    let id = match kind {
        RoomTag => {
            let value = fields.get("priority").trim();
            if !valid_priority(value) {
                return Err(SaveError::Invalid(
                    "Enter a priority from 0 to 99.99 with at most two decimal places.",
                ));
            }
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
                    .parse()
                    .map_err(|_| SaveError::Invalid("An invalid roster member was selected."))
            })
            .collect::<Result<_, _>>()?;
        clear(&mut db, kind, member, id).await?;
        let query = format!(
            "INSERT INTO {} ({}, {}) SELECT ?, id FROM {} WHERE id = ?",
            member.relation,
            kind.id_column(),
            member.member_id,
            member.roster
        );
        for member_id in ids {
            let saved = sqlx::query(AssertSqlSafe(query.clone()))
                .bind(id)
                .bind(member_id)
                .execute(&mut *db)
                .await?;
            if saved.rows_affected() != 1 {
                return Err(SaveError::Invalid(
                    "A selected roster member no longer exists.",
                ));
            }
        }
    }
    db.commit().await?;
    Ok(())
}

fn saved_id(id: Option<i32>, result: MySqlQueryResult) -> Result<i32, SaveError> {
    if let Some(id) = id {
        Ok(id)
    } else {
        i32::try_from(result.last_insert_id())
            .map_err(|_| SaveError::Invalid("No more groups can be created."))
    }
}

fn valid_priority(value: &str) -> bool {
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    !whole.is_empty()
        && whole.bytes().all(|byte| byte.is_ascii_digit())
        && whole.parse::<u8>().is_ok_and(|number| number <= 99)
        && fraction.len() <= 2
        && fraction.bytes().all(|byte| byte.is_ascii_digit())
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
