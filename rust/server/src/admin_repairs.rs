use std::collections::{BTreeMap, BTreeSet};

use sqlx::MySqlConnection;

use crate::{
    admin_scores::score_audit, board_snapshot, publication, round_audit, settings::Settings,
};

use crate::board_outrounds::{teams, winner_loser};

pub(super) enum Edit {
    Inround(BTreeMap<i32, round_audit::Snapshot>),
    Outround(BTreeMap<i32, board_snapshot::Snapshot>),
}

impl Edit {
    pub(super) async fn capture(
        db: &mut MySqlConnection,
        model: &str,
        id: Option<i32>,
    ) -> Result<Option<Self>, sqlx::Error> {
        match (model, id) {
            ("round", Some(id)) => Ok(Some(Self::Inround(score_audit(db, id).await?))),
            ("round", None) => Ok(Some(Self::Inround(BTreeMap::new()))),
            ("outround", _) => Ok(Some(Self::Outround(
                board_snapshot::capture(db, board_snapshot::Pairings::Outround).await?,
            ))),
            _ => Ok(None),
        }
    }

    pub(super) async fn record(
        self,
        db: &mut MySqlConnection,
        id: i32,
        user: i32,
    ) -> Result<Option<&'static str>, sqlx::Error> {
        match self {
            Self::Inround(before) => {
                let after = score_audit(db, id).await?;
                round_audit::record(db, &before, &after, user, "admin correction").await?;
            }
            Self::Outround(before) => {
                let after = board_snapshot::capture(db, board_snapshot::Pairings::Outround).await?;
                let updated = after.get(&id).ok_or(sqlx::Error::RowNotFound)?;
                let old = before.get(&id);
                if old == Some(updated) {
                    return Ok(None);
                }
                let changed = old.is_none_or(|previous| {
                    competitive_values(previous) != competitive_values(updated)
                });
                if changed && breaks_qualification(id, old, updated, &after) {
                    return Ok(Some(
                        "Re-pair the later stage before changing its qualifying team.",
                    ));
                }
                let entrants_changed = old.is_none_or(|previous| {
                    previous.division != updated.division
                        || previous.num_teams != updated.num_teams
                        || teams(previous) != teams(updated)
                });
                if entrants_changed && invalid_entrants(id, updated, &after) {
                    return Ok(Some(
                        "Choose distinct teams that are still eligible for this stage.",
                    ));
                }
                let edit_before = old
                    .map(|previous| (id, previous.clone()))
                    .into_iter()
                    .collect();
                let edit_after = BTreeMap::from([(id, updated.clone())]);
                board_snapshot::audit_pairings(
                    db,
                    &edit_before,
                    &edit_after,
                    user,
                    "admin correction",
                )
                .await?;
                if let Some(previous) = old
                    && (previous.division != updated.division
                        || previous.num_teams != updated.num_teams
                        || previous.gov_team != updated.gov_team
                        || previous.opp_team != updated.opp_team)
                    && let (Some(division), Some(size)) = (previous.division, previous.num_teams)
                {
                    prune_published_stage(db, division, size, previous, &after).await?;
                }
                if changed {
                    let divisions: BTreeSet<_> = old
                        .and_then(|round| round.division)
                        .into_iter()
                        .chain(updated.division)
                        .collect();
                    for division in divisions {
                        crate::board_outrounds::recompute_staff_seeds(db, user, &after, division)
                            .await?;
                    }
                }
                let settings = Settings::load(db, publication::VISIBILITY_KEYS).await?;
                publication::refresh_placements(db, &settings).await?;
            }
        }
        Ok(None)
    }
}

fn breaks_qualification(
    id: i32,
    old: Option<&board_snapshot::Snapshot>,
    new: &board_snapshot::Snapshot,
    pairings: &BTreeMap<i32, board_snapshot::Snapshot>,
) -> bool {
    let advanced = |stage: &board_snapshot::Snapshot, team: i32| {
        pairings.iter().any(|(&peer, next)| {
            peer != id
                && stage.division == next.division
                && next
                    .num_teams
                    .zip(stage.num_teams)
                    .is_some_and(|(next, current)| next < current)
                && teams(next).contains(&team)
        })
    };
    let new_winner = winner_loser(new).map(|(winner, _)| winner);
    if let Some(old) = old
        && let Some((winner, _)) = winner_loser(old)
        && (new_winner != Some(winner)
            || old.division != new.division
            || old.num_teams != new.num_teams)
        && advanced(old, winner)
    {
        return true;
    }
    winner_loser(new).is_some_and(|(_, loser)| advanced(new, loser))
}

fn invalid_entrants(
    id: i32,
    round: &board_snapshot::Snapshot,
    pairings: &BTreeMap<i32, board_snapshot::Snapshot>,
) -> bool {
    let [gov, opp] = teams(round);
    gov == opp
        || pairings.iter().any(|(&peer, earlier)| {
            if peer == id || earlier.division != round.division {
                return false;
            }
            if earlier.num_teams == round.num_teams {
                return teams(earlier)
                    .iter()
                    .any(|team| *team == gov || *team == opp);
            }
            earlier
                .num_teams
                .zip(round.num_teams)
                .is_some_and(|(earlier, now)| earlier > now)
                && winner_loser(earlier).is_some_and(|(_, loser)| loser == gov || loser == opp)
        })
}

fn competitive_values(round: &board_snapshot::Snapshot) -> [Option<i32>; 5] {
    [
        round.division,
        round.num_teams,
        round.victor,
        round.gov_team.as_ref().map(|team| team.id),
        round.opp_team.as_ref().map(|team| team.id),
    ]
}

pub(super) const LATER_STAGE: &str = "Resolve the later stage before deleting this pairing.";

pub(super) fn later_stage_depends_on(
    before: &BTreeMap<i32, board_snapshot::Snapshot>,
    removed: &BTreeSet<i32>,
) -> bool {
    removed.iter().filter_map(|id| before.get(id)).any(|round| {
        before.iter().any(|(id, later)| {
            !removed.contains(id)
                && round.division == later.division
                && later
                    .num_teams
                    .zip(round.num_teams)
                    .is_some_and(|(next, current)| next < current)
        })
    })
}

pub(super) async fn finish_outround_changes(
    db: &mut MySqlConnection,
    user: i32,
    before: &BTreeMap<i32, board_snapshot::Snapshot>,
    removed: &BTreeSet<i32>,
) -> Result<(), sqlx::Error> {
    let after = board_snapshot::capture(db, board_snapshot::Pairings::Outround).await?;
    let mut divisions = BTreeSet::new();
    for round in removed.iter().filter_map(|id| before.get(id)) {
        if let (Some(division), Some(size)) = (round.division, round.num_teams) {
            prune_published_stage(db, division, size, round, &after).await?;
            divisions.insert(division);
        }
    }
    board_snapshot::audit_pairings(db, before, &after, user, "admin deletion").await?;
    if !divisions.is_empty() {
        let settings = Settings::load(db, publication::VISIBILITY_KEYS).await?;
        for division in divisions {
            crate::board_outrounds::recompute_staff_seeds(db, user, &after, division).await?;
            publication::refresh_division(db, &settings, division).await?;
        }
    }
    Ok(())
}

async fn prune_published_stage(
    db: &mut MySqlConnection,
    division: i32,
    size: i32,
    removed: &board_snapshot::Snapshot,
    remaining: &std::collections::BTreeMap<i32, board_snapshot::Snapshot>,
) -> Result<(), sqlx::Error> {
    let stage: Vec<_> = remaining
        .values()
        .filter(|round| round.division == Some(division) && round.num_teams == Some(size))
        .collect();
    if !stage.is_empty() {
        let remaining_teams: std::collections::BTreeSet<_> = stage
            .iter()
            .flat_map(|round| [&round.gov_team, &round.opp_team])
            .flatten()
            .map(|team| team.id)
            .collect();
        for team in [&removed.gov_team, &removed.opp_team].into_iter().flatten() {
            if !remaining_teams.contains(&team.id) {
                sqlx::query(
                    "DELETE p FROM tab_publishedteamplacement p
                            JOIN tab_publishedoutroundstage s ON s.id = p.stage_id
                            WHERE s.type_of_round = ? AND s.num_teams = ? AND p.team_id = ?",
                )
                .bind(division)
                .bind(size)
                .bind(team.id)
                .execute(&mut *db)
                .await?;
            }
        }
        return Ok(());
    }
    sqlx::query(
        "DELETE p FROM tab_publishedteamplacement p
                JOIN tab_publishedoutroundstage s ON s.id = p.stage_id
                WHERE s.type_of_round = ? AND s.num_teams = ?",
    )
    .bind(division)
    .bind(size)
    .execute(&mut *db)
    .await?;
    sqlx::query("DELETE FROM tab_publishedoutroundstage WHERE type_of_round = ? AND num_teams = ?")
        .bind(division)
        .bind(size)
        .execute(db)
        .await?;
    Ok(())
}
