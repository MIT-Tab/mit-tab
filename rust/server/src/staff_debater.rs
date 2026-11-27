use std::collections::BTreeMap;

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::{Value, context};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App,
    board_snapshot::Identity,
    legacy_html,
    legacy_session::Actor,
    login::TemplateCsrf,
    registration_form::Fields,
    staff_roster_data::{Error, Kind},
    staff_roster_fields::{apda_id_error, checkbox, email_errors, input, name_error, select},
    staff_roster_notice, unavailable,
};

#[derive(Default)]
struct Debater {
    name: String,
    email: String,
    novice_status: String,
    needs_crash_housing: bool,
    requests_wgm_housing: bool,
    qualified: bool,
    apda_id: String,
    school: String,
}

type DebaterRow = (
    String,
    Option<String>,
    i32,
    bool,
    bool,
    bool,
    Option<i32>,
    Option<i32>,
);

impl From<DebaterRow> for Debater {
    fn from(row: DebaterRow) -> Self {
        let (
            name,
            email,
            novice_status,
            needs_crash_housing,
            requests_wgm_housing,
            qualified,
            apda_id,
            school,
        ) = row;
        Self {
            name,
            email: email.unwrap_or_default(),
            novice_status: novice_status.to_string(),
            needs_crash_housing,
            requests_wgm_housing,
            qualified,
            apda_id: optional_id(apda_id),
            school: optional_id(school),
        }
    }
}

fn optional_id(id: Option<i32>) -> String {
    id.map_or_else(String::new, |id| id.to_string())
}

impl From<&Fields> for Debater {
    fn from(fields: &Fields) -> Self {
        let value = |key| fields.all(key).last().cloned().unwrap_or_default();
        Self {
            name: value("name"),
            email: value("email"),
            novice_status: value("novice_status"),
            needs_crash_housing: fields.flag("needs_crash_housing"),
            requests_wgm_housing: fields.flag("requests_wgm_housing"),
            qualified: fields.flag("qualified"),
            apda_id: value("apda_id"),
            school: value("school"),
        }
    }
}

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
    let current = match id {
        Some(id) => sqlx::query_as::<_, DebaterRow>(
            "SELECT name, email, novice_status, needs_crash_housing, requests_wgm_housing,
                    qualified, apda_id, school_id FROM tab_debater WHERE id = ?",
        )
        .bind(id)
        .fetch_optional(&mut *db)
        .await?
        .map(Debater::from)
        .ok_or(Error::Missing)?,
        None => Debater {
            apda_id: "-1".into(),
            ..Debater::default()
        },
    };
    let path = id.map_or_else(|| "/enter_debater/".into(), |id| format!("/debater/{id}/"));
    let title = if id.is_some() {
        format!("Viewing Debater: {}", current.name)
    } else {
        "Create Debater:".into()
    };
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    let mut links = Vec::new();
    let mut team_schools = Vec::new();
    let mut debater_rounds = Vec::new();
    let mut ranking_groups = Vec::new();
    if let Some(id) = id {
        if shell
            .get_attr("can_data_entry")
            .is_ok_and(|value| value.is_true())
        {
            links.push((format!("/debater/{id}/delete/"), "Delete".to_owned()));
        }
        team_schools = teams(&mut db, id).await?;
        links.extend(team_schools.iter().map(|entry| {
            (
                format!("/team/{}/", entry.team.id),
                format!("View {}", entry.team.name),
            )
        }));
        if matches!(
            actor,
            Actor::User {
                superuser: true,
                board: false,
                ..
            }
        ) {
            debater_rounds = history(&mut db, id).await?;
            ranking_groups = sqlx::query_as::<_, (i32, String)>(
                "SELECT g.id, g.name FROM tab_rankinggroup g
                 JOIN tab_rankinggroup_debaters m ON m.rankinggroup_id = g.id
                 WHERE m.debater_id = ? ORDER BY g.id",
            )
            .bind(id)
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .map(|(id, name)| Identity { id, name })
            .collect();
        }
    }
    let has_hybrid_school = team_schools
        .iter()
        .any(|entry| entry.hybrid_school.is_some());
    let visible_fields = form_fields(&mut db, id, current, posted).await?;
    db.commit().await?;
    let mut response = staff_roster_notice::render(
        app,
        headers,
        &path,
        if id.is_some() {
            "tab/debater_detail.html"
        } else {
            "common/data_entry.html"
        },
        shell,
        context! {
            title, form => context! { visible_fields }, csrf_input => csrf.input(), return_path,
            debater_obj => id.is_some(), debater_rounds, team_schools, has_hybrid_school, ranking_groups, links,
        },
        if id.is_some() && posted.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
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
    current: Debater,
    posted: Option<(&Fields, &str)>,
) -> Result<Vec<Value>, sqlx::Error> {
    let schools =
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_school ORDER BY name")
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .map(|(id, name)| (id.to_string(), name))
            .collect::<Vec<_>>();
    let levels = [
        ("0".into(), "Varsity".into()),
        ("1".into(), "Novice".into()),
    ];
    let (
        mut invalid_name,
        mut invalid_email,
        mut invalid_level,
        mut invalid_apda_id,
        mut invalid_school,
    ) = (None, Vec::new(), None, None, None);
    let fields = if let Some((posted, message)) = posted {
        let form = Debater::from(posted);
        invalid_name = name_error(db, Kind::Debater, id, &form.name, message).await?;
        invalid_email = email_errors(form.email.trim());
        invalid_apda_id = apda_id_error(&form.apda_id);
        if form.novice_status.is_empty() {
            invalid_level = Some("This field is required.".into());
        } else if !matches!(form.novice_status.as_str(), "0" | "1") {
            invalid_level = Some(format!(
                "Select a valid choice. {} is not one of the available choices.",
                form.novice_status
            ));
        }
        if !form.school.trim().is_empty() {
            let selected = form.school.trim().parse::<i32>().map(|id| id.to_string());
            if !selected.is_ok_and(|selected| schools.iter().any(|(id, _)| *id == selected)) {
                invalid_school = Some(
                    "Select a valid choice. That choice is not one of the available choices."
                        .into(),
                );
            }
        }
        form
    } else {
        current
    };
    Ok(vec![
        input(
            "name",
            "Name:",
            "text",
            &fields.name,
            r#" maxlength="30""#,
            true,
            invalid_name,
        ),
        input(
            "email",
            "Email:",
            "email",
            &fields.email,
            r#" maxlength="254""#,
            false,
            invalid_email,
        ),
        select(
            "novice_status",
            "Novice status:",
            &levels,
            &fields.novice_status,
            true,
            true,
            invalid_level,
        ),
        checkbox(
            "needs_crash_housing",
            "Needs crash housing:",
            fields.needs_crash_housing,
        ),
        checkbox(
            "requests_wgm_housing",
            "Requests wgm housing:",
            fields.requests_wgm_housing,
        ),
        checkbox("qualified", "Qualified:", fields.qualified),
        input(
            "apda_id",
            "Apda id:",
            "number",
            &fields.apda_id,
            "",
            false,
            invalid_apda_id,
        ),
        select(
            "school",
            "School:",
            &schools,
            &fields.school,
            false,
            true,
            invalid_school,
        ),
    ])
}

#[derive(Serialize)]
struct TeamSchool {
    team: Identity,
    school: Identity,
    hybrid_school: Option<Identity>,
}

async fn teams(db: &mut MySqlConnection, debater: i32) -> Result<Vec<TeamSchool>, sqlx::Error> {
    let rows = sqlx::query_as::<_, (i32, String, i32, String, Option<i32>, Option<String>)>(
        "SELECT t.id, t.name, s.id, s.name, h.id, h.name FROM tab_team t
         JOIN tab_team_debaters m ON m.team_id = t.id JOIN tab_school s ON s.id = t.school_id
         LEFT JOIN tab_school h ON h.id = t.hybrid_school_id WHERE m.debater_id = ? ORDER BY t.id",
    )
    .bind(debater)
    .fetch_all(db)
    .await?;
    Ok(rows
        .into_iter()
        .map(
            |(id, name, school_id, school_name, hybrid_id, hybrid_name)| TeamSchool {
                team: Identity { id, name },
                school: Identity {
                    id: school_id,
                    name: school_name,
                },
                hybrid_school: hybrid_id
                    .zip(hybrid_name)
                    .map(|(id, name)| Identity { id, name }),
            },
        )
        .collect())
}

type Score = (
    i32,
    i32,
    String,
    String,
    Option<i32>,
    Option<String>,
    Option<i32>,
    Option<String>,
);

async fn history(db: &mut MySqlConnection, debater: i32) -> Result<Vec<Value>, sqlx::Error> {
    let scores = sqlx::query_as::<_, Score>(
        "SELECT r.id, r.round_number, CAST(s.speaks AS CHAR), CAST(s.ranks AS CHAR),
                m.id, m.name, c.id, c.name
         FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id
         LEFT JOIN tab_room m ON m.id = r.room_id LEFT JOIN tab_judge c ON c.id = r.chair_id
         WHERE s.debater_id = ? ORDER BY r.round_number, s.id",
    )
    .bind(debater)
    .fetch_all(&mut *db)
    .await?;
    let mut panels: BTreeMap<_, Vec<_>> = BTreeMap::new();
    if !scores.is_empty() {
        for (round, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
            "SELECT DISTINCT s.round_id, j.id, j.name
             FROM tab_roundstats s JOIN tab_round_judges m ON m.round_id = s.round_id
             JOIN tab_judge j ON j.id = m.judge_id WHERE s.debater_id = ? ORDER BY j.name, j.id",
        )
        .bind(debater)
        .fetch_all(db)
        .await?
        {
            panels.entry(round).or_default().push(Identity { id, name });
        }
    }
    Ok(scores
        .into_iter()
        .map(
            |(id, round_number, speaks, ranks, room_id, room_name, chair_id, chair_name)| {
                context! {
                    speaks, ranks, round => context! {
                        id, round_number, chair_id,
                        room => room_id.zip(room_name).map(|(id, name)| Identity { id, name }),
                        chair => chair_id.zip(chair_name).map(|(id, name)| Identity { id, name }),
                        judges => panels.get(&id).map(Vec::as_slice).unwrap_or_default(),
                    },
                }
            },
        )
        .collect())
}
