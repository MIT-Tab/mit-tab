use std::collections::{BTreeMap, HashSet};

use axum::{Json, extract::State, response::Response};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{App, private, unavailable};

/// These three legacy API routes use decided outrounds and four-win teams.
/// The public live feed has its own publication rules and must use its own
/// placement data source.
const PLACEMENTS: &str = "
    WITH victories AS (
        SELECT gov_team_id AS team_id FROM tab_round WHERE victor IN (1, 3, 6)
        UNION ALL
        SELECT opp_team_id FROM tab_round WHERE victor IN (2, 4, 6)
        UNION ALL
        SELECT bye_team_id FROM tab_bye
    ),
    resolved AS (
        SELECT type_of_round AS division,
               IF(victor IN (1, 3), gov_team_id, opp_team_id) AS team_id,
               IF(num_teams <= 2, 1, num_teams DIV 4 + 1) AS place
        FROM tab_outround WHERE victor IN (1, 2, 3, 4)
        UNION ALL
        SELECT type_of_round,
               IF(victor IN (1, 3), opp_team_id, gov_team_id),
               num_teams DIV 2 + 1
        FROM tab_outround WHERE victor IN (1, 2, 3, 4)
    ),
    placed AS (
        SELECT division, team_id, place FROM resolved
        UNION ALL
        SELECT team.break_preference, victories.team_id, 999
        FROM victories JOIN tab_team team ON team.id = victories.team_id
        WHERE NOT EXISTS (
            SELECT 1 FROM resolved r WHERE r.division = team.break_preference
                AND r.team_id = victories.team_id
        )
        GROUP BY team.break_preference, victories.team_id HAVING COUNT(*) = 4
    )
    SELECT division, team_id, MIN(place)
    FROM placed
    WHERE division IN (0, 1)
    GROUP BY division, team_id
    ORDER BY division, MIN(place), team_id
";

const ROSTERS: &str = "
    SELECT t.id, d.id, d.apda_id
    FROM tab_team t
    LEFT JOIN tab_team_debaters membership ON membership.team_id = t.id
    LEFT JOIN tab_debater d ON d.id = membership.debater_id
    ORDER BY t.id, d.name
";

#[derive(Serialize)]
struct DebaterRef {
    apda_id: Option<i32>,
    tournament_id: i32,
}

pub struct Placements {
    divisions: [Vec<i32>; 2],
    rosters: BTreeMap<i32, Vec<DebaterRef>>,
}

impl Placements {
    async fn load(app: &App) -> Result<Self, sqlx::Error> {
        let mut snapshot = app.db.begin().await?;
        let result = Self::load_from(&mut snapshot).await?;
        snapshot.commit().await?;
        Ok(result)
    }

    pub async fn load_from(db: &mut MySqlConnection) -> Result<Self, sqlx::Error> {
        let mut divisions = [Vec::new(), Vec::new()];
        for (division, team_id, _) in sqlx::query_as::<_, (i32, i32, i64)>(PLACEMENTS)
            .fetch_all(&mut *db)
            .await?
        {
            divisions[division as usize].push(team_id);
        }
        let mut rosters: BTreeMap<_, Vec<_>> = BTreeMap::new();
        for (team_id, debater_id, apda_id) in
            sqlx::query_as::<_, (i32, Option<i32>, Option<i32>)>(ROSTERS)
                .fetch_all(&mut *db)
                .await?
        {
            let members = rosters.entry(team_id).or_default();
            if let Some(tournament_id) = debater_id {
                members.push(DebaterRef {
                    apda_id,
                    tournament_id,
                });
            }
        }
        Ok(Self { divisions, rosters })
    }

    pub fn legacy_fields(&self) -> serde_json::Value {
        serde_json::json!({
            "varsity_team_placements": self.for_division(0),
            "novice_team_placements": self.for_division(1),
            "non_placing_teams": self.remaining(),
        })
    }

    fn remaining(&self) -> Vec<&Vec<DebaterRef>> {
        let placed: HashSet<_> = self.divisions.iter().flatten().collect();
        self.rosters
            .iter()
            .filter_map(|(team, members)| (!placed.contains(team)).then_some(members))
            .collect()
    }

    fn for_division(&self, division: usize) -> Vec<&[DebaterRef]> {
        self.divisions[division]
            .iter()
            .filter_map(|team| self.rosters.get(team).map(Vec::as_slice))
            .collect()
    }
}

pub async fn varsity(State(app): State<App>) -> Response {
    match Placements::load(&app).await {
        Ok(results) => private(Json(
            serde_json::json!({"varsity_team_placements": results.for_division(0)}),
        )),
        Err(_) => unavailable(),
    }
}

pub async fn novice(State(app): State<App>) -> Response {
    match Placements::load(&app).await {
        Ok(results) => private(Json(
            serde_json::json!({"novice_team_placements": results.for_division(1)}),
        )),
        Err(_) => unavailable(),
    }
}

pub async fn non_placing(State(app): State<App>) -> Response {
    match Placements::load(&app).await {
        Ok(results) => private(Json(
            serde_json::json!({"non_placing_teams": results.remaining()}),
        )),
        Err(_) => unavailable(),
    }
}
