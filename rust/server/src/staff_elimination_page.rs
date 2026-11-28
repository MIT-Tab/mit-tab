use std::collections::{BTreeMap, HashMap, HashSet};

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::{Value, context};
use serde::Deserialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    board_snapshot::{self, Identity, Pairings, Snapshot},
    legacy_html,
    legacy_session::Actor,
    login::TemplateCsrf,
    pairing_status::{self, Judge},
    settings::Settings,
    staff_elimination_store::{self as store, Error, Stage},
};

#[derive(Default, Deserialize)]
pub struct Selection {
    select_varsity: Option<String>,
    select_novice: Option<String>,
}

impl Selection {
    pub fn sizes(self) -> [Option<i32>; 2] {
        [self.select_varsity, self.select_novice]
            .map(|size| size.and_then(|size| size.parse().ok()))
    }
}

pub fn stages(snapshots: &BTreeMap<i32, Snapshot>) -> impl Iterator<Item = Stage> + '_ {
    snapshots
        .values()
        .filter_map(|pairing| Stage::new(pairing.division?, pairing.num_teams?))
}

fn options(settings: &Settings, snapshots: &BTreeMap<i32, Snapshot>) -> [Vec<i32>; 2] {
    let mut sizes = [Vec::new(), Vec::new()];
    let present: HashSet<_> = stages(snapshots)
        .map(|stage| (stage.division, stage.size))
        .collect();
    for (division, count) in store::break_sizes(settings).into_iter().enumerate() {
        let mut size = store::stage_size(count).unwrap_or_default();
        while size >= 2 {
            if present.contains(&(division as i32, size)) {
                sizes[division].push(size);
            }
            size /= 2;
        }
    }
    sizes
}

fn team(team: Option<&Identity>) -> Option<Value> {
    team.map(|team| context! { id => team.id, display_backend => &team.name })
}

pub async fn render(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    return_path: &str,
    requested: [Option<i32>; 2],
    fallback: Stage,
) -> Result<Response, Error> {
    if !matches!(actor, Actor::User { board: false, .. }) {
        return Ok(access::access_denied());
    }
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, store::SETTINGS).await?;
    if store::break_sizes(&settings).iter().any(|count| *count < 0) {
        return Ok(access::found("/"));
    }
    let snapshots = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let options = options(&settings, &snapshots);
    let defaults = requested.iter().all(Option::is_none);
    let selected: [Option<i32>; 2] = std::array::from_fn(|division| {
        let size = if defaults {
            options[division].last().copied()
        } else {
            requested[division]
        };
        size.filter(|size| options[division].contains(size))
    });
    let mut scope: Vec<_> = selected
        .into_iter()
        .enumerate()
        .filter_map(|(division, size)| Stage::new(division as i32, size?))
        .collect();
    if scope.is_empty() {
        scope.push(fallback);
    }

    let roster = roster(&mut db).await?;
    let mut history = board_snapshot::histories(&mut db, "outround").await?;
    let mut manual = pairing_status::manual_events(&mut db, Pairings::Outround).await?;
    let warnings_by_round = warnings(&mut db).await?;
    let lost: HashSet<_> = snapshots.values().filter_map(loser).collect();
    let (judges, rooms) = resources(&mut db).await?;
    let assignments: HashMap<i32, Judge> = judges
        .iter()
        .map(|judge| {
            (
                judge.id,
                Judge::from_row(
                    judge.id,
                    judge.name.clone(),
                    judge.rank.clone(),
                    judge.wing,
                    judge.request_wing,
                    judge.no_wings,
                ),
            )
        })
        .collect();
    let mut sections = Vec::new();
    let mut warnings = Vec::new();
    let mut excluded_teams = Vec::new();
    let mut used_judges = HashSet::new();
    let mut used_rooms = HashSet::new();
    let mode = settings.integer("outround_pairing_mode", 0);
    let left: [usize; 2] = std::array::from_fn(|division| {
        let lost: HashSet<_> = snapshots
            .values()
            .filter(|pairing| pairing.division == Some(division as i32))
            .filter_map(loser)
            .collect();
        roster
            .iter()
            .filter(|(kind, team)| *kind == division as i32 && !lost.contains(&team.id))
            .count()
    });
    let once = challenge_once(&mut db, mode).await?;
    let challenge = |division: usize, size: i32| {
        matches!(mode, 1 | 2)
            && !once[division]
            && size >= 4
            && (size as u32).is_power_of_two()
            && left[division] == size as usize
    };
    for stage in &scope {
        let division = stage.division as usize;
        let label = stage.label();
        let mut outrounds = Vec::new();
        let mut paired = HashSet::new();
        for (&id, data) in snapshots.iter().filter(|(_, data)| stage.matches(data)) {
            paired.extend(data.gov_team.as_ref().map(|team| team.id));
            paired.extend(data.opp_team.as_ref().map(|team| team.id));
            if data.victor == Some(0) {
                used_judges.extend(data.judges.iter().map(|judge| judge.id));
                used_rooms.extend(data.room.as_ref().map(|room| room.id));
            }
            if let Some(tags) = warnings_by_round.get(&id)
                && let (Some(room), Some(gov), Some(opp)) =
                    (&data.room, &data.gov_team, &data.opp_team)
            {
                let plural = if tags.len() == 1 { "" } else { "s" };
                warnings.push(format!(
                    "{label}: {} vs {} requires tag{plural} {} that are not assigned to room {}",
                    gov.name,
                    opp.name,
                    tags.join(", "),
                    room.name
                ));
            }
            let edits = history.take_staff_edits(id);
            let positions: Vec<_> = [(edits.gov, "gov"), (edits.opp, "opp")]
                .into_iter()
                .filter_map(|(edited, side)| edited.then_some(side))
                .collect();
            let judges: Vec<_> = data
                .judges
                .iter()
                .filter_map(|judge| assignments.get(&judge.id))
                .collect();
            let choice_label = match data.choice {
                Some(1) => "Gov",
                Some(2) => "Opp",
                _ => "No",
            };
            outrounds.push(context! {
                id, gov_team => team(data.gov_team.as_ref()), opp_team => team(data.opp_team.as_ref()),
                chair_id => data.chair, room => &data.room, victor => data.victor,
                judges, get_choice_display => choice_label, manual_team_positions => positions,
                manual_events => manual.remove(&id).unwrap_or_default(),
                native_revision => data.revision(),
            });
        }
        excluded_teams.extend(roster.iter().filter_map(|(kind, team)| {
            (*kind == stage.division && !lost.contains(&team.id) && !paired.contains(&team.id))
                .then_some(team)
        }));
        let panel_key = if division == 0 {
            "var_panel_size"
        } else {
            "nov_panel_size"
        };
        let judge_slots: Vec<_> = (1..=settings.integer(panel_key, 3).min(512)).collect();
        sections.push(context! {
            type_of_round => stage.division, num_teams => stage.size, label, outrounds, judge_slots,
            pairing_released => settings.integer(store::VISIBILITY[division], 256) <= stage.size,
            challenge_entry_pending => challenge(division, stage.size / 2),
        });
    }
    let mut tabcard_stages = Vec::new();
    for stage in &scope {
        if !tabcard_stages.contains(&stage.size) {
            tabcard_stages.push(stage.size);
        }
    }
    let label = if let [stage] = scope.as_slice() {
        stage.label()
    } else {
        "Selected Outrounds".into()
    };
    let pending: Vec<_> = (0..2).filter_map(|division| {
        let size = roster.iter().filter(|(kind, _)| *kind == division as i32).count() as i32;
        let stage = Stage::new(division as i32, size)?;
        (challenge(division, size) && !stages(&snapshots).any(|stage| stage.division == division as i32))
            .then(|| context! { kind => division, size, previous_size => size * 2, label => stage.division_name() })
    }).collect();

    let (checked, non_checkins): (Vec<_>, Vec<_>) = judges
        .iter()
        .filter(|judge| !used_judges.contains(&judge.id))
        .partition(|judge| judge.checked);
    let excluded_judges: Vec<_> = checked
        .iter()
        .filter_map(|judge| assignments.get(&judge.id))
        .collect();
    let non_checkins: Vec<_> = non_checkins
        .iter()
        .filter_map(|judge| assignments.get(&judge.id))
        .collect();
    let available_rooms: Vec<_> = rooms
        .iter()
        .filter(|room| !used_rooms.contains(&room.id))
        .collect();
    let max = excluded_teams
        .len()
        .max(excluded_judges.len())
        .max(non_checkins.len())
        .max(available_rooms.len())
        .max(1);
    let excluded_people: Vec<_> = (0..max)
        .map(|i| {
            (
                excluded_teams.get(i),
                excluded_judges.get(i),
                non_checkins.get(i),
                available_rooms.get(i),
            )
        })
        .collect();
    let options_for = |division: i32| {
        options[division as usize]
            .iter()
            .map(|&num_teams| {
                context! {
                    num_teams, label => Stage { division, size: num_teams }.label(),
                }
            })
            .collect::<Vec<_>>()
    };
    let shell = legacy_html::shell(&mut db, actor, &fallback.url()).await?;
    db.commit().await?;
    let csrf = TemplateCsrf::new(headers).map_err(|_| Error::Database)?;
    let mut response = crate::staff_roster_notice::render(
        app,
        headers,
        &fallback.url(),
        "outrounds/pairing_base.html",
        shell,
        context! {
            choice => settings.flag("choice", false), return_path, csrf_input => csrf.input(),
            num_teams => fallback.size, stats_round_numbers => tabcard_stages,
            label, pending_challenges => pending, outround_sections => sections,
            selected_varsity => selected[0], selected_novice => selected[1],
            available_varsity_rounds => options_for(0), available_novice_rounds => options_for(1),
            warnings, excluded_teams, excluded_judges, non_checkins, available_rooms, excluded_people,
        },
        StatusCode::OK,
    );
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}

fn loser(snapshot: &Snapshot) -> Option<i32> {
    match snapshot.victor {
        Some(1 | 3) => snapshot.opp_team.as_ref().map(|team| team.id),
        Some(2 | 4) => snapshot.gov_team.as_ref().map(|team| team.id),
        _ => None,
    }
}

async fn challenge_once(db: &mut MySqlConnection, mode: i32) -> Result<[bool; 2], sqlx::Error> {
    if mode != 2 {
        return Ok([false; 2]);
    }
    let generations: HashMap<_, _> = sqlx::query_as::<_, (i32, i32)>(
        "SELECT type_of_team, MIN(id) FROM tab_breakingteam WHERE type_of_team IN (0, 1) GROUP BY type_of_team"
    ).fetch_all(&mut *db).await?.into_iter().collect();
    let keys: [String; 4] = std::array::from_fn(|index| {
        let division = index / 2;
        if index % 2 == 1 {
            format!(
                "challenge_once_applied_{division}_{}",
                generations
                    .get(&(division as i32))
                    .copied()
                    .unwrap_or_default()
            )
        } else {
            format!("challenge_once_applied_{division}")
        }
    });
    let settings = Settings::load(db, &keys.each_ref().map(String::as_str)).await?;
    Ok(std::array::from_fn(|division| {
        settings.flag(&keys[division * 2], false) || settings.flag(&keys[division * 2 + 1], false)
    }))
}

async fn roster(db: &mut MySqlConnection) -> Result<Vec<(i32, Identity)>, sqlx::Error> {
    Ok(sqlx::query_as::<_, (i32, i32, String)>(
        "SELECT b.type_of_team, t.id, t.name FROM tab_breakingteam b
         JOIN tab_team t ON t.id = b.team_id ORDER BY b.id",
    )
    .fetch_all(db)
    .await?
    .into_iter()
    .map(|(kind, id, name)| (kind, Identity { id, name }))
    .collect())
}

struct JudgeStatus {
    id: i32,
    name: String,
    rank: String,
    wing: bool,
    request_wing: bool,
    no_wings: bool,
    checked: bool,
}

async fn resources(
    db: &mut MySqlConnection,
) -> Result<(Vec<JudgeStatus>, Vec<Identity>), sqlx::Error> {
    let judges = sqlx::query_as::<_, (i32, String, String, bool, bool, bool, bool)>(
        "SELECT j.id, j.name, CAST(j.rank AS CHAR), j.wing_only, j.request_wing, j.no_wings,
             EXISTS(SELECT 1 FROM tab_checkin c WHERE c.judge_id = j.id AND c.round_number = 0)
         FROM tab_judge j ORDER BY j.name, j.id",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(
        |(id, name, rank, wing, request_wing, no_wings, checked)| JudgeStatus {
            id,
            name,
            rank,
            wing,
            request_wing,
            no_wings,
            checked,
        },
    )
    .collect();
    let rooms = sqlx::query_as::<_, (i32, String)>(
        "SELECT m.id, m.name FROM tab_room m JOIN tab_roomcheckin c ON c.room_id = m.id AND c.round_number = 0 ORDER BY m.name, m.id",
    ).fetch_all(db).await?.into_iter().map(|(id, name)| Identity {id, name}).collect();
    Ok((judges, rooms))
}

async fn warnings(db: &mut MySqlConnection) -> Result<HashMap<i32, Vec<String>>, sqlx::Error> {
    let rows = sqlx::query_as::<_, (i32, String)>(
        "WITH required_tags AS (
           SELECT r.id, r.room_id, x.roomtag_id FROM tab_outround r
           JOIN tab_team_required_room_tags x ON x.team_id IN (r.gov_team_id, r.opp_team_id)
           WHERE r.room_id IS NOT NULL
           UNION
           SELECT r.id, r.room_id, x.roomtag_id FROM tab_outround r
           JOIN tab_outround_judges j ON j.outround_id = r.id
           JOIN tab_judge_required_room_tags x ON x.judge_id = j.judge_id WHERE r.room_id IS NOT NULL
         )
         SELECT r.id, tag.tag FROM required_tags r JOIN tab_roomtag tag ON tag.id = r.roomtag_id
         WHERE NOT EXISTS(SELECT 1 FROM tab_room_tags x WHERE x.room_id = r.room_id AND x.roomtag_id = r.roomtag_id)
         ORDER BY r.id, r.roomtag_id",
    ).fetch_all(db).await?;
    let mut warnings = HashMap::<_, Vec<_>>::new();
    for (id, tag) in rows {
        warnings.entry(id).or_default().push(tag);
    }
    Ok(warnings)
}

fn challenge_context(state: Option<&store::ChallengeState>, mode: i32) -> Value {
    let Some(state) = state.filter(|state| state.enabled) else {
        return context! {enabled => false};
    };
    let used: HashSet<_> = state
        .picks
        .iter()
        .flat_map(|(&id, &opponent)| [id, opponent])
        .collect();
    let pending: Vec<_> = state
        .entries
        .iter()
        .filter(|team| !used.contains(&team.team))
        .collect();
    let info = |team: &store::Entry| {
        let seed = state.display_seed(team);
        context! { id => team.team, name => &team.name, seed, label => format!("[{seed}] {}", team.name) }
    };
    let saved: Vec<_> = state
        .entries
        .iter()
        .filter_map(|team| {
            let target = state.picks.get(&team.team)?;
            let opponent = state.entries.iter().find(|other| other.team == *target)?;
            Some(context! {challenger => info(team), challenged => info(opponent)})
        })
        .collect();
    let next = (pending.len() > 2).then(|| info(pending[0]));
    let automatic_pair: Vec<_> = if pending.len() == 2 {
        pending.iter().map(|team| info(team)).collect()
    } else {
        Vec::new()
    };
    let mut options: Vec<_> = pending.iter().skip(1).copied().collect();
    options.sort_by_cached_key(|team| (team.name.to_lowercase(), team.team));
    context! {
        enabled => true, num_teams => state.stage.size,
        mode_label => if mode == 1 {"Challenge Each Round"} else {"Challenge Once (Seed Bracket)"},
        seed_basis_label => if state.uses_original_seeds() {"Original Seeds"} else {"Previous Round Seeds"},
        choices_entered => saved.len(), choices_total => (state.entries.len() / 2).saturating_sub(1),
        saved_challenges => saved, next_challenger => next, automatic_pair,
        opponent_options => options.into_iter().map(info).collect::<Vec<_>>(),
    }
}

pub async fn prepare(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    prior: Stage,
    checks: bool,
    message: Option<&str>,
    return_path: Option<&str>,
) -> Result<Response, Error> {
    let mut db = app.db.begin().await?;
    let settings = Settings::load(&mut db, store::SETTINGS).await?;
    let snapshots = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let lost: HashSet<_> = snapshots.values().filter_map(loser).collect();
    let left = roster(&mut db)
        .await?
        .into_iter()
        .filter(|(division, team)| *division == prior.division && !lost.contains(&team.id))
        .count();
    let (judges, rooms) = resources(&mut db).await?;
    let judges = judges.iter().filter(|judge| judge.checked).count();
    let panel_key = if prior.division == 0 {
        "var_panel_size"
    } else {
        "nov_panel_size"
    };
    let needed_rooms = left / 2;
    let needed_judges = needed_rooms * settings.integer(panel_key, 3).max(0) as usize;
    let pending = snapshots
        .values()
        .any(|pairing| prior.matches(pairing) && pairing.victor == Some(0));
    let state = match store::prepare(&mut db, &settings, prior).await {
        Ok(state) => Some(state),
        Err(Error::Invalid(_)) => None,
        Err(error) => return Err(error),
    };
    let checks_label = [
        ("Have more rounds?".into(), prior.size > 2, "Have enough teams!", "Not enough teams"),
        (format!("Enough judges checked in for Out-rounds? Need {needed_judges}, have {judges}"),
            judges >= needed_judges, "Judges are checked in", "Not enough judges"),
        (format!("N/2 Rooms available Round Out-rounds? Need {needed_rooms}, have {}", rooms.len()),
            rooms.len() >= needed_rooms, "Rooms are checked in", "Not enough rooms"),
        (format!("All Rounds properly entered for Round {}", prior.label()), !pending,
         "All rounds look good", "Not all rounds are entered. 'You have not entered all the results from the previous round.'"),
    ].map(|(text, pass, good, bad)| (text, if pass {"Yes"} else {"No"}, if pass {good} else {bad}));
    let path = format!("/outround_pairing/pair/{}/{}/", prior.division, prior.size);
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    db.commit().await?;
    let csrf = TemplateCsrf::new(headers).map_err(|_| Error::Database)?;
    let mut response = crate::staff_roster_notice::render(
        app,
        headers,
        &path,
        "pairing/pair_round.html",
        shell,
        context! {
            title => format!("Prepare {} Round of {}", prior.division_name(), prior.size / 2),
            csrf_input => csrf.input(), check_status => checks_label, return_path => return_path.unwrap_or(&path),
            checked_in_judges => Vec::<Value>::new(),
            show_challenge_entry => !checks && state.as_ref().is_some_and(|state| state.enabled),
            challenge_context => challenge_context(state.as_ref(), settings.integer("outround_pairing_mode", 0)),
            messages => message.map(|text| [context! {text, tags => "danger", level => 40}]),
            DEFAULT_MESSAGE_LEVELS => context! {ERROR => 40},
        },
        if message.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}

pub async fn ballot(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: i32,
    message: Option<&str>,
) -> Result<Response, Error> {
    let mut db = app.db.begin().await?;
    let mut pairings = board_snapshot::capture(&mut db, Pairings::Outround).await?;
    let pairing = pairings.remove(&id).ok_or(Error::Missing)?;
    let (Some(gov), Some(opp), Some(size)) =
        (&pairing.gov_team, &pairing.opp_team, pairing.num_teams)
    else {
        return Err(Error::Missing);
    };
    let path = format!("/outround/{id}/result/");
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    db.commit().await?;
    let csrf = TemplateCsrf::new(headers).map_err(|_| Error::Database)?;
    let mut select =
        String::from("<select name=\"winner\" class=\"form-control\" id=\"id_winner\">\n");
    for (code, label) in [
        "UNKNOWN",
        "GOV",
        "OPP",
        "GOV via Forfeit",
        "OPP via Forfeit",
    ]
    .into_iter()
    .enumerate()
    {
        let selected = if code > 0 && pairing.victor == Some(code as i32) {
            " selected"
        } else {
            ""
        };
        select.push_str(&format!(
            "  <option value=\"{code}\"{selected}>{label}</option>\n\n"
        ));
    }
    select.push_str("</select>");
    let hidden = [Value::from_safe_string(format!(
        "<input type=\"hidden\" name=\"round_instance\" value=\"{id}\" id=\"id_round_instance\">"
    ))];
    let revision_input = format!(
        "<input type=\"hidden\" name=\"revision\" value=\"{}\">",
        pairing.revision()
    );
    let mut response = legacy_html::render(
        "outrounds/ballot.html",
        shell,
        context! {
            title => format!("Entering Ballot for Outround {size} between {} and {}", gov.name, opp.name),
            csrf_input => csrf.input(), round_input => revision_input, form => context! {
                hidden_fields => hidden, errors => usize::from(message.is_some()),
                non_field_errors => message,
            },
            widgets => context! { winner => context! {
                field => context! { label_tag => Value::from_safe_string("<label for=\"id_winner\">Which team won the round?</label>".into()) },
                input => select,
            }},
        },
        if message.is_some() {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            StatusCode::OK
        },
    );
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}
