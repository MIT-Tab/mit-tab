use std::collections::HashMap;

use mittab_tabulation::{
    ballots::ScoringPolicy,
    judges::{Debate, InroundPolicy, Judge, Panel, RankingMode, WingMode, assign_inround},
    rankings::Stat,
};
use sqlx::MySqlConnection;

use crate::{settings::Settings, staff_pairing::ChangeError, tournament_data::TournamentData};

pub async fn plan(
    db: &mut MySqlConnection,
    settings: &Settings,
    round: i32,
) -> Result<Vec<Panel>, ChangeError> {
    let rows = sqlx::query_as::<_, (i32, i32, i32, i32, i32)>(
        "SELECT r.id, r.gov_team_id, r.opp_team_id, g.seed, o.seed FROM tab_round r
         JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         WHERE r.round_number = ? ORDER BY r.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?;
    let scores = if round > 1 {
        let data = TournamentData::load(db).await?;
        data.scores_by_team(ScoringPolicy {
            current_round: settings.integer("cur_round", 1),
            lenient_late_through: settings.integer("lenient_late", 0),
        })
        .map_err(ChangeError::Conflict)?
    } else {
        HashMap::new()
    };
    let debates: Vec<_> = rows
        .into_iter()
        .map(|(id, gov, opp, gov_seed, opp_seed)| {
            let teams = [gov, opp];
            if round == 1 {
                Debate {
                    id,
                    teams,
                    bubble: false,
                    strength: [
                        f64::from(gov_seed.max(opp_seed)),
                        f64::from(gov_seed.min(opp_seed)),
                        0.0,
                    ],
                }
            } else {
                let govt = &scores[&i64::from(gov)];
                let opp = &scores[&i64::from(opp)];
                let [gov_wins, opp_wins] = [govt.get(Stat::Wins), opp.get(Stat::Wins)];
                let [gov_speaks, opp_speaks] = [govt.get(Stat::Speaks), opp.get(Stat::Speaks)];
                Debate {
                    id,
                    teams,
                    bubble: [gov_wins, opp_wins].contains(&f64::from(round - 1)),
                    strength: [
                        gov_wins.max(opp_wins),
                        gov_speaks.max(opp_speaks),
                        gov_speaks.min(opp_speaks),
                    ],
                }
            }
        })
        .collect();

    let mut judges: Vec<_> = sqlx::query_as::<_, (i32, i32, bool, bool, bool)>(
        "SELECT j.id, CAST(j.rank * 100 AS SIGNED), j.wing_only, j.request_wing, j.no_wings
           FROM tab_judge j JOIN tab_checkin c ON c.judge_id = j.id
           WHERE c.round_number = ? ORDER BY j.name, j.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, rank, wing_only, request_wing, no_wings)| Judge {
        id,
        rank,
        wing_only,
        request_wing,
        no_wings,
        ..Judge::default()
    })
    .collect();
    let indices: HashMap<_, _> = judges
        .iter()
        .enumerate()
        .map(|(index, judge)| (judge.id, index))
        .collect();
    for (judge, team) in sqlx::query_as::<_, (i32, i32)>(
        "SELECT s.judge_id, s.team_id FROM tab_scratch s
         JOIN tab_checkin c ON c.judge_id = s.judge_id WHERE c.round_number = ?
         UNION
         SELECT s.judge_id, t.id FROM tab_judge_schools s
         JOIN tab_checkin c ON c.judge_id = s.judge_id
         JOIN tab_team t ON s.school_id IN (t.school_id, t.hybrid_school_id)
         WHERE c.round_number = ?",
    )
    .bind(round)
    .bind(round)
    .fetch_all(&mut *db)
    .await?
    {
        judges[indices[&judge]].conflicts.insert(team);
    }
    for (judge_id, gov, opp, chair, prior_round) in sqlx::query_as::<_, (i32, i32, i32, Option<i32>, i32)>(
        "SELECT j.judge_id, r.gov_team_id, r.opp_team_id, r.chair_id, r.round_number FROM tab_round_judges j
         JOIN tab_round r ON r.id = j.round_id JOIN tab_checkin c ON c.judge_id = j.judge_id
         WHERE c.round_number = ? AND r.round_number <> ?",
    ).bind(round).bind(round).fetch_all(db).await? {
        let judge = &mut judges[indices[&judge_id]];
        for team in [gov, opp] {
            *judge.rejudges.entry(team).or_default() += 1;
        }
        if prior_round < round && let Some(chair) = chair.filter(|&chair| chair != judge_id) {
            *judge.chaired_by.entry(chair).or_default() += 1;
        }
    }

    let policy = InroundPolicy {
        ranking: if settings.integer("judge_pairing_mode", 0) == 1 {
            RankingMode::Classic
        } else {
            RankingMode::Shared
        },
        assign_wings: settings.flag("pair_wings", true),
        wings: match settings.integer("wing_pairing_mode", 0) {
            0 => WingMode::HelpChairs,
            2 => WingMode::Random,
            _ => WingMode::Learning,
        },
        prioritize_bubbles: settings.integer("inround_round_priority", 0) == 1,
        allow_rejudges: settings.flag("allow_rejudges", false),
        rejudge_penalty: settings.integer("rejudge_penalty", 100),
        repeat_wing_penalty: settings.integer("repeat_wing_penalty", 10),
    };
    assign_inround(&debates, &judges, &policy)
        .map_err(|_| ChangeError::Conflict("Could not assign an eligible chair to every debate. Check judge availability and conflicts."))
}

pub async fn save(
    db: &mut MySqlConnection,
    round: i32,
    panels: Vec<Panel>,
) -> Result<(), sqlx::Error> {
    for query in [
        "DELETE j FROM tab_round_judges j JOIN tab_round r ON r.id = j.round_id WHERE r.round_number = ?",
        "DELETE j FROM tab_manualjudgeassignment j JOIN tab_round r ON r.id = j.round_id WHERE r.round_number = ?",
    ] {
        sqlx::query(query).bind(round).execute(&mut *db).await?;
    }
    for panel in panels {
        sqlx::query("UPDATE tab_round SET chair_id = ? WHERE id = ?")
            .bind(panel.chair)
            .bind(panel.debate)
            .execute(&mut *db)
            .await?;
        for judge in std::iter::once(panel.chair).chain(panel.wings) {
            sqlx::query("INSERT INTO tab_round_judges (round_id, judge_id) VALUES (?, ?)")
                .bind(panel.debate)
                .bind(judge)
                .execute(&mut *db)
                .await?;
        }
    }
    sqlx::query(
        "INSERT INTO tab_auditevent (object_id, event_type, created_at, object_repr, changes, note, content_type_id)
         SELECT r.id, 'judges_paired', UTC_TIMESTAMP(6), CONCAT('Round ', r.round_number, ' between ', g.name, ' and ', o.name),
              JSON_OBJECT(), '', ct.id
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         JOIN django_content_type ct ON ct.app_label = 'tab' AND ct.model = 'round'
         WHERE r.round_number = ?",
    ).bind(round).execute(db).await?;
    Ok(())
}
