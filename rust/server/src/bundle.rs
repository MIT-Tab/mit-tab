use std::collections::{BTreeMap, HashMap};

use serde::Serialize;
use serde_json::{Value, json};
use sqlx::MySqlConnection;

#[derive(Serialize)]
struct Side<'a> {
    debater_ids: Vec<i32>,
    source_names: Vec<&'a str>,
}

#[derive(Serialize)]
struct Judge<'a> {
    original_name: &'a str,
    is_chair: bool,
}

#[derive(Serialize)]
struct Stats {
    speaks: f64,
    ranks: f64,
}

type Membership = BTreeMap<i32, Vec<i32>>;

pub async fn export(
    db: &mut MySqlConnection,
    tournament_name: &str,
    exported_at: &str,
) -> Result<Value, sqlx::Error> {
    let schools: Vec<_> = sqlx::query_as::<_, (i32, Option<i32>, String)>(
        "SELECT id, apda_id, name FROM tab_school ORDER BY id",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, apda_id, name)| json!({"id": id, "apda_id": apda(apda_id), "name": name}))
    .collect();
    let (members, schools_by_debater) = memberships(db).await?;
    let mut names = HashMap::new();
    let mut debaters = Vec::new();
    for (id, apda_id, name, status) in sqlx::query_as::<_, (i32, Option<i32>, String, i32)>(
        "SELECT id, apda_id, name, novice_status FROM tab_debater ORDER BY id",
    )
    .fetch_all(&mut *db)
    .await?
    {
        debaters.push(json!({
            "id": id,
            "apda_id": apda(apda_id),
            "name": name,
            "novice_status": division(status),
            "school_id": schools_by_debater.get(&id),
        }));
        names.insert(id, name);
    }

    let sideline = |team| {
        let ids = members.get(&team).map_or(&[][..], Vec::as_slice);
        Side {
            debater_ids: ids.to_vec(),
            source_names: ids.iter().map(|id| names[id].as_str()).collect(),
        }
    };
    let judges_by_round = judge_assignments(db).await?;
    let mut stats_by_round = round_stats(db).await?;
    let mut rounds = Vec::new();
    const ROUND_ROWS: &str = "
        SELECT 0, id, round_number, 0, victor, gov_team_id, opp_team_id, chair_id FROM tab_round
        UNION ALL
        SELECT 1, id, num_teams, type_of_round, victor, gov_team_id, opp_team_id, chair_id
        FROM tab_outround
    ";
    type RoundRow = (i64, i32, i32, i64, i32, i32, i32, Option<i32>);
    for (stage, id, number, round_type, victor, gov, opp, chair) in
        sqlx::query_as::<_, RoundRow>(ROUND_ROWS)
            .fetch_all(&mut *db)
            .await?
    {
        let judges = judges_by_round
            .get(&(stage, id))
            .map_or(&[][..], Vec::as_slice);
        let mut serialized_judges = Vec::new();
        if let Some((_, name)) = judges.iter().find(|(judge_id, _)| Some(*judge_id) == chair) {
            serialized_judges.push(Judge {
                original_name: name,
                is_chair: true,
            });
        }
        serialized_judges.extend(
            judges
                .iter()
                .filter(|(judge_id, _)| Some(*judge_id) != chair)
                .map(|(_, name)| Judge {
                    original_name: name,
                    is_chair: false,
                }),
        );
        let gov = sideline(gov);
        let opp = sideline(opp);

        let (key, value) = if stage == 0 {
            let key = format!("prelim:{id}");
            let value = json!({
                "import_key": key, "round_number": number, "label": format!("Round {number}"),
                "stage": "prelim", "division": null, "elim_size": null, "victor": victor,
                "gov": gov, "opp": opp, "judges": serialized_judges,
                "stats": stats_by_round.remove(&id).unwrap_or_default(),
            });
            (key, value)
        } else {
            let division = division(round_type as i32);
            let key = format!("outround:{division}:{number}:{id}");
            let division_title = if division == "novice" {
                "Novice"
            } else {
                "Varsity"
            };
            let value = json!({
                "import_key": key, "round_number": number,
                "label": format!("{division_title} {}", elimination_label(number)),
                "stage": "outround", "division": division, "elim_size": number, "victor": victor,
                "gov": gov, "opp": opp, "judges": serialized_judges,
            });
            (key, value)
        };
        rounds.push((stage, number, key, value));
    }
    rounds.sort_by(|left, right| (&left.0, &left.1, &left.2).cmp(&(&right.0, &right.1, &right.2)));
    let rounds: Vec<_> = rounds.into_iter().map(|(_, _, _, round)| round).collect();
    Ok(json!({
        "schema_version": 1,
        "source": "mit_tab_black_rod_bundle",
        "exported_at": exported_at,
        "tournament_name": tournament_name,
        "schools": schools,
        "debaters": debaters,
        "rounds": rounds,
    }))
}

async fn memberships(
    db: &mut MySqlConnection,
) -> Result<(Membership, HashMap<i32, i32>), sqlx::Error> {
    let rows = sqlx::query_as::<_, (i32, i32, i32, Option<i32>)>(
        "SELECT t.id, m.debater_id, t.school_id, t.hybrid_school_id
         FROM tab_team t JOIN tab_team_debaters m ON m.team_id = t.id
         ORDER BY t.id, m.debater_id",
    )
    .fetch_all(db)
    .await?;
    let mut members: Membership = BTreeMap::new();
    let mut schools = HashMap::new();
    for (team_id, debater, school, hybrid) in rows {
        let ids = members.entry(team_id).or_default();
        let debater_school = if ids.len() == 1 {
            hybrid.unwrap_or(school)
        } else {
            school
        };
        schools.entry(debater).or_insert(debater_school);
        ids.push(debater);
    }
    Ok((members, schools))
}

type JudgeAssignments = HashMap<(i64, i32), Vec<(i32, String)>>;

async fn judge_assignments(db: &mut MySqlConnection) -> Result<JudgeAssignments, sqlx::Error> {
    // Project only assigned public judge names and chair roles. No scores,
    // rank data, scratches, or eligibility information enter the bundle.
    const QUERY: &str = "
        SELECT 0 AS stage, round_id AS debate, j.id AS judge, j.name
        FROM tab_round_judges m JOIN tab_judge j ON j.id = m.judge_id
        UNION
        SELECT 0, r.id, j.id, j.name
        FROM tab_round r JOIN tab_judge j ON j.id = r.chair_id
        UNION
        SELECT 1, outround_id, j.id, j.name
        FROM tab_outround_judges m JOIN tab_judge j ON j.id = m.judge_id
        UNION
        SELECT 1, r.id, j.id, j.name
        FROM tab_outround r JOIN tab_judge j ON j.id = r.chair_id
        ORDER BY stage, debate, judge
    ";
    let mut by_round: JudgeAssignments = HashMap::new();
    for (stage, debate, judge, name) in sqlx::query_as::<_, (i64, i32, i32, String)>(QUERY)
        .fetch_all(db)
        .await?
    {
        by_round
            .entry((stage, debate))
            .or_default()
            .push((judge, name));
    }
    Ok(by_round)
}

async fn round_stats(
    db: &mut MySqlConnection,
) -> Result<HashMap<i32, BTreeMap<i32, Stats>>, sqlx::Error> {
    let mut by_round: HashMap<i32, BTreeMap<i32, Stats>> = HashMap::new();
    for (round, debater, speaks, ranks) in sqlx::query_as::<_, (i32, i32, String, String)>(
        "SELECT round_id, debater_id, CAST(speaks AS CHAR), CAST(ranks AS CHAR)
         FROM tab_roundstats ORDER BY id",
    )
    .fetch_all(db)
    .await?
    {
        let parse = |value: String| {
            value
                .parse::<f64>()
                .map_err(|_| sqlx::Error::Protocol("Invalid ballot stat".into()))
        };
        by_round.entry(round).or_default().insert(
            debater,
            Stats {
                speaks: parse(speaks)?,
                ranks: parse(ranks)?,
            },
        );
    }
    Ok(by_round)
}

fn division(status: i32) -> &'static str {
    if status == 1 { "novice" } else { "varsity" }
}

fn apda(id: Option<i32>) -> Option<i32> {
    id.filter(|&id| id != -1)
}

fn elimination_label(size: i32) -> String {
    match size {
        2 => "Final",
        4 => "Semifinal",
        8 => "Quarterfinal",
        16 => "Octofinal",
        32 => "Double-Octofinal",
        64 => "Triple-Octofinal",
        _ => return format!("Elim of {size}"),
    }
    .into()
}
