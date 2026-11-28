use axum::{http::StatusCode, response::Response};
use minijinja::{Value, context};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    legacy_html,
    settings::Settings,
    staff_elimination_page::Selection,
    staff_round_choices,
    staff_round_store::{Debate, Detail},
};

pub async fn render(
    db: &mut MySqlConnection,
    kind: Debate,
    detail: &Detail,
    (path, current, side): (&str, Option<i32>, Option<bool>),
    selection: Selection,
) -> Result<Response, sqlx::Error> {
    let out = kind == Debate::Outround;
    let (template, page) = if let Some(side) = side {
        (
            "pairing/team_dropdown.html",
            teams(db, detail, current, side, out).await?,
        )
    } else if path.contains("alternative_judges") {
        let page = if out {
            let scope = checked_scope(db, detail, selection).await?;
            outround_judges(db, detail, current, scope).await?
        } else {
            judges(db, detail, current).await?
        };
        ("pairing/judge_dropdown.html", page)
    } else {
        let scope = if out {
            checked_scope(db, detail, selection).await?
        } else {
            [None; 2]
        };
        (
            "pairing/room_dropdown.html",
            rooms(db, kind, detail, current, scope).await?,
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
    out: bool,
) -> Result<Value, sqlx::Error> {
    let current = current.unwrap_or_default();
    let rows = if out {
        sqlx::query_as::<_, (i32, String, bool)>(
            "SELECT t.id, t.name, EXISTS(SELECT 1 FROM tab_outround r
               WHERE r.num_teams = ? AND t.id IN (r.gov_team_id, r.opp_team_id))
             FROM tab_team t JOIN tab_breakingteam b ON b.team_id = t.id
             WHERE b.type_of_team = (SELECT type_of_team FROM tab_breakingteam WHERE team_id = ?)
             ORDER BY t.name, t.id",
        )
        .bind(detail.stage)
        .bind(current)
        .fetch_all(db)
        .await?
    } else {
        sqlx::query_as::<_, (i32, String, bool)>(
            "SELECT t.id, t.name, EXISTS(SELECT 1 FROM tab_round r
                 WHERE r.round_number = ? AND t.id IN (r.gov_team_id, r.opp_team_id))
             FROM tab_team t ORDER BY t.name, t.id",
        )
        .bind(detail.stage)
        .fetch_all(db)
        .await?
    };
    let mut excluded_teams = Vec::new();
    let mut included_teams = Vec::new();
    let mut current_team = None;
    for (id, display_backend, paired) in rows {
        let team = Team {
            id,
            display_backend,
        };
        if id == current {
            if out {
                included_teams.push(Team {
                    id: team.id,
                    display_backend: team.display_backend.clone(),
                });
            }
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
        excluded_teams, included_teams, is_outround => out,
    })
}

async fn rooms(
    db: &mut MySqlConnection,
    kind: Debate,
    detail: &Detail,
    current: Option<i32>,
    scope: [Option<i32>; 2],
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
    let used = if kind == Debate::Outround {
        sqlx::query_scalar::<_, i32>(
            "SELECT DISTINCT room_id FROM tab_outround WHERE victor = 0 AND room_id IS NOT NULL
               AND id <> ? AND ((type_of_round = 0 AND num_teams = ?) OR (type_of_round = 1 AND num_teams = ?))",
        ).bind(detail.id).bind(scope[0]).bind(scope[1]).fetch_all(&mut *db).await?
    } else {
        Vec::new()
    };
    let (paired, available): (Vec<_>, Vec<_>) = staff_round_choices::rooms(db, kind, detail)
        .await?
        .into_iter()
        .partition(|room| {
            if kind == Debate::Outround {
                used.contains(&room.id)
            } else {
                room.occupied
            }
        });
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

async fn checked_scope(
    db: &mut MySqlConnection,
    detail: &Detail,
    selection: Selection,
) -> Result<[Option<i32>; 2], sqlx::Error> {
    let mut scope = selection.sizes();
    for (division, size) in scope.iter_mut().enumerate() {
        if size.is_some() && !sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = ? AND num_teams = ?)",
        ).bind(division as i32).bind(*size).fetch_one(&mut *db).await? {
            *size = None;
        }
    }
    if scope == [None; 2]
        && let Some(division @ (0 | 1)) = detail.snapshot.division
    {
        scope[division as usize] = Some(detail.stage);
    }
    Ok(scope)
}

async fn outround_judges(
    db: &mut MySqlConnection,
    detail: &Detail,
    current: Option<i32>,
    scope: [Option<i32>; 2],
) -> Result<Value, sqlx::Error> {
    type Row = (
        i32,
        String,
        String,
        bool,
        bool,
        bool,
        bool,
        bool,
        bool,
        bool,
    );
    let rows = sqlx::query_as::<_, Row>(
        "SELECT j.id, j.name, CAST(j.rank AS CHAR), j.wing_only, j.request_wing, j.no_wings,
          EXISTS(SELECT 1 FROM tab_checkin c WHERE c.judge_id = j.id AND c.round_number = 0),
          EXISTS(SELECT 1 FROM tab_outround_judges p JOIN tab_outround r ON r.id = p.outround_id
            WHERE p.judge_id = j.id AND r.victor = 0
              AND ((r.type_of_round = 0 AND r.num_teams = ?) OR (r.type_of_round = 1 AND r.num_teams = ?))),
          EXISTS(SELECT 1 FROM tab_outround_judges p WHERE p.judge_id = j.id AND p.outround_id = r.id),
          NOT EXISTS(SELECT 1 FROM tab_scratch s
            WHERE s.judge_id = j.id AND s.team_id IN (r.gov_team_id, r.opp_team_id))
          AND NOT EXISTS(SELECT 1 FROM tab_judge_schools jsc JOIN tab_team t
            ON t.id IN (r.gov_team_id, r.opp_team_id)
            WHERE jsc.judge_id = j.id AND jsc.school_id IN (t.school_id, t.hybrid_school_id))
         FROM tab_judge j JOIN tab_outround r ON r.id = ?
         WHERE j.id = ? OR EXISTS(SELECT 1 FROM tab_checkin c WHERE c.judge_id = j.id AND c.round_number = 0)
         ORDER BY j.name, j.id"
    ).bind(scope[0]).bind(scope[1]).bind(detail.id).bind(current).fetch_all(db).await?;
    let mut included_judges = Vec::new();
    let mut excluded_judges = Vec::new();
    let mut current_judge = None;
    for (
        id,
        name,
        rank,
        wing_only,
        request_wing,
        no_wings,
        checked,
        occupied,
        assigned,
        eligible,
    ) in rows
    {
        if current == Some(id) {
            current_judge =
                Some(context! { name => &name, rank => &rank, wing_only, request_wing, no_wings });
        }
        if checked && eligible && !assigned {
            let number = rank.parse::<f64>().unwrap_or_default();
            let rank = if number.fract() == 0.0 {
                format!("{number:.1}")
            } else {
                number.to_string()
            };
            let option = (name, id, rank, wing_only, request_wing, no_wings);
            if occupied {
                included_judges.push(option);
            } else {
                excluded_judges.push(option);
            }
        }
    }
    included_judges.sort_by_cached_key(|row| (row.0.to_lowercase(), row.1));
    excluded_judges.sort_by_cached_key(|row| (row.0.to_lowercase(), row.1));
    if current.is_some() && current_judge.is_none() {
        return Err(sqlx::Error::RowNotFound);
    }
    let judge = current_judge.unwrap_or_else(|| context! {name => "No judge"});
    Ok(context! {
        round_obj => context! { id => detail.id, chair => context! {id => detail.snapshot.chair} },
        current_judge_id => current, current_judge_name => judge.get_attr("name").ok(),
        current_judge_rank => judge.get_attr("rank").ok(), current_judge_obj => judge,
        included_judges, excluded_judges, is_outround => true,
    })
}
