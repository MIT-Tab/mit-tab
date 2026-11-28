use std::collections::{BTreeMap, BTreeSet};

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::{Value, context};
use sqlx::MySqlConnection;

use crate::{
    App,
    admin_html::django_datetime,
    board_snapshot::Identity,
    legacy_html,
    legacy_session::Actor,
    login::TemplateCsrf,
    public_rankings::policy,
    registration_form::{Fields, integer_input},
    settings::Settings,
    staff_roster_data::{self as roster, Error, Kind, TEAM_CHECKIN_CONFLICT, TeamRow},
    staff_roster_fields::{
        Choices, INVALID_CHOICE, MEDIA, REQUIRED, audit_history, checkbox, checkbox_value, input,
        multi_error, multi_select, name_error, options, reference_options, select,
    },
    staff_roster_notice,
    staff_scratches::MAX_BATCH,
    tournament_data::TournamentData,
    unavailable,
};

struct Options {
    schools: Choices,
    debaters: Choices,
    registrations: Choices,
    tags: Choices,
    seeds: Choices,
    divisions: Choices,
}

impl Options {
    async fn load(db: &mut MySqlConnection) -> Result<Self, sqlx::Error> {
        Ok(Self {
            schools: options(db, "SELECT id, name FROM tab_school ORDER BY name").await?,
            debaters: options(db, "SELECT id, name FROM tab_debater ORDER BY name").await?,
            registrations: options(
                db,
                "SELECT r.id, CONCAT(s.name, ' — ', r.email) FROM registration_registration r
                 JOIN tab_school s ON s.id = r.school_id ORDER BY r.id",
            )
            .await?,
            tags: options(db, "SELECT id, tag FROM tab_roomtag ORDER BY id").await?,
            seeds: choices(["Unseeded", "Free Seed", "Half Seed", "Full Seed"]),
            divisions: choices(["Varsity", "Novice"]),
        })
    }
}

fn choices(labels: impl IntoIterator<Item = &'static str>) -> Choices {
    labels
        .into_iter()
        .enumerate()
        .map(|(index, label)| (index.to_string(), label.into()))
        .collect()
}

const ALREADY_ASSIGNED: &str = concat!(
    "A debater cannot already be on a different team!\n",
    "                    Consider editing the debaters' existing team,\n",
    "                     or removing them from it before creating this one.",
);

fn scratch_count(value: &str) -> Result<usize, &'static str> {
    if value.is_empty() {
        return Err(REQUIRED);
    }
    let count = integer_input(value)
        .map_err(|_| "Enter a whole number.")?
        .max(0) as usize;
    if count > MAX_BATCH {
        return Err("Enter no more than 50 initial scratches.");
    }
    Ok(count)
}

pub fn validate_post(form: &Fields, initial_scratches: bool) -> Result<usize, Error> {
    for (name, values) in [
        ("seed", &["0", "1", "2", "3"][..]),
        ("break_preference", &["0", "1"]),
    ] {
        let selected = form.all(name).last().map_or("", String::as_str);
        if !values.contains(&selected) {
            return Err(Error::Invalid(
                "Choose valid seeding and break preferences.",
            ));
        }
    }
    for name in ["school", "hybrid_school", "registration"] {
        let value = form.get(name);
        if !value.is_empty() && value.parse::<i32>().is_err() {
            return Err(Error::Invalid(INVALID_CHOICE));
        }
    }
    for name in ["debaters", "required_room_tags"] {
        if form
            .all(name)
            .iter()
            .any(|value| value.trim().parse::<i32>().is_err())
        {
            return Err(Error::Invalid(INVALID_CHOICE));
        }
    }
    if initial_scratches {
        scratch_count(form.get("number_scratches")).map_err(Error::Invalid)
    } else {
        Ok(0)
    }
}

fn submitted_checkin(fields: &Fields, creating: bool) -> Option<bool> {
    let checked = checkbox_value(fields, "checked_in");
    let changed = roster::team_checkin_initial(fields).is_some_and(|initial| initial != checked);
    (fields.flag("mittab_checkins_present") && (creating || changed)).then_some(checked)
}

async fn identities(
    db: &mut MySqlConnection,
    team: i32,
    query: &'static str,
) -> Result<Vec<Identity>, sqlx::Error> {
    Ok(sqlx::query_as::<_, (i32, String)>(query)
        .bind(team)
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(id, name)| Identity { id, name })
        .collect())
}

async fn field_errors(
    db: &mut MySqlConnection,
    id: Option<i32>,
    fields: &Fields,
    choices: &Options,
    initial_scratches: bool,
    message: &str,
) -> Result<BTreeMap<&'static str, String>, Error> {
    let raw = |name| fields.all(name).last().map_or("", String::as_str);
    let mut errors = BTreeMap::new();
    let mut add = |name, error| {
        if let Some(error) = error {
            errors.insert(name, error);
        }
    };
    add(
        "name",
        name_error(db, Kind::Team, id, raw("name"), message).await?,
    );
    for (name, options, required) in [
        ("school", &choices.schools, true),
        ("hybrid_school", &choices.schools, false),
        ("registration", &choices.registrations, false),
    ] {
        add(name, reference_options(options, raw(name), required));
    }
    for (name, options) in [
        ("seed", &choices.seeds),
        ("break_preference", &choices.divisions),
    ] {
        let value = raw(name);
        add(
            name,
            if value.is_empty() {
                Some(REQUIRED.into())
            } else {
                (!options.iter().any(|(key, _)| key == value)).then(|| {
                    format!("Select a valid choice. {value} is not one of the available choices.")
                })
            },
        );
    }
    let submitted_members = fields.all("debaters");
    let error = if let Some(error) = multi_error(&choices.debaters, submitted_members) {
        Some(error)
    } else {
        let ids = roster::ids(fields, "debaters")?;
        if id.is_some() && !(1..=2).contains(&ids.len()) {
            Some("You must select 1 or 2 debaters!".into())
        } else {
            roster::team_members_conflict(db, id, &ids)
                .await?
                .then(|| ALREADY_ASSIGNED.into())
        }
    };
    add("debaters", error);
    add(
        "required_room_tags",
        multi_error(&choices.tags, fields.all("required_room_tags")),
    );
    if initial_scratches {
        add(
            "number_scratches",
            scratch_count(fields.get("number_scratches"))
                .err()
                .map(str::to_owned),
        );
    }
    Ok(errors)
}

fn initial(team: Option<&TeamRow>, members: &[Identity], tags: &BTreeSet<i32>) -> Fields {
    let mut fields = Fields::default();
    if let Some(team) = team {
        fields.push("name", &team.name);
        fields.push("school", team.school);
        for (name, value) in [
            ("hybrid_school", team.hybrid),
            ("registration", team.registration),
        ] {
            if let Some(value) = value {
                fields.push(name, value);
            }
        }
        fields.push("seed", team.seed);
        fields.push("break_preference", team.division);
        if team.ranking_public {
            fields.push("ranking_public", "on");
        }
        for member in members {
            fields.push("debaters", member.id);
        }
        for tag in tags {
            fields.push("required_room_tags", tag);
        }
    } else {
        fields.push("break_preference", 0);
        fields.push("ranking_public", "on");
        fields.push("number_scratches", 0);
    }
    fields
}

fn widgets(
    fields: &Fields,
    choices: &Options,
    mut errors: BTreeMap<&'static str, String>,
    checked_in: Option<bool>,
    initial_scratches: bool,
) -> Vec<Value> {
    let value = |name| fields.all(name).last().map_or("", String::as_str);
    let mut output = vec![
        input(
            "name",
            "Name:",
            "text",
            value("name"),
            r#" maxlength="35""#,
            true,
            errors.remove("name"),
        ),
        select(
            "school",
            "School:",
            &choices.schools,
            value("school"),
            true,
            true,
            errors.remove("school"),
        ),
        select(
            "hybrid_school",
            "Hybrid school:",
            &choices.schools,
            value("hybrid_school"),
            false,
            true,
            errors.remove("hybrid_school"),
        ),
        multi_select(
            "debaters",
            "Debaters:",
            &choices.debaters,
            fields.all("debaters"),
            errors.remove("debaters"),
        ),
        select(
            "registration",
            "Registration:",
            &choices.registrations,
            value("registration"),
            false,
            true,
            errors.remove("registration"),
        ),
        select(
            "seed",
            "Seed:",
            &choices.seeds,
            value("seed"),
            true,
            true,
            errors.remove("seed"),
        ),
    ];
    if let Some(checked_in) = checked_in {
        output.push(checkbox("checked_in", "Checked in:", checked_in));
    }
    output.extend([
        multi_select(
            "required_room_tags",
            "Required room tags:",
            &choices.tags,
            fields.all("required_room_tags"),
            errors.remove("required_room_tags"),
        ),
        select(
            "break_preference",
            "Break preference:",
            &choices.divisions,
            value("break_preference"),
            false,
            false,
            errors.remove("break_preference"),
        ),
        checkbox(
            "ranking_public",
            "Ranking public:",
            checkbox_value(fields, "ranking_public"),
        ),
    ]);
    if initial_scratches {
        output.push(input(
            "number_scratches",
            "How many initial scratches?",
            "number",
            value("number_scratches"),
            "",
            true,
            errors.remove("number_scratches"),
        ));
    }
    output
}

pub async fn show(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    id: Option<i32>,
    return_path: &str,
    posted: Option<(&Fields, &str)>,
) -> Result<Response, Error> {
    let Ok(csrf) = TemplateCsrf::new(headers) else {
        return Ok(unavailable());
    };
    let path = id.map_or_else(|| "/enter_team/".into(), |id| format!("/team/{id}/"));
    let mut db = app.db.begin().await?;
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    let capability = |name| shell.get_attr(name).is_ok_and(|value| value.is_true());
    let checkins = capability("can_checkins");
    let initial_scratches =
        id.is_none() && (capability("can_add_scratches") || capability("can_view_scratches"));
    let team = if let Some(id) = id {
        Some(TeamRow::load(&mut db, id, checkins).await?)
    } else {
        None
    };
    let saved_checkin = team.as_ref().is_none_or(|team| team.checked_in);
    let checked_in = checkins.then(|| {
        posted
            .and_then(|(fields, _)| submitted_checkin(fields, id.is_none()))
            .unwrap_or(saved_checkin)
    });
    let checkin_initial =
        (checkins && id.is_some()).then_some(if saved_checkin { "1" } else { "0" });
    let choices = Options::load(&mut db).await?;
    let mut team_obj = None;
    let mut team_stats = None;
    let mut audit_events = Vec::new();
    let mut links = Vec::new();
    let title;
    let mut errors = BTreeMap::new();
    let initial_fields;
    let fields = if let Some((fields, error)) = posted {
        title = if id.is_none() {
            "Create Team".into()
        } else {
            String::new()
        };
        errors = field_errors(&mut db, id, fields, &choices, initial_scratches, error).await?;
        fields
    } else if let Some((id, team)) = id.zip(team) {
        title = format!("Viewing Team: {}", team.name);
        if capability("can_data_entry") {
            links.push((format!("/team/{id}/delete/"), "Delete".into()));
        }
        if capability("can_view_scratches") {
            links.push((
                format!("/team/{id}/scratches/view/"),
                format!("Scratches for {}", team.name),
            ));
        }
        let members: Vec<_> = roster::team_members(&mut db, id)
            .await?
            .into_iter()
            .map(|(id, name)| Identity { id, name })
            .collect();
        let mut ranking_groups = Vec::new();
        if matches!(
            actor,
            Actor::User {
                superuser: true,
                board: false,
                ..
            }
        ) {
            let settings = Settings::load(&mut db, &["cur_round", "lenient_late"]).await?;
            team_stats = TournamentData::load(&mut db)
                .await?
                .team_details(i64::from(id), policy(&settings));
            ranking_groups = identities(&mut db, id,
                "SELECT g.id, g.name FROM tab_rankinggroup g
                 JOIN tab_rankinggroup_teams m ON m.rankinggroup_id = g.id WHERE m.team_id = ? ORDER BY g.name",
            ).await?;
        }
        let school = |id: i32| {
            let key = id.to_string();
            choices
                .schools
                .iter()
                .find(|(value, _)| *value == key)
                .map(|(_, name)| Identity {
                    id,
                    name: name.clone(),
                })
        };
        audit_events = audit_history(&mut db, Kind::Team, id).await?;
        initial_fields = initial(
            Some(&team),
            &members,
            &roster::team_tags(&mut db, id).await?,
        );
        team_obj = Some(context! {
            id, debaters => members, school => school(team.school), hybrid_school => team.hybrid.and_then(school),
            ranking_groups, created_by_display => team.creator, created_at => team.created.map(django_datetime),
        });
        &initial_fields
    } else {
        title = "Create Team".into();
        initial_fields = initial(None, &[], &BTreeSet::new());
        &initial_fields
    };
    let visible_fields = widgets(fields, &choices, errors, checked_in, initial_scratches);
    let conflict = posted.is_some_and(|(_, error)| error == TEAM_CHECKIN_CONFLICT);
    let non_field_errors = conflict.then(|| {
        Value::from_safe_string(format!(
            r#"<ul class="errorlist nonfield"><li>{TEAM_CHECKIN_CONFLICT}</li></ul>"#
        ))
    });
    db.commit().await?;
    let template = if team_obj.is_some() {
        "tab/team_detail.html"
    } else {
        "common/data_entry.html"
    };
    let mut response = staff_roster_notice::render(
        app,
        headers,
        &path,
        template,
        shell,
        context! {
            title, csrf_input => csrf.input(), return_path, links, team_obj, team_stats, audit_events,
            team_checkins => checkins, team_checkin_initial => checkin_initial,
            form => context! { visible_fields, non_field_errors, media => Value::from_safe_string(MEDIA.into()) },
        },
        if conflict {
            StatusCode::CONFLICT
        } else {
            StatusCode::OK
        },
    );
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}
