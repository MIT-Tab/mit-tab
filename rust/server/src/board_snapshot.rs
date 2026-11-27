use std::collections::{BTreeMap, HashMap};

use chrono::NaiveDateTime;
use ring::digest;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::MySqlConnection;

#[derive(Clone, Deserialize, PartialEq, Serialize)]
pub struct Identity {
    pub id: i32,
    pub name: String,
}

#[derive(Clone, Deserialize, PartialEq, Serialize)]
pub struct Scores {
    pub debater: i32,
    pub name: String,
    pub role: Option<String>,
    pub speaks: String,
    pub ranks: String,
}

#[derive(Clone, Default, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct Snapshot {
    pub gov_team: Option<Identity>,
    pub opp_team: Option<Identity>,
    pub room: Option<Identity>,
    pub judges: Vec<Identity>,
    pub chair: Option<i32>,
    pub victor: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub num_teams: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub division: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub choice: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sidelock: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub round_number: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scores: Option<Vec<Scores>>,
}

impl Snapshot {
    pub fn revision(&self) -> String {
        let bytes = serde_json::to_vec(self).expect("serializable pairing");
        hex::encode(digest::digest(&digest::SHA256, &bytes))
    }
}

#[derive(Clone, Copy)]
pub enum Pairings {
    Inround(i32),
    Outround,
}

pub async fn capture(
    db: &mut MySqlConnection,
    scope: Pairings,
) -> Result<BTreeMap<i32, Snapshot>, sqlx::Error> {
    let prelim = match scope {
        Pairings::Inround(number) => Some(number),
        Pairings::Outround => None,
    };
    let query = if prelim.is_some() {
        "SELECT r.id, g.id, g.name, o.id, o.name, m.id, m.name, r.chair_id, r.victor,
                    r.round_number, NULL, NULL, NULL
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id
         JOIN tab_team o ON o.id = r.opp_team_id LEFT JOIN tab_room m ON m.id = r.room_id
         WHERE r.round_number = ? ORDER BY r.id"
    } else {
        "SELECT r.id, g.id, g.name, o.id, o.name, m.id, m.name, r.chair_id, r.victor,
                    r.num_teams, r.type_of_round, r.choice, r.sidelock
         FROM tab_outround r JOIN tab_team g ON g.id = r.gov_team_id
         JOIN tab_team o ON o.id = r.opp_team_id LEFT JOIN tab_room m ON m.id = r.room_id
         ORDER BY r.id"
    };
    type Row = (
        i32,
        i32,
        String,
        i32,
        String,
        Option<i32>,
        Option<String>,
        Option<i32>,
        i32,
        i32,
        Option<i32>,
        Option<i32>,
        Option<bool>,
    );
    let mut selection = sqlx::query_as::<_, Row>(query);
    if let Some(number) = prelim {
        selection = selection.bind(number);
    }
    let mut snapshots: BTreeMap<_, _> = selection
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(
            |(
                id,
                gov,
                gname,
                opp,
                oname,
                room,
                rname,
                chair,
                victor,
                stage,
                division,
                choice,
                sidelock,
            )| {
                (
                    id,
                    Snapshot {
                        gov_team: Some(Identity {
                            id: gov,
                            name: gname,
                        }),
                        opp_team: Some(Identity {
                            id: opp,
                            name: oname,
                        }),
                        room: room.zip(rname).map(|(id, name)| Identity { id, name }),
                        judges: Vec::new(),
                        chair,
                        victor: Some(victor),
                        num_teams: prelim.is_none().then_some(stage),
                        division,
                        choice,
                        sidelock,
                        round_number: prelim.map(|_| stage),
                        scores: prelim.map(|_| Vec::new()),
                    },
                )
            },
        )
        .collect();

    let judges = if let Some(number) = prelim {
        sqlx::query_as::<_, (i32, i32, String)>(
            "SELECT x.round_id, j.id, j.name FROM tab_round_judges x
             JOIN tab_round r ON r.id = x.round_id JOIN tab_judge j ON j.id = x.judge_id
             WHERE r.round_number = ? ORDER BY j.name, j.id",
        )
        .bind(number)
        .fetch_all(&mut *db)
        .await?
    } else {
        sqlx::query_as::<_, (i32, i32, String)>(
            "SELECT x.outround_id, j.id, j.name FROM tab_outround_judges x
             JOIN tab_judge j ON j.id = x.judge_id ORDER BY j.name, j.id",
        )
        .fetch_all(&mut *db)
        .await?
    };
    for (round, id, name) in judges {
        if let Some(snapshot) = snapshots.get_mut(&round) {
            snapshot.judges.push(Identity { id, name });
        }
    }
    if let Some(number) = prelim {
        for (round, debater, name, role, speaks, ranks) in sqlx::query_as::<_, (i32, i32, String, Option<String>, String, String)>(
            "SELECT s.round_id, d.id, d.name, s.debater_role, CAST(s.speaks AS CHAR), CAST(s.ranks AS CHAR)
             FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id
             JOIN tab_debater d ON d.id = s.debater_id WHERE r.round_number = ?
             ORDER BY s.debater_role, s.id",
        ).bind(number).fetch_all(&mut *db).await? {
            if let Some(snapshot) = snapshots.get_mut(&round) && let Some(scores) = &mut snapshot.scores {
                scores.push(Scores { debater, name, role, speaks, ranks });
            }
        }
    }
    Ok(snapshots)
}

#[derive(Serialize)]
pub struct History {
    pub actor: String,
    pub time: String,
    pub changes: Vec<Change>,
    #[serde(skip)]
    pub legacy_time: String,
    #[serde(skip)]
    pub label: &'static str,
}

#[derive(Serialize)]
pub struct Change {
    pub field: &'static str,
    pub before: String,
    pub after: String,
}

pub struct Histories {
    pub retained: HashMap<i32, Vec<History>>,
    pub removed: BTreeMap<i32, Snapshot>,
    current_judges: HashMap<i32, std::collections::BTreeSet<String>>,
}

#[derive(Serialize)]
pub struct StaffEdits {
    pub gov: bool,
    pub opp: bool,
    pub judges: std::collections::BTreeSet<String>,
    pub history: Vec<History>,
}

impl Histories {
    pub fn manual_judge_names(&self, id: i32) -> Option<&std::collections::BTreeSet<String>> {
        self.current_judges.get(&id)
    }

    pub fn take_staff_edits(&mut self, id: i32) -> StaffEdits {
        let history = self.retained.remove(&id).unwrap_or_default();
        let changed = |field| {
            history
                .iter()
                .any(|event| event.changes.iter().any(|change| change.field == field))
        };
        StaffEdits {
            gov: changed("government"),
            opp: changed("opposition"),
            judges: self.current_judges.remove(&id).unwrap_or_default(),
            history,
        }
    }
}

#[derive(Deserialize)]
struct PublicChange {
    source: String,
    before: Option<Snapshot>,
    after: Option<Snapshot>,
}

const MANUAL_SOURCES: &[&str] = &[
    "board correction",
    "board team swap",
    "staff room correction",
    "staff team correction",
    "staff side correction",
    "staff outround result",
    "staff choice correction",
    "admin correction",
    "admin scores",
    "admin deletion",
];

pub async fn histories(
    db: &mut MySqlConnection,
    model: &'static str,
) -> Result<Histories, sqlx::Error> {
    let mut retained = HashMap::<_, Vec<_>>::new();
    let mut removed = BTreeMap::new();
    let mut current_judges: HashMap<i32, std::collections::BTreeSet<String>> = HashMap::new();
    type Event = (
        i32,
        String,
        String,
        NaiveDateTime,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
        bool,
    );
    for (id, kind, actor, time, public, assigned, removed_judge, chair, new_chair, old_chair, current) in
        sqlx::query_as::<_, Event>(
            "SELECT CAST(a.object_id AS SIGNED), a.event_type, COALESCE(u.username, 'Unknown'), a.created_at,
             CASE WHEN JSON_TYPE(JSON_EXTRACT(a.changes, '$.public')) = 'OBJECT'
                THEN CAST(JSON_EXTRACT(a.changes, '$.public') AS CHAR) END,
             CASE WHEN JSON_TYPE(JSON_EXTRACT(a.changes, '$.assigned_judge')) = 'STRING'
                THEN JSON_UNQUOTE(JSON_EXTRACT(a.changes, '$.assigned_judge')) END,
             CASE WHEN JSON_TYPE(JSON_EXTRACT(a.changes, '$.removed_judge')) = 'STRING'
                THEN JSON_UNQUOTE(JSON_EXTRACT(a.changes, '$.removed_judge')) END,
             CASE WHEN JSON_TYPE(JSON_EXTRACT(a.changes, '$.chair')) = 'STRING'
                THEN JSON_UNQUOTE(JSON_EXTRACT(a.changes, '$.chair')) END,
             CASE WHEN JSON_TYPE(JSON_EXTRACT(a.changes, '$.new_chair')) = 'STRING'
                THEN JSON_UNQUOTE(JSON_EXTRACT(a.changes, '$.new_chair')) END,
             CASE WHEN JSON_TYPE(JSON_EXTRACT(a.changes, '$.old_chair')) = 'STRING'
                THEN JSON_UNQUOTE(JSON_EXTRACT(a.changes, '$.old_chair')) END,
             NOT EXISTS(SELECT 1 FROM tab_auditevent reset
                WHERE reset.content_type_id = a.content_type_id AND reset.object_id = a.object_id
                AND reset.event_type = 'judges_paired' AND reset.id > a.id)
             FROM tab_auditevent a JOIN django_content_type t ON t.id = a.content_type_id
             LEFT JOIN auth_user u ON u.id = a.user_id
             WHERE t.app_label = 'tab' AND t.model = ?
             AND (JSON_TYPE(JSON_EXTRACT(a.changes, '$.public')) = 'OBJECT'
                  OR a.event_type IN ('manual_judge_assign', 'manual_judge_remove', 'manual_chair_change'))
             ORDER BY a.created_at, a.id",
        ).bind(model).fetch_all(db).await?
    {
        let changes = if let Some(public) = public {
            let Ok(change) = serde_json::from_str::<PublicChange>(&public) else { continue; };
            if !MANUAL_SOURCES.contains(&change.source.as_str()) {
                continue;
            }
            let differences = compare(change.before.as_ref(), change.after.as_ref());
            if change.after.is_none() && let Some(before) = change.before {
                removed.insert(id, before);
            }
            differences
        } else {
            let entries = match kind.as_str() {
                "manual_judge_assign" => vec![("assigned judge", assigned), ("removed judge", removed_judge)],
                "manual_judge_remove" => vec![("removed judge", removed_judge)],
                "manual_chair_change" => vec![("chair", chair), ("new chair", new_chair), ("old chair", old_chair)],
                _ => Vec::new(),
            };
            if current {
                let judges = current_judges.entry(id).or_default();
                for (field, name) in &entries {
                    if let Some(name) = name {
                        if *field == "removed judge" {
                            judges.remove(name);
                        } else {
                            judges.insert(name.clone());
                        }
                    }
                }
            }
            entries.into_iter().filter_map(|(field, value)| value.map(|after| Change {
                field, before: String::new(), after,
            })).collect()
        };
        if !changes.is_empty() {
            retained.entry(id).or_default().push(History {
                actor, time: time.format("%b %d %H:%M UTC").to_string(), changes,
                legacy_time: time.format("%Y-%m-%d %H:%M:%S UTC").to_string(),
                label: match kind.as_str() {
                    "create" => "Created",
                    "edit" => "Edited",
                    "manual_judge_assign" => "Manual judge assignment",
                    "manual_judge_remove" => "Manual judge removal",
                    "manual_chair_change" => "Manual chair change",
                    "judges_paired" => "Automatic judge assignment",
                    _ => "Changed",
                },
            });
        }
    }
    Ok(Histories {
        retained,
        removed,
        current_judges,
    })
}

fn compare(before: Option<&Snapshot>, after: Option<&Snapshot>) -> Vec<Change> {
    let before = before.cloned().unwrap_or_default();
    let after = after.cloned().unwrap_or_default();
    let identity = |value: Option<Identity>| value.map_or("Unassigned".into(), |v| v.name);
    let judges = |values: Vec<Identity>| {
        values
            .into_iter()
            .map(|j| j.name)
            .collect::<Vec<_>>()
            .join(", ")
    };
    let scores = |values: Option<Vec<Scores>>| {
        values
            .into_iter()
            .flatten()
            .map(|score| {
                format!(
                    "{} ({}) {} speaks, {} rank",
                    score.name,
                    score.role.unwrap_or_default().to_uppercase(),
                    score.speaks,
                    score.ranks
                )
            })
            .collect::<Vec<_>>()
            .join("; ")
    };
    let value = |value: Option<i32>| value.map_or("Unassigned".into(), |value| value.to_string());
    let mut changes = Vec::new();
    macro_rules! diff {
        ($name:ident, $label:literal, $display:expr) => {
            if before.$name != after.$name {
                changes.push(Change {
                    field: $label,
                    before: $display(before.$name),
                    after: $display(after.$name),
                });
            }
        };
    }
    diff!(gov_team, "government", identity);
    diff!(opp_team, "opposition", identity);
    diff!(room, "room", identity);
    diff!(judges, "judges", judges);
    diff!(chair, "chair", value);
    diff!(choice, "choice", |choice: Option<i32>| choice_name(
        choice.unwrap_or(0)
    )
    .into());
    diff!(victor, "outcome", |victor: Option<i32>| outcome(
        victor.unwrap_or(0)
    )
    .into());
    diff!(scores, "scores", scores);
    changes
}

pub fn outcome(code: i32) -> &'static str {
    crate::ballot_entry::OUTCOMES
        .iter()
        .find(|&&(value, _)| value == code)
        .map_or("Pending", |&(_, label)| label)
}

pub fn choice_name(code: i32) -> &'static str {
    match code {
        1 => "Government",
        2 => "Opposition",
        _ => "No choice",
    }
}

pub async fn record(
    db: &mut MySqlConnection,
    model: &'static str,
    id: i32,
    user: i32,
    reference: String,
    changes: Value,
    created: bool,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO tab_auditevent
           (object_id, event_type, created_at, object_repr, changes, note, content_type_id, user_id)
         VALUES (?, ?, UTC_TIMESTAMP(6), ?, ?, '',
            (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = ?), ?)",
    )
    .bind(id)
    .bind(if created { "create" } else { "edit" })
    .bind(reference)
    .bind(changes.to_string())
    .bind(model)
    .bind(user)
    .execute(db)
    .await?;
    Ok(())
}

pub async fn audit_pairings(
    db: &mut MySqlConnection,
    before: &BTreeMap<i32, Snapshot>,
    after: &BTreeMap<i32, Snapshot>,
    user: i32,
    source: &'static str,
) -> Result<(), sqlx::Error> {
    for &id in before
        .keys()
        .chain(after.keys().filter(|id| !before.contains_key(id)))
    {
        let old = before.get(&id);
        let new = after.get(&id);
        if old != new {
            let snapshot = new.or(old).expect("pairing audit snapshot");
            let reference = format!(
                "Outround {} between {} and {}",
                snapshot.num_teams.unwrap_or(0),
                snapshot
                    .gov_team
                    .as_ref()
                    .map_or("", |team| team.name.as_str()),
                snapshot
                    .opp_team
                    .as_ref()
                    .map_or("", |team| team.name.as_str())
            );
            let changes = json!({"public": {"source": source, "before": old, "after": new}});
            record(db, "outround", id, user, reference, changes, old.is_none()).await?;
        }
    }
    Ok(())
}
