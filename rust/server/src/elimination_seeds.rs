use std::collections::{BTreeMap, BTreeSet, HashMap};

use mittab_tabulation::outrounds::{self, Seed, SeedBasis};
use sqlx::{MySql, MySqlConnection, QueryBuilder};

use crate::{board_snapshot::Snapshot, settings::Settings};

fn prefix(division: i32, size: i32, generation: i32) -> String {
    format!("rust_seed_{division}_{size}_{generation}_")
}

pub async fn save(
    db: &mut MySqlConnection,
    division: i32,
    size: i32,
    generation: i32,
    seeds: Option<&BTreeMap<i32, i32>>,
) -> Result<(), sqlx::Error> {
    let prefix = prefix(division, size, generation);
    sqlx::query("DELETE FROM tab_tabsettings WHERE LEFT(`key`, ?) = ?")
        .bind(prefix.len() as u32)
        .bind(&prefix)
        .execute(&mut *db)
        .await?;
    if let Some(seeds) = seeds.filter(|seeds| !seeds.is_empty()) {
        QueryBuilder::<MySql>::new("INSERT INTO tab_tabsettings (`key`, value) ")
            .push_values(seeds, |mut row, (team, seed)| {
                row.push_bind(format!("{prefix}{team}")).push_bind(seed);
            })
            .build()
            .execute(db)
            .await?;
    }
    Ok(())
}

pub struct Replay {
    division: i32,
    generation: i32,
    mode: i32,
    once_used: bool,
    once_happened: bool,
    picks_generation: i32,
    seed_basis: SeedBasis,
}

impl Replay {
    pub async fn load(
        db: &mut MySqlConnection,
        division: i32,
        generation: i32,
    ) -> Result<Self, sqlx::Error> {
        let picks = format!("challenge_pick_generation_{division}");
        let once = format!("challenge_once_applied_{division}_{generation}");
        let settings = Settings::load(
            db,
            &[
                "outround_pairing_mode",
                "challenge_seed_basis",
                &picks,
                &once,
            ],
        )
        .await?;
        Ok(Self {
            division,
            generation,
            mode: settings.integer("outround_pairing_mode", 0),
            once_used: false,
            once_happened: settings.flag(&once, false),
            picks_generation: settings.integer(&picks, 0),
            seed_basis: if settings.integer("challenge_seed_basis", 1) == 1 {
                SeedBasis::Effective
            } else {
                SeedBasis::Original
            },
        })
    }

    pub async fn apply_stage(
        &mut self,
        db: &mut MySqlConnection,
        size: i32,
        stage: &[&Snapshot],
        active: &BTreeSet<i32>,
        original: &HashMap<i32, i32>,
        effective: &mut HashMap<i32, i32>,
    ) -> Result<(), sqlx::Error> {
        if size < 4 || active.len() != size as usize {
            return Ok(());
        }
        let prefix = prefix(self.division, size, self.generation);
        let applied: BTreeMap<_, _> = sqlx::query_as::<_, (String, i32)>(
            "SELECT `key`, value FROM tab_tabsettings WHERE LEFT(`key`, ?) = ? AND value IS NOT NULL ORDER BY id",
        ).bind(prefix.len() as u32).bind(&prefix).fetch_all(&mut *db).await?
            .into_iter().filter_map(|(key, seed)| key.strip_prefix(&prefix)?.parse::<i32>().ok().map(|team| (team, seed)))
            .collect();
        let valid = applied.keys().eq(active.iter())
            && applied
                .values()
                .copied()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .eq(1..=size);
        if valid {
            effective.extend(applied);
            self.once_used = true;
            return Ok(());
        }

        // Imported tournaments retain generated debates and challenge choices.
        // An old choice is only applicable when its reconstructed draw matches
        // the actual stage. New tournaments persist every applied seed above.
        if self.mode != 1 && !(self.mode == 2 && self.once_happened && !self.once_used) {
            return Ok(());
        }
        let picks = if self.picks_generation == self.generation {
            sqlx::query_as::<_, (i32, i32)>(
                "SELECT challenger_id, challenged_id FROM tab_outroundchallenge WHERE type_of_round = ? AND num_teams = ?",
            ).bind(self.division).bind(size).fetch_all(&mut *db).await?.into_iter().collect()
        } else {
            BTreeMap::new()
        };
        let entries: Vec<_> = active
            .iter()
            .map(|&team| Seed {
                team,
                original: original[&team],
                effective: effective[&team],
            })
            .collect();
        let basis = if self.mode == 2 {
            SeedBasis::Effective
        } else {
            self.seed_basis
        };
        let Ok(draw) = outrounds::challenge(&entries, basis, &picks) else {
            return Ok(());
        };
        let ordered = |first: i32, second: i32| (first.min(second), first.max(second));
        let paired = stage
            .iter()
            .filter_map(|pair| {
                Some(ordered(
                    pair.gov_team.as_ref()?.id,
                    pair.opp_team.as_ref()?.id,
                ))
            })
            .collect::<BTreeSet<_>>();
        if draw
            .pairings
            .iter()
            .map(|&(a, b)| ordered(a, b))
            .collect::<BTreeSet<_>>()
            == paired
        {
            effective.extend(draw.seeds);
            self.once_used = true;
        }
        Ok(())
    }
}
