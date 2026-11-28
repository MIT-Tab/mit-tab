use std::collections::BTreeSet;

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::{Value, context};
use ring::hmac;
use sqlx::MySqlConnection;

use crate::{
    App,
    admin_html::django_datetime,
    board_snapshot::Identity,
    legacy_html,
    legacy_session::{Actor, cookie},
    login::TemplateCsrf,
    registration_form::Fields,
    staff_room::checkin_fields,
    staff_roster_data::{
        self as roster, Error, JUDGE_CHECKIN_CONFLICT, JUDGE_FLAGS, JudgeRow, Kind,
    },
    staff_roster_fields::{
        MEDIA, WING_CONFLICT, audit_history, checkbox, checkbox_value, checkbox_with_help,
        email_errors, input, judge_code_errors, judge_experience, multi_error, multi_select,
        name_error, options, rank_input, reference_options, select,
    },
    staff_roster_notice, unavailable,
};

const EDIT_CONFLICT: &str = "Judge details changed. Review the current values before saving again.";

fn revision_data(
    headers: &HeaderMap,
    id: i32,
    judge: &JudgeRow,
    schools: &BTreeSet<i32>,
    tags: &BTreeSet<i32>,
) -> Option<Vec<u8>> {
    let session = cookie(headers, "sessionid")?;
    let mut contents = format!("mittab judge edit v1\0{session}\0{id}\0").into_bytes();
    contents.extend(
        serde_json::to_vec(&(
            &judge.name,
            &judge.rank,
            judge.experience,
            judge.flags,
            &judge.email,
            judge.registration,
            &judge.code,
            schools,
            tags,
        ))
        .ok()?,
    );
    Some(contents)
}

pub async fn verify_edit(
    app: &App,
    db: &mut MySqlConnection,
    headers: &HeaderMap,
    id: i32,
    fields: &Fields,
) -> Result<(), Error> {
    let posted = fields.get("mittab_judge_edit_revision");
    if posted.len() != 64 {
        return Err(Error::Invalid(EDIT_CONFLICT));
    }
    let judge = JudgeRow::load(db, id, true).await?;
    let (schools, tags) = roster::judge_links(db, id, true).await?;
    let verified = hex::decode(posted)
        .ok()
        .zip(revision_data(headers, id, &judge, &schools, &tags))
        .is_some_and(|(proof, data)| hmac::verify(&app.admin_notice_key, &data, &proof).is_ok());
    if verified {
        Ok(())
    } else {
        Err(Error::Invalid(EDIT_CONFLICT))
    }
}

fn initial(judge: &JudgeRow, schools: BTreeSet<i32>, tags: BTreeSet<i32>) -> Fields {
    let mut fields = Fields::default();
    fields.push("name", &judge.name);
    fields.push("rank", &judge.rank);
    fields.push("email", judge.email.as_deref().unwrap_or_default());
    fields.push("ballot_code", judge.code.as_deref().unwrap_or_default());
    if let Some(years) = judge.experience {
        fields.push("reported_experience", years);
    }
    if let Some(registration) = judge.registration {
        fields.push("registration", registration);
    }
    for (name, checked) in JUDGE_FLAGS.into_iter().zip(judge.flags) {
        if checked {
            fields.push(name, "on");
        }
    }
    for school in schools {
        fields.push("schools", school);
    }
    for tag in tags {
        fields.push("required_room_tags", tag);
    }
    fields
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
    let path = id.map_or_else(|| "/enter_judge/".into(), |id| format!("/judge/{id}/"));
    let mut db = app.db.begin().await?;
    let shell = legacy_html::shell(&mut db, actor, &path).await?;
    let capability = |name| shell.get_attr(name).is_ok_and(|value| value.is_true());
    let mut title = "Create Judge".into();
    let mut links = Vec::new();
    let mut judge_rounds = Vec::new();
    let mut audit_events = Vec::new();
    let mut judge_obj = None;
    let mut edit_conflict = posted.is_some_and(|(_, message)| message == EDIT_CONFLICT);
    let mut judge_edit_revision = None;
    let mut initial_fields = Fields::default();
    if let Some(id) = id {
        let judge = JudgeRow::load(&mut db, id, false).await?;
        let (schools, tags) = roster::judge_links(&mut db, id, false).await?;
        judge_edit_revision = revision_data(headers, id, &judge, &schools, &tags)
            .map(|data| hex::encode(hmac::sign(&app.admin_notice_key, &data).as_ref()));
        // An intervening edit after validation failed also invalidates the bound form.
        edit_conflict |= posted.is_some_and(|(fields, _)| {
            Some(fields.get("mittab_judge_edit_revision")) != judge_edit_revision.as_deref()
        });
        let attempted_name = posted
            .filter(|_| !edit_conflict)
            .map(|(fields, _)| fields.get("name"))
            .filter(|name| !name.is_empty() && name.chars().count() <= 30 && !name.contains('\0'));
        let display_name = attempted_name.unwrap_or(&judge.name);
        title = format!("Viewing Judge: {display_name}");
        if capability("can_data_entry") {
            links.push((format!("/judge/{id}/delete/"), "Delete".into()));
        }
        if capability("can_view_scratches") {
            links.push((
                format!("/judge/{id}/scratches/view/"),
                format!("Scratches for {display_name}"),
            ));
        }
        if posted.is_none()
            && matches!(
                actor,
                Actor::User {
                    superuser: true,
                    board: false,
                    ..
                }
            )
        {
            judge_rounds = rounds(&mut db, id).await?;
        }
        audit_events = audit_history(&mut db, Kind::Judge, id).await?;
        judge_obj = Some(context! {
            created_by_display => &judge.creator, created_at => judge.created.map(django_datetime),
        });
        if posted.is_none() || edit_conflict {
            initial_fields = initial(&judge, schools, tags);
        }
    }
    let bound = posted.filter(|_| !edit_conflict);
    let fields = bound.map_or(&initial_fields, |(fields, _)| fields);
    let mut visible_fields = widgets(&mut db, id, fields, bound.map(|(_, error)| error)).await?;
    let judge_checkin_revision = if let Some(id) = id.filter(|_| capability("can_checkins")) {
        let snapshot = roster::checkin_snapshot(&mut db, Kind::Judge, id, false).await?;
        let submitted = bound.map(|(fields, _)| fields).filter(|fields| {
            fields.flag("mittab_checkins_present")
                && fields.get("mittab_judge_checkin_revision") == snapshot.revision
        });
        visible_fields.extend(checkin_fields(&snapshot, submitted, checkbox_value));
        Some(snapshot.revision)
    } else {
        None
    };
    let checkin_conflict = posted.is_some_and(|(_, error)| error == JUDGE_CHECKIN_CONFLICT);
    let message = if edit_conflict {
        Some(EDIT_CONFLICT)
    } else if checkin_conflict {
        Some(JUDGE_CHECKIN_CONFLICT)
    } else if posted.is_some()
        && checkbox_value(fields, "request_wing")
        && checkbox_value(fields, "no_wings")
    {
        Some(WING_CONFLICT)
    } else {
        None
    };
    let non_field_errors = message.map(|message| {
        Value::from_safe_string(format!(
            r#"<ul class="errorlist nonfield"><li>{message}</li></ul>"#
        ))
    });
    db.commit().await?;
    let template = if id.is_some() {
        "tab/judge_detail.html"
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
            title, csrf_input => csrf.input(), return_path, links, judge_obj, judge_rounds, audit_events,
            judge_checkin_revision, judge_edit_revision,
            form => context! { visible_fields, non_field_errors, media => Value::from_safe_string(MEDIA.into()) },
        },
        if edit_conflict || checkin_conflict {
            StatusCode::CONFLICT
        } else {
            StatusCode::OK
        },
    );
    csrf.set_cookie(&mut response, headers);
    Ok(response)
}

async fn widgets(
    db: &mut MySqlConnection,
    id: Option<i32>,
    fields: &Fields,
    message: Option<&str>,
) -> Result<Vec<Value>, sqlx::Error> {
    let schools = options(db, "SELECT id, name FROM tab_school ORDER BY name").await?;
    let registrations = options(
        db,
        "SELECT r.id, CONCAT(s.name, ' — ', r.email) FROM registration_registration r
         JOIN tab_school s ON s.id = r.school_id ORDER BY r.id",
    )
    .await?;
    let tags = options(db, "SELECT id, tag FROM tab_roomtag ORDER BY id").await?;
    let value = |name| fields.all(name).last().map_or("", String::as_str);
    let flag = |name| checkbox_value(fields, name);
    let mut code_errors = Vec::new();
    let invalid_name = if let Some(message) = message {
        let code = fields.get("ballot_code");
        code_errors = judge_code_errors(code);
        if code_errors.is_empty() && !code.is_empty() {
            let duplicate = sqlx::query_scalar::<_, bool>(
                "SELECT EXISTS(SELECT 1 FROM tab_judge WHERE ballot_code = ? AND id <> COALESCE(?, 0))",
            ).bind(code).bind(id).fetch_one(&mut *db).await?;
            if duplicate {
                code_errors.push("Judge with this Ballot code already exists.".into());
            }
        }
        let message = if message == "That name or code already exists." {
            "Enter a valid name."
        } else {
            message
        };
        name_error(db, Kind::Judge, id, value("name"), message).await?
    } else {
        None
    };
    let bound = message.is_some();
    Ok(vec![
        input(
            "name",
            "Name:",
            "text",
            value("name"),
            r#" maxlength="30""#,
            true,
            invalid_name,
        ),
        input(
            "rank",
            "Rank:",
            "number",
            value("rank"),
            r#" step="0.01""#,
            true,
            message
                .and_then(|_| rank_input(value("rank")).err())
                .map(str::to_owned),
        ),
        input(
            "reported_experience",
            "Reported experience:",
            "number",
            value("reported_experience"),
            r#" min="0""#,
            false,
            message
                .and_then(|_| judge_experience(fields.get("reported_experience")).err())
                .map(str::to_owned),
        ),
        checkbox(
            "needs_crash_housing",
            "Needs crash housing:",
            flag("needs_crash_housing"),
        ),
        checkbox(
            "requests_wgm_housing",
            "Requests wgm housing:",
            flag("requests_wgm_housing"),
        ),
        multi_select(
            "schools",
            "Schools:",
            &schools,
            fields.all("schools"),
            message.and_then(|_| multi_error(&schools, fields.all("schools"))),
        ),
        input(
            "email",
            "Email:",
            "email",
            value("email"),
            r#" maxlength="254""#,
            false,
            if bound {
                email_errors(fields.get("email"))
            } else {
                Vec::new()
            },
        ),
        select(
            "registration",
            "Registration:",
            &registrations,
            value("registration"),
            false,
            true,
            message.and_then(|_| reference_options(&registrations, value("registration"), false)),
        ),
        input(
            "ballot_code",
            "Ballot code:",
            "text",
            value("ballot_code"),
            r#" maxlength="255""#,
            false,
            code_errors,
        ),
        checkbox("is_dino", "Is dino:", flag("is_dino")),
        checkbox("wing_only", "Wing only:", flag("wing_only")),
        checkbox_with_help(
            "request_wing",
            "Request wing:",
            flag("request_wing"),
            Some("Prioritize giving this judge a wing when they chair an inround."),
        ),
        checkbox_with_help(
            "no_wings",
            "No wings:",
            flag("no_wings"),
            Some("Deprioritize giving this judge a wing when they chair an inround."),
        ),
        multi_select(
            "required_room_tags",
            "Required room tags:",
            &tags,
            fields.all("required_room_tags"),
            message.and_then(|_| multi_error(&tags, fields.all("required_room_tags"))),
        ),
    ])
}

async fn rounds(db: &mut MySqlConnection, judge: i32) -> Result<Vec<Value>, sqlx::Error> {
    let rounds = sqlx::query_as::<
        _,
        (
            i32,
            i32,
            i32,
            String,
            i32,
            String,
            Option<i32>,
            Option<String>,
        ),
    >(
        "SELECT r.id, r.round_number, g.id, g.name, o.id, o.name, room.id, room.name
         FROM tab_round_judges j JOIN tab_round r ON j.round_id = r.id
         JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_room room ON room.id = r.room_id WHERE j.judge_id = ? ORDER BY r.id",
    )
    .bind(judge)
    .fetch_all(db)
    .await?;
    Ok(rounds.into_iter().map(|(id, round_number, gov_id, gov, opp_id, opp, room_id, room)| context! {
        id, round_number, gov_team => Identity { id: gov_id, name: gov }, opp_team => Identity { id: opp_id, name: opp },
        room => room_id.zip(room).map(|(id, name)| Identity { id, name }),
    }).collect())
}
