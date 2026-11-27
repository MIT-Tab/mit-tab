use std::collections::{BTreeSet, HashMap};

use axum::{
    Extension, Form,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, Uri},
    response::Response,
};
use minijinja::{Value, context};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    ballot_entry::Submitted,
    legacy_html,
    legacy_session::{self, Actor, SessionError},
    login::{self, TemplateCsrf},
    settings::Settings,
    staff_pairing::lock_round_if_configured,
    staff_roster_fields::{self, Choices},
    staff_roster_notice, unavailable,
};

const SEARCH: &str = "/team_portal/";
const SETTINGS: &[&str] = &["cur_round", "judges_public"];

struct Access {
    name: bool,
    scratches: bool,
    slots: u16,
}

async fn config(db: &mut MySqlConnection) -> Result<Access, sqlx::Error> {
    let (name, scratches, slots) = sqlx::query_as::<_, (bool, bool, u16)>(
        "SELECT team_name_changes_allowed, disc_scratches_open, disc_scratch_quantity
         FROM registration_registrationconfig WHERE id = 1",
    )
    .fetch_optional(db)
    .await?
    .unwrap_or((false, false, 0));
    Ok(Access {
        name,
        scratches,
        slots,
    })
}

struct Team {
    id: i32,
    name: String,
    code: String,
    school: String,
    seed: i32,
}

fn seed_name(seed: i32) -> String {
    match seed {
        3 => "Full Seed".into(),
        2 => "Half Seed".into(),
        1 => "Free Seed".into(),
        0 => "Unseeded".into(),
        _ => seed.to_string(),
    }
}

pub(crate) fn valid_code(code: &str) -> bool {
    !code.is_empty()
        && code.len() <= 255
        && code
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

async fn find(
    db: &mut MySqlConnection,
    code: &str,
    lock: bool,
) -> Result<Option<Team>, sqlx::Error> {
    let query = if lock {
        "SELECT t.id, t.name, t.team_code, s.name, t.seed FROM tab_team t JOIN tab_school s ON s.id = t.school_id
         WHERE t.team_code = ? FOR UPDATE"
    } else {
        "SELECT t.id, t.name, t.team_code, s.name, t.seed FROM tab_team t JOIN tab_school s ON s.id = t.school_id
         WHERE t.team_code = ?"
    };
    Ok(
        sqlx::query_as::<_, (i32, String, String, String, i32)>(query)
            .bind(code)
            .fetch_optional(db)
            .await?
            .filter(|(_, _, stored, _, _)| valid_code(stored) && stored.eq_ignore_ascii_case(code))
            .map(|(id, name, code, school, seed)| Team {
                id,
                name,
                code,
                school,
                seed,
            }),
    )
}

fn render(
    app: &App,
    headers: &HeaderMap,
    path: &str,
    template: &str,
    shell: Value,
    page: Value,
) -> Response {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return unavailable();
    };
    let mut response = staff_roster_notice::render_team(
        app,
        headers,
        path,
        template,
        shell,
        context! { csrf_input => csrf.input(), ..page },
    );
    csrf.set_cookie(&mut response, headers);
    response
}

pub async fn search(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let actor = authorized_actor(&mut db, &app, actor, &headers).await?;
        let shell = legacy_html::shell(&mut db, actor, SEARCH).await?;
        db.commit().await?;
        Ok(render(
            &app,
            &headers,
            SEARCH,
            "registration/team_portal_search.html",
            shell,
            context! {},
        ))
    }
    .await;
    portal_result(&app, &headers, result).await
}

pub async fn lookup(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(fields): Form<Submitted>,
) -> Response {
    if !valid_csrf(&headers, &fields) {
        return login::csrf_error();
    }
    let code = fields.get("team_code").map_or("", |code| code.trim());
    let result = async {
        let mut db = app.db.acquire().await?;
        authorized_actor(&mut db, &app, actor, &headers).await?;
        if valid_code(code)
            && let Some(team) = find(&mut db, code, false).await?
        {
            return Ok(access::found(target(&team.code)));
        }
        let message = if code.is_empty() {
            "Please enter the team code provided by tab."
        } else {
            "We couldn't find a team for that code. Double-check and try again."
        };
        Ok(staff_roster_notice::team_redirect(
            &app, &headers, SEARCH, message, true,
        ))
    }
    .await;
    portal_result(&app, &headers, result).await
}

fn target(code: &str) -> String {
    format!("{SEARCH}{code}/")
}

fn valid_csrf(headers: &HeaderMap, fields: &Submitted) -> bool {
    login::valid_csrf(
        headers,
        fields.get("csrfmiddlewaretoken").map_or("", String::as_str),
    )
}

#[derive(Serialize)]
struct Named {
    name: String,
}

#[derive(Serialize)]
struct Judge {
    id: i32,
    name: String,
}

#[derive(Serialize)]
struct Pairing {
    round_number: i32,
    opponent: Opponent,
    side: &'static str,
    room: Option<Named>,
    chair: Option<Judge>,
    judges: Vec<Judge>,
}

#[derive(Serialize)]
struct Opponent {
    name: String,
    debaters: Vec<Named>,
}

async fn recent(
    db: &mut MySqlConnection,
    team: i32,
    through: i32,
) -> Result<Vec<Pairing>, sqlx::Error> {
    if through < 1 {
        return Ok(Vec::new());
    }
    let mut panels = HashMap::<_, Vec<_>>::new();
    for (round_id, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
        "SELECT r.id, j.id, j.name FROM tab_round r JOIN tab_round_judges x ON x.round_id = r.id
         JOIN tab_judge j ON j.id = x.judge_id WHERE r.round_number <= ?
         AND ? IN (r.gov_team_id, r.opp_team_id)
         ORDER BY j.name, j.id",
    )
    .bind(through)
    .bind(team)
    .fetch_all(&mut *db)
    .await?
    {
        panels.entry(round_id).or_default().push(Judge { id, name });
    }
    let mut debaters = HashMap::<_, Vec<_>>::new();
    for (round_id, name) in sqlx::query_as::<_, (i32, String)>(
        "SELECT r.id, d.name FROM tab_round r
         JOIN tab_team_debaters x ON x.team_id = IF(r.gov_team_id = ?, r.opp_team_id, r.gov_team_id)
         JOIN tab_debater d ON d.id = x.debater_id
         WHERE r.round_number <= ? AND ? IN (r.gov_team_id, r.opp_team_id)
         ORDER BY d.name, d.id",
    )
    .bind(team)
    .bind(through)
    .bind(team)
    .fetch_all(&mut *db)
    .await?
    {
        debaters.entry(round_id).or_default().push(Named { name });
    }
    type Row = (
        i32,
        i32,
        String,
        bool,
        Option<String>,
        Option<i32>,
        Option<String>,
    );
    Ok(sqlx::query_as::<_, Row>(
        "SELECT r.id, r.round_number, o.name, r.gov_team_id = ?, m.name, j.id, j.name
         FROM tab_round r JOIN tab_team o ON o.id = IF(r.gov_team_id = ?, r.opp_team_id, r.gov_team_id)
         LEFT JOIN tab_room m ON m.id = r.room_id LEFT JOIN tab_judge j ON j.id = r.chair_id
         WHERE r.round_number <= ? AND ? IN (r.gov_team_id, r.opp_team_id)
         ORDER BY r.round_number, r.id",
    ).bind(team).bind(team).bind(through).bind(team).fetch_all(db).await?.into_iter().map(
        |(id, round_number, name, govt, room, chair_id, chair)| Pairing {
            round_number, opponent: Opponent { name, debaters: debaters.remove(&id).unwrap_or_default() },
            side: if govt { "Gov" } else { "Opp" },
            room: room.map(|name| Named { name }),
            chair: chair_id.zip(chair).map(|(id, name)| Judge { id, name }),
            judges: panels.remove(&id).unwrap_or_default(),
        }
    ).collect())
}

pub async fn page(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path(code): Path<String>,
) -> Response {
    view(&app, actor, &headers, uri.path(), &code, None).await
}

enum Feedback {
    Name(String),
    Scratch(ScratchSelection),
    Closed,
    Unknown,
}

struct ScratchSelection {
    wanted: BTreeSet<i32>,
    errors: Vec<Option<String>>,
    repeated: bool,
}

impl ScratchSelection {
    fn valid(&self) -> bool {
        !self.repeated && self.errors.iter().all(Option::is_none)
    }
}

fn scratch_selection(quantity: u16, options: &Choices, fields: &Submitted) -> ScratchSelection {
    let allowed: BTreeSet<_> = options.iter().map(|(id, _)| id.as_str()).collect();
    let mut wanted = BTreeSet::new();
    let mut repeated = false;
    let errors = (0..quantity)
        .map(|index| {
            let raw = fields
                .get(&format!("scratch_{index}"))
                .map_or("", String::as_str);
            if raw.is_empty() {
                return None;
            }
            if !allowed.contains(raw) {
                return Some(format!(
                    "Select a valid choice. {raw} is not one of the available choices."
                ));
            }
            let id = raw.parse().expect("judge option ID");
            repeated |= !wanted.insert(id);
            None
        })
        .collect();
    ScratchSelection {
        wanted,
        errors,
        repeated,
    }
}

async fn scratch_options(db: &mut MySqlConnection, team: i32) -> Result<Choices, sqlx::Error> {
    let options = sqlx::query_as::<_, (i32, String)>(
        "SELECT j.id, j.name FROM tab_judge j WHERE NOT EXISTS(
            SELECT 1 FROM tab_scratch s WHERE s.judge_id = j.id AND s.team_id = ? AND s.scratch_type = 1
         ) ORDER BY j.name, j.id",
    ).bind(team).fetch_all(db).await?;
    Ok(std::iter::once((String::new(), "No scratch".into()))
        .chain(options.into_iter().map(|(id, name)| (id.to_string(), name)))
        .collect())
}

fn name_form(team: &Team, posted: Option<(&Submitted, &Feedback)>) -> Value {
    let entered = posted.filter(|(fields, _)| {
        fields
            .get("action")
            .is_some_and(|action| action == "update_name")
    });
    let name = entered.map_or(team.name.as_str(), |(fields, _)| {
        fields.get("name").map_or("", String::as_str)
    });
    let errors = entered.and_then(|(_, feedback)| match feedback {
        Feedback::Name(error) => Some(error.as_str()),
        _ => None,
    });
    let widget = staff_roster_fields::input(
        "name",
        "Team name",
        "text",
        name,
        r#" maxlength="35""#,
        true,
        None,
    );
    context! {
        name => context! {
            id_for_label => "id_name",
            input => widget.get_attr("input").unwrap_or_default(),
            errors => errors.into_iter().collect::<Vec<_>>(),
        },
    }
}

fn scratch_form(
    quantity: u16,
    options: &Choices,
    current: &[Judge],
    posted: Option<(&Submitted, &Feedback)>,
) -> Value {
    let entered = posted.filter(|(fields, _)| {
        fields
            .get("action")
            .is_some_and(|action| action == "update_scratches")
    });
    let invalid = entered.and_then(|(_, feedback)| match feedback {
        Feedback::Scratch(selection) => Some(selection),
        _ => None,
    });
    let fields: Vec<_> = (0..usize::from(quantity))
        .map(|index| {
            let field = format!("scratch_{index}");
            let selected = if let Some((fields, _)) = entered {
                fields.get(&field).cloned().unwrap_or_default()
            } else {
                current
                    .get(index)
                    .map(|judge| judge.id.to_string())
                    .unwrap_or_default()
            };
            let widget =
                staff_roster_fields::select(&field, "", options, &selected, false, false, None);
            let error = invalid.and_then(|selection| selection.errors.get(index)?.as_deref());
            context! {
                id_for_label => format!("id_{field}"),
                label => format!("Scratch {}", index + 1),
                input => widget.get_attr("input").unwrap_or_default(),
                errors => error.into_iter().collect::<Vec<_>>(),
            }
        })
        .collect();
    let non_field_errors: Vec<_> = invalid
        .is_some_and(|selection| selection.repeated)
        .then_some("Select each judge at most once.")
        .into_iter()
        .collect();
    context! { fields, non_field_errors, errors => invalid.is_some() }
}

async fn view(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    path: &str,
    code: &str,
    posted: Option<(&Submitted, &Feedback)>,
) -> Response {
    let result = async {
        let mut db = app.db.begin().await?;
        let actor = authorized_actor(&mut db, app, actor, headers).await?;
        let shell = legacy_html::shell(&mut db, actor, path).await?;
        let team = if valid_code(code) { find(&mut db, code, false).await? } else { None };
        let Some(team) = team else {
            return Ok(legacy_html::render("common/404.html", shell, context! {}, StatusCode::NOT_FOUND));
        };
        let settings = Settings::load(&mut db, SETTINGS).await?;
        let config = config(&mut db).await?;
        let cur = settings.integer("cur_round", 1);
        let locked = cur > 1;
        let judges_public = settings.flag("judges_public", false);
        let disc_open = config.scratches && judges_public;
        let people: Vec<_> = sqlx::query_as::<_, (String, Option<String>, i32)>(
            "SELECT d.name, s.name, d.novice_status FROM tab_team_debaters x
             JOIN tab_debater d ON d.id = x.debater_id LEFT JOIN tab_school s ON s.id = d.school_id
             WHERE x.team_id = ? ORDER BY d.name, d.id",
        ).bind(team.id).fetch_all(&mut *db).await?.into_iter().map(
            |(name, school, status)| context! {
                name,
                school => school.map(|name| Named { name }),
                get_novice_status_display => match status {
                    0 => "Varsity".into(),
                    1 => "Novice".into(),
                    _ => status.to_string(),
                },
            }
        ).collect();
        let current: Vec<_> = if judges_public {
            sqlx::query_as::<_, (i32, String)>(
                "SELECT j.id, j.name FROM tab_scratch x JOIN tab_judge j ON j.id = x.judge_id
                 WHERE x.team_id = ? AND x.scratch_type = 0 ORDER BY j.name, j.id",
            ).bind(team.id).fetch_all(&mut *db).await?.into_iter()
                .map(|(id, name)| Judge { id, name }).collect()
        } else { Vec::new() };
        let scratch_form = if disc_open && config.slots > 0 && !locked {
            let options = scratch_options(&mut db, team.id).await?;
            Some(scratch_form(config.slots, &options, &current, posted))
        } else { None };
        let history = recent(&mut db, team.id, cur.saturating_sub(2)).await?;
        db.commit().await?;
        let messages = posted.filter(|(_, feedback)| matches!(feedback, Feedback::Unknown))
            .map(|_| [context! { text => "Unknown team portal action.", tags => "error", level => 40 }]);
        Ok(render(app, headers, path, "registration/team_portal.html", shell, context! {
            team => context! {
                name => &team.name,
                school => context! { name => &team.school },
                get_seed_display => seed_name(team.seed),
                debaters => people,
            },
            config => context! {
                team_name_changes_allowed => config.name,
                disc_scratch_quantity => config.slots,
            },
            name_form => name_form(&team, posted),
            scratch_form,
            disc_scratches_open => disc_open,
            judge_list_is_public => judges_public,
            scratches => current.into_iter().map(|judge| context! { judge }).collect::<Vec<_>>(),
            edits_locked_by_pairings => locked,
            team_round_history => history,
            messages,
        }))
    }.await;
    portal_result(app, headers, result).await
}

enum UpdateError {
    Storage,
    NotFound,
    Form(Feedback),
    Board,
    Session(SessionError),
    Denied,
}

impl From<sqlx::Error> for UpdateError {
    fn from(_: sqlx::Error) -> Self {
        Self::Storage
    }
}

async fn authorized_actor(
    db: &mut MySqlConnection,
    app: &App,
    original: Actor,
    headers: &HeaderMap,
) -> Result<Actor, UpdateError> {
    let Some(key) = legacy_session::cookie(headers, "sessionid") else {
        return Ok(Actor::Anonymous);
    };
    let current = legacy_session::actor_on(db, key, app.django_secret.as_deref())
        .await
        .map_err(UpdateError::Session)?;
    match (original, current) {
        (_, Actor::User { board: true, .. }) => Err(UpdateError::Board),
        (Actor::Anonymous, _) => Ok(current),
        (Actor::User { id, .. }, Actor::User { id: current_id, .. }) if id == current_id => {
            Ok(current)
        }
        _ => Err(UpdateError::Denied),
    }
}

async fn portal_result(
    app: &App,
    headers: &HeaderMap,
    result: Result<Response, UpdateError>,
) -> Response {
    match result {
        Ok(response) => response,
        Err(UpdateError::Board) => access::board_restricted(app, headers).await,
        Err(UpdateError::Denied) => access::access_denied(),
        Err(UpdateError::Session(error)) => error.response(),
        Err(_) => unavailable(),
    }
}

pub async fn update(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path(code): Path<String>,
    Form(fields): Form<Submitted>,
) -> Response {
    if !valid_csrf(&headers, &fields) {
        return login::csrf_error();
    }
    if !valid_code(&code) {
        return view(&app, actor, &headers, uri.path(), &code, None).await;
    }
    match save(&app, actor, &headers, &code, &fields).await {
        Ok((code, message)) => {
            staff_roster_notice::team_redirect(&app, &headers, &target(&code), message, false)
        }
        Err(UpdateError::NotFound) => view(&app, actor, &headers, uri.path(), &code, None).await,
        Err(UpdateError::Form(feedback)) => {
            view(
                &app,
                actor,
                &headers,
                uri.path(),
                &code,
                Some((&fields, &feedback)),
            )
            .await
        }
        Err(error) => portal_result(&app, &headers, Err(error)).await,
    }
}

async fn save(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    code: &str,
    fields: &Submitted,
) -> Result<(String, &'static str), UpdateError> {
    let mut db = app.db.begin().await?;
    lock_round_if_configured(&mut db).await?;
    sqlx::query("SELECT id FROM registration_registrationconfig WHERE id = 1 FOR UPDATE")
        .fetch_optional(&mut *db)
        .await?;
    let team = find(&mut db, code, true).await?;
    authorized_actor(&mut db, app, actor, headers).await?;
    let team = team.ok_or(UpdateError::NotFound)?;
    let settings = Settings::load(&mut db, SETTINGS).await?;
    let locked = settings.integer("cur_round", 1) > 1;
    let config = config(&mut db).await?;
    let message = match fields.get("action").map(String::as_str) {
        Some("update_name") => {
            if locked || !config.name {
                return Err(UpdateError::Form(Feedback::Closed));
            }
            save_name(
                &mut db,
                &team,
                fields.get("name").map_or("", String::as_str),
            )
            .await?;
            "Team name updated."
        }
        Some("update_scratches") => {
            if locked
                || !config.scratches
                || config.slots == 0
                || !settings.flag("judges_public", false)
            {
                return Err(UpdateError::Form(Feedback::Closed));
            }
            let options = scratch_options(&mut db, team.id).await?;
            let selection = scratch_selection(config.slots, &options, fields);
            if !selection.valid() {
                return Err(UpdateError::Form(Feedback::Scratch(selection)));
            }
            replace_scratches(&mut db, team.id, selection.wanted).await?;
            "Disc scratches updated."
        }
        _ => return Err(UpdateError::Form(Feedback::Unknown)),
    };
    db.commit().await?;
    Ok((team.code, message))
}

async fn save_name(
    db: &mut MySqlConnection,
    team: &Team,
    entered: &str,
) -> Result<(), UpdateError> {
    let name = entered.trim();
    let invalid = |error| UpdateError::Form(Feedback::Name(error));
    let length = name.chars().count();
    if name.is_empty() {
        return Err(invalid("This field is required.".into()));
    }
    if length > 35 {
        return Err(invalid(format!(
            "Ensure this value has at most 35 characters (it has {length})."
        )));
    }
    if name.contains('\0') {
        return Err(invalid("Null characters are not allowed.".into()));
    }
    let duplicate = "A team with this name already exists.";
    let exists = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM tab_team WHERE name = ? AND id <> ?)",
    )
    .bind(name)
    .bind(team.id)
    .fetch_one(&mut *db)
    .await?;
    if exists {
        return Err(invalid(duplicate.into()));
    }
    match sqlx::query("UPDATE tab_team SET name = ? WHERE id = ?")
        .bind(name)
        .bind(team.id)
        .execute(db)
        .await
    {
        Err(sqlx::Error::Database(error)) if error.is_unique_violation() => {
            Err(invalid(duplicate.into()))
        }
        Err(error) => Err(error.into()),
        Ok(_) => Ok(()),
    }
}

async fn replace_scratches(
    db: &mut MySqlConnection,
    id: i32,
    wanted: BTreeSet<i32>,
) -> Result<(), UpdateError> {
    let existing: BTreeSet<_> = sqlx::query_scalar::<_, i32>(
        "SELECT judge_id FROM tab_scratch WHERE team_id = ? AND scratch_type = 0 FOR UPDATE",
    )
    .bind(id)
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    for judge in existing.difference(&wanted) {
        sqlx::query(
            "DELETE FROM tab_scratch WHERE team_id = ? AND judge_id = ? AND scratch_type = 0",
        )
        .bind(id)
        .bind(judge)
        .execute(&mut *db)
        .await?;
    }
    for judge in wanted.difference(&existing) {
        sqlx::query("INSERT INTO tab_scratch (team_id, judge_id, scratch_type, created_at) VALUES (?, ?, 0, UTC_TIMESTAMP(6))")
            .bind(id).bind(judge).execute(&mut *db).await?;
    }
    Ok(())
}
