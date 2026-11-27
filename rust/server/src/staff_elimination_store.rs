use std::collections::{BTreeMap, BTreeSet};

use mittab_tabulation::outrounds::{self, Seed, SeedBasis};
use serde::Serialize;
use serde_json::json;
use sqlx::MySqlConnection;

use crate::{
    App,
    board::BoardError,
    board_outrounds,
    board_snapshot::{self, Pairings, Snapshot},
    entity_store, publication,
    settings::{self, SettingValue, Settings},
    staff_pairing::lock_round,
};

pub const SETTINGS: &[&str] = &[
    "cur_round",
    "tot_rounds",
    "lenient_late",
    "var_teams_to_break",
    "nov_teams_to_break",
    "sidelock",
    "outround_pairing_mode",
    "challenge_seed_basis",
    "var_teams_visible",
    "nov_teams_visible",
    "var_panel_size",
    "nov_panel_size",
    "choice",
];
pub const VISIBILITY: [&str; 2] = ["var_teams_visible", "nov_teams_visible"];
const PAIRING_PUBLIC: [&str; 2] = ["var_outrounds_public", "nov_outrounds_public"];

#[derive(Debug)]
pub enum Error {
    Invalid(&'static str),
    Missing,
    Database,
}

impl From<sqlx::Error> for Error {
    fn from(_: sqlx::Error) -> Self {
        Self::Database
    }
}

impl From<BoardError> for Error {
    fn from(error: BoardError) -> Self {
        match error {
            BoardError::Conflict(message) => Self::Invalid(message),
            BoardError::Missing => Self::Missing,
            BoardError::Closed | BoardError::Storage => Self::Database,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize)]
pub struct Stage {
    pub division: i32,
    pub size: i32,
}

impl Stage {
    pub fn new(division: i32, size: i32) -> Option<Self> {
        (matches!(division, 0 | 1) && (2..=512).contains(&size) && (size as u32).is_power_of_two())
            .then_some(Self { division, size })
    }

    pub fn division_name(self) -> &'static str {
        if self.division == 0 {
            "Varsity"
        } else {
            "Novice"
        }
    }

    pub fn label(self) -> String {
        format!(
            "[{}] Ro{}",
            if self.division == 0 { "V" } else { "N" },
            self.size
        )
    }

    pub fn url(self) -> String {
        format!("/outround_pairing/{}/{}", self.division, self.size)
    }

    pub fn matches(self, pairing: &Snapshot) -> bool {
        pairing.division == Some(self.division) && pairing.num_teams == Some(self.size)
    }
}

pub fn break_sizes(settings: &Settings) -> [i32; 2] {
    [
        settings.integer("var_teams_to_break", 8),
        settings.integer("nov_teams_to_break", 4),
    ]
}

pub fn stage_size(teams: i32) -> Option<i32> {
    (2..=256)
        .contains(&teams)
        .then(|| (teams as u32).next_power_of_two() as i32)
}

pub async fn readiness(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<Vec<&'static str>, sqlx::Error> {
    let [varsity, novice] = break_sizes(settings);
    let mut problems = Vec::new();
    if [varsity, novice]
        .into_iter()
        .any(|size| !(0..=256).contains(&size))
        || varsity.max(novice) < 2
    {
        problems.push("Configure a varsity or novice break with at least two teams.");
    }
    let total = settings.integer("tot_rounds", 5);
    let current = settings.integer("cur_round", 1);
    if current <= total {
        problems.push("Pair the final preliminary round before breaking teams.");
    }
    let pending: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM tab_round WHERE round_number BETWEEN 1 AND ? AND victor = 0)",
    )
    .bind(total)
    .fetch_one(&mut *db)
    .await?;
    if pending {
        problems.push("Enter all preliminary outcomes before breaking teams.");
    }
    let attendance: bool = sqlx::query_scalar(
        "SELECT EXISTS(
            SELECT 1 FROM tab_round d JOIN tab_bye b ON b.round_number = d.round_number
             AND b.bye_team_id IN (d.gov_team_id, d.opp_team_id)
            UNION ALL
            SELECT 1 FROM tab_round d JOIN tab_noshow n ON n.round_number = d.round_number
             AND n.no_show_team_id IN (d.gov_team_id, d.opp_team_id)
        )",
    )
    .fetch_one(db)
    .await?;
    if attendance {
        problems.push("Resolve teams that debated a round while marked absent or on a bye.");
    }
    Ok(problems)
}

pub async fn break_tournament(app: &App, user: i32, confirm: bool) -> Result<(), Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    if let Some(problem) = readiness(&mut db, &settings).await?.first() {
        return Err(Error::Invalid(problem));
    }
    let before = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    if !before.is_empty() && !confirm {
        return Err(Error::Invalid(
            "Confirm replacement of the existing elimination rounds.",
        ));
    }
    publication::refresh_placements(&mut db, &settings).await?;
    let round = settings.integer("cur_round", 1).to_string();
    app.backups
        .save_named(&format!("Before the Break Round {round}"), 6, &round)
        .await
        .map_err(Error::Invalid)?;
    let existing: Vec<_> = before.keys().copied().collect();
    entity_store::delete_outrounds(&mut db, &existing).await?;
    sqlx::query("DELETE FROM tab_outroundchallenge")
        .execute(&mut *db)
        .await?;
    sqlx::query("DELETE FROM tab_breakingteam")
        .execute(&mut *db)
        .await?;
    board_outrounds::seed_break(&mut db, user, &settings, false).await?;
    for division in 0..=1 {
        settings::put(
            &mut db,
            VISIBILITY[division as usize],
            SettingValue::Number(256),
        )
        .await?;
        let stage = eligible(&mut db, division, &BTreeMap::new()).await?;
        let Some(size) = stage_size(stage.len() as i32) else {
            continue;
        };
        let choices = challenge_state(&mut db, &settings, division, size, stage).await?;
        if !choices.enabled {
            create_stage(&mut db, user, &settings, choices).await?;
        }
    }
    let after = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    board_snapshot::audit_pairings(&mut db, &before, &after, user, "staff break").await?;
    db.commit().await?;
    Ok(())
}

#[derive(Clone, Serialize)]
pub struct Entry {
    pub id: i32,
    pub team: i32,
    pub name: String,
    pub seed: i32,
    pub effective: i32,
}

pub async fn eligible(
    db: &mut MySqlConnection,
    division: i32,
    previous: &BTreeMap<i32, Snapshot>,
) -> Result<Vec<Entry>, sqlx::Error> {
    let lost: BTreeSet<_> = previous
        .values()
        .filter(|snapshot| snapshot.division == Some(division))
        .filter_map(|snapshot| winner_loser(snapshot).map(|(_, loser)| loser))
        .collect();
    Ok(sqlx::query_as::<_, (i32, i32, String, i32, i32)>(
        "SELECT b.id, b.team_id, t.name, b.seed, b.effective_seed FROM tab_breakingteam b
         JOIN tab_team t ON t.id = b.team_id WHERE b.type_of_team = ? ORDER BY b.seed, t.id",
    )
    .bind(division)
    .fetch_all(db)
    .await?
    .into_iter()
    .filter(|(_, team, ..)| !lost.contains(team))
    .map(|(id, team, name, seed, effective)| Entry {
        id,
        team,
        name,
        seed,
        effective,
    })
    .collect())
}

fn winner_loser(snapshot: &Snapshot) -> Option<(i32, i32)> {
    let gov = snapshot.gov_team.as_ref()?.id;
    let opp = snapshot.opp_team.as_ref()?.id;
    match snapshot.victor? {
        1 | 3 => Some((gov, opp)),
        2 | 4 => Some((opp, gov)),
        _ => None,
    }
}

pub struct ChallengeState {
    pub stage: Stage,
    pub enabled: bool,
    pub entries: Vec<Entry>,
    pub picks: BTreeMap<i32, i32>,
    pub generation: i32,
    basis: SeedBasis,
    once: bool,
}

fn generation_key(division: i32) -> String {
    format!("challenge_pick_generation_{division}")
}

fn once_key(division: i32, generation: i32) -> String {
    format!("challenge_once_applied_{division}_{generation}")
}

async fn challenge_state(
    db: &mut MySqlConnection,
    settings: &Settings,
    division: i32,
    size: i32,
    mut entries: Vec<Entry>,
) -> Result<ChallengeState, Error> {
    let stage =
        Stage::new(division, size).ok_or(Error::Invalid("Choose a valid elimination stage."))?;
    let generation = sqlx::query_scalar::<_, Option<i32>>(
        "SELECT MIN(id) FROM tab_breakingteam WHERE type_of_team = ?",
    )
    .bind(division)
    .fetch_one(&mut *db)
    .await?
    .unwrap_or_default();
    let generation_key = generation_key(division);
    let once_key = once_key(division, generation);
    let current = Settings::load(db, &[&generation_key, &once_key]).await?;
    let mode = settings.integer("outround_pairing_mode", 0);
    let once = mode == 2;
    let enabled = matches!(mode, 1 | 2)
        && size >= 4
        && entries.len() == size as usize
        && !(once && current.flag(&once_key, false));
    let basis = if once || settings.integer("challenge_seed_basis", 1) == 1 {
        SeedBasis::Effective
    } else {
        SeedBasis::Original
    };
    if enabled {
        entries.sort_by_key(|entry| {
            (
                if basis == SeedBasis::Original {
                    entry.seed
                } else {
                    entry.effective
                },
                entry.team,
            )
        });
    }
    let picks = if enabled && current.integer(&generation_key, 0) == generation {
        sqlx::query_as::<_, (i32, i32)>(
            "SELECT challenger_id, challenged_id FROM tab_outroundchallenge WHERE type_of_round = ? AND num_teams = ?",
        ).bind(division).bind(size).fetch_all(db).await?.into_iter().collect()
    } else {
        BTreeMap::new()
    };
    Ok(ChallengeState {
        stage,
        enabled,
        entries,
        picks,
        generation,
        basis,
        once,
    })
}

pub async fn prepare(
    db: &mut MySqlConnection,
    settings: &Settings,
    prior: Stage,
) -> Result<ChallengeState, Error> {
    if prior.size < 4 {
        return Err(Error::Invalid("There are no more rounds to pair."));
    }
    let existing = board_snapshot::capture(db, Pairings::Outround).await?;
    let previous: BTreeMap<_, _> = existing
        .into_iter()
        .filter(|(_, snapshot)| {
            snapshot.division == Some(prior.division)
                && snapshot.num_teams.is_some_and(|size| size >= prior.size)
        })
        .collect();
    let pending = previous.values().any(|snapshot| snapshot.victor == Some(0));
    if pending {
        return Err(Error::Invalid(
            "Enter the previous elimination outcomes before pairing.",
        ));
    }
    if !previous.is_empty() && !previous.values().any(|snapshot| prior.matches(snapshot)) {
        return Err(Error::Invalid("The previous stage has no pairings."));
    }
    let entries = eligible(db, prior.division, &previous).await?;
    if stage_size(entries.len() as i32) != Some(prior.size / 2) {
        return Err(Error::Invalid(
            "The remaining teams do not match this stage of the bracket.",
        ));
    }
    challenge_state(db, settings, prior.division, prior.size / 2, entries).await
}

pub async fn set_pick(
    app: &App,
    prior: Stage,
    challenger: i32,
    opponent: Option<i32>,
) -> Result<(), Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let mut state = prepare(&mut db, &settings, prior).await?;
    if !state.enabled {
        return Err(Error::Invalid(
            "Challenge entries are unavailable for this stage.",
        ));
    }
    if !state.entries.iter().any(|entry| entry.team == challenger) {
        return Err(Error::Invalid("Choose a team in this stage."));
    }
    let key = generation_key(prior.division);
    let stored = Settings::load(&mut db, &[&key]).await?;
    if stored.integer(&key, 0) != state.generation {
        sqlx::query("DELETE FROM tab_outroundchallenge WHERE type_of_round = ?")
            .bind(prior.division)
            .execute(&mut *db)
            .await?;
        settings::put(&mut db, &key, SettingValue::Number(state.generation)).await?;
    }
    if let Some(opponent) = opponent {
        state.picks.insert(challenger, opponent);
        let seeds = seeds(&state.entries);
        outrounds::challenge(&seeds, state.basis, &state.picks).map_err(challenge_error)?;
        sqlx::query(
            "INSERT INTO tab_outroundchallenge (num_teams, type_of_round, challenger_id, challenged_id)
             VALUES (?, ?, ?, ?) ON DUPLICATE KEY UPDATE challenged_id = ?",
        ).bind(state.stage.size).bind(prior.division).bind(challenger).bind(opponent).bind(opponent)
            .execute(&mut *db).await?;
    } else {
        sqlx::query("DELETE FROM tab_outroundchallenge WHERE type_of_round = ? AND num_teams = ? AND challenger_id = ?")
            .bind(prior.division).bind(state.stage.size).bind(challenger).execute(&mut *db).await?;
    }
    db.commit().await?;
    Ok(())
}

fn seeds(entries: &[Entry]) -> Vec<Seed> {
    entries
        .iter()
        .map(|entry| Seed {
            team: entry.team,
            original: entry.seed,
            effective: entry.effective,
        })
        .collect()
}

fn challenge_error(error: outrounds::ChallengeError) -> Error {
    match error {
        outrounds::ChallengeError::UnavailableTeam => {
            Error::Invalid("A challenge refers to a team outside this stage.")
        }
        outrounds::ChallengeError::RepeatedTeam => {
            Error::Invalid("Each team may appear in at most one challenge.")
        }
        outrounds::ChallengeError::MissingOpponent => {
            Error::Invalid("The challenge picks do not leave valid opponents.")
        }
        _ => Error::Invalid("The bracket or effective seeds are inconsistent."),
    }
}

pub async fn pair_next(app: &App, user: i32, prior: Stage) -> Result<Stage, Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let state = prepare(&mut db, &settings, prior).await?;
    let target = state.stage;
    let before = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let remove: Vec<_> = before
        .iter()
        .filter(|(_, snapshot)| {
            snapshot.division == Some(prior.division)
                && snapshot.num_teams.is_some_and(|size| size < prior.size)
        })
        .map(|(&id, _)| id)
        .collect();
    publication::refresh_placements(&mut db, &settings).await?;
    let name = format!("Round-of-{}-{}", prior.size, prior.division_name());
    app.backups
        .save_named(&format!("Before Pairing Round {name}"), 4, &name)
        .await
        .map_err(Error::Invalid)?;
    entity_store::delete_outrounds(&mut db, &remove).await?;
    create_stage(&mut db, user, &settings, state).await?;
    let after = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    board_snapshot::audit_pairings(&mut db, &before, &after, user, "staff elimination pairing")
        .await?;
    db.commit().await?;
    Ok(target)
}

async fn create_stage(
    db: &mut MySqlConnection,
    user: i32,
    settings: &Settings,
    state: ChallengeState,
) -> Result<(), Error> {
    let stage = state.stage;
    let seeds = seeds(&state.entries);
    let pairs = if state.enabled {
        let draw =
            outrounds::challenge(&seeds, state.basis, &state.picks).map_err(challenge_error)?;
        crate::elimination_seeds::save(
            db,
            stage.division,
            stage.size,
            state.generation,
            Some(&draw.seeds),
        )
        .await?;
        for entry in &state.entries {
            let effective = draw.seeds[&entry.team];
            update_seed(db, user, entry, effective).await?;
        }
        if state.once {
            settings::put(
                db,
                &once_key(stage.division, state.generation),
                SettingValue::Number(1),
            )
            .await?;
        }
        draw.pairings
    } else {
        crate::elimination_seeds::save(db, stage.division, stage.size, state.generation, None)
            .await?;
        outrounds::standard(&seeds, stage.size as usize).map_err(challenge_error)?
    };
    if pairs.is_empty() {
        return Err(Error::Invalid(
            "No matchups can be formed from these seeds.",
        ));
    }
    let rooms: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM tab_roomcheckin WHERE round_number = 0")
            .fetch_one(&mut *db)
            .await?;
    if rooms < pairs.len() as i64 {
        return Err(Error::Invalid(
            "Check in enough rooms for this elimination stage.",
        ));
    }
    for (first, second) in pairs {
        let (government, opposition, locked) =
            board_outrounds::sides(db, first, second, settings.flag("sidelock", false)).await?;
        sqlx::query("INSERT INTO tab_outround (num_teams, type_of_round, gov_team_id, opp_team_id, victor, sidelock, choice)
                     VALUES (?, ?, ?, ?, 0, ?, 0)")
            .bind(stage.size).bind(stage.division).bind(government).bind(opposition).bind(locked).execute(&mut *db).await?;
    }
    settings::put(
        db,
        PAIRING_PUBLIC[stage.division as usize],
        SettingValue::Number(0),
    )
    .await?;
    let key = VISIBILITY[stage.division as usize];
    let stored = Settings::load(db, &[key]).await?.integer(key, 256);
    if stored <= stage.size {
        settings::put(db, key, SettingValue::Number(stage.size * 2)).await?;
    }
    Ok(())
}

async fn update_seed(
    db: &mut MySqlConnection,
    user: i32,
    entry: &Entry,
    effective: i32,
) -> Result<(), Error> {
    if entry.effective != effective {
        sqlx::query("UPDATE tab_breakingteam SET effective_seed = ? WHERE id = ?")
            .bind(effective)
            .bind(entry.id)
            .execute(&mut *db)
            .await?;
        board_snapshot::record(
            db,
            "breakingteam",
            entry.id,
            user,
            entry.name.clone(),
            json!({"effective_seed": {"before": entry.effective, "after": effective}}),
            false,
        )
        .await?;
    }
    Ok(())
}

pub async fn release(app: &App, stage: Stage) -> Result<bool, Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = ? AND num_teams = ?)",
    )
    .bind(stage.division)
    .bind(stage.size)
    .fetch_one(&mut *db)
    .await?;
    if !exists {
        return Err(Error::Missing);
    }
    let key = VISIBILITY[stage.division as usize];
    let before = Settings::load(&mut db, &VISIBILITY).await?;
    publication::refresh_placements(&mut db, &before).await?;
    let publish = before.integer(key, 256) != stage.size;
    let limit = if publish { stage.size } else { stage.size * 2 };
    settings::put(&mut db, key, SettingValue::Number(limit)).await?;
    let after = Settings::load(&mut db, &VISIBILITY).await?;
    publication::refresh_placements(&mut db, &after).await?;
    db.commit().await?;
    Ok(publish)
}

pub async fn ballot(
    app: &App,
    user: i32,
    id: i32,
    winner: i32,
    revision: &str,
) -> Result<Stage, Error> {
    if !matches!(winner, 1..=4) {
        return Err(Error::Invalid("Select a winner."));
    }
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let before = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let original = before.get(&id).cloned().ok_or(Error::Missing)?;
    if !revision.is_empty() && revision != original.revision() {
        return Err(Error::Invalid(
            "This pairing changed. Reload it before saving.",
        ));
    }
    let stage = Stage::new(
        original.division.ok_or(Error::Missing)?,
        original.num_teams.ok_or(Error::Missing)?,
    )
    .ok_or(Error::Missing)?;
    let mut current = original.clone();
    current.victor = Some(winner);
    let (won, lost) = winner_loser(&current).ok_or(Error::Missing)?;
    sqlx::query("UPDATE tab_outround SET victor = ? WHERE id = ?")
        .bind(winner)
        .bind(id)
        .execute(&mut *db)
        .await?;
    if let Some((old_winner, _)) = winner_loser(&original)
        && old_winner != won
    {
        let other: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM tab_outround WHERE type_of_round = ? AND num_teams < ? AND ? IN (gov_team_id, opp_team_id))",
        ).bind(stage.division).bind(stage.size).bind(old_winner).fetch_one(&mut *db).await?;
        if other {
            return Err(Error::Invalid(
                "Re-pair the later stage before changing its qualifying team.",
            ));
        }
    }
    let seeded: i64 = sqlx::query_scalar(
        "SELECT COUNT(DISTINCT team_id) FROM tab_breakingteam
         WHERE type_of_team = ? AND team_id IN (?, ?)",
    )
    .bind(stage.division)
    .bind(won)
    .bind(lost)
    .fetch_one(&mut *db)
    .await?;
    if seeded != 2 {
        return Err(Error::Invalid("Both teams must have a break seed."));
    }
    board_snapshot::audit_pairings(
        &mut db,
        &BTreeMap::from([(id, original)]),
        &BTreeMap::from([(id, current.clone())]),
        user,
        "staff outround result",
    )
    .await?;
    let mut after = before;
    after.insert(id, current);
    crate::board_outrounds::recompute_staff_seeds(&mut db, user, &after, stage.division).await?;
    let visibility = Settings::load(&mut db, &VISIBILITY).await?;
    publication::refresh_placements(&mut db, &visibility).await?;
    db.commit().await?;
    Ok(stage)
}

pub async fn choice(app: &App, user: i32, id: i32) -> Result<(Stage, i32), Error> {
    let mut db = app.db.begin().await?;
    lock_round(&mut db).await?;
    let before = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let original = before.get(&id).ok_or(Error::Missing)?;
    let stage = Stage::new(
        original.division.ok_or(Error::Missing)?,
        original.num_teams.ok_or(Error::Missing)?,
    )
    .ok_or(Error::Missing)?;
    let value = (original.choice.unwrap_or_default() + 1).rem_euclid(3);
    sqlx::query("UPDATE tab_outround SET choice = ? WHERE id = ?")
        .bind(value)
        .bind(id)
        .execute(&mut *db)
        .await?;
    let mut after = original.clone();
    after.choice = Some(value);
    board_snapshot::audit_pairings(
        &mut db,
        &BTreeMap::from([(id, original.clone())]),
        &BTreeMap::from([(id, after)]),
        user,
        "staff choice correction",
    )
    .await?;
    db.commit().await?;
    Ok((stage, value))
}
