use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::json;
use sqlx::MySqlConnection;

#[derive(PartialEq, Serialize)]
struct Identity {
    id: i32,
    name: String,
}

#[derive(PartialEq, Serialize)]
struct Score {
    debater: i32,
    role: Option<String>,
    name: String,
    speaks: String,
    ranks: String,
}

#[derive(PartialEq, Serialize)]
pub struct Snapshot {
    round_number: i32,
    gov_team: Identity,
    opp_team: Identity,
    room: Option<Identity>,
    judges: Vec<Identity>,
    chair: Option<i32>,
    victor: i32,
    scores: Vec<Score>,
}

type RoundRow = (
    i32,
    i32,
    String,
    i32,
    String,
    Option<i32>,
    Option<String>,
    Option<i32>,
    i32,
);

pub async fn capture(
    db: &mut MySqlConnection,
    number: i32,
) -> Result<BTreeMap<i32, Snapshot>, sqlx::Error> {
    capture_scope(db, number, None).await
}

pub async fn capture_one(
    db: &mut MySqlConnection,
    number: i32,
    id: i32,
) -> Result<BTreeMap<i32, Snapshot>, sqlx::Error> {
    capture_scope(db, number, Some(id)).await
}

async fn capture_scope(
    db: &mut MySqlConnection,
    number: i32,
    id: Option<i32>,
) -> Result<BTreeMap<i32, Snapshot>, sqlx::Error> {
    let mut rounds = BTreeMap::new();
    for (id, gov, gov_name, opp, opp_name, room, room_name, chair, victor) in
        sqlx::query_as::<_, RoundRow>(
            "SELECT r.id, r.gov_team_id, g.name, r.opp_team_id, o.name, r.room_id, m.name, r.chair_id, r.victor
             FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id
             JOIN tab_team o ON o.id = r.opp_team_id
             LEFT JOIN tab_room m ON m.id = r.room_id WHERE r.round_number = ? AND (? IS NULL OR r.id = ?) ORDER BY r.id",
        ).bind(number).bind(id).bind(id).fetch_all(&mut *db).await?
    {
        rounds.insert(id, Snapshot {
            round_number: number,
            gov_team: Identity { id: gov, name: gov_name },
            opp_team: Identity { id: opp, name: opp_name },
            room: room.zip(room_name).map(|(id, name)| Identity { id, name }),
            judges: Vec::new(),
            chair,
            victor,
            scores: Vec::new(),
        });
    }
    for (round, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
        "SELECT r.id, j.id, j.name FROM tab_round r
         JOIN tab_round_judges x ON x.round_id = r.id JOIN tab_judge j ON j.id = x.judge_id
         WHERE r.round_number = ? AND (? IS NULL OR r.id = ?) ORDER BY j.name, j.id",
    )
    .bind(number)
    .bind(id)
    .bind(id)
    .fetch_all(&mut *db)
    .await?
    {
        if let Some(snapshot) = rounds.get_mut(&round) {
            snapshot.judges.push(Identity { id, name });
        }
    }
    for (round, debater, role, name, speaks, ranks) in
        sqlx::query_as::<_, (i32, i32, Option<String>, String, String, String)>(
            "SELECT r.id, d.id, s.debater_role, d.name, CAST(s.speaks AS CHAR), CAST(s.ranks AS CHAR)
             FROM tab_round r JOIN tab_roundstats s ON s.round_id = r.id JOIN tab_debater d ON d.id = s.debater_id
             WHERE r.round_number = ? AND (? IS NULL OR r.id = ?) ORDER BY s.debater_role, s.id",
        ).bind(number).bind(id).bind(id).fetch_all(db).await?
    {
        if let Some(snapshot) = rounds.get_mut(&round) {
            snapshot.scores.push(Score { debater, role, name, speaks, ranks });
        }
    }
    Ok(rounds)
}

pub async fn record(
    db: &mut MySqlConnection,
    before: &BTreeMap<i32, Snapshot>,
    after: &BTreeMap<i32, Snapshot>,
    user: i32,
    source: &'static str,
) -> Result<(), sqlx::Error> {
    let events = before
        .keys()
        .chain(after.keys().filter(|id| !before.contains_key(id)));
    for &id in events {
        let old = before.get(&id);
        let new = after.get(&id);
        if old == new {
            continue;
        }
        let reference = new.or(old).expect("audit event snapshot");
        let object = if source == "admin deletion" && new.is_none() {
            format!("Round {id}")
        } else {
            format!(
                "Round {} between {} and {}",
                reference.round_number, reference.gov_team.name, reference.opp_team.name
            )
        };
        let changes = json!({ "public": { "before": old, "after": new, "source": source } });
        sqlx::query(
            "INSERT INTO tab_auditevent
               (object_id, event_type, created_at, object_repr, changes, note, content_type_id, user_id)
             VALUES (?, ?, UTC_TIMESTAMP(6), ?, ?, '',
                (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = 'round'), ?)",
        )
        .bind(id).bind(if old.is_none() { "create" } else { "edit" }).bind(object)
        .bind(changes.to_string()).bind(user).execute(&mut *db).await?;
    }
    Ok(())
}
