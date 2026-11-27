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
    staff_roster_data::{Error, Kind},
    staff_roster_fields::{apda_id_error, input, name_error},
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
    let current = if let Some(id) = id {
        Some(
            sqlx::query_as::<_, (String, Option<i32>)>(
                "SELECT name, apda_id FROM tab_school WHERE id = ?",
            )
            .bind(id)
            .fetch_optional(&mut *db)
            .await?
            .ok_or(Error::Missing)?,
        )
    } else {
        None
    };
    let title = match &current {
        Some((name, _)) => format!("Viewing School: {name}"),
        None => "Create School".into(),
    };
    let (school_teams, school_hybrid_teams, school_judges) = if let Some(id) = id {
        let (teams, hybrids) = school_teams(&mut db, id).await?;
        let judges = sqlx::query_as::<_, (i32, String, String)>(
            "SELECT j.id, j.name, CAST(j.`rank` AS CHAR)
             FROM tab_judge j JOIN tab_judge_schools s ON s.judge_id = j.id
             WHERE s.school_id = ? ORDER BY j.name",
        )
        .bind(id)
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(|(id, name, rank)| context! { id, name, rank })
        .collect();
        (teams, hybrids, judges)
    } else {
        (Vec::new(), Vec::new(), Vec::new())
    };
    let path = id.map_or_else(|| "/enter_school/".into(), |id| format!("/school/{id}/"));
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    let apda_id = match &current {
        Some((_, value)) => value.map_or_else(String::new, |value| value.to_string()),
        None => "-1".into(),
    };
    let name = current.as_ref().map_or("", |(name, _)| name);
    let visible_fields = school_fields(&mut db, id, name, &apda_id, posted).await?;
    db.commit().await?;
    let mut response = staff_roster_notice::render(
        app,
        headers,
        &path,
        if id.is_some() {
            "tab/school_detail.html"
        } else {
            "common/data_entry.html"
        },
        shell,
        context! {
            title, form => context! { visible_fields }, csrf_input => csrf.input(), return_path,
            school_teams, school_hybrid_teams, school_judges,
            links => id.map(|id| [(format!("/school/{id}/delete/"), "Delete")]),
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

async fn school_fields(
    db: &mut MySqlConnection,
    id: Option<i32>,
    name: &str,
    apda_id: &str,
    posted: Option<(&Fields, &str)>,
) -> Result<[Value; 2], sqlx::Error> {
    let (mut invalid_name, mut id_error) = (None, None);
    let mut name = name.to_owned();
    let mut apda_id = apda_id.to_owned();
    if let Some((fields, message)) = posted {
        name = fields.all("name").last().cloned().unwrap_or_default();
        apda_id = fields.all("apda_id").last().cloned().unwrap_or_default();
        invalid_name = name_error(db, Kind::School, id, &name, message).await?;
        id_error = apda_id_error(&apda_id);
    }
    Ok([
        input(
            "name",
            "Name:",
            "text",
            &name,
            r#" maxlength="50""#,
            true,
            invalid_name,
        ),
        input(
            "apda_id", "Apda id:", "number", &apda_id, "", false, id_error,
        ),
    ])
}

#[derive(Clone, Serialize)]
struct Team {
    id: i32,
    name: String,
    school: School,
    debaters: Vec<Debater>,
}

#[derive(Clone, Serialize)]
struct School {
    id: i32,
    name: String,
}

#[derive(Clone, Serialize)]
struct Debater {
    id: i32,
    name: String,
}

async fn school_teams(
    db: &mut MySqlConnection,
    school: i32,
) -> Result<(Vec<Team>, Vec<Team>), sqlx::Error> {
    let teams = sqlx::query_as::<_, (i32, String, i32, String, Option<i32>)>(
        "SELECT t.id, t.name, s.id, s.name, t.hybrid_school_id
         FROM tab_team t JOIN tab_school s ON s.id = t.school_id
         WHERE t.school_id = ? OR t.hybrid_school_id = ? ORDER BY t.id",
    )
    .bind(school)
    .bind(school)
    .fetch_all(&mut *db)
    .await?;
    let mut debaters = BTreeMap::<_, Vec<_>>::new();
    if !teams.is_empty() {
        for (team, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
            "SELECT t.id, d.id, d.name FROM tab_team t
             JOIN tab_team_debaters td ON td.team_id = t.id JOIN tab_debater d ON d.id = td.debater_id
             WHERE t.school_id = ? OR t.hybrid_school_id = ? ORDER BY d.name",
        ).bind(school).bind(school).fetch_all(db).await? {
            debaters.entry(team).or_default().push(Debater { id, name });
        }
    }
    let mut primary = Vec::new();
    let mut hybrid = Vec::new();
    for (id, name, primary_id, primary_name, hybrid_id) in teams {
        let team = Team {
            id,
            name,
            school: School {
                id: primary_id,
                name: primary_name,
            },
            debaters: debaters.remove(&id).unwrap_or_default(),
        };
        if primary_id == school && hybrid_id == Some(school) {
            primary.push(team.clone());
            hybrid.push(team);
        } else if primary_id == school {
            primary.push(team);
        } else {
            hybrid.push(team);
        }
    }
    Ok((primary, hybrid))
}
