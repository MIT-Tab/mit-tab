use std::collections::BTreeMap;

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::{Value, context};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, legacy_html,
    legacy_session::Actor,
    login::TemplateCsrf,
    registration_form::Fields,
    staff_roles,
    staff_roster_data::{self, CheckinSnapshot, Error, Kind, ROOM_CHECKIN_CONFLICT},
    staff_roster_fields::{checkbox, input, multi_select, name_error, rank_input},
    staff_roster_notice, unavailable,
};

pub async fn show(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: Option<i32>,
    return_path: &str,
    posted: Option<(&Fields, &str)>,
) -> Result<Response, Error> {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return Ok(unavailable());
    };
    let mut db = app.db.begin().await?;
    let (name, rank) = match id {
        Some(id) => sqlx::query_as::<_, (String, String)>(
            "SELECT name, CAST(`rank` AS CHAR) FROM tab_room WHERE id = ?",
        )
        .bind(id)
        .fetch_optional(&mut *db)
        .await?
        .ok_or(Error::Missing)?,
        None => (String::new(), String::new()),
    };
    let path = id.map_or_else(|| "/enter_room/".into(), |id| format!("/room/{id}/"));
    let title = if id.is_some() {
        format!("Viewing Room: {name}")
    } else {
        "Create Room".into()
    };
    let mut visible_fields = form_fields(&mut db, id, &name, &rank, posted).await?;
    let room_checkin_revision = if let Some(room) = id
        && staff_roles::can_check_in(&mut *db, actor).await?
    {
        let snapshot =
            staff_roster_data::checkin_snapshot(&mut db, Kind::Room, room, false).await?;
        let submitted = posted.map(|(fields, _)| fields).filter(|fields| {
            fields.flag("mittab_checkins_present")
                && fields.get("mittab_room_checkin_revision") == snapshot.revision
        });
        visible_fields.extend(checkin_fields(&snapshot, submitted, Fields::flag));
        Some(snapshot.revision)
    } else {
        None
    };
    let conflict = posted.is_some_and(|(_, message)| message == ROOM_CHECKIN_CONFLICT);
    let non_field_errors = conflict.then(|| {
        Value::from_safe_string(format!(
            r#"<ul class="errorlist nonfield"><li>{ROOM_CHECKIN_CONFLICT}</li></ul>"#
        ))
    });
    let mut room_rounds = Vec::new();
    let mut room_outrounds = Vec::new();
    // Django only includes history on an administrator's unbound form.
    if let Some(id) = id.filter(|_| {
        posted.is_none()
            && matches!(
                actor,
                Actor::User {
                    superuser: true,
                    board: false,
                    ..
                }
            )
    }) {
        (room_rounds, room_outrounds) = history(&mut db, id).await?;
    }
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    db.commit().await?;
    let mut response = staff_roster_notice::render(
        app,
        headers,
        &path,
        if id.is_some() {
            "tab/room_detail.html"
        } else {
            "common/data_entry.html"
        },
        shell,
        context! {
            title, form => context! { visible_fields, non_field_errors }, csrf_input => csrf.input(), return_path,
            room_rounds, room_outrounds, room_checkin_revision,
            links => id.map(|id| [(format!("/room/{id}/delete/"), "Delete")]),
        },
        if conflict {
            StatusCode::CONFLICT
        } else {
            StatusCode::OK
        },
    );
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}

async fn form_fields(
    db: &mut MySqlConnection,
    id: Option<i32>,
    name: &str,
    rank: &str,
    posted: Option<(&Fields, &str)>,
) -> Result<Vec<Value>, sqlx::Error> {
    let rows = sqlx::query_as::<_, (i32, String, bool)>(
        "SELECT t.id, t.tag, EXISTS(SELECT 1 FROM tab_room_tags r WHERE r.room_id = ? AND r.roomtag_id = t.id)
         FROM tab_roomtag t ORDER BY t.id",
    ).bind(id).fetch_all(&mut *db).await?;
    let mut selected_ids = Vec::new();
    let mut options = Vec::with_capacity(rows.len());
    for (id, tag, selected) in rows {
        if selected {
            selected_ids.push(id.to_string());
        }
        options.push((id.to_string(), tag));
    }
    let (mut invalid_name, mut invalid_rank, mut invalid_tags) = (None, None, None);
    let (name, rank, selected) = if let Some((fields, message)) = posted {
        let name = fields.all("name").last().map_or("", String::as_str);
        let rank = fields.all("rank").last().map_or("", String::as_str);
        let selected = fields.all("tags");
        invalid_name = name_error(db, Kind::Room, id, name, message).await?;
        invalid_rank = rank_input(rank).err().map(str::to_owned);
        if let Some(value) = selected
            .iter()
            .find(|value| value.trim().parse::<i64>().is_err())
        {
            invalid_tags = Some(format!("“{value}” is not a valid value."));
        } else if let Some(value) = selected
            .iter()
            .find(|value| !options.iter().any(|(id, _)| id == *value))
        {
            invalid_tags = Some(format!(
                "Select a valid choice. {value} is not one of the available choices."
            ));
        }
        (name, rank, selected)
    } else {
        (name, rank, selected_ids.as_slice())
    };
    Ok(vec![
        input(
            "name",
            "Name:",
            "text",
            name,
            r#" maxlength="30""#,
            true,
            invalid_name,
        ),
        input(
            "rank",
            "Rank:",
            "number",
            rank,
            r#" step="0.01""#,
            true,
            invalid_rank,
        ),
        multi_select("tags", "Tags:", &options, selected, invalid_tags),
    ])
}

pub(super) fn checkin_fields<'a>(
    snapshot: &'a CheckinSnapshot,
    posted: Option<&'a Fields>,
    selected_value: fn(&Fields, &str) -> bool,
) -> impl Iterator<Item = Value> + 'a {
    snapshot.rounds.clone().map(move |round| {
        let field = format!("checkin_{}", round - 1);
        let label = if round == 0 {
            "Checked in for outrounds?".into()
        } else {
            format!("Checked in for round {round}?")
        };
        let selected = posted.map_or_else(
            || snapshot.checked.contains(&round),
            |fields| selected_value(fields, &field),
        );
        checkbox(&field, &label, selected)
    })
}

#[derive(Serialize)]
struct Participant {
    id: i32,
    name: String,
}

type RoomRound = (
    i32,
    bool,
    i32,
    i32,
    String,
    i32,
    String,
    Option<i32>,
    Option<String>,
);

async fn history(
    db: &mut MySqlConnection,
    room: i32,
) -> Result<(Vec<Value>, Vec<Value>), sqlx::Error> {
    let rounds = sqlx::query_as::<_, RoomRound>(
        "SELECT r.id, FALSE AS elimination, r.round_number AS stage,
                g.id AS gov_id, g.name AS gov_name, o.id AS opp_id, o.name AS opp_name,
                c.id AS chair_id, c.name AS chair_name
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_judge c ON c.id = r.chair_id WHERE r.room_id = ?
         UNION ALL
         SELECT r.id, TRUE AS elimination, r.num_teams AS stage,
                g.id AS gov_id, g.name AS gov_name, o.id AS opp_id, o.name AS opp_name,
                c.id AS chair_id, c.name AS chair_name
         FROM tab_outround r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_judge c ON c.id = r.chair_id WHERE r.room_id = ?
         ORDER BY elimination, IF(elimination, 0, stage), id",
    ).bind(room).bind(room).fetch_all(&mut *db).await?;
    let mut panels: BTreeMap<_, Vec<_>> = BTreeMap::new();
    if !rounds.is_empty() {
        for (elimination, round, id, name) in sqlx::query_as::<_, (bool, i32, i32, String)>(
            "SELECT FALSE AS elimination, r.id AS round_id, j.id AS judge_id, j.name
             FROM tab_round r JOIN tab_round_judges m ON m.round_id = r.id
             JOIN tab_judge j ON j.id = m.judge_id WHERE r.room_id = ?
             UNION ALL
             SELECT TRUE, r.id, j.id, j.name FROM tab_outround r
             JOIN tab_outround_judges m ON m.outround_id = r.id
             JOIN tab_judge j ON j.id = m.judge_id WHERE r.room_id = ?
             ORDER BY name, judge_id",
        )
        .bind(room)
        .bind(room)
        .fetch_all(db)
        .await?
        {
            panels
                .entry((elimination, round))
                .or_default()
                .push(Participant { id, name });
        }
    }
    let mut prelim = Vec::new();
    let mut elimination = Vec::new();
    for (id, is_elimination, stage, gov_id, gov_name, opp_id, opp_name, chair_id, chair_name) in
        rounds
    {
        let value = context! {
            id, round_number => stage, num_teams => stage,
            gov_team => Participant { id: gov_id, name: gov_name },
            opp_team => Participant { id: opp_id, name: opp_name },
            chair_id,
            chair => chair_id.zip(chair_name).map(|(id, name)| Participant { id, name }),
            judges => panels.remove(&(is_elimination, id)).unwrap_or_default(),
        };
        if is_elimination {
            elimination.push(value);
        } else {
            prelim.push(value);
        }
    }
    Ok((prelim, elimination))
}
