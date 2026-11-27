use std::collections::{BTreeMap, HashMap};

use chrono::NaiveDateTime;
use ring::digest;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use sqlx::{AssertSqlSafe, MySqlConnection};

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
    match scope {
        Pairings::Inround(number) => {
            capture_selection(db, Some(("r.round_number = ?", number))).await
        }
        Pairings::Outround => capture_selection(db, None).await,
    }
}

pub(super) async fn capture_team(
    db: &mut MySqlConnection,
    team: i32,
) -> Result<BTreeMap<i32, Snapshot>, sqlx::Error> {
    capture_selection(db, Some(("? IN (r.gov_team_id, r.opp_team_id)", team))).await
}

async fn capture_selection(
    db: &mut MySqlConnection,
    prelim: Option<(&'static str, i32)>,
) -> Result<BTreeMap<i32, Snapshot>, sqlx::Error> {
    let query = if let Some((condition, _)) = prelim {
        format!(
            "SELECT r.id, g.id, g.name, o.id, o.name, m.id, m.name, r.chair_id, r.victor,
                    r.round_number, NULL, NULL, NULL
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id
         JOIN tab_team o ON o.id = r.opp_team_id LEFT JOIN tab_room m ON m.id = r.room_id
         WHERE {condition} ORDER BY r.id"
        )
    } else {
        "SELECT r.id, g.id, g.name, o.id, o.name, m.id, m.name, r.chair_id, r.victor,
                    r.num_teams, r.type_of_round, r.choice, r.sidelock
         FROM tab_outround r JOIN tab_team g ON g.id = r.gov_team_id
         JOIN tab_team o ON o.id = r.opp_team_id LEFT JOIN tab_room m ON m.id = r.room_id
         ORDER BY r.id"
            .into()
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
    let mut selection = sqlx::query_as::<_, Row>(AssertSqlSafe(query));
    if let Some((_, value)) = prelim {
        selection = selection.bind(value);
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

    let judges = if let Some((condition, value)) = prelim {
        sqlx::query_as::<_, (i32, i32, String)>(AssertSqlSafe(format!(
            "SELECT x.round_id, j.id, j.name FROM tab_round_judges x
             JOIN tab_round r ON r.id = x.round_id JOIN tab_judge j ON j.id = x.judge_id
             WHERE {condition} ORDER BY j.name, j.id",
        )))
        .bind(value)
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
    if let Some((condition, value)) = prelim {
        for (round, debater, name, role, speaks, ranks) in sqlx::query_as::<_, (i32, i32, String, Option<String>, String, String)>(AssertSqlSafe(format!(
            "SELECT s.round_id, d.id, d.name, s.debater_role, CAST(s.speaks AS CHAR), CAST(s.ranks AS CHAR)
             FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id
             JOIN tab_debater d ON d.id = s.debater_id WHERE {condition}
             ORDER BY s.debater_role, s.id",
        ))).bind(value).fetch_all(&mut *db).await? {
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
    #[serde(skip)]
    pub structured: bool,
}

#[derive(Serialize)]
pub struct Change {
    pub field: &'static str,
    pub before: String,
    pub after: String,
}

pub struct Histories {
    pub retained: HashMap<i32, Vec<History>>,
    pub removed: Vec<(i32, Snapshot)>,
    current_judges: HashMap<i32, std::collections::BTreeSet<String>>,
    board_judges: HashMap<i32, std::collections::BTreeSet<String>>,
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
        self.board_judges.get(&id)
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
    before: Option<Value>,
    after: Option<Value>,
}

fn audited_snapshot(raw: Value) -> Option<Snapshot> {
    let Value::Object(mut fields) = raw else {
        return None;
    };
    fn field<T: DeserializeOwned>(
        fields: &mut serde_json::Map<String, Value>,
        key: &str,
    ) -> Option<T> {
        serde_json::from_value(fields.remove(key)?).ok()
    }
    let mut judges: Vec<Identity> = field::<Vec<Value>>(&mut fields, "judges")
        .unwrap_or_default()
        .into_iter()
        .filter_map(|judge| serde_json::from_value(judge).ok())
        .collect();
    // The order of an imported panel must not encode calibration or assignment priority.
    judges.sort_by_cached_key(|judge| (judge.name.to_lowercase(), judge.id));
    let scores = field::<Vec<Value>>(&mut fields, "scores")
        .unwrap_or_default()
        .into_iter()
        .filter_map(|score| {
            let score = score.as_object()?;
            let text = |key| match score.get(key) {
                Some(Value::String(value)) => value.clone(),
                Some(Value::Number(value)) => value.to_string(),
                _ => "None".into(),
            };
            Some(Scores {
                debater: score
                    .get("debater")
                    .and_then(Value::as_i64)
                    .and_then(|id| i32::try_from(id).ok())
                    .unwrap_or_default(),
                name: text("name"),
                role: score
                    .get("role")
                    .filter(|role| role.is_string() || role.is_number())
                    .map(|_| text("role")),
                speaks: text("speaks"),
                ranks: text("ranks"),
            })
        })
        .collect();
    Some(Snapshot {
        gov_team: field(&mut fields, "gov_team"),
        opp_team: field(&mut fields, "opp_team"),
        room: field(&mut fields, "room"),
        judges,
        chair: field(&mut fields, "chair"),
        victor: field(&mut fields, "victor"),
        num_teams: field(&mut fields, "num_teams"),
        division: field(&mut fields, "division"),
        choice: field(&mut fields, "choice"),
        sidelock: field(&mut fields, "sidelock"),
        round_number: field(&mut fields, "round_number"),
        scores: Some(scores),
    })
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
    let mut removed = Vec::new();
    let mut current_judges: HashMap<i32, std::collections::BTreeSet<String>> = HashMap::new();
    let mut board_judges = HashMap::<i32, std::collections::BTreeSet<String>>::new();
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
        let structured = public.is_some();
        if current && matches!(kind.as_str(), "manual_judge_assign" | "manual_judge_remove" | "manual_chair_change") {
            board_judges.entry(id).or_default().extend([
                assigned.as_ref(), chair.as_ref(), new_chair.as_ref(),
            ].into_iter().flatten().cloned());
        }
        let changes = if let Some(public) = public {
            let Ok(change) = serde_json::from_str::<PublicChange>(&public) else { continue; };
            if !MANUAL_SOURCES.contains(&change.source.as_str()) {
                continue;
            }
            let deletion = change.after.is_none();
            let before = change.before.and_then(audited_snapshot);
            let after = change.after.and_then(audited_snapshot);
            let differences = compare(before.as_ref(), after.as_ref());
            if deletion && let Some(before) = before {
                removed.push((id, before));
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
                actor, time: time.format("%b %d %H:%M UTC").to_string(), changes, structured,
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
        board_judges,
    })
}

fn compare(before: Option<&Snapshot>, after: Option<&Snapshot>) -> Vec<Change> {
    let had_pairing = before.is_some();
    let has_pairing = after.is_some();
    let before = before.cloned().unwrap_or_default();
    let after = after.cloned().unwrap_or_default();
    let identity = |value: Option<Identity>| value.map_or("Unassigned".into(), |v| v.name);
    let judges = |values: Option<Vec<Identity>>| {
        values.map_or_else(
            || "Unassigned".into(),
            |values| {
                values
                    .into_iter()
                    .map(|j| j.name)
                    .collect::<Vec<_>>()
                    .join(", ")
            },
        )
    };
    let scores = |values: Option<Vec<Scores>>| {
        values.map_or_else(
            || "Unassigned".into(),
            |values| {
                values
                    .into_iter()
                    .map(|score| {
                        format!(
                            "{} ({}): {} speaks, {} rank",
                            score.name,
                            score.role.unwrap_or_default().to_uppercase(),
                            score.speaks,
                            score.ranks
                        )
                    })
                    .collect::<Vec<_>>()
                    .join("; ")
            },
        )
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
    diff!(chair, "chair", value);
    diff!(choice, "choice", |choice: Option<i32>| match choice {
        None => "Unassigned",
        Some(code @ 0..=2) => choice_name(code),
        _ => "Unknown",
    }
    .into());
    diff!(victor, "outcome", |victor: Option<i32>| match victor {
        None => "Unassigned",
        Some(code @ 0..=6) => outcome(code),
        _ => "Unknown",
    }
    .into());
    diff!(round_number, "round number", value);
    diff!(num_teams, "stage", value);
    diff!(division, "division", value);
    diff!(sidelock, "sidelock", |locked| match locked {
        Some(true) => "True",
        Some(false) => "False",
        None => "Unassigned",
    }
    .into());
    let before_judges = had_pairing.then_some(before.judges);
    let after_judges = has_pairing.then_some(after.judges);
    if before_judges != after_judges {
        changes.push(Change {
            field: "judges",
            before: judges(before_judges),
            after: judges(after_judges),
        });
    }
    let before_scores = had_pairing.then(|| before.scores.unwrap_or_default());
    let after_scores = has_pairing.then(|| after.scores.unwrap_or_default());
    if before_scores != after_scores {
        changes.push(Change {
            field: "scores",
            before: scores(before_scores),
            after: scores(after_scores),
        });
    }
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
            let reference = if source == "admin deletion" && new.is_none() {
                format!("Outround {id}")
            } else {
                format!(
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
                )
            };
            let changes = json!({"public": {"source": source, "before": old, "after": new}});
            record(db, "outround", id, user, reference, changes, old.is_none()).await?;
        }
    }
    Ok(())
}
