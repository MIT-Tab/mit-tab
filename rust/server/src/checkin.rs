use std::collections::{BTreeSet, HashMap, HashSet};

use axum::{
    Extension, Json,
    body::Bytes,
    extract::{Query, State},
    http::{HeaderMap, StatusCode, header},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::context;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    checkin_data::{self as data, ChangeError, Entity},
    legacy_html,
    legacy_session::{Actor, cookie},
    login, private,
    settings::Settings,
    staff_pairing::lock_round,
    staff_roster_notice, unavailable,
};

const PAGE: &str = "/batch_checkin/";
const SETTINGS: &[&str] = &["tot_rounds", "debaters_public"];
const RESTORED_COOKIE: &str = "mittab_checkins_restored";

#[derive(Deserialize)]
pub struct Filters {
    history: Option<String>,
    page: Option<String>,
    before: Option<String>,
}

#[derive(Serialize)]
struct RosterName {
    id: i32,
    name: String,
}

#[derive(Serialize)]
struct TeamRow {
    entity: RosterName,
    school: RosterName,
    debaters: String,
    checked_in: bool,
}

#[derive(Serialize)]
struct Judge {
    id: i32,
    name: String,
    wing_only: bool,
    request_wing: bool,
    no_wings: bool,
}

#[derive(Serialize)]
struct GridRow<'a, T> {
    entity: &'a T,
    schools: &'a [RosterName],
    checkins: Vec<bool>,
    expected_checkins: Vec<bool>,
}

#[derive(Serialize)]
struct RoundCount {
    index: i32,
    round_number: i32,
    count: usize,
}

#[derive(Serialize)]
struct Undo {
    id: String,
    count: usize,
}

impl From<data::Change> for Undo {
    fn from(change: data::Change) -> Self {
        Self {
            id: data::printable(&change.token),
            count: change.cells.len(),
        }
    }
}

async fn team_rows(
    db: &mut MySqlConnection,
    display_debaters: bool,
) -> Result<Vec<TeamRow>, sqlx::Error> {
    let mut names: HashMap<i32, Vec<String>> = HashMap::new();
    if display_debaters {
        let debaters = sqlx::query_as::<_, (i32, String)>(
            "SELECT td.team_id, d.name FROM tab_team_debaters td
               JOIN tab_debater d ON d.id = td.debater_id ORDER BY d.name",
        )
        .fetch_all(&mut *db)
        .await?;
        for (id, name) in debaters {
            names.entry(id).or_default().push(name);
        }
    }
    let rows = sqlx::query_as::<_, (i32, String, i32, String, bool)>(
        "SELECT t.id, t.name, s.id, s.name, t.checked_in FROM tab_team t
           JOIN tab_school s ON s.id = t.school_id ORDER BY t.id",
    )
    .fetch_all(db)
    .await?
    .into_iter()
    .map(|(id, name, school_id, school, checked_in)| TeamRow {
        entity: RosterName { id, name },
        school: RosterName {
            id: school_id,
            name: school,
        },
        debaters: names
            .get(&id)
            .map(|names| names.join(", "))
            .unwrap_or_default(),
        checked_in,
    })
    .collect();
    Ok(rows)
}

fn counts<T>(rows: &[GridRow<'_, T>], rounds: &[i32]) -> (Vec<RoundCount>, usize) {
    let mut counts = vec![0; rounds.len()];
    for row in rows {
        for (count, &checked) in counts.iter_mut().zip(&row.checkins) {
            *count += usize::from(checked);
        }
    }
    let outs = counts.pop().unwrap_or_default();
    let prelims = rounds[..rounds.len() - 1]
        .iter()
        .copied()
        .zip(counts)
        .map(|(round_number, count)| RoundCount {
            index: round_number,
            round_number,
            count,
        })
        .collect();
    (prelims, outs)
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Query(filters): Query<Filters>,
) -> Response {
    if let Some(kind) = &filters.history {
        let Some(entity) = Entity::parse(kind) else {
            return private((StatusCode::BAD_REQUEST, "Unknown check-in category."));
        };
        return history(&app, actor, &headers, entity, filters, false).await;
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, SETTINGS).await?;
        let rounds: Vec<_> = (1..=settings.integer("tot_rounds", 0))
            .chain(std::iter::once(0))
            .collect();
        let teams = team_rows(&mut db, settings.flag("debaters_public", true)).await?;
        let judges: Vec<_> = sqlx::query_as::<_, (i32, String, bool, bool, bool)>(
            "SELECT id, name, wing_only, request_wing, no_wings FROM tab_judge ORDER BY name",
        )
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .map(|(id, name, wing_only, request_wing, no_wings)| Judge {
            id,
            name,
            wing_only,
            request_wing,
            no_wings,
        })
        .collect();
        let mut schools = HashMap::<i32, Vec<RosterName>>::new();
        for (judge, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
            "SELECT x.judge_id, s.id, s.name FROM tab_judge_schools x
               JOIN tab_school s ON s.id = x.school_id ORDER BY s.name",
        )
        .fetch_all(&mut *db)
        .await?
        {
            schools
                .entry(judge)
                .or_default()
                .push(RosterName { id, name });
        }
        let judge_checkins = data::checked(&mut db, Entity::Judge).await?;
        let expected = data::checked(&mut db, Entity::Expected).await?;
        let judge_rows: Vec<_> = judges
            .iter()
            .map(|judge| GridRow {
                entity: judge,
                schools: schools.get(&judge.id).map_or(&[], Vec::as_slice),
                checkins: rounds
                    .iter()
                    .map(|&round| judge_checkins.contains(&(judge.id, Some(round))))
                    .collect(),
                expected_checkins: rounds
                    .iter()
                    .map(|&round| expected.contains(&(judge.id, Some(round))))
                    .collect(),
            })
            .collect();
        let expected_rows: Vec<_> = judge_rows
            .iter()
            .map(|judge| GridRow {
                entity: judge.entity,
                schools: judge.schools,
                checkins: judge.expected_checkins.clone(),
                expected_checkins: judge.expected_checkins.clone(),
            })
            .collect();
        let rooms: Vec<_> =
            sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_room ORDER BY name")
                .fetch_all(&mut *db)
                .await?
                .into_iter()
                .map(|(id, name)| RosterName { id, name })
                .collect();
        let room_checkins = data::checked(&mut db, Entity::Room).await?;
        let room_rows: Vec<_> = rooms
            .iter()
            .map(|room| GridRow {
                entity: room,
                schools: &[],
                checkins: rounds
                    .iter()
                    .map(|&round| room_checkins.contains(&(room.id, Some(round))))
                    .collect(),
                expected_checkins: Vec::new(),
            })
            .collect();
        let mut undo = HashMap::new();
        for entity in Entity::ALL {
            if let Some(change) = data::latest(&mut db, entity).await? {
                undo.insert(
                    entity.key(),
                    context! {
                        token => data::printable(&change.token),
                        cells => change.cells,
                    },
                );
            }
        }
        let shell = legacy_html::shell(&mut db, actor, PAGE).await?;
        db.commit().await?;

        let (judge_round_counts, judge_outround_count) = counts(&judge_rows, &rounds);
        let (judge_expectation_round_counts, judge_expectation_outround_count) =
            counts(&expected_rows, &rounds);
        let (room_round_counts, room_outround_count) = counts(&room_rows, &rounds);
        let Ok(csrf) = login::TemplateCsrf::new(&headers) else {
            return Ok(unavailable());
        };
        let restored = cookie(&headers, RESTORED_COOKIE)
            .and_then(|raw| raw.parse::<usize>().ok())
            .filter(|edits| *edits > 0);
        let messages = restored.map(|edits| {
            [context! {
                tags => "success",
                level => 25,
                text => format!("Reversed {edits} check-in edits."),
            }]
        });
        let mut response = staff_roster_notice::render(
            &app,
            &headers,
            PAGE,
            "batch_check_in/check_in.html",
            shell,
            context! {
                csrf_input => csrf.input(),
                messages,
                DEFAULT_MESSAGE_LEVELS => context! { ERROR => 40 },
                checkin_undo => undo,
                team_data => teams,
                team_headers => ["School", "Team", "Debater Names"],
                judge_data => judge_rows,
                judge_expectation_data => expected_rows,
                judge_headers => ["School", "Judge"],
                room_data => room_rows,
                room_headers => ["Room"],
                checkin_round_numbers => rounds,
                team_total_count => teams.len(),
                team_checked_in_count => teams.iter().filter(|row| row.checked_in).count(),
                judge_total_count => judges.len(),
                room_total_count => rooms.len(),
                judge_round_counts, judge_expectation_round_counts, room_round_counts,
                judge_outround_count, judge_expectation_outround_count, room_outround_count,
            },
            StatusCode::OK,
        );
        csrf.set_cookie(&mut response, &headers);
        if restored.is_some() {
            login::set_cookie(
                &mut response,
                RESTORED_COOKIE,
                "",
                0,
                login::secure_request(&headers),
                true,
            );
        }
        Ok::<_, sqlx::Error>(response)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Serialize)]
struct HistoryChange {
    token: String,
    checked: bool,
    undone: bool,
    cells: BTreeSet<(i32, Option<i32>)>,
    created_at: NaiveDateTime,
}

async fn history(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    entity: Entity,
    filters: Filters,
    mut conflict: bool,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let target = filters.before.filter(|token| !token.is_empty());
        if target.is_some() {
            lock_round(&mut db).await?;
        }
        let requested = filters
            .page
            .as_deref()
            .and_then(|page| page.trim().parse().ok())
            .unwrap_or(1);
        let (rows, number, pages) = data::history(&mut db, entity, requested).await?;
        let mut preview = None;
        if let Some(token) = &target {
            match data::restore(&mut db, entity, token, None).await {
                Ok(result) => preview = Some(result),
                Err(ChangeError::Conflict) => conflict = true,
                Err(ChangeError::Storage) => return Ok(unavailable()),
            }
        }
        let entries: Vec<_> = rows
            .into_iter()
            .map(|change| {
                let rounds = change
                    .cells
                    .iter()
                    .filter_map(|(_, round)| *round)
                    .collect::<BTreeSet<_>>();
                (
                    HistoryChange {
                        token: data::printable(&change.token),
                        checked: change.checked,
                        undone: change.undone,
                        cells: change.cells,
                        created_at: change.created,
                    },
                    rounds,
                )
            })
            .collect();
        let shell = legacy_html::shell(&mut db, actor, PAGE).await?;
        db.commit().await?;
        let Ok(csrf) = login::TemplateCsrf::new(headers) else {
            return Ok(unavailable());
        };
        let code = if conflict {
            StatusCode::CONFLICT
        } else {
            StatusCode::OK
        };
        let has_preview = preview.is_some();
        let mut response = legacy_html::render(
            "batch_check_in/history.html",
            shell,
            context! {
                csrf_input => csrf.input(),
                entity_type => entity.key(),
                tab_name => entity.title(),
                tab_id => entity.fragment(),
                page => context! {
                    number,
                    paginator => context! { num_pages => pages },
                    has_previous => number > 1,
                    has_next => number < pages,
                    previous_page_number => number.saturating_sub(1),
                    next_page_number => number.saturating_add(1),
                },
                entries,
                target,
                preview,
                conflict,
            },
            code,
        );
        if has_preview {
            csrf.set_cookie(&mut response, headers);
        }
        Ok::<_, sqlx::Error>(response)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if !headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.split(';').next() == Some("application/x-www-form-urlencoded"))
    {
        return private((
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "Submit a check-in form.",
        ));
    }
    let form: Vec<_> = url::form_urlencoded::parse(&body).collect();
    let value = |field: &str| {
        form.iter()
            .rev()
            .find(|(key, _)| key == field)
            .map_or("", |(_, value)| value.as_ref())
    };
    if !login::valid_csrf(&headers, value("csrfmiddlewaretoken")) {
        return private((StatusCode::FORBIDDEN, "CSRF verification failed."));
    }
    let action = value("action");
    let Some(entity) = Entity::parse(value("entity_type")) else {
        return answer(false, StatusCode::BAD_REQUEST);
    };
    let result: Result<Response, ChangeError> = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        if action == "undo" || action == "restore" {
            let target = value("change_id");
            let head = if action == "undo" {
                target
            } else {
                value("head")
            };
            let restored = data::restore(&mut db, entity, target, Some(head)).await?;
            db.commit().await?;
            if action == "restore" {
                let mut response = access::found(format!("{PAGE}#{}", entity.fragment()));
                login::set_cookie(
                    &mut response,
                    RESTORED_COOKIE,
                    &restored.edits.to_string(),
                    60,
                    login::secure_request(&headers),
                    true,
                );
                return Ok(response);
            }
            return Ok(answer(true, StatusCode::OK));
        }
        if !matches!(action, "check_in" | "check_out") {
            return Ok(answer(false, StatusCode::BAD_REQUEST));
        }
        let ids: HashSet<_> = form
            .iter()
            .filter(|(key, _)| key == "entity_ids[]")
            .filter_map(|(_, raw)| raw.parse::<i32>().ok().filter(|&id| id > 0))
            .collect();
        let rounds = if entity == Entity::Team {
            BTreeSet::from([None])
        } else {
            let settings = Settings::load(&mut db, SETTINGS).await?;
            let last = settings.integer("tot_rounds", 0);
            form.iter()
                .filter(|(key, _)| key == "rounds[]")
                .filter_map(|(_, raw)| raw.parse::<i32>().ok())
                .filter(|round| (0..=last).contains(round))
                .map(Some)
                .collect()
        };
        if ids.len().saturating_mul(rounds.len()) > 100_000 {
            return Ok(answer(false, StatusCode::BAD_REQUEST));
        }
        let change = if !ids.is_empty() && !rounds.is_empty() {
            data::apply(&mut db, entity, &ids, &rounds, action == "check_in")
                .await?
                .map(Undo::from)
        } else {
            None
        };
        db.commit().await?;
        Ok(if !value("include_undo").is_empty() {
            private(Json(json!({"success": true, "undo": change})))
        } else {
            answer(true, StatusCode::OK)
        })
    }
    .await;
    match result {
        Ok(response) => response,
        Err(ChangeError::Storage) => unavailable(),
        Err(ChangeError::Conflict) if action == "restore" => {
            history(
                &app,
                actor,
                &headers,
                entity,
                Filters {
                    history: None,
                    page: None,
                    before: None,
                },
                true,
            )
            .await
        }
        Err(ChangeError::Conflict) => answer(false, StatusCode::CONFLICT),
    }
}

fn answer(success: bool, code: StatusCode) -> Response {
    private((code, Json(json!({"success": success}))))
}
