use std::collections::HashMap;

use mittab_tabulation::{
    ballots::ScoringPolicy,
    legacy_random::LegacyRandom,
    rankings::{Score, Stat},
    rooms::{Room, RoomError, Tag, assign_rooms},
};
use ring::rand::{SecureRandom, SystemRandom};
use sqlx::MySqlConnection;

use crate::{settings::Settings, staff_pairing::ChangeError, tournament_data::TournamentData};

pub async fn plan(
    db: &mut MySqlConnection,
    settings: &Settings,
    round: i32,
) -> Result<Vec<(i32, i32)>, ChangeError> {
    let mut pairings = sqlx::query_as::<_, (i32, i32, i32, i32, i32)>(
        "SELECT r.id, r.gov_team_id, r.opp_team_id, g.seed, o.seed FROM tab_round r
         JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         WHERE r.round_number = ? ORDER BY r.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?;
    if pairings.is_empty() {
        return Err(ChangeError::Conflict(
            "There are no debates to assign rooms to.",
        ));
    }

    let seeding = settings.flag("enable_room_seeding", false);
    if !seeding {
        let mut seed = [0_u8; 4];
        SystemRandom::new()
            .fill(&mut seed)
            .map_err(|_| ChangeError::Storage)?;
        LegacyRandom::seeded(u32::from_ne_bytes(seed).into()).shuffle(&mut pairings);
    } else if round == 1 {
        pairings
            .sort_by_key(|&(_, _, _, gov, opp)| std::cmp::Reverse((gov.max(opp), gov.min(opp))));
    } else {
        let data = TournamentData::load(db).await?;
        let scores = data
            .scores_by_team(ScoringPolicy {
                current_round: settings.integer("cur_round", 1),
                lenient_late_through: settings.integer("lenient_late", 0),
            })
            .map_err(ChangeError::Conflict)?;
        let strength = |gov: i32, opp: i32| {
            let government = &scores[&i64::from(gov)];
            let opposition = &scores[&i64::from(opp)];
            seeded_key(government, opposition)
        };
        pairings.sort_by(|&(_, gov_a, opp_a, _, _), &(_, gov_b, opp_b, _, _)| {
            let a = strength(gov_a, opp_a);
            let b = strength(gov_b, opp_b);
            b.0.total_cmp(&a.0)
                .then_with(|| b.1.total_cmp(&a.1))
                .then_with(|| b.2.total_cmp(&a.2))
        });
    }

    let rooms = sqlx::query_as::<_, (i32, i32)>(
        "SELECT r.id, CAST(r.rank * 100 AS SIGNED) FROM tab_room r
         JOIN tab_roomcheckin c ON c.room_id = r.id WHERE c.round_number = ? ORDER BY r.id",
    )
    .bind(round)
    .fetch_all(&mut *db)
    .await?;
    let mut tags_by_room: HashMap<i32, Vec<i64>> = HashMap::new();
    for (room, tag) in sqlx::query_as::<_, (i32, i32)>(
        "SELECT t.room_id, t.roomtag_id FROM tab_room_tags t JOIN tab_roomcheckin c ON c.room_id = t.room_id
         WHERE c.round_number = ?",
    ).bind(round).fetch_all(&mut *db).await? {
        tags_by_room.entry(room).or_default().push(i64::from(tag));
    }
    let mut required_by_pairing: HashMap<i32, Vec<Tag>> = HashMap::new();
    for (pairing, tag, priority) in sqlx::query_as::<_, (i32, i32, i32)>(
        "SELECT r.id, t.id, CAST(t.priority * 100 AS SIGNED) FROM tab_round r
         JOIN tab_team_required_room_tags x ON x.team_id IN (r.gov_team_id, r.opp_team_id)
         JOIN tab_roomtag t ON t.id = x.roomtag_id WHERE r.round_number = ?
         UNION
         SELECT r.id, t.id, CAST(t.priority * 100 AS SIGNED) FROM tab_round r
         JOIN tab_round_judges j ON j.round_id = r.id
         JOIN tab_judge_required_room_tags x ON x.judge_id = j.judge_id
         JOIN tab_roomtag t ON t.id = x.roomtag_id WHERE r.round_number = ?",
    )
    .bind(round)
    .bind(round)
    .fetch_all(db)
    .await?
    {
        required_by_pairing.entry(pairing).or_default().push(Tag {
            id: i64::from(tag),
            priority_hundredths: priority,
        });
    }
    let room_specs: Vec<_> = rooms
        .iter()
        .map(|&(id, rank_hundredths)| Room {
            rank_hundredths,
            tags: tags_by_room.get(&id).map_or(&[], Vec::as_slice),
        })
        .collect();
    let requirements: Vec<_> = pairings
        .iter()
        .map(|(id, ..)| required_by_pairing.get(id).map_or(&[][..], Vec::as_slice))
        .collect();
    let assignments = assign_rooms(&requirements, &room_specs, seeding).map_err(|error| {
        let message = match error {
            RoomError::InsufficientRooms { .. } => "Check in more rooms before assigning rooms.",
            _ => "Could not assign a room to every debate.",
        };
        ChangeError::Conflict(message)
    })?;
    Ok(pairings
        .iter()
        .zip(assignments)
        .map(|((pairing, ..), index)| (*pairing, rooms[index].0))
        .collect())
}

pub(crate) fn seeded_key(gov: &Score, opp: &Score) -> (f64, f64, f64) {
    let gov_speaks = gov.get(Stat::Speaks);
    let opp_speaks = opp.get(Stat::Speaks);
    (
        gov.get(Stat::Wins).max(opp.get(Stat::Wins)),
        gov_speaks.max(opp_speaks),
        gov_speaks.min(opp_speaks),
    )
}
