use std::collections::BTreeMap;

use chrono::{DateTime, NaiveDateTime, TimeDelta, Utc};
use sqlx::{MySql, MySqlConnection, QueryBuilder};

use crate::settings::Settings;

pub const VISIBILITY_KEYS: &[&str] = &["var_teams_visible", "nov_teams_visible"];
pub type PublishedPlacements = [BTreeMap<i32, i32>; 2];

pub async fn refresh_placements(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<PublishedPlacements, sqlx::Error> {
    let mut opening_size = [0; 2];
    for (division, size) in sqlx::query_as::<_, (i32, u32)>(
        "SELECT type_of_round, num_teams FROM tab_publishedoutroundstage
         WHERE type_of_round IN (0, 1)",
    )
    .fetch_all(&mut *db)
    .await?
    {
        let slot = &mut opening_size[division as usize];
        *slot = (*slot).max(size as i32);
    }

    let mut released: BTreeMap<(i32, i32), BTreeMap<i32, i32>> = BTreeMap::new();
    for (division, size, government, opposition, outcome) in
        sqlx::query_as::<_, (i32, i32, i32, i32, i32)>(
            "SELECT type_of_round, num_teams, gov_team_id, opp_team_id, victor FROM tab_outround
         WHERE (type_of_round = 0 AND num_teams >= ?)
            OR (type_of_round = 1 AND num_teams >= ?)
         ORDER BY id",
        )
        .bind(settings.integer(VISIBILITY_KEYS[0], 256))
        .bind(settings.integer(VISIBILITY_KEYS[1], 256))
        .fetch_all(&mut *db)
        .await?
    {
        let slot = &mut opening_size[division as usize];
        *slot = (*slot).max(size);
        let placements = released.entry((division, size)).or_default();
        let winner = match outcome {
            1 | 3 => Some(government),
            2 | 4 => Some(opposition),
            _ => None,
        };
        for team in [government, opposition] {
            let place = if winner == Some(team) {
                winning_floor(size)
            } else {
                size.div_euclid(2) + 1
            };
            placements.insert(team, place);
        }
    }

    for ((division, size), mut placements) in released {
        if size == opening_size[division as usize] {
            let breaking_teams: Vec<_> = sqlx::query_scalar::<_, i32>(
                "SELECT team_id FROM tab_breakingteam WHERE type_of_team = ?",
            )
            .bind(division)
            .fetch_all(&mut *db)
            .await?;
            for team in breaking_teams {
                placements.entry(team).or_insert(winning_floor(size));
            }
        }
        replace_stage(db, division, size, placements).await?;
    }

    let mut published = [BTreeMap::new(), BTreeMap::new()];
    for (division, team, place) in sqlx::query_as::<_, (i32, i32, u32)>(
        "SELECT stage.type_of_round, placement.team_id, MIN(placement.place)
         FROM tab_publishedteamplacement placement
         JOIN tab_publishedoutroundstage stage ON stage.id = placement.stage_id
         WHERE stage.type_of_round IN (0, 1)
         GROUP BY stage.type_of_round, placement.team_id",
    )
    .fetch_all(db)
    .await?
    {
        published[division as usize].insert(team, place as i32);
    }
    Ok(published)
}

fn winning_floor(size: i32) -> i32 {
    if size <= 2 { 1 } else { size.div_euclid(4) + 1 }
}

async fn replace_stage(
    db: &mut MySqlConnection,
    division: i32,
    size: i32,
    placements: BTreeMap<i32, i32>,
) -> Result<(), sqlx::Error> {
    // A unique key serializes even the first publication of a stage. Keep the
    // lock through the replacement and apply the same order in every refresh.
    sqlx::query(
        "INSERT INTO tab_publishedoutroundstage (type_of_round, num_teams) VALUES (?, ?)
         ON DUPLICATE KEY UPDATE id = id",
    )
    .bind(division)
    .bind(size)
    .execute(&mut *db)
    .await?;
    let stage = sqlx::query_scalar::<_, i32>(
        "SELECT id FROM tab_publishedoutroundstage WHERE type_of_round = ? AND num_teams = ? FOR UPDATE",
    ).bind(division).bind(size).fetch_one(&mut *db).await?;
    let existing: BTreeMap<_, _> = sqlx::query_as::<_, (i32, u32)>(
        "SELECT team_id, place FROM tab_publishedteamplacement WHERE stage_id = ? FOR UPDATE",
    )
    .bind(stage)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(team, place)| (team, place as i32))
    .collect();
    if existing == placements {
        return Ok(());
    }

    sqlx::query("DELETE FROM tab_publishedteamplacement WHERE stage_id = ?")
        .bind(stage)
        .execute(&mut *db)
        .await?;
    if !placements.is_empty() {
        QueryBuilder::<MySql>::new(
            "INSERT INTO tab_publishedteamplacement (stage_id, team_id, place) ",
        )
        .push_values(placements, |mut row, (team, place)| {
            row.push_bind(stage).push_bind(team).push_bind(place);
        })
        .build()
        .execute(db)
        .await?;
    }
    Ok(())
}

pub async fn completion_time(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<Option<DateTime<Utc>>, sqlx::Error> {
    let final_round = settings.integer("tot_rounds", 0);
    if final_round < 1 {
        return Ok(None);
    }
    const COMPLETE: &str = "
        SELECT IF(COUNT(*) > 0 AND
           COUNT(CASE WHEN victor <> 0 AND result_entered_at IS NOT NULL THEN 1 END) = COUNT(*),
           MAX(result_entered_at), NULL)
        FROM tab_round WHERE round_number = ?
    ";
    let completed = sqlx::query_scalar::<_, Option<NaiveDateTime>>(COMPLETE)
        .bind(final_round)
        .fetch_one(db)
        .await?;
    Ok(completed.map(|value| value.and_utc()))
}

pub fn speakers_released(
    placements: &PublishedPlacements,
    completed_at: Option<DateTime<Utc>>,
    now: DateTime<Utc>,
) -> bool {
    placements.iter().any(|division| !division.is_empty())
        || completed_at
            .is_some_and(|completed| now.signed_duration_since(completed) >= TimeDelta::hours(3))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn publication_requires_three_hours_or_public_placements() {
        let mut placements = [BTreeMap::new(), BTreeMap::new()];
        let completed = DateTime::from_timestamp(1_752_000_000, 0).unwrap();
        let boundary = completed + TimeDelta::hours(3);
        assert!(!speakers_released(
            &placements,
            Some(completed),
            boundary - TimeDelta::microseconds(1)
        ));
        assert!(speakers_released(&placements, Some(completed), boundary));
        assert!(!speakers_released(&placements, None, boundary));
        placements[1].insert(1, 5);
        assert!(speakers_released(&placements, None, completed));
    }
}
