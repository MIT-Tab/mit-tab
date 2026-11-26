use std::collections::{BTreeMap, BTreeSet, HashMap};

use mittab_tabulation::{
    judges::{self, Debate, Judge, OutroundGroup, OutroundPolicy, Panel, RankingMode},
    legacy_random::LegacyRandom,
    rooms::{Room, Tag, assign_rooms},
};
use ring::rand::{SecureRandom, SystemRandom};
use sqlx::{MySqlConnection, QueryBuilder};

use crate::{
    App,
    board_snapshot::{self, Pairings, Snapshot},
    settings::Settings,
    staff_elimination_store::{Error, Stage},
    staff_pairing::lock_round,
};

pub enum Assignment {
    Rooms,
    Judges,
}

const SETTINGS: &[&str] = &[
    "judge_pairing_mode",
    "outs_judge_pairing_mode",
    "outs_round_priority",
    "outround_judge_priority",
    "var_panel_size",
    "nov_panel_size",
    "enable_room_seeding",
];

pub async fn assign(
    app: &App,
    user: i32,
    selected: &[Stage],
    assignment: Assignment,
) -> Result<(), Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let before = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let active: Vec<_> = selected
        .iter()
        .copied()
        .filter(|stage| before.values().any(|pairing| stage.matches(pairing)))
        .collect();
    if active.is_empty() {
        return Err(Error::Invalid("Choose an existing elimination stage."));
    }
    let groups = groups(&mut db, &before, &active, &settings).await?;
    if groups.iter().all(|(_, debates)| debates.is_empty()) {
        return Ok(());
    }
    let label = active
        .iter()
        .map(|stage| format!("Ro{}-{}", stage.size, stage.division_name()))
        .collect::<Vec<_>>()
        .join(", ");
    let (event, kind, name) = match assignment {
        Assignment::Judges => (
            "staff elimination judge assignment",
            3,
            "Before Judge Assign",
        ),
        Assignment::Rooms => ("staff elimination room assignment", 5, "Before Room Assign"),
    };
    app.backups
        .save_named(&format!("{name} Round {label}"), kind, &label)
        .await
        .map_err(Error::Invalid)?;
    match assignment {
        Assignment::Judges => judge_assignments(&mut db, &groups, &settings).await?,
        Assignment::Rooms => room_assignments(&mut db, &groups, &settings).await?,
    }
    let after = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    board_snapshot::audit_pairings(&mut db, &before, &after, user, event).await?;
    db.commit().await?;
    Ok(())
}

async fn groups(
    db: &mut MySqlConnection,
    before: &BTreeMap<i32, Snapshot>,
    stages: &[Stage],
    settings: &Settings,
) -> Result<Vec<(Stage, Vec<Debate>)>, Error> {
    let seeds: BTreeMap<_, _> =
        sqlx::query_as::<_, (i32, i32)>("SELECT team_id, effective_seed FROM tab_breakingteam")
            .fetch_all(db)
            .await?
            .into_iter()
            .collect();
    let middle = settings.integer("outs_round_priority", 0) == 1;
    let mut groups = Vec::new();
    for &stage in stages {
        let mut group = Vec::new();
        for (&id, matchup) in before
            .iter()
            .filter(|(_, snapshot)| stage.matches(snapshot) && snapshot.victor == Some(0))
        {
            let gov = matchup.gov_team.as_ref().ok_or(Error::Missing)?.id;
            let opp = matchup.opp_team.as_ref().ok_or(Error::Missing)?.id;
            let [gov_seed, opp_seed] =
                [gov, opp].map(|id| seeds.get(&id).copied().unwrap_or(i32::MAX));
            let priority = if middle {
                gov_seed.max(opp_seed)
            } else {
                gov_seed.min(opp_seed)
            };
            group.push((
                priority,
                Debate {
                    id,
                    teams: [gov, opp],
                    strength: [0.0; 3],
                    bubble: false,
                },
            ));
        }
        group.sort_by_key(|(priority, debate)| (*priority, debate.id));
        groups.push((stage, group.into_iter().map(|(_, debate)| debate).collect()));
    }
    Ok(groups)
}

async fn judge_assignments(
    db: &mut MySqlConnection,
    groups: &[(Stage, Vec<Debate>)],
    settings: &Settings,
) -> Result<(), Error> {
    let mut conflicts: HashMap<i32, std::collections::HashSet<i32>> = HashMap::new();
    for (judge, team) in sqlx::query_as::<_, (i32, i32)>(
        "SELECT s.judge_id, s.team_id FROM tab_scratch s JOIN tab_checkin c
         ON s.judge_id = c.judge_id AND c.round_number = 0
         UNION
         SELECT affiliations.judge_id, t.id FROM tab_judge_schools affiliations JOIN tab_team t
          ON affiliations.school_id IN (t.school_id, t.hybrid_school_id)
         JOIN tab_checkin c ON affiliations.judge_id = c.judge_id AND c.round_number = 0",
    )
    .fetch_all(&mut *db)
    .await?
    {
        conflicts.entry(judge).or_default().insert(team);
    }
    let judges: Vec<_> = sqlx::query_as::<_, (i32, i32)>(
        "SELECT j.id, CAST(j.`rank` * 100 AS SIGNED) FROM tab_judge j
         JOIN tab_checkin c ON j.id = c.judge_id AND c.round_number = 0 ORDER BY j.name, j.id",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, rank)| Judge {
        id,
        rank,
        conflicts: conflicts.remove(&id).unwrap_or_default(),
        ..Judge::default()
    })
    .collect();
    let groups: Vec<_> = groups
        .iter()
        .map(|(stage, debates)| {
            let key = if stage.division == 0 {
                "var_panel_size"
            } else {
                "nov_panel_size"
            };
            let panel_size = settings.integer(key, 3).clamp(0, 25) as usize;
            OutroundGroup {
                division: stage.division,
                panel_size,
                debates,
            }
        })
        .collect();
    let policy = OutroundPolicy {
        ranking: if settings.integer("judge_pairing_mode", 0) == 1 {
            RankingMode::Classic
        } else {
            RankingMode::Shared
        },
        snake: settings.integer("outs_judge_pairing_mode", 0) == 0,
        novice_chairs_first: settings.integer("outround_judge_priority", 0) == 1,
    };
    let assignments = judges::assign_outrounds(&groups, &judges, &policy).map_err(|_| {
        Error::Invalid(
            "There are not enough eligible judges for every panel in the selected stages.",
        )
    })?;
    let ids: Vec<_> = groups
        .iter()
        .flat_map(|group| group.debates.iter().map(|debate| debate.id))
        .collect();
    let mut remove = QueryBuilder::new("DELETE FROM tab_outround_judges WHERE outround_id IN (");
    let mut bind = remove.separated(", ");
    for &id in &ids {
        bind.push_bind(id);
    }
    remove.push(")").build().execute(&mut *db).await?;
    let mut clear = QueryBuilder::new("UPDATE tab_outround SET chair_id = NULL WHERE id IN (");
    let mut bind = clear.separated(", ");
    for id in ids {
        bind.push_bind(id);
    }
    clear.push(")").build().execute(&mut *db).await?;
    for Panel {
        debate,
        chair,
        wings,
    } in assignments
    {
        sqlx::query("UPDATE tab_outround SET chair_id = ? WHERE id = ?")
            .bind(chair)
            .bind(debate)
            .execute(&mut *db)
            .await?;
        for judge in std::iter::once(chair).chain(wings) {
            sqlx::query("INSERT INTO tab_outround_judges (outround_id, judge_id) VALUES (?, ?)")
                .bind(debate)
                .bind(judge)
                .execute(&mut *db)
                .await?;
        }
        sqlx::query(
            "INSERT INTO tab_auditevent (object_id, event_type, created_at, object_repr, changes, note, content_type_id)
             SELECT r.id, 'judges_paired', UTC_TIMESTAMP(6), CONCAT('Outround ', r.num_teams, ' between ', g.name, ' and ', o.name),
                    JSON_OBJECT(), '', ct.id FROM tab_outround r JOIN tab_team g ON g.id = r.gov_team_id
              JOIN tab_team o ON o.id = r.opp_team_id JOIN django_content_type ct ON ct.app_label = 'tab' AND ct.model = 'outround'
             WHERE r.id = ?",
        ).bind(debate).execute(&mut *db).await?;
    }
    Ok(())
}

async fn room_assignments(
    db: &mut MySqlConnection,
    groups: &[(Stage, Vec<Debate>)],
    settings: &Settings,
) -> Result<(), Error> {
    let mut active: Vec<_> = groups.iter().flat_map(|(_, debates)| debates).collect();
    let seeding = settings.flag("enable_room_seeding", false);
    if !seeding {
        let mut seed = [0; 8];
        SystemRandom::new()
            .fill(&mut seed)
            .map_err(|_| Error::Database)?;
        LegacyRandom::seeded(u64::from_ne_bytes(seed)).shuffle(&mut active);
    }
    let available = sqlx::query_as::<_, (i32, i32)>(
        "SELECT r.id, CAST(r.`rank` * 100 AS SIGNED) FROM tab_room r JOIN tab_roomcheckin c
         ON c.room_id = r.id AND c.round_number = 0 ORDER BY r.`rank` DESC, c.id",
    )
    .fetch_all(&mut *db)
    .await?;
    let mut room_tags: HashMap<i32, Vec<i64>> = HashMap::new();
    for (room, tag) in
        sqlx::query_as::<_, (i32, i32)>("SELECT room_id, roomtag_id FROM tab_room_tags")
            .fetch_all(&mut *db)
            .await?
    {
        room_tags.entry(room).or_default().push(i64::from(tag));
    }
    let mut needed: BTreeMap<i32, BTreeSet<(i32, i32)>> = BTreeMap::new();
    for (id, tag, priority) in sqlx::query_as::<_, (i32, i32, i32)>(
        "SELECT r.id, t.id, CAST(t.priority * 100 AS SIGNED) FROM tab_outround r
         JOIN tab_team_required_room_tags x ON x.team_id IN (r.gov_team_id, r.opp_team_id)
         JOIN tab_roomtag t ON t.id = x.roomtag_id WHERE r.victor = 0
         UNION
         SELECT r.id, t.id, CAST(t.priority * 100 AS SIGNED) FROM tab_outround r
         JOIN tab_outround_judges j ON j.outround_id = r.id JOIN tab_judge_required_room_tags x ON x.judge_id = j.judge_id
         JOIN tab_roomtag t ON t.id = x.roomtag_id WHERE r.victor = 0",
    ).fetch_all(&mut *db).await? { needed.entry(id).or_default().insert((tag, priority)); }
    let rooms: Vec<_> = available
        .iter()
        .map(|&(id, rank_hundredths)| Room {
            rank_hundredths,
            tags: room_tags.get(&id).map_or(&[], Vec::as_slice),
        })
        .collect();
    let requirements: Vec<Vec<_>> = active
        .iter()
        .map(|debate| {
            needed
                .get(&debate.id)
                .into_iter()
                .flatten()
                .map(|&(id, priority_hundredths)| Tag {
                    id: i64::from(id),
                    priority_hundredths,
                })
                .collect()
        })
        .collect();
    let required = requirements.iter().map(Vec::as_slice).collect::<Vec<_>>();
    let plan = assign_rooms(&required, &rooms, seeding)
        .map_err(|_| Error::Invalid("Check in enough rooms for the selected stages."))?;
    for (debate, room) in active.into_iter().zip(plan) {
        sqlx::query("UPDATE tab_outround SET room_id = ? WHERE id = ?")
            .bind(available[room].0)
            .bind(debate.id)
            .execute(&mut *db)
            .await?;
    }
    Ok(())
}
