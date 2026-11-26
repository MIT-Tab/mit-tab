use axum::{Json, extract::State, response::Response};
use serde::Serialize;
use serde_json::{Value, json};
use sqlx::{MySql, MySqlExecutor, QueryBuilder};

use crate::{App, private, unavailable};

// Match the Django Count annotations, including the combined count when a
// team has been on both sides. Legacy data can contain repeated team members.
pub(crate) const ELIGIBLE_TEAMS: &str = "
    WITH eligible_teams AS (
        SELECT t.id, t.school_id
        FROM tab_team t
        LEFT JOIN tab_round g ON g.gov_team_id = t.id
        LEFT JOIN tab_round o ON o.opp_team_id = t.id
        GROUP BY t.id, t.school_id
        HAVING COUNT(g.id) + COUNT(o.id) >= 3
    )
";

pub async fn counts(State(app): State<App>) -> Response {
    respond(counts_payload(&app.db).await)
}

pub async fn new_debaters(State(app): State<App>) -> Response {
    respond(debaters_payload(&app.db).await)
}

pub async fn new_schools(State(app): State<App>) -> Response {
    respond(schools_payload(&app.db).await)
}

fn respond(result: Result<Value, sqlx::Error>) -> Response {
    match result {
        Ok(value) => private(Json(value)),
        Err(_) => unavailable(),
    }
}

pub async fn counts_payload<'e>(db: impl MySqlExecutor<'e>) -> Result<Value, sqlx::Error> {
    let (varsity, novice, teams) = QueryBuilder::<MySql>::new(ELIGIBLE_TEAMS)
        .push(
            "SELECT COUNT(CASE WHEN d.novice_status = 0 THEN 1 END),
                      COUNT(CASE WHEN d.novice_status = 1 THEN 1 END),
                      (SELECT COUNT(*) FROM eligible_teams)
               FROM eligible_teams t
               JOIN tab_team_debaters members ON members.team_id = t.id
               JOIN tab_debater d ON d.id = members.debater_id",
        )
        .build_query_as::<(i64, i64, i64)>()
        .fetch_one(db)
        .await?;
    Ok(json!({"debater_counts": {"varsity": varsity, "novice": novice, "teams": teams}}))
}

#[derive(Serialize)]
struct NewDebater {
    name: String,
    novice_status: i32,
    school_id: Option<i32>,
    school_name: String,
    debater_id: i32,
}

pub async fn debaters_payload<'e>(db: impl MySqlExecutor<'e>) -> Result<Value, sqlx::Error> {
    let rows: Vec<_> = QueryBuilder::<MySql>::new(ELIGIBLE_TEAMS)
        .push(
            "SELECT d.name, d.novice_status, school.apda_id, school.name, d.id
               FROM eligible_teams t
               JOIN tab_team_debaters members ON members.team_id = t.id
               JOIN tab_debater d ON d.id = members.debater_id
               JOIN tab_school school ON school.id = t.school_id
               WHERE d.apda_id = -1
               ORDER BY d.name, d.id, t.id",
        )
        .build_query_as::<(String, i32, Option<i32>, String, i32)>()
        .fetch_all(db)
        .await?
        .into_iter()
        .map(
            |(name, novice_status, school_id, school_name, debater_id)| NewDebater {
                name,
                novice_status,
                school_id,
                school_name,
                debater_id,
            },
        )
        .collect();
    Ok(json!({"new_debater_data": rows}))
}

pub async fn schools_payload<'e>(db: impl MySqlExecutor<'e>) -> Result<Value, sqlx::Error> {
    let rows = QueryBuilder::<MySql>::new(ELIGIBLE_TEAMS)
        .push(
            "SELECT DISTINCT school.name
               FROM eligible_teams t
               JOIN tab_team_debaters members ON members.team_id = t.id
               JOIN tab_debater d ON d.id = members.debater_id
               JOIN tab_school school ON school.id = t.school_id
               WHERE d.apda_id = -1 AND school.apda_id = -1
               ORDER BY school.name",
        )
        .build_query_scalar::<String>()
        .fetch_all(db)
        .await?;
    Ok(json!({"new_schools": rows}))
}
