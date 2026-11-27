use std::{cmp::Reverse, collections::HashMap};

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::context;
use mittab_tabulation::ballots::ScoringPolicy;
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access, board_snapshot, legacy_html, legacy_session::Actor, login::TemplateCsrf, private,
    room_assignment::seeded_key, settings::Settings, staff_pairing::ChangeError,
    tournament_data::TournamentData, tournament_maintenance,
};

#[derive(Serialize)]
struct Named {
    id: i32,
    name: String,
}

#[derive(Serialize)]
struct Team {
    id: i32,
    display_backend: String,
}

#[derive(Serialize)]
struct Judge {
    id: i32,
    name: String,
    rank: String,
    wing_only: bool,
    request_wing: bool,
    no_wings: bool,
    has_rank: bool,
    high_rank: bool,
    manual_label: Option<String>,
    rejudge_count: i64,
}

impl Judge {
    fn from_row(
        id: i32,
        name: String,
        rank: String,
        wing_only: bool,
        request_wing: bool,
        no_wings: bool,
    ) -> Self {
        let numeric = rank.parse::<f64>().unwrap_or_default();
        Self {
            id,
            name,
            rank,
            wing_only,
            request_wing,
            no_wings,
            has_rank: numeric != 0.0,
            high_rank: numeric > 5.0,
            manual_label: None,
            rejudge_count: 0,
        }
    }
}

#[derive(Serialize)]
struct ManualEvent {
    label: &'static str,
    user_display: String,
    note: String,
}

#[derive(Serialize)]
struct Pairing {
    id: i32,
    gov_team: Team,
    opp_team: Team,
    chair_id: Option<i32>,
    room: Option<Named>,
    victor: i32,
    judges: Vec<Judge>,
    manual_team_positions: Vec<&'static str>,
    manual_events: Vec<ManualEvent>,
    #[serde(skip)]
    seeds: (i32, i32),
}

pub async fn render(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    selected: Option<i32>,
) -> Result<Response, ChangeError> {
    // This projection contains judge calibration. Board uses its own pairing view.
    if !matches!(actor, Actor::User { board: false, .. }) {
        return Ok(access::access_denied());
    }
    let mut db = app.db.begin().await?;
    let settings = Settings::load(
        &mut db,
        &[
            "cur_round",
            "tot_rounds",
            "lenient_late",
            "pairing_released",
        ],
    )
    .await?;
    let current = settings.integer("cur_round", 1).saturating_sub(1);
    let round_number = selected.unwrap_or(current);
    if selected.is_some() && !(1..=current).contains(&round_number) {
        return Ok(private((StatusCode::NOT_FOUND, "Round not found.")));
    }
    let path = selected.map_or_else(
        || "/pairings/status/".into(),
        |round| format!("/round/{round}/"),
    );
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    let mut round_info = pairings(&mut db, round_number).await?;
    sort_pairings(&mut db, &settings, round_number, &mut round_info).await?;
    let warnings = room_warnings(&mut db, round_number, &round_info).await?;
    let round_numbers = sqlx::query_scalar::<_, i32>(
        "SELECT DISTINCT round_number FROM tab_round ORDER BY round_number",
    )
    .fetch_all(&mut *db)
    .await?;
    let teams = unpaired_teams(&mut db, round_number).await?;
    let (excluded_judges, non_checkins) = unpaired_judges(&mut db, round_number).await?;
    let rooms = sqlx::query_as::<_, (i32, String)>(
        "SELECT m.id, m.name FROM tab_room m WHERE m.rank <> 0 AND NOT EXISTS (
            SELECT 1 FROM tab_round r WHERE r.round_number = ? AND r.room_id = m.id
        ) ORDER BY m.name, m.id",
    )
    .bind(round_number)
    .fetch_all(&mut *db)
    .await?;
    let schools =
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_school ORDER BY name, id")
            .fetch_all(&mut *db)
            .await?;
    db.commit().await?;

    let excluded_teams_no_bye: Vec<_> = teams
        .iter()
        .filter(|(_, _, bye)| !bye)
        .map(|(id, name, _)| Named {
            id: *id,
            name: name.clone(),
        })
        .collect();
    let teams: Vec<_> = teams
        .into_iter()
        .map(|(id, name, _)| Named { id, name })
        .collect();
    let rooms: Vec<_> = rooms
        .into_iter()
        .map(|(id, name)| Named { id, name })
        .collect();
    let schools: Vec<_> = schools
        .into_iter()
        .map(|(id, name)| Named { id, name })
        .collect();
    let size = teams
        .len()
        .max(excluded_judges.len())
        .max(non_checkins.len());
    let excluded_people = (0..size)
        .map(|index| {
            (
                teams.get(index),
                excluded_judges.get(index),
                non_checkins.get(index),
                rooms.get(index),
            )
        })
        .collect::<Vec<_>>();
    let csrf = TemplateCsrf::new(headers).map_err(|_| ChangeError::Storage)?;
    let mut response = legacy_html::render(
        "pairing/pairing_control.html",
        shell,
        context! {
            csrf_input => csrf.input(),
            round_number, round_numbers,
            round_input => format!("<input type=\"hidden\" name=\"round\" value=\"{round_number}\">"),
            warnings, round_info => &round_info,
            tot_rounds => settings.integer("tot_rounds", 5),
            pairing_released => settings.integer("pairing_released", 0) == 1,
            judges_assigned => round_info.iter().all(|pairing| !pairing.judges.is_empty()),
            rooms_assigned => round_info.iter().all(|pairing| pairing.room.is_some()),
            simulate_round_button => tournament_maintenance::simulation_enabled(),
            num_excluded => excluded_teams_no_bye.len(),
            excluded_teams_no_bye,
            excluded_teams => &teams,
            excluded_judges => &excluded_judges,
            non_checkins => &non_checkins,
            available_rooms => &rooms,
            excluded_people,
            all_schools => schools,
            judge_slots => [1, 2, 3],
        },
        StatusCode::OK,
    );
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}

async fn sort_pairings(
    db: &mut MySqlConnection,
    settings: &Settings,
    round: i32,
    rows: &mut [Pairing],
) -> Result<(), ChangeError> {
    if round == 1 {
        rows.sort_by_key(|pairing| {
            Reverse((
                pairing.seeds.0.max(pairing.seeds.1),
                pairing.seeds.0.min(pairing.seeds.1),
            ))
        });
    } else if rows.len() > 1 {
        let data = TournamentData::load(db).await?;
        let scores = data
            .scores_by_team(ScoringPolicy {
                current_round: settings.integer("cur_round", 1),
                lenient_late_through: settings.integer("lenient_late", 0),
            })
            .map_err(ChangeError::Conflict)?;
        let strength = |pairing: &Pairing| {
            seeded_key(
                &scores[&i64::from(pairing.gov_team.id)],
                &scores[&i64::from(pairing.opp_team.id)],
            )
        };
        rows.sort_by(|left, right| {
            let (a, b) = (strength(left), strength(right));
            b.0.total_cmp(&a.0)
                .then_with(|| b.1.total_cmp(&a.1))
                .then_with(|| b.2.total_cmp(&a.2))
        });
    }
    Ok(())
}

async fn pairings(db: &mut MySqlConnection, round: i32) -> Result<Vec<Pairing>, sqlx::Error> {
    type Round = (
        i32,
        i32,
        String,
        i32,
        i32,
        String,
        i32,
        Option<i32>,
        Option<i32>,
        Option<String>,
        i32,
    );
    let rows = sqlx::query_as::<_, Round>(
        "SELECT r.id, g.id, g.name, g.seed, o.id, o.name, o.seed, r.chair_id, m.id, m.name, r.victor
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_room m ON m.id = r.room_id WHERE r.round_number = ? ORDER BY r.id",
    ).bind(round).fetch_all(&mut *db).await?;
    if rows.is_empty() {
        return Ok(Vec::new());
    }
    let mut panels = panels(db, round).await?;
    let mut events = manual_events(db, round).await?;
    let mut edits = board_snapshot::histories(db, "round").await?;
    Ok(rows
        .into_iter()
        .map(
            |(
                id,
                gov,
                government,
                gov_seed,
                opp,
                opposition,
                opp_seed,
                chair_id,
                room,
                room_name,
                victor,
            )| {
                let edit = edits.take_staff_edits(id);
                let mut manual_team_positions = Vec::new();
                if edit.gov {
                    manual_team_positions.push("gov");
                }
                if edit.opp {
                    manual_team_positions.push("opp");
                }
                Pairing {
                    id,
                    chair_id,
                    victor,
                    manual_team_positions,
                    seeds: (gov_seed, opp_seed),
                    gov_team: Team {
                        id: gov,
                        display_backend: government,
                    },
                    opp_team: Team {
                        id: opp,
                        display_backend: opposition,
                    },
                    room: room.zip(room_name).map(|(id, name)| Named { id, name }),
                    judges: panels.remove(&id).unwrap_or_default(),
                    manual_events: events.remove(&id).unwrap_or_default(),
                }
            },
        )
        .collect())
}

async fn panels(
    db: &mut MySqlConnection,
    round: i32,
) -> Result<HashMap<i32, Vec<Judge>>, sqlx::Error> {
    type Panel = (
        i32,
        i32,
        String,
        String,
        bool,
        bool,
        bool,
        Option<String>,
        i64,
    );
    let mut panels: HashMap<_, Vec<_>> = HashMap::new();
    for (round_id, id, name, rank, wing, request, no_wings, manual, count) in sqlx::query_as::<_, Panel>(
        "SELECT r.id, j.id, j.name, CAST(j.rank AS CHAR), j.wing_only, j.request_wing, j.no_wings,
            IF(manual.id IS NULL, NULL, COALESCE(u.username, 'Unknown')),
            GREATEST(
                (SELECT COUNT(*) FROM tab_round_judges old JOIN tab_round seen ON seen.id = old.round_id
                 WHERE old.judge_id = j.id AND r.gov_team_id IN (seen.gov_team_id, seen.opp_team_id)),
                (SELECT COUNT(*) FROM tab_round_judges old JOIN tab_round seen ON seen.id = old.round_id
                 WHERE old.judge_id = j.id AND r.opp_team_id IN (seen.gov_team_id, seen.opp_team_id))
            )
         FROM tab_round r JOIN tab_round_judges x ON x.round_id = r.id JOIN tab_judge j ON j.id = x.judge_id
         LEFT JOIN tab_manualjudgeassignment manual ON manual.round_id = r.id AND manual.judge_id = j.id
         LEFT JOIN auth_user u ON u.id = manual.created_by_id
         WHERE r.round_number = ? ORDER BY j.name, j.id",
    ).bind(round).fetch_all(db).await? {
        let mut judge = Judge::from_row(id, name, rank, wing, request, no_wings);
        judge.manual_label = manual.map(|user| format!("Manually assigned by {user}"));
        judge.rejudge_count = count;
        panels.entry(round_id).or_default().push(judge);
    }
    Ok(panels)
}

async fn manual_events(
    db: &mut MySqlConnection,
    round: i32,
) -> Result<HashMap<i32, Vec<ManualEvent>>, sqlx::Error> {
    let mut events: HashMap<_, Vec<_>> = HashMap::new();
    for (id, kind, user_display, note) in sqlx::query_as::<_, (i32, String, String, String)>(
        "SELECT r.id, a.event_type, COALESCE(u.username, 'Unknown'), a.note
         FROM tab_round r JOIN tab_auditevent a ON a.object_id = r.id
         JOIN django_content_type t ON t.id = a.content_type_id
         LEFT JOIN auth_user u ON u.id = a.user_id
         WHERE r.round_number = ? AND t.app_label = 'tab' AND t.model = 'round'
           AND a.event_type IN ('manual_judge_assign', 'manual_judge_remove', 'manual_chair_change')
           AND NOT EXISTS (SELECT 1 FROM tab_auditevent reset WHERE reset.object_id = r.id
              AND reset.content_type_id = a.content_type_id AND reset.event_type = 'judges_paired' AND reset.id > a.id)
         ORDER BY a.created_at DESC, a.id DESC",
    ).bind(round).fetch_all(db).await? {
        let label = match kind.as_str() {
            "manual_judge_assign" => "Manual judge assignment",
            "manual_judge_remove" => "Manual judge removal",
            _ => "Manual chair change",
        };
        events.entry(id).or_default().push(ManualEvent { label, user_display, note });
    }
    Ok(events)
}

async fn room_warnings(
    db: &mut MySqlConnection,
    round: i32,
    rows: &[Pairing],
) -> Result<Vec<String>, sqlx::Error> {
    if rows.iter().all(|pairing| pairing.room.is_none()) {
        return Ok(Vec::new());
    }
    let mut missing: HashMap<_, Vec<_>> = HashMap::new();
    for (id, tag) in sqlx::query_as::<_, (i32, String)>(
        "WITH missing AS (
            SELECT r.id, r.room_id, x.roomtag_id FROM tab_round r
            JOIN tab_team_required_room_tags x ON x.team_id IN (r.gov_team_id, r.opp_team_id)
            WHERE r.round_number = ? AND r.room_id IS NOT NULL
            UNION
            SELECT r.id, r.room_id, x.roomtag_id FROM tab_round r
            JOIN tab_round_judges p ON p.round_id = r.id JOIN tab_judge_required_room_tags x ON x.judge_id = p.judge_id
            WHERE r.round_number = ? AND r.room_id IS NOT NULL
         )
         SELECT m.id, tag.tag FROM missing m JOIN tab_roomtag tag ON tag.id = m.roomtag_id
         WHERE NOT EXISTS (SELECT 1 FROM tab_room_tags t WHERE t.room_id = m.room_id AND t.roomtag_id = m.roomtag_id)
         ORDER BY m.id, m.roomtag_id",
    ).bind(round).bind(round).fetch_all(db).await? {
        missing.entry(id).or_default().push(tag);
    }
    Ok(rows
        .iter()
        .filter_map(|pairing| {
            let room = pairing.room.as_ref()?;
            let tags = missing.get(&pairing.id)?;
            let plural = if tags.len() > 1 { "s" } else { "" };
            Some(format!(
                "{} vs {} requires tag{plural} {} that are not assigned to room {}",
                pairing.gov_team.display_backend,
                pairing.opp_team.display_backend,
                tags.join(", "),
                room.name
            ))
        })
        .collect())
}

async fn unpaired_teams(
    db: &mut MySqlConnection,
    round: i32,
) -> Result<Vec<(i32, String, bool)>, sqlx::Error> {
    sqlx::query_as(
        "SELECT t.id, t.name, EXISTS(SELECT 1 FROM tab_bye b WHERE b.round_number = ? AND b.bye_team_id = t.id)
         FROM tab_team t WHERE t.checked_in = TRUE
         AND NOT EXISTS (SELECT 1 FROM tab_round r WHERE r.round_number = ? AND t.id IN (r.gov_team_id, r.opp_team_id))
         ORDER BY t.id",
    ).bind(round).bind(round).fetch_all(db).await
}

async fn unpaired_judges(
    db: &mut MySqlConnection,
    round: i32,
) -> Result<(Vec<Judge>, Vec<Judge>), sqlx::Error> {
    let mut checked_in = Vec::new();
    let mut absent = Vec::new();
    for (id, name, rank, checked) in sqlx::query_as::<_, (i32, String, String, bool)>(
        "SELECT j.id, j.name, CAST(j.rank AS CHAR),
           EXISTS(SELECT 1 FROM tab_checkin c WHERE c.judge_id = j.id AND c.round_number = ?)
         FROM tab_judge j WHERE NOT EXISTS (SELECT 1 FROM tab_round_judges x
              JOIN tab_round r ON r.id = x.round_id WHERE r.round_number = ? AND x.judge_id = j.id)
         ORDER BY j.name, j.id",
    )
    .bind(round)
    .bind(round)
    .fetch_all(db)
    .await?
    {
        let judge = Judge::from_row(id, name, rank, false, false, false);
        if checked {
            checked_in.push(judge);
        } else {
            absent.push(judge);
        }
    }
    Ok((checked_in, absent))
}
