use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use axum::{
    Extension, Form,
    extract::{Path, State, rejection::FormRejection},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use mittab_tabulation::{
    ballots::{RoundFilter, ScoringPolicy},
    bracket::opening_matchups,
};
use ring::rand::{SecureRandom, SystemRandom};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    board::{BoardError, csrf_valid, lock_board, respond_error},
    board_snapshot::{self, Identity, Pairings, Snapshot},
    legacy_html,
    legacy_session::Actor,
    login::TemplateCsrf,
    publication,
    settings::Settings,
    tournament_data::TournamentData,
    unavailable,
};

const PATH: &str = "/apda-board/outrounds/";

#[derive(Clone, Serialize)]
struct Seed {
    id: i32,
    team: i32,
    name: String,
    original: i32,
    effective: i32,
    division: i32,
}

async fn seeds(db: &mut MySqlConnection) -> Result<Vec<Seed>, sqlx::Error> {
    Ok(sqlx::query_as::<_, (i32, i32, String, i32, i32, i32)>(
        "SELECT b.id, t.id, t.name, b.seed, b.effective_seed, b.type_of_team
           FROM tab_breakingteam b JOIN tab_team t ON t.id = b.team_id ORDER BY t.name, t.id",
    )
    .fetch_all(db)
    .await?
    .into_iter()
    .map(|(id, team, name, original, effective, division)| Seed {
        id,
        team,
        name,
        original,
        effective,
        division,
    })
    .collect())
}

fn pairing(id: i32, snapshot: &Snapshot, history: &board_snapshot::Histories) -> minijinja::Value {
    let events = history.retained.get(&id);
    let mut positions = Vec::new();
    if let Some(events) = events {
        for (field, side) in [("government", "gov"), ("opposition", "opp")] {
            if events
                .iter()
                .any(|event| event.changes.iter().any(|change| change.field == field))
            {
                positions.push(side);
            }
        }
    }
    let manual_judge_names = history.manual_judge_names(id);
    let history: Vec<_> = events.into_iter().flatten().map(|event| {
        let mut changes: Vec<_> = event.changes.iter().map(|change| {
            let field = match change.field {
                "government" => "gov team",
                "opposition" => "opp team",
                "outcome" => "victor",
                "stage" => "num teams",
                field => field,
            };
            let text = |value: &str| -> String {
                let value = match (field, value) {
                    ("choice", "Government") => "Gov",
                    ("choice", "Opposition") => "Opp",
                    ("choice", "No choice") => "No",
                    ("victor", "Pending") => "UNKNOWN",
                    ("victor", "Government") => "GOV",
                    ("victor", "Opposition") => "OPP",
                    ("victor", "Government by forfeit") => "GOV via Forfeit",
                    ("victor", "Opposition by forfeit") => "OPP via Forfeit",
                    (_, other) => other,
                };
                value.to_owned()
            };
            (field, context! {field, before => text(&change.before), after => text(&change.after)})
        }).collect();
        if changes.iter().all(|(_, detail)| detail.get_attr("before").is_ok_and(|before| before.is_true())) {
            changes.sort_by_key(|(field, _)| *field);
        }
        let changes: Vec<_> = changes.into_iter().map(|(_, detail)| detail).collect();
        context! { actor => &event.actor, time => &event.legacy_time, label => event.label, changes }
    }).collect();
    context! {
        id, revision => snapshot.revision(), gov_team => &snapshot.gov_team, opp_team => &snapshot.opp_team,
        room => &snapshot.room, judges => &snapshot.judges, chair => snapshot.chair, victor => snapshot.victor,
        choice => snapshot.choice, num_teams => snapshot.num_teams,
        manual_team_positions => positions, manual_judge_names => manual_judge_names.cloned().unwrap_or_default(),
        history,
    }
}

pub async fn list(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    if !matches!(actor, Actor::User { board: true, .. }) {
        return access::access_denied();
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let snapshots = board_snapshot::capture(&mut db, Pairings::Outround).await?;
        let history = board_snapshot::histories(&mut db, "outround").await?;
        let teams = seeds(&mut db).await?;
        let rooms: Vec<_> = sqlx::query_as::<_, (i32, String)>(
            "SELECT id, name FROM tab_room ORDER BY name, id",
        ).fetch_all(&mut *db).await?.into_iter().map(|(id, name)| Identity { id, name }).collect();
        let shell = legacy_html::shell(&mut db, actor, PATH).await?;
        db.commit().await?;
        let mut divisions = Vec::new();
        for (id, label) in [(0, "Varsity"), (1, "Novice")] {
            let mut by_stage: BTreeMap<_, Vec<_>> = BTreeMap::new();
            for (&round, data) in snapshots.iter().filter(|(_, snapshot)| snapshot.division == Some(id)) {
                by_stage.entry(data.num_teams.unwrap_or(0)).or_default().push(pairing(round, data, &history));
            }
            let stages: Vec<_> = by_stage.into_iter().rev()
                .map(|(size, pairings)| context! {size, pairings}).collect();
            let deleted: Vec<_> = history.removed.iter().filter(|(round, snapshot)| {
                snapshot.division == Some(id) && !snapshots.contains_key(round)
            }).map(|(&round, snapshot)| pairing(round, snapshot, &history)).collect();
            let teams: Vec<_> = teams.iter().filter(|team| team.division == id).map(|team| context! {
                team_id => team.team, team__name => &team.name, seed => team.original,
            }).collect();
            let problems = problems(&snapshots, id);
            divisions.push(context! { id, label, stages, teams, deleted, problems });
        }
        let Ok(csrf) = TemplateCsrf::new(&headers) else { return Ok(unavailable()); };
        let mut response = legacy_html::render("apda_board/outrounds.html", shell, context! {
            divisions, rooms, csrf_input => csrf.input(),
            outcomes => [(0, "UNKNOWN"), (1, "GOV"), (2, "OPP"), (3, "GOV via Forfeit"), (4, "OPP via Forfeit")],
        }, StatusCode::OK);
        csrf.set_cookie(&mut response, &headers);
        Ok::<_, sqlx::Error>(response)
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

pub(super) fn teams(snapshot: &Snapshot) -> [i32; 2] {
    [
        snapshot.gov_team.as_ref().map_or(0, |team| team.id),
        snapshot.opp_team.as_ref().map_or(0, |team| team.id),
    ]
}

pub(super) fn winner_loser(snapshot: &Snapshot) -> Option<(i32, i32)> {
    let [gov, opp] = teams(snapshot);
    match snapshot.victor {
        Some(1 | 3) => Some((gov, opp)),
        Some(2 | 4) => Some((opp, gov)),
        _ => None,
    }
}

fn by_stage(snapshots: &BTreeMap<i32, Snapshot>, division: i32) -> BTreeMap<i32, Vec<&Snapshot>> {
    let mut stages: BTreeMap<_, Vec<_>> = BTreeMap::new();
    for pairing in snapshots
        .values()
        .filter(|pairing| pairing.division == Some(division))
    {
        stages
            .entry(pairing.num_teams.unwrap_or(0))
            .or_default()
            .push(pairing);
    }
    stages
}

fn problems(snapshots: &BTreeMap<i32, Snapshot>, division: i32) -> Vec<String> {
    let mut eliminated = HashSet::new();
    let mut problems = Vec::new();
    for (size, stage) in by_stage(snapshots, division).into_iter().rev() {
        let mut seen = HashSet::new();
        let mut problem = false;
        for pairing in &stage {
            for team in teams(pairing) {
                problem |= !seen.insert(team) || eliminated.contains(&team);
            }
        }
        if problem {
            problems.push(format!(
                "Round of {size} has duplicate or previously eliminated teams."
            ));
        }
        eliminated.extend(
            stage
                .iter()
                .filter_map(|pairing| winner_loser(pairing).map(|(_, loser)| loser)),
        );
    }
    problems
}

async fn initialize_break(
    db: &mut MySqlConnection,
    user: i32,
    settings: &Settings,
) -> Result<(), BoardError> {
    let existing = sqlx::query_scalar::<_, i64>(
        "SELECT EXISTS(SELECT 1 FROM tab_breakingteam) OR EXISTS(SELECT 1 FROM tab_outround)",
    )
    .fetch_one(&mut *db)
    .await?;
    if existing != 0 {
        return Ok(());
    }
    seed_break(db, user, settings, true).await
}

pub async fn seed_break(
    db: &mut MySqlConnection,
    user: i32,
    settings: &Settings,
    require_varsity: bool,
) -> Result<(), BoardError> {
    let vars = settings.integer("var_teams_to_break", 8);
    let novs = settings.integer("nov_teams_to_break", 4);
    if !(0..=256).contains(&vars) || !(0..=256).contains(&novs) || (require_varsity && vars < 2) {
        return Err(BoardError::Conflict("Check the configured break sizes."));
    }
    let data = TournamentData::load(db).await?;
    let standings = data
        .scores(
            ScoringPolicy {
                current_round: settings.integer("cur_round", 1),
                lenient_late_through: settings.integer("lenient_late", 0),
            },
            RoundFilter::default(),
        )
        .map_err(BoardError::Conflict)?;
    let team_rows: HashMap<_, _> = sqlx::query_as::<_, (i32, String, bool, bool)>(
        "SELECT t.id, t.name, t.break_preference = 0,
                NOT EXISTS(SELECT 1 FROM tab_team_debaters m
                    JOIN tab_debater d ON d.id = m.debater_id WHERE m.team_id = t.id AND d.novice_status <> 1)
         FROM tab_team t",
    ).fetch_all(&mut *db).await?.into_iter().map(|(id, name, vars, novs)| (i64::from(id), (name, vars, novs))).collect();
    let varsity: Vec<_> = standings
        .iter()
        .filter(|entry| team_rows[&entry.id].1)
        .take(vars as usize)
        .map(|entry| entry.id)
        .collect();
    if require_varsity && varsity.len() < 2 {
        return Err(BoardError::Conflict(
            "The varsity break needs at least two eligible teams.",
        ));
    }
    let chosen: HashSet<_> = varsity.iter().copied().collect();
    let novice = standings
        .iter()
        .filter(|entry| team_rows[&entry.id].2 && !chosen.contains(&entry.id))
        .take(novs as usize)
        .map(|entry| entry.id)
        .collect();
    for (division, selection) in [(0, varsity), (1, novice)] {
        for (position, team) in selection.into_iter().enumerate() {
            let seed = position as i32 + 1;
            let id = sqlx::query(
                "INSERT INTO tab_breakingteam
                    (seed, effective_seed, type_of_team, team_id) VALUES (?, ?, ?, ?)",
            )
            .bind(seed)
            .bind(seed)
            .bind(division)
            .bind(team)
            .execute(&mut *db)
            .await?
            .last_insert_id() as i32;
            board_snapshot::record(
                db,
                "breakingteam",
                id,
                user,
                team_rows[&team].0.clone(),
                json!({"seed": seed, "division": division, "team": team}),
                true,
            )
            .await?;
        }
    }
    Ok(())
}

async fn recompute(
    db: &mut MySqlConnection,
    user: i32,
    snapshots: &BTreeMap<i32, Snapshot>,
    division: i32,
    mut entries: Vec<Seed>,
) -> Result<Vec<Seed>, sqlx::Error> {
    entries.retain(|entry| entry.division == division);
    let originals: HashMap<_, _> = entries
        .iter()
        .map(|entry| (entry.team, entry.original))
        .collect();
    let mut new = originals.clone();
    let mut active = originals.keys().copied().collect::<BTreeSet<_>>();
    let generation = entries
        .iter()
        .map(|entry| entry.id)
        .min()
        .unwrap_or_default();
    let mut replay = crate::elimination_seeds::Replay::load(db, division, generation).await?;
    for (size, stage) in by_stage(snapshots, division).into_iter().rev() {
        replay
            .apply_stage(db, size, &stage, &active, &originals, &mut new)
            .await?;
        for pairing in stage {
            if let Some((winner, loser)) = winner_loser(pairing)
                && active.contains(&winner)
                && active.remove(&loser)
            {
                new.insert(winner, new[&winner].min(new[&loser]));
                new.insert(loser, originals[&loser]);
            }
        }
    }
    for entry in &mut entries {
        let effective = new[&entry.team];
        if effective == entry.effective {
            continue;
        }
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
        entry.effective = effective;
    }
    Ok(entries)
}

pub async fn recompute_staff_seeds(
    db: &mut MySqlConnection,
    user: i32,
    snapshots: &BTreeMap<i32, Snapshot>,
    division: i32,
) -> Result<(), sqlx::Error> {
    let entries = seeds(db).await?;
    recompute(db, user, snapshots, division, entries).await?;
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PairForm {
    csrfmiddlewaretoken: String,
    division: i32,
}

pub async fn pair(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    form: Result<Form<PairForm>, FormRejection>,
) -> Response {
    let Ok(Form(form)) = form else {
        return crate::board::invalid_form();
    };
    if !csrf_valid(&headers, &form.csrfmiddlewaretoken) {
        return crate::board::csrf_error();
    }
    match pair_next(&app, actor, form.division).await {
        Ok(()) => access::found(PATH),
        Err(error) => respond_error(actor, error, PATH),
    }
}

async fn pair_next(app: &App, actor: Actor, division: i32) -> Result<(), BoardError> {
    if !matches!(division, 0 | 1) {
        return Err(BoardError::Conflict("Choose a valid division."));
    }
    let mut db = app.db.begin().await?;
    let user = lock_board(&mut db, actor).await?;
    let settings = Settings::load(
        &mut db,
        &[
            "cur_round",
            "lenient_late",
            "var_teams_to_break",
            "nov_teams_to_break",
            "sidelock",
        ],
    )
    .await?;
    initialize_break(&mut db, user, &settings).await?;
    let before = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    if !problems(&before, division).is_empty() {
        return Err(BoardError::Conflict(
            "Correct the inconsistent bracket teams before pairing.",
        ));
    }
    let scoped: Vec<_> = before
        .values()
        .filter(|pairing| pairing.division == Some(division))
        .collect();
    if scoped.iter().any(|pairing| pairing.victor == Some(0)) {
        return Err(BoardError::Conflict(
            "Enter the existing outround outcomes before pairing the next round.",
        ));
    }
    let prior = scoped.iter().filter_map(|pairing| pairing.num_teams).min();
    let eliminated: HashSet<_> = scoped
        .iter()
        .filter_map(|pairing| winner_loser(pairing).map(|(_, loser)| loser))
        .collect();
    let entries = seeds(&mut db).await?;
    let surviving: Vec<_> = recompute(&mut db, user, &before, division, entries)
        .await?
        .into_iter()
        .filter(|entry| !eliminated.contains(&entry.team))
        .collect();
    if surviving.len() < 2 {
        return Err(BoardError::Conflict(
            "There are no more rounds to pair in this division.",
        ));
    }
    let size = surviving
        .len()
        .checked_next_power_of_two()
        .and_then(|size| i32::try_from(size).ok())
        .ok_or(BoardError::Conflict("Check the bracket seeds."))?;
    if prior.is_some_and(|prior| size >= prior) {
        return Err(BoardError::Conflict(
            "Check the previous round’s teams and outcomes.",
        ));
    }
    let seeded: HashMap<_, _> = surviving
        .iter()
        .map(|entry| (entry.effective, entry.team))
        .collect();
    if seeded.len() != surviving.len() || seeded.keys().any(|seed| *seed < 1 || *seed > size) {
        return Err(BoardError::Conflict("Check the bracket’s effective seeds."));
    }
    let matchups: Vec<_> = opening_matchups(size as usize)
        .unwrap_or_default()
        .into_iter()
        .filter_map(|(a, b)| seeded.get(&(a as i32)).zip(seeded.get(&(b as i32))))
        .collect();
    if matchups.is_empty() {
        return Err(BoardError::Conflict(
            "Check the bracket seeds. No matchups could be formed.",
        ));
    }
    let rooms = sqlx::query_scalar::<_, i32>(
        "SELECT r.id FROM tab_room r JOIN tab_roomcheckin c ON c.room_id = r.id
         WHERE c.round_number = 0 AND NOT EXISTS(
             SELECT 1 FROM tab_outround o WHERE o.victor = 0 AND o.room_id = r.id)
         ORDER BY r.name, r.id",
    )
    .fetch_all(&mut *db)
    .await?;
    if rooms.len() < matchups.len() {
        return Err(BoardError::Conflict(
            "There are not enough unoccupied rooms checked in for outrounds.",
        ));
    }
    for ((&first, &second), room) in matchups.into_iter().zip(rooms) {
        let (gov, opp, locked) =
            sides(&mut db, first, second, settings.flag("sidelock", false)).await?;
        sqlx::query("INSERT INTO tab_outround
                (num_teams, type_of_round, victor, gov_team_id, opp_team_id, room_id, sidelock, choice)
              VALUES (?, ?, 0, ?, ?, ?, ?, 0)")
            .bind(size).bind(division).bind(gov).bind(opp).bind(room).bind(locked)
            .execute(&mut *db).await?;
    }
    let after = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    board_snapshot::audit_pairings(&mut db, &before, &after, user, "board pairing").await?;
    db.commit().await?;
    Ok(())
}

pub async fn sides(
    db: &mut MySqlConnection,
    first: i32,
    second: i32,
    sidelock: bool,
) -> Result<(i32, i32, bool), BoardError> {
    if sidelock {
        let (ab, ba) = sqlx::query_as::<_, (i64, i64)>(
            "SELECT EXISTS(SELECT 1 FROM tab_round WHERE gov_team_id = ? AND opp_team_id = ?),
                    EXISTS(SELECT 1 FROM tab_round WHERE gov_team_id = ? AND opp_team_id = ?)",
        )
        .bind(first)
        .bind(second)
        .bind(second)
        .bind(first)
        .fetch_one(&mut *db)
        .await?;
        if ab != 0 {
            return Ok((second, first, true));
        }
        if ba != 0 {
            return Ok((first, second, true));
        }
    }
    let count = |team| {
        sqlx::query_scalar::<_, i64>(
            "SELECT (SELECT COUNT(*) FROM tab_round WHERE gov_team_id = ?)
              + (SELECT COUNT(*) FROM tab_outround WHERE gov_team_id = ?)",
        )
        .bind(team)
        .bind(team)
    };
    let a = count(first).fetch_one(&mut *db).await?;
    let b = count(second).fetch_one(db).await?;
    let first_gov = if a == b {
        let mut random = [0; 1];
        SystemRandom::new()
            .fill(&mut random)
            .map_err(|_| BoardError::Storage)?;
        random[0] % 2 == 0
    } else {
        a < b
    };
    Ok(if first_gov {
        (first, second, false)
    } else {
        (second, first, false)
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Correction {
    csrfmiddlewaretoken: String,
    revision: String,
    action: String,
    victor: Option<i32>,
    position: Option<String>,
    team: Option<i32>,
    room: Option<String>,
    choice: Option<i32>,
}

pub async fn correct(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Path(id): Path<i32>,
    form: Result<Form<Correction>, FormRejection>,
) -> Response {
    let Ok(Form(form)) = form else {
        return crate::board::invalid_form();
    };
    if !csrf_valid(&headers, &form.csrfmiddlewaretoken) {
        return crate::board::csrf_error();
    }
    match correction(&app, actor, id, form).await {
        Ok(()) => access::found(PATH),
        Err(error) => respond_error(actor, error, PATH),
    }
}

async fn correction(app: &App, actor: Actor, id: i32, form: Correction) -> Result<(), BoardError> {
    let mut db = app.db.begin().await?;
    let user = lock_board(&mut db, actor).await?;
    let before = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let row = before.get(&id).ok_or(BoardError::Missing)?;
    if form.revision != row.revision() {
        return Err(BoardError::Conflict(
            "This round changed since you opened the page. Reload before correcting it.",
        ));
    }
    let division = row.division.ok_or(BoardError::Missing)?;
    let stage = row.num_teams.ok_or(BoardError::Missing)?;
    let [mut gov, mut opp] = teams(row);
    let mut winner = row.victor.unwrap_or(0);
    let mut choice = row.choice.unwrap_or(0);
    let mut room = row.room.as_ref().map(|room| room.id);
    let mut peer_changes = BTreeMap::new();
    const INVALID: BoardError = BoardError::Conflict("Choose a valid correction.");
    match form.action.as_str() {
        "result" => {
            winner = form
                .victor
                .filter(|value| (0..=4).contains(value))
                .ok_or(INVALID)?
        }
        "sides" => {
            std::mem::swap(&mut gov, &mut opp);
            winner = match winner {
                1 => 2,
                2 => 1,
                3 => 4,
                4 => 3,
                value => value,
            };
            choice = match choice {
                1 => 2,
                2 => 1,
                value => value,
            };
        }
        "team" => {
            let replacement = form.team.filter(|&team| team > 0).ok_or(INVALID)?;
            let known = sqlx::query_scalar::<_, i64>(
                "SELECT EXISTS(SELECT 1 FROM tab_breakingteam WHERE team_id = ? AND type_of_team = ?)",
            ).bind(replacement).bind(division).fetch_one(&mut *db).await?;
            if known == 0 {
                return Err(BoardError::Conflict(
                    "Choose a breaking team from this division.",
                ));
            }
            let (slot, other) = match form.position.as_deref() {
                Some("gov") => (&mut gov, opp),
                Some("opp") => (&mut opp, gov),
                _ => return Err(INVALID),
            };
            if other == replacement {
                return Err(BoardError::Conflict(
                    "Use Switch sides to swap these teams.",
                ));
            }
            if *slot != replacement {
                for (&peer, pairing) in &before {
                    if peer == id
                        || pairing.division != Some(division)
                        || pairing.num_teams != Some(stage)
                    {
                        continue;
                    }
                    let [peer_gov, peer_opp] = teams(pairing);
                    if [peer_gov, peer_opp].contains(&replacement) {
                        let new_gov = if peer_gov == replacement {
                            *slot
                        } else {
                            peer_gov
                        };
                        let new_opp = if peer_opp == replacement {
                            *slot
                        } else {
                            peer_opp
                        };
                        sqlx::query("UPDATE tab_outround SET gov_team_id = ?, opp_team_id = ?, victor = 0 WHERE id = ?")
                            .bind(new_gov).bind(new_opp).bind(peer).execute(&mut *db).await?;
                        peer_changes.insert(peer, pairing.clone());
                    }
                }
                *slot = replacement;
                winner = 0;
            }
        }
        "room" => {
            room = match form.room.as_deref().unwrap_or("").trim() {
                "" => None,
                value => Some(
                    value
                        .parse::<i32>()
                        .ok()
                        .filter(|&id| id > 0)
                        .ok_or(INVALID)?,
                ),
            };
            if let Some(room_id) = room {
                let known = sqlx::query_scalar::<_, i64>(
                    "SELECT EXISTS(SELECT 1 FROM tab_room WHERE id = ?)",
                )
                .bind(room_id)
                .fetch_one(&mut *db)
                .await?;
                if known == 0 {
                    return Err(BoardError::Conflict("Choose a valid room."));
                }
            }
        }
        "choice" => {
            choice = form
                .choice
                .filter(|value| (0..=2).contains(value))
                .ok_or(INVALID)?
        }
        _ => return Err(INVALID),
    }
    sqlx::query("UPDATE tab_outround SET gov_team_id = ?, opp_team_id = ?, room_id = ?, victor = ?, choice = ? WHERE id = ?")
        .bind(gov).bind(opp).bind(room).bind(winner).bind(choice).bind(id).execute(&mut *db).await?;
    let after = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    for (&changed, previous) in peer_changes.iter().chain(std::iter::once((&id, row))) {
        let corrected = &after[&changed];
        let Some(room_id) = corrected.room.as_ref().map(|room| room.id) else {
            continue;
        };
        let occupied_before = previous.victor == Some(0)
            && previous
                .room
                .as_ref()
                .is_some_and(|room| room.id == room_id);
        if corrected.victor == Some(0)
            && !occupied_before
            && after.iter().any(|(&peer, pairing)| {
                peer != changed
                    && pairing.victor == Some(0)
                    && pairing.room.as_ref().is_some_and(|room| room.id == room_id)
            })
        {
            return Err(BoardError::Conflict(
                "That room is already assigned to an undecided outround.",
            ));
        }
    }
    let peer_after = after
        .iter()
        .filter(|(id, _)| peer_changes.contains_key(id))
        .map(|(&id, pairing)| (id, pairing.clone()))
        .collect();
    board_snapshot::audit_pairings(&mut db, &peer_changes, &peer_after, user, "board team swap")
        .await?;
    let change_before = BTreeMap::from([(id, row.clone())]);
    let change_after = BTreeMap::from([(id, after[&id].clone())]);
    board_snapshot::audit_pairings(
        &mut db,
        &change_before,
        &change_after,
        user,
        "board correction",
    )
    .await?;
    if (form.action == "result" && winner_loser(row) != winner_loser(&after[&id]))
        || (form.action == "team" && teams(row) != teams(&after[&id]))
    {
        let entries = seeds(&mut db).await?;
        recompute(&mut db, user, &after, division, entries).await?;
    }
    let settings = Settings::load(&mut db, publication::VISIBILITY_KEYS).await?;
    publication::refresh_placements(&mut db, &settings).await?;
    db.commit().await?;
    Ok(())
}
