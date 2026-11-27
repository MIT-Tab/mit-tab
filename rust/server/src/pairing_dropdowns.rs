use axum::{http::StatusCode, response::Response};
use minijinja::{Value, context};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    legacy_html,
    settings::Settings,
    staff_round_choices,
    staff_round_store::{Debate, Detail},
};

pub async fn render(
    db: &mut MySqlConnection,
    detail: &Detail,
    path: &str,
    current: Option<i32>,
    side: Option<bool>,
) -> Result<Response, sqlx::Error> {
    let (template, page) = if let Some(side) = side {
        (
            "pairing/team_dropdown.html",
            teams(db, detail, current, side).await?,
        )
    } else if path.contains("alternative_judges") {
        (
            "pairing/judge_dropdown.html",
            judges(db, detail, current).await?,
        )
    } else {
        (
            "pairing/room_dropdown.html",
            rooms(db, detail, current).await?,
        )
    };
    Ok(legacy_html::render(
        template,
        Value::UNDEFINED,
        page,
        StatusCode::OK,
    ))
}

#[derive(Serialize)]
struct Team {
    id: i32,
    display_backend: String,
}

async fn teams(
    db: &mut MySqlConnection,
    detail: &Detail,
    current: Option<i32>,
    government: bool,
) -> Result<Value, sqlx::Error> {
    let current = current.unwrap_or_default();
    let rows = sqlx::query_as::<_, (i32, String, bool)>(
        "SELECT t.id, t.name, EXISTS(SELECT 1 FROM tab_round r
             WHERE r.round_number = ? AND t.id IN (r.gov_team_id, r.opp_team_id))
         FROM tab_team t ORDER BY t.name, t.id",
    )
    .bind(detail.stage)
    .fetch_all(db)
    .await?;
    let mut excluded_teams = Vec::new();
    let mut included_teams = Vec::new();
    let mut current_team = None;
    for (id, display_backend, paired) in rows {
        let team = Team {
            id,
            display_backend,
        };
        if id == current {
            current_team = Some(team);
        } else if paired {
            included_teams.push(team);
        } else {
            excluded_teams.push(team);
        }
    }
    if current_team.is_none() {
        return Err(sqlx::Error::RowNotFound);
    }
    Ok(context! {
        round_obj => context! {
            id => detail.id,
            gov_team => &detail.snapshot.gov_team,
            opp_team => &detail.snapshot.opp_team,
        },
        round_id => detail.id,
        current_team_id => current,
        current_team,
        position => if government { "gov" } else { "opp" },
        excluded_teams, included_teams,
    })
}

async fn rooms(
    db: &mut MySqlConnection,
    detail: &Detail,
    current: Option<i32>,
) -> Result<Value, sqlx::Error> {
    let current_room = if let Some(id) = current {
        sqlx::query_scalar::<_, String>("SELECT name FROM tab_room WHERE id = ?")
            .bind(id)
            .fetch_optional(&mut *db)
            .await?
            .map(|name| context! { id, name })
    } else {
        None
    };
    let (paired, available): (Vec<_>, Vec<_>) =
        staff_round_choices::rooms(db, Debate::Inround, detail)
            .await?
            .into_iter()
            .partition(|room| room.occupied);
    Ok(context! {
        round_obj => context! { id => detail.id },
        current_room,
        viable_unpaired_rooms => available,
        viable_paired_rooms => paired,
    })
}

type JudgeChoice = (String, i32, String, Option<i64>, bool, bool, bool);

async fn judges(
    db: &mut MySqlConnection,
    detail: &Detail,
    current: Option<i32>,
) -> Result<Value, sqlx::Error> {
    let allow_rejudges = Settings::load(db, &["allow_rejudges"])
        .await?
        .flag("allow_rejudges", false);
    type Row = (i32, String, String, bool, bool, bool, bool, bool, bool, i64);
    let rows = sqlx::query_as::<_, Row>(
        "SELECT j.id, j.name, CAST(j.rank AS CHAR), j.wing_only, j.request_wing, j.no_wings,
            EXISTS(SELECT 1 FROM tab_round_judges x JOIN tab_round r ON r.id = x.round_id
              WHERE x.judge_id = j.id AND r.round_number = matchup.round_number),
            EXISTS(SELECT 1 FROM tab_checkin c WHERE c.judge_id = j.id AND c.round_number = matchup.round_number),
            (
              NOT EXISTS (SELECT 1 FROM tab_scratch s
                 WHERE s.judge_id = j.id AND s.team_id IN (matchup.gov_team_id, matchup.opp_team_id))
              AND NOT EXISTS (SELECT 1 FROM tab_judge_schools a JOIN tab_team t
                 ON t.id IN (matchup.gov_team_id, matchup.opp_team_id)
                 WHERE a.judge_id = j.id AND a.school_id IN (t.school_id, t.hybrid_school_id))
              AND (? OR NOT EXISTS (SELECT 1 FROM tab_round_judges x JOIN tab_round r ON r.id = x.round_id
                 WHERE x.judge_id = j.id AND
                   (matchup.gov_team_id IN (r.gov_team_id, r.opp_team_id) OR matchup.opp_team_id IN (r.gov_team_id, r.opp_team_id))))
            ),
            GREATEST(
              (SELECT COUNT(*) FROM tab_round_judges x JOIN tab_round r ON r.id = x.round_id
                WHERE x.judge_id = j.id AND r.id <> matchup.id AND matchup.gov_team_id IN (r.gov_team_id, r.opp_team_id)),
              (SELECT COUNT(*) FROM tab_round_judges x JOIN tab_round r ON r.id = x.round_id
                WHERE x.judge_id = j.id AND r.id <> matchup.id AND matchup.opp_team_id IN (r.gov_team_id, r.opp_team_id))
            )
         FROM tab_judge j CROSS JOIN tab_round matchup WHERE matchup.id = ?
           AND (j.id = ? OR EXISTS(SELECT 1 FROM tab_checkin c
                WHERE c.judge_id = j.id AND c.round_number = matchup.round_number))
         ORDER BY j.name, j.id",
    ).bind(allow_rejudges).bind(detail.id).bind(current).fetch_all(db).await?;
    let mut excluded_judges = Vec::new();
    let mut included_judges = Vec::new();
    let mut current_judge = None;
    for (id, name, rank, wing, request_wing, no_wings, paired, checked, eligible, prior) in rows {
        let count = (prior > 0).then_some(prior + 1);
        if current == Some(id) {
            current_judge = Some(context! {
                id, name => &name, rank => &rank, wing_only => wing, request_wing, no_wings,
                rejudge_display => count,
            });
        }
        if eligible && checked {
            let numeric = rank.parse::<f64>().unwrap_or_default();
            let rank = if numeric.fract() == 0.0 {
                format!("{numeric:.1}")
            } else {
                numeric.to_string()
            };
            let item = (name, id, rank, count, wing, request_wing, no_wings);
            if paired {
                included_judges.push(item);
            } else {
                excluded_judges.push(item);
            }
        }
    }
    let sort = |names: &mut Vec<JudgeChoice>| {
        names.sort_by_cached_key(|row| (row.0.to_lowercase(), row.1));
    };
    sort(&mut excluded_judges);
    sort(&mut included_judges);
    if current.is_some() && current_judge.is_none() {
        return Err(sqlx::Error::RowNotFound);
    }
    let judge = current_judge.unwrap_or_else(|| context! { name => "No judge" });
    Ok(context! {
        round_obj => context! { id => detail.id, chair => context! { id => detail.snapshot.chair } },
        current_judge_id => current,
        current_judge_name => judge.get_attr("name").ok(),
        current_judge_rank => judge.get_attr("rank").ok(),
        current_judge_rejudge_display => judge.get_attr("rejudge_display").ok(),
        current_judge_obj => judge,
        excluded_judges, included_judges,
    })
}
