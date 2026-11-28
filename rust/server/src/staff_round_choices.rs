use serde::Serialize;
use sqlx::{AssertSqlSafe, MySqlConnection, Row};

use crate::{
    settings::Settings,
    staff_round_store::{Debate, Detail},
};

#[derive(Serialize)]
pub struct Choice {
    pub(crate) id: i32,
    name: String,
    pub(crate) occupied: bool,
    badges: Vec<String>,
}

#[derive(Serialize)]
pub struct Choices {
    pub government: Vec<Choice>,
    pub opposition: Vec<Choice>,
    pub judges: Vec<Choice>,
    pub rooms: Vec<Choice>,
}

pub async fn all(
    db: &mut MySqlConnection,
    kind: Debate,
    detail: &Detail,
) -> Result<Choices, sqlx::Error> {
    Ok(Choices {
        government: teams(db, kind, detail, true).await?,
        opposition: teams(db, kind, detail, false).await?,
        judges: judges(db, kind, detail).await?,
        rooms: rooms(db, kind, detail).await?,
    })
}

pub async fn teams(
    db: &mut MySqlConnection,
    kind: Debate,
    detail: &Detail,
    government: bool,
) -> Result<Vec<Choice>, sqlx::Error> {
    let stage = kind.stage_column();
    let table = kind.table();
    let other = detail.team(!government).map_or(0, |team| team.id);
    let original = detail.team(government).map_or(0, |team| team.id);
    let sql = if kind == Debate::Outround {
        format!(
            "SELECT t.id, t.name,
          EXISTS(SELECT 1 FROM {table} r WHERE r.{stage} = ? AND r.type_of_round = ?
              AND t.id IN (r.gov_team_id, r.opp_team_id)) AS occupied
          FROM tab_team t JOIN tab_breakingteam b ON b.team_id = t.id AND b.type_of_team = ?
          WHERE t.id NOT IN (?, ?) ORDER BY t.name, t.id"
        )
    } else {
        format!("SELECT t.id, t.name,
           EXISTS(SELECT 1 FROM {table} r WHERE r.{stage} = ? AND t.id IN (r.gov_team_id, r.opp_team_id)) AS occupied
           FROM tab_team t WHERE t.id NOT IN (?, ?) ORDER BY t.name, t.id")
    };
    let mut select =
        sqlx::query_as::<_, (i32, String, bool)>(AssertSqlSafe(sql)).bind(detail.stage);
    if kind == Debate::Outround {
        let division = detail.snapshot.division.unwrap_or_default();
        select = select.bind(division).bind(division);
    }
    Ok(select
        .bind(original)
        .bind(other)
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(id, name, occupied)| Choice {
            id,
            name,
            occupied,
            badges: Vec::new(),
        })
        .collect())
}

pub async fn judges(
    db: &mut MySqlConnection,
    kind: Debate,
    detail: &Detail,
) -> Result<Vec<Choice>, sqlx::Error> {
    let gov = detail.team(true).map_or(0, |team| team.id);
    let opp = detail.team(false).map_or(0, |team| team.id);
    let allow_rejudges = kind == Debate::Outround
        || Settings::load(db, &["allow_rejudges"])
            .await?
            .flag("allow_rejudges", false);
    let (table, column) = kind.panel();
    let scope = if kind == Debate::Inround {
        "r.round_number = ?"
    } else {
        "r.num_teams = ? AND r.type_of_round = ? AND r.victor = 0"
    };
    let used = format!(
        "EXISTS(SELECT 1 FROM {table} p JOIN {} r ON r.id = p.{column}
        WHERE p.judge_id = j.id AND {scope} AND r.id <> ?) AS occupied",
        kind.table()
    );
    let sql = format!(
        "SELECT j.id, j.name, CAST(j.`rank` AS CHAR) AS calibration, j.wing_only, j.request_wing, j.no_wings,
            {used},
            GREATEST(
              (SELECT COUNT(*) FROM tab_round_judges a JOIN tab_round seen ON seen.id = a.round_id
                WHERE a.judge_id = j.id AND (? OR seen.id <> ?) AND ? IN (seen.gov_team_id, seen.opp_team_id)),
              (SELECT COUNT(*) FROM tab_round_judges a JOIN tab_round seen ON seen.id = a.round_id
                WHERE a.judge_id = j.id AND (? OR seen.id <> ?) AND ? IN (seen.gov_team_id, seen.opp_team_id))
            ) AS previous
         FROM tab_judge j JOIN tab_checkin c ON c.judge_id = j.id AND c.round_number = ?
         WHERE NOT EXISTS(SELECT 1 FROM tab_scratch x WHERE x.judge_id = j.id AND x.team_id IN (?, ?))
           AND NOT EXISTS(
             SELECT 1 FROM tab_judge_schools f JOIN tab_team t ON t.id IN (?, ?)
             WHERE f.judge_id = j.id AND f.school_id IN (t.school_id, t.hybrid_school_id)
           )
           AND NOT EXISTS(SELECT 1 FROM {table} panel WHERE panel.{column} = ? AND panel.judge_id = j.id)
         ORDER BY j.name, j.id"
    );
    let mut query = sqlx::query(AssertSqlSafe(sql)).bind(detail.stage);
    if kind == Debate::Outround {
        query = query.bind(detail.snapshot.division.unwrap_or_default());
    }
    let out = kind == Debate::Outround;
    let rows = query
        .bind(detail.id)
        .bind(out)
        .bind(detail.id)
        .bind(gov)
        .bind(out)
        .bind(detail.id)
        .bind(opp)
        .bind(if out { 0 } else { detail.stage })
        .bind(gov)
        .bind(opp)
        .bind(gov)
        .bind(opp)
        .bind(detail.id)
        .fetch_all(db)
        .await?;
    let mut choices = Vec::new();
    for row in rows {
        let seen: i64 = row.try_get("previous")?;
        if !allow_rejudges && seen > 0 {
            continue;
        }
        let mut badges = vec![row.try_get("calibration")?];
        for (key, label) in [
            ("wing_only", "Wing only"),
            ("request_wing", "Requests a wing"),
            ("no_wings", "Requests no wings"),
        ] {
            if row.try_get::<bool, _>(key)? {
                badges.push(label.into());
            }
        }
        if !out && seen > 0 {
            badges.push(format!(
                "Judged this team {seen} time{}",
                if seen == 1 { "" } else { "s" }
            ));
        }
        choices.push(Choice {
            id: row.try_get("id")?,
            name: row.try_get("name")?,
            occupied: row.try_get("occupied")?,
            badges,
        });
    }
    Ok(choices)
}

pub async fn rooms(
    db: &mut MySqlConnection,
    kind: Debate,
    detail: &Detail,
) -> Result<Vec<Choice>, sqlx::Error> {
    let (table, column) = kind.panel();
    let used = if kind == Debate::Inround {
        "EXISTS(SELECT 1 FROM tab_round r WHERE r.room_id = m.id) AS occupied"
    } else {
        "EXISTS(SELECT 1 FROM tab_outround r WHERE r.room_id = m.id AND r.num_teams = ?
                 AND r.type_of_round = ? AND r.victor = 0 AND r.id <> ?) AS occupied"
    };
    let sql = format!(
        "SELECT m.id, m.name, {used}
         FROM tab_room m JOIN tab_roomcheckin c ON m.id = c.room_id AND c.round_number = ?
         WHERE NOT EXISTS(
            SELECT 1 FROM tab_team_required_room_tags t WHERE t.team_id IN (?, ?)
            AND NOT EXISTS(SELECT 1 FROM tab_room_tags own WHERE own.room_id = m.id AND own.roomtag_id = t.roomtag_id)
         ) AND NOT EXISTS(
            SELECT 1 FROM tab_judge_required_room_tags j JOIN {table} p ON p.judge_id = j.judge_id
            WHERE p.{column} = ?
            AND NOT EXISTS(SELECT 1 FROM tab_room_tags own WHERE own.room_id = m.id AND own.roomtag_id = j.roomtag_id)
         ) ORDER BY m.name, m.id"
    );
    let mut query = sqlx::query_as::<_, (i32, String, bool)>(AssertSqlSafe(sql));
    if kind == Debate::Outround {
        query = query
            .bind(detail.stage)
            .bind(detail.snapshot.division.unwrap_or_default())
            .bind(detail.id);
    }
    Ok(query
        .bind(if kind == Debate::Outround {
            0
        } else {
            detail.stage
        })
        .bind(detail.team(true).map_or(0, |team| team.id))
        .bind(detail.team(false).map_or(0, |team| team.id))
        .bind(detail.id)
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(id, name, occupied)| Choice {
            id,
            name,
            occupied,
            badges: Vec::new(),
        })
        .collect())
}
