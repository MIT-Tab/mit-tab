use std::collections::{BTreeMap, BTreeSet};

use axum::{
    Extension, Router,
    body::Bytes,
    extract::{RawQuery, State},
    http::{HeaderMap, Method, StatusCode, Uri},
    response::{Html, Response},
    routing::get,
};
use chrono::NaiveDateTime;
use minijinja::{Value, context};
use ring::hmac;
use sqlx::{AssertSqlSafe, MySqlConnection, QueryBuilder};

use crate::{
    App, access,
    admin_cascade::Cascade,
    admin_changelist::{self, Query, filter_ids},
    admin_html::{self, Shell},
    admin_model_data::{BoundField, Form, Kind, MANAGED, Resource},
    admin_model_list::{self, ListKind},
    admin_records::readonly,
    admin_registration_inlines::Inlines,
    admin_repairs::{self, LATER_STAGE},
    admin_scores::score_audit,
    board_snapshot, entity_store,
    legacy_html::escape,
    legacy_session::{Actor, cookie},
    login, private, publication,
    registration_form::Fields,
    roster_log, round_audit,
    settings::Settings,
    staff_pairing::lock_round_if_configured,
    unavailable,
};

pub fn routes() -> Router<App> {
    MANAGED.iter().fold(Router::new(), |router, model| {
        let application = admin_changelist::application(model);
        let model = format!("/admin/{application}/{model}/");
        router
            .route(&model, get(entry).post(entry))
            .route(&format!("{model}add/"), get(entry).post(entry))
            .route(&format!("{model}{{id}}/change/"), get(entry).post(entry))
            .route(&format!("{model}{{id}}/delete/"), get(entry).post(entry))
            .route(&format!("{model}{{id}}/history/"), get(entry))
    })
}

#[derive(Clone, Copy)]
enum Screen {
    List,
    Add,
    Change(i32),
    Delete(i32),
    History(i32),
}

impl Screen {
    fn find(path: &str) -> Option<(&str, Self)> {
        let segments: Vec<_> = path.trim_matches('/').split('/').collect();
        let screen = match segments.as_slice() {
            ["admin", app, model] => (app, model, Self::List),
            ["admin", app, model, "add"] => (app, model, Self::Add),
            ["admin", app, model, id, operation] => {
                let id = id.parse().ok()?;
                let screen = match *operation {
                    "change" => Self::Change(id),
                    "delete" => Self::Delete(id),
                    "history" => Self::History(id),
                    _ => return None,
                };
                (app, model, screen)
            }
            _ => return None,
        };
        (admin_changelist::application(screen.1) == *screen.0).then_some((screen.1, screen.2))
    }
}

struct Page<'a> {
    shell: Shell,
    resource: Resource,
    query: Query,
    model_url: String,
    headers: &'a HeaderMap,
    user: i32,
    actor: Actor,
    target: &'a str,
}

impl Page<'_> {
    fn markup(self, template: &str, context: Value) -> Response {
        let base = context! {
            opts => self.resource.list.model.opts(),
            app_url => format!("/admin/{}/", self.resource.list.model.application()),
            model_url => &self.model_url,
        };
        self.shell
            .render(template, context! { ..base, ..context }, self.headers)
    }

    fn preserved(&self) -> &str {
        self.query.get("_changelist_filters").unwrap_or_default()
    }

    fn list_url(&self) -> String {
        let params = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(url::form_urlencoded::parse(self.preserved().as_bytes()))
            .finish();
        if params.is_empty() {
            self.model_url.clone()
        } else {
            format!("{}?{params}", self.model_url)
        }
    }

    fn add_or_change_url(&self, id: Option<i32>) -> String {
        let target = id.map_or_else(
            || format!("{}add/", self.model_url),
            |id| format!("{}{id}/change/", self.model_url),
        );
        format!(
            "{target}{}",
            admin_html::preserved_filters(self.preserved())
        )
    }
}

async fn entry(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    uri: Uri,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
    method: Method,
    body: Bytes,
) -> Response {
    let Some((model, screen)) = Screen::find(uri.path()) else {
        return private((StatusCode::NOT_FOUND, "Administration page not found."));
    };
    let Actor::User {
        id: user,
        board: false,
        staff: true,
        superuser: true,
    } = actor
    else {
        return access::access_denied();
    };
    if matches!(screen, Screen::List) && method != Method::POST {
        return admin_changelist::get(
            &app,
            actor,
            &headers,
            admin_changelist::application(model),
            model,
            raw,
        )
        .await
        .unwrap_or_else(unavailable);
    }
    let post = (method == Method::POST).then(|| Fields::parse(&body));
    if let Some(post) = &post
        && !login::valid_csrf(&headers, post.get("csrfmiddlewaretoken"))
    {
        return login::csrf_error();
    }
    let Some(resource) = Resource::find(model) else {
        return private((StatusCode::NOT_FOUND, "Administration page not found."));
    };
    let query = Query::new(raw);
    if query
        .get("_to_field")
        .is_some_and(|field| !matches!(field, "" | "id"))
        || post
            .as_ref()
            .is_some_and(|post| !matches!(post.get("_to_field"), "" | "id"))
    {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }
    if matches!(screen, Screen::List)
        && let Some(kind) = ListKind::find(model).filter(|kind| kind.editable())
        && let Some(post) = post.as_ref().filter(|post| !post.all("_save").is_empty())
    {
        return admin_model_list::list(&app, actor, &headers, kind, query, Some((post, user)))
            .await;
    }
    let shell = match Shell::load(&app, actor, &headers, uri.path()).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let model_url = resource.list.model.url();
    let page = Page {
        shell,
        resource,
        query,
        model_url,
        user,
        actor,
        headers: &headers,
        target: uri
            .path_and_query()
            .map_or_else(|| uri.path(), |query| query.as_str()),
    };
    let result = match screen {
        Screen::List => bulk(&app, page, &post.expect("list POST")).await,
        Screen::Add => change(&app, page, None, post.as_ref()).await,
        Screen::Change(id) => change(&app, page, Some(id), post.as_ref()).await,
        Screen::Delete(id) => delete(&app, page, id, post.as_ref()).await,
        Screen::History(id) => history(&app, page, id).await,
    };
    result.unwrap_or_else(|_| unavailable())
}

async fn change(
    app: &App,
    page: Page<'_>,
    id: Option<i32>,
    post: Option<&Fields>,
) -> Result<Response, sqlx::Error> {
    let resource = &page.resource;
    let mut db = app.db.begin().await?;
    if post.is_some() {
        lock_round_if_configured(&mut db).await?;
    }
    let can_add = admin_model_list::can_add(&mut db, resource.list.model.name).await?;
    if id.is_none() && !can_add {
        return Ok(crate::motions::forbidden(Extension(page.actor)).await);
    }
    let (initial, original) = if let Some(id) = id {
        let Some(initial) = resource.load(&mut db, id, post.is_some()).await? else {
            return Ok(access::found("/admin/"));
        };
        (Some(initial), resource.describe(&mut db, id).await?)
    } else {
        (None, None)
    };
    let pairing_revision = initial
        .as_ref()
        .filter(|_| resource.pairing())
        .and_then(|initial| AdminFormProof::for_session(&page, "admin pairing edit", initial));
    let stale_pairing = post
        .zip(pairing_revision.as_ref())
        .is_some_and(|(post, proof)| !proof.verified(app, post.get("mittab_pairing_revision")));
    let revision = if resource.registration_config() {
        Some((
            "mittab_configuration_revision",
            "admin registration configuration edit",
        ))
    } else if resource.public_information() {
        Some(("mittab_content_revision", "admin public information edit"))
    } else if resource.motion() {
        Some(("mittab_motion_revision", "admin motion edit"))
    } else {
        None
    };
    let resource_revision =
        initial
            .as_ref()
            .zip(revision)
            .and_then(|(initial, (name, purpose))| {
                AdminFormProof::for_session(&page, purpose, initial).map(|proof| (name, proof))
            });
    let stale_resource = post
        .zip(resource_revision.as_ref())
        .is_some_and(|(post, (name, proof))| !proof.verified(app, post.get(name)));
    let stale = stale_pairing || stale_resource;
    let mut inlines = if resource.registration() {
        Some(Inlines::load(&mut db, id, post.is_some()).await?)
    } else {
        None
    };
    let registration_revision =
        initial
            .as_ref()
            .zip(inlines.as_ref())
            .and_then(|(parent, inlines)| {
                AdminFormProof::for_session(
                    &page,
                    "admin registration edit",
                    (parent, inlines.revision()),
                )
            });
    let stale_fields = post
        .zip(registration_revision.as_ref())
        .is_some_and(|(post, proof)| {
            !proof.verified(app, post.get("mittab_registration_revision"))
        });
    let stale_removal = if let Some((inlines, post)) = inlines.as_ref().zip(post)
        && !stale_fields
        && inlines.requests_removal(post)
    {
        registration_deletion_proof(&page, &mut db, inlines, true)
            .await?
            .is_none_or(|proof| {
                !proof.verified(app, post.get("mittab_registration_delete_revision"))
            })
    } else {
        false
    };
    let stale_registration = stale_fields || stale_removal;
    let bound = post.filter(|_| !stale && !stale_registration);
    let mut form = Form::load(resource, &mut db, initial.as_deref(), bound, &page.query).await?;
    form.pairing_revision = pairing_revision.map(|revision| revision.token(app));
    form.resource_revision = resource_revision.map(|(name, proof)| (name, proof.token(app)));
    form.readonly =
        admin_model_list::readonly_fields(&mut db, resource.list.model.name, id).await?;
    form.can_add = can_add;
    if let Some(inlines) = &mut inlines {
        inlines.token = registration_revision.map(|proof| proof.token(app));
        inlines.bind(bound, &mut db).await?;
    }
    form.inlines = inlines;
    if stale || stale_registration {
        form.non_field_error = Some(if stale_registration {
            "This registration changed. Review the current values before saving."
        } else if stale_resource && resource.registration_config() {
            "This registration configuration changed. Review the current values before saving."
        } else if stale_resource && resource.motion() {
            "This motion changed. Review the current values before saving."
        } else if stale_resource {
            "This content changed. Review the current values before saving."
        } else {
            "This pairing changed. Review the current values before saving."
        });
        refresh_registration_deletion_proof(app, &page, &mut db, &mut form).await?;
        db.commit().await?;
        return Ok(form_conflict(render_form(
            page,
            id,
            original.as_deref(),
            form,
            None,
            post,
        )));
    }
    let attribution = if resource.attributed() {
        Some(attribution(&mut db, resource, id).await?)
    } else {
        None
    };
    if let Some(post) = post
        && let Some(values) = form
            .clean(resource, &mut db, id, initial.as_deref())
            .await?
        && form.inlines.as_ref().is_none_or(Inlines::valid)
    {
        let effects = match resource.list.model.name {
            "tabsettings" => SettingEffects::for_keys(
                initial
                    .iter()
                    .filter_map(|row| row[0].as_deref())
                    .chain(values[0].as_deref()),
            ),
            "breakingteam" => SettingEffects {
                placements: true,
                roster: false,
            },
            _ => SettingEffects::default(),
        };
        effects.refresh(&mut db).await?;
        let pairing = admin_repairs::Edit::capture(&mut db, resource.list.model.name, id).await?;
        match resource
            .save(&mut db, id, &values, page.user, initial.as_deref())
            .await
        {
            Ok(saved) => {
                if let Some(pairing) = pairing
                    && let Some(message) = pairing.record(&mut db, saved, page.user).await?
                {
                    db.rollback().await?;
                    form.non_field_error = Some(message);
                    return Ok(form_conflict(render_form(
                        page,
                        id,
                        original.as_deref(),
                        form,
                        attribution,
                        Some(post),
                    )));
                }
                effects.refresh(&mut db).await?;
                let description = resource
                    .describe(&mut db, saved)
                    .await?
                    .ok_or(sqlx::Error::RowNotFound)?;
                record_roster_edit(&mut db, resource, saved, initial.as_deref(), &values, &form)
                    .await?;
                let changed = changed_fields(resource, initial.as_deref(), &values);
                if resource.attributed() {
                    record_attribution(
                        &mut db,
                        &page,
                        saved,
                        &description,
                        &changed,
                        initial.as_deref(),
                        &values,
                    )
                    .await?;
                }
                let (flag, mut message) = change_log(id.is_none(), &changed);
                if let Some(inlines) = &mut form.inlines {
                    let mut events: Vec<serde_json::Value> =
                        serde_json::from_str(&message).expect("serialized change log");
                    events.extend(inlines.save(&mut db, saved, page.user).await?);
                    message = serde_json::to_string(&events).expect("serializable change log");
                }
                let log_id = admin_html::log_action(
                    &mut db,
                    resource.list.model.name,
                    page.user,
                    saved,
                    &description,
                    flag,
                    message,
                )
                .await?;
                db.commit().await?;
                if !post.all("_popup").is_empty() {
                    return Ok(popup_response(
                        id.map(|_| "change"),
                        saved,
                        Some(&description),
                    ));
                }
                let (destination, notice) = if !post.all("_continue").is_empty() {
                    (page.add_or_change_url(Some(saved)), "continue")
                } else if can_add && !post.all("_addanother").is_empty() {
                    (page.add_or_change_url(None), "another")
                } else {
                    (page.list_url(), "save")
                };
                return Ok(admin_html::redirect_with_notice(
                    app,
                    page.headers,
                    destination,
                    log_id,
                    notice,
                ));
            }
            Err(sqlx::Error::Database(error))
                if error.is_unique_violation()
                    && id.is_none()
                    && resource.list.model.name == "registrationconfig" =>
            {
                db.rollback().await?;
                return Ok(crate::motions::forbidden(Extension(page.actor)).await);
            }
            Err(sqlx::Error::Database(error))
                if error.is_unique_violation() && !resource.unique.is_empty() =>
            {
                db.rollback().await?;
                let mut db = app.db.acquire().await?;
                if form
                    .check_duplicates(resource, &mut db, id, &values)
                    .await?
                {
                    refresh_registration_deletion_proof(app, &page, &mut db, &mut form).await?;
                    return Ok(render_form(
                        page,
                        id,
                        original.as_deref(),
                        form,
                        attribution,
                        Some(post),
                    ));
                }
                return Err(sqlx::Error::Database(error));
            }
            Err(error) => return Err(error),
        }
    }
    refresh_registration_deletion_proof(app, &page, &mut db, &mut form).await?;
    db.commit().await?;
    Ok(render_form(
        page,
        id,
        original.as_deref(),
        form,
        attribution,
        post,
    ))
}

async fn registration_deletion_proof(
    page: &Page<'_>,
    db: &mut MySqlConnection,
    inlines: &Inlines,
    lock: bool,
) -> Result<Option<AdminFormProof>, sqlx::Error> {
    if inlines.token.is_none() && !lock {
        return Ok(None);
    }
    let (cascade, pairings) = inlines.deletion_scope(db, lock).await?;
    Ok(AdminFormProof::for_session(
        page,
        "admin registration inline deletion",
        (inlines.revision(), cascade.scope(), pairings.scope()),
    ))
}

async fn refresh_registration_deletion_proof(
    app: &App,
    page: &Page<'_>,
    db: &mut MySqlConnection,
    form: &mut Form,
) -> Result<(), sqlx::Error> {
    if let Some(inlines) = &mut form.inlines {
        inlines.removal_token = registration_deletion_proof(page, db, inlines, false)
            .await?
            .map(|proof| proof.token(app));
    }
    Ok(())
}

async fn record_attribution(
    db: &mut MySqlConnection,
    page: &Page<'_>,
    id: i32,
    description: &str,
    changed: &[&crate::admin_model_data::Field],
    previous: Option<&[Option<String>]>,
    values: &[Option<String>],
) -> Result<(), sqlx::Error> {
    let model = page.resource.list.model.name;
    let mut changes = serde_json::json!({});
    if let Some(previous) = previous {
        changes["fields"] =
            serde_json::json!(changed.iter().map(|field| field.name).collect::<Vec<_>>());
        if model == "judge" {
            let before = page.resource.value("is_dino", previous) == Some("1");
            let after = page.resource.value("is_dino", values) == Some("1");
            if before != after {
                changes["is_dino"] = serde_json::json!({"before": before, "after": after});
            }
        }
    }
    sqlx::query(
        "INSERT INTO tab_auditevent (object_id, event_type, created_at, object_repr, changes, note, content_type_id, user_id)
         VALUES (?, ?, UTC_TIMESTAMP(6), ?, ?, '',
             (SELECT id FROM django_content_type WHERE app_label = 'tab' AND model = ?), ?)",
    ).bind(id).bind(if previous.is_some() { "edit" } else { "create" }).bind(description)
        .bind(changes.to_string()).bind(model).bind(page.user).execute(db).await?;
    Ok(())
}

async fn record_roster_edit(
    db: &mut MySqlConnection,
    resource: &Resource,
    id: i32,
    before: Option<&[Option<String>]>,
    after: &[Option<String>],
    form: &Form,
) -> Result<(), sqlx::Error> {
    let model = resource.list.model.name;
    if !matches!(model, "school" | "debater" | "team" | "judge") || !roster_log::enabled(db).await?
    {
        return Ok(());
    }
    let old = |name| before.and_then(|row| resource.value(name, row));
    let new = |name| resource.value(name, after);
    let name = new("name").unwrap_or_default();
    let old_name = old("name").unwrap_or_default();
    match model {
        "school" if before.is_some() && old_name != name => {
            roster_log::school_renamed(db, id, old_name, name).await?;
        }
        "debater" if before.is_some() => {
            roster_log::debater_edited(db, id, old_name, name, old("school"), new("school"))
                .await?;
        }
        "team" | "judge" => {
            let is_team = model == "team";
            let kind = if is_team { "teams" } else { "judges" };
            if before.is_none() {
                roster_log::record(db, kind, &format!("Added {model} “{name}”."), false).await?;
            } else if is_team {
                for field in ["school", "hybrid_school"] {
                    if old(field) != new(field) {
                        let (old, new) =
                            roster_log::affiliation_names(db, old(field), new(field)).await?;
                        roster_log::record(
                            db,
                            kind,
                            &format!("Changed affiliation for “{name}”: {old} → {new}."),
                            false,
                        )
                        .await?;
                    }
                }
            } else if old_name != name {
                roster_log::record(
                    db,
                    kind,
                    &format!("Changed name for “{name}”: {old_name} → {name}."),
                    false,
                )
                .await?;
            }
            let field = if is_team { "debaters" } else { "schools" };
            if old(field) != new(field) {
                let options = &form
                    .fields
                    .iter()
                    .find(|bound| bound.field.name == field)
                    .expect("roster relationship")
                    .options;
                let before = membership_ids(old(field));
                let after = membership_ids(new(field));
                let labels: BTreeMap<_, _> = options.iter().map(|(id, name)| (*id, name)).collect();
                for (added, affected) in [
                    (false, before.difference(&after)),
                    (true, after.difference(&before)),
                ] {
                    for id in affected {
                        let Some(related) = labels.get(id) else {
                            return Err(sqlx::Error::RowNotFound);
                        };
                        let verb = if added { "Added" } else { "Removed" };
                        let preposition = if !added {
                            "from"
                        } else if is_team {
                            "on"
                        } else {
                            "for"
                        };
                        let noun = if is_team { "debater" } else { "affiliation" };
                        let message =
                            format!("{verb} {noun} “{related}” {preposition} {model} “{name}”.");
                        roster_log::record(db, kind, &message, is_team).await?;
                    }
                }
            }
        }
        _ => {}
    }
    Ok(())
}

fn membership_ids(value: Option<&str>) -> BTreeSet<i32> {
    value
        .into_iter()
        .flat_map(|ids| ids.split(','))
        .map(|id| id.parse().expect("validated relationship id"))
        .collect()
}

async fn attribution(
    db: &mut MySqlConnection,
    resource: &Resource,
    id: Option<i32>,
) -> Result<[Value; 2], sqlx::Error> {
    let (user, date) = if let Some(id) = id {
        let (user_id, username, date): (Option<i32>, Option<String>, Option<NaiveDateTime>) =
            sqlx::query_as(AssertSqlSafe(format!(
                "SELECT u.id, u.username, t.created_at FROM {} t LEFT JOIN auth_user u
                 ON u.id = t.created_by_id WHERE t.id = ?",
                resource.list.table(),
            )))
            .bind(id)
            .fetch_one(db)
            .await?;
        (user_id.zip(username), date)
    } else {
        (None, None)
    };
    let linked_user = user.map_or_else(
        || "-".into(),
        |(id, name)| {
            format!(
                "<a href=\"/admin/auth/user/{id}/change/\">{}</a>",
                escape(&name)
            )
        },
    );
    Ok([
        readonly("created_by", "Created by", linked_user),
        readonly(
            "created_at",
            "Created at",
            date.map_or_else(|| "-".into(), admin_html::django_datetime),
        ),
    ])
}

fn form_conflict(mut response: Response) -> Response {
    if response.status().is_success() {
        *response.status_mut() = StatusCode::CONFLICT;
    }
    response
}

fn render_form(
    page: Page<'_>,
    id: Option<i32>,
    subtitle: Option<&str>,
    form: Form,
    attribution: Option<[Value; 2]>,
    post: Option<&Fields>,
) -> Response {
    let resource = &page.resource;
    let widgets = form
        .fields
        .iter()
        .map(field_line)
        .collect::<Result<Vec<_>, _>>();
    let Ok(mut widgets) = widgets else {
        return unavailable();
    };
    widgets.extend(form.readonly);
    widgets.extend(attribution.into_iter().flatten());
    let Ok(inline_admin_formsets) = form.inlines.as_ref().map(Inlines::markup).transpose() else {
        return unavailable();
    };
    let errors = form
        .fields
        .iter()
        .filter(|field| field.error.is_some())
        .count()
        + usize::from(form.non_field_error.is_some())
        + form.inlines.as_ref().map_or(0, Inlines::error_count);
    let add = id.is_none();
    let action = if add { "Add" } else { "Change" };
    let popup = page.query.get("_popup").is_some()
        || post.is_some_and(|post| !post.all("_popup").is_empty());
    let suffix = admin_html::preserved_filters(page.preserved());
    let object_url = id.map(|id| format!("{}{id}/", page.model_url));
    let to_field = page
        .query
        .get("_to_field")
        .or_else(|| post.map(|post| post.get("_to_field")));
    let ctx = context! {
        title => format!("{action} {}", resource.list.model.singular),
        original => subtitle, subtitle, change => !add, add,
        is_popup => popup, is_popup_var => "_popup", to_field, to_field_var => "_to_field",
        object_url, form_url => &suffix, filter_suffix => &suffix,
        adminform => [context! { classes => "", lines => widgets }],
        pairing_revision => form.pairing_revision,
        resource_revision => form.resource_revision.as_ref().map(|(_, token)| token),
        resource_revision_name => form.resource_revision.as_ref().map(|(name, _)| name),
        registration_revision => form.inlines.as_ref().and_then(|inlines| inlines.token.as_deref()),
        registration_delete_revision => form.inlines.as_ref().and_then(|inlines| inlines.removal_token.as_deref()),
        inline_admin_formsets,
        show_delete_link => !popup && !add, show_save => true,
        show_save_and_add_another => !popup && form.can_add, show_save_and_continue => !popup,
        has_view_permission => true, can_change => true,
        errors => (0..errors).collect::<Vec<_>>(),
        non_field_errors => form.non_field_error.map(|message| error_list(message, true)).unwrap_or_default(),
        media => if resource.registration() {
            admin_html::registration_admin_scripts()
        } else if resource.fields.iter().any(|field| field.filter_label.is_some()) {
            admin_html::filtered_select_scripts()
        } else { admin_html::scripts() },
        prepopulated_fields_json => "[]",
    };
    page.markup("admin/change_form.html", ctx)
}

fn decimal_equal(old: Option<&str>, new: Option<&str>) -> bool {
    let number = |value: Option<&str>| value.and_then(|value| value.parse::<f64>().ok());
    number(old) == number(new)
}

fn changed_fields(
    resource: &Resource,
    initial: Option<&[Option<String>]>,
    values: &[Option<String>],
) -> Vec<&'static crate::admin_model_data::Field> {
    let Some(old) = initial else {
        return Vec::new();
    };
    resource
        .fields
        .iter()
        .zip(old.iter().zip(values))
        .filter_map(|(field, (old, new))| {
            let equal = match field.kind {
                Kind::Decimal => decimal_equal(old.as_deref(), new.as_deref()),
                Kind::Json => {
                    let json = |raw: &Option<String>| {
                        raw.as_deref()
                            .and_then(|raw| serde_json::from_str::<serde_json::Value>(raw).ok())
                    };
                    json(old) == json(new)
                }
                Kind::EasternDateTime => {
                    let time = |raw: &Option<String>| {
                        raw.as_deref()
                            .and_then(admin_model_list::parse_db_date)
                            .map(|date| date.and_utc().timestamp())
                    };
                    time(old) == time(new)
                }
                _ => old == new,
            };
            (!equal).then_some(field)
        })
        .collect()
}

fn change_log(add: bool, changed: &[&crate::admin_model_data::Field]) -> (u8, String) {
    if add {
        return (1, r#"[{"added": {}}]"#.into());
    }
    let message = if changed.is_empty() {
        "[]".into()
    } else {
        let names: Vec<_> = changed.iter().map(|field| field.label).collect();
        serde_json::json!([{"changed": {"fields": names}}]).to_string()
    };
    (2, message)
}

pub(super) fn error_list(message: &str, non_field: bool) -> String {
    let class = if non_field {
        "errorlist nonfield"
    } else {
        "errorlist"
    };
    format!("<ul class=\"{class}\"><li>{}</li></ul>", escape(message))
}

pub(super) fn render_widget(
    bound: &BoundField,
    name: &str,
    use_required_attribute: bool,
) -> Result<String, minijinja::Error> {
    let field = bound.field;
    let checked = bound.value.as_deref() == Some("1");
    let required = if use_required_attribute && field.required {
        " required"
    } else {
        ""
    };
    let value = bound
        .value
        .as_ref()
        .filter(|value| !value.is_empty())
        .map_or_else(String::new, |value| {
            format!(r#" value="{}""#, escape(value))
        });
    let widget = match field.kind {
        Kind::Text(max) | Kind::Email(max) | Kind::HttpUrl(max) => {
            let input_type = if matches!(field.kind, Kind::Email(_)) {
                "email"
            } else {
                "text"
            };
            format!(
                r#"<input type="{input_type}" name="{name}"{value} class="vTextField" maxlength="{max}"{required} id="id_{name}">"#
            )
        }
        Kind::Textarea => format!(
            "<textarea name=\"{name}\" cols=\"40\" rows=\"10\" class=\"vLargeTextField\"{required} id=\"id_{name}\">\n{}</textarea>",
            escape(bound.value.as_deref().unwrap_or_default()),
        ),
        Kind::Json => {
            let payload = escape(bound.value.as_deref().unwrap_or_default());
            let initial = bound
                .initial_json
                .as_deref()
                .filter(|raw| !raw.is_empty())
                .map_or_else(String::new, |raw| format!(r#" value="{}""#, escape(raw)));
            format!(
                "<textarea name=\"{name}\" cols=\"40\" rows=\"10\" id=\"id_{name}\">\n{payload}</textarea><input type=\"hidden\" name=\"initial-{name}\"{initial} id=\"initial-id_{name}\">"
            )
        }
        Kind::EasternDateTime => {
            let value = if bound.value.as_deref() == Some("") {
                r#" value="""#
            } else {
                &value
            };
            format!(r#"<input type="datetime-local" name="{name}"{value} id="id_{name}">"#)
        }
        Kind::BallotCode => format!(
            r#"<input type="text" name="{name}"{value} class="vTextField" maxlength="255"{required} id="id_{name}">"#
        ),
        Kind::Integer | Kind::Experience | Kind::PositiveSmallInteger | Kind::PositiveInteger => {
            let min = if matches!(
                field.kind,
                Kind::Experience | Kind::PositiveSmallInteger | Kind::PositiveInteger
            ) {
                r#" min="0""#
            } else {
                ""
            };
            format!(
                r#"<input type="number" name="{name}"{value} class="vIntegerField"{min}{required} id="id_{name}">"#
            )
        }
        Kind::Decimal => format!(
            r#"<input type="number" name="{name}"{value} step="0.01"{required} id="id_{name}">"#
        ),
        Kind::Checkbox => {
            let state = if checked { " checked" } else { "" };
            format!(r#"<input type="checkbox" name="{name}" id="id_{name}"{state}>"#)
        }
        Kind::Choice(_) | Kind::Relation(_) | Kind::Multiple(_) => {
            let relation = match field.kind {
                Kind::Relation(relation) => Some(relation),
                Kind::Multiple(link) => Some(link.relation),
                _ => None,
            };
            let multiple = matches!(field.kind, Kind::Multiple(_));
            let blank = matches!(field.kind, Kind::Relation(_))
                || matches!(field.kind, Kind::Choice(_)) && field.initial.is_none();
            let select = admin_html::render_fragment(
                "admin/model_select.html",
                context! {
                    name, options => &bound.options, selected => &bound.selected,
                    has_selection => bound.value.as_deref().is_some_and(|value| !value.is_empty()),
                    blank, multiple, filter_label => field.filter_label, required => use_required_attribute && (blank || multiple) && field.required,
                },
            )?;
            if let Some(relation) = relation.filter(|_| field.related_widget) {
                admin_html::render_fragment("admin/widgets/related_widget_wrapper.html", context! {
                    name, model => relation.model,
                    rendered_widget => Value::from_safe_string(select.trim_end_matches('\n').into()),
                    change_related_template_url => format!("{}__fk__/change/", relation.admin_url),
                    delete_related_template_url => format!("{}__fk__/delete/", relation.admin_url),
                    add_related_url => format!("{}add/", relation.admin_url),
                    url_params => "_to_field=id&_popup=1",
                    view_related_url_params => "_to_field=id",
                    can_change_related => !multiple, can_add_related => true,
                    can_delete_related => field.can_delete_related,
                    can_view_related => !multiple,
                })?.trim().to_owned()
            } else {
                select.trim_end_matches('\n').to_owned()
            }
        }
    };
    Ok(widget)
}

fn field_line(bound: &BoundField) -> Result<Value, minijinja::Error> {
    let field = bound.field;
    let name = field.name;
    let widget = render_widget(bound, name, true)?;
    let class = if field.required {
        r#" class="required""#
    } else {
        ""
    };
    let checkbox = matches!(field.kind, Kind::Checkbox);
    let label_class = if checkbox {
        r#" class="vCheckboxLabel""#
    } else {
        class
    };
    let errors = bound
        .error
        .as_deref()
        .map(|message| {
            if matches!(field.kind, Kind::HttpUrl(_)) && message == "Enter a valid URL." {
                // Django runs the URL validator for the model field and in TournamentLinkBase.clean().
                r#"<ul class="errorlist"><li>Enter a valid URL.</li><li>Enter a valid URL.</li></ul>"#.into()
            } else {
                error_list(message, false)
            }
        })
        .unwrap_or_default();
    let suffix = if checkbox { "" } else { ":" };
    let help_text = field.help_text.or_else(|| {
        matches!(field.kind, Kind::Multiple(_))
            .then_some("Hold down “Control”, or “Command” on a Mac, to select more than one.")
    });
    Ok(context! {
        has_visible_field => true, errors,
        fields => [context! {
            field => context! {name, help_text, id_for_label => format!("id_{name}")}, widget, is_checkbox => checkbox,
            label_tag => format!("<label{label_class} for=\"id_{name}\">{}{suffix}</label>", field.label),
        }],
    })
}

pub(super) fn popup_response(action: Option<&str>, id: i32, description: Option<&str>) -> Response {
    let quoted = |value: &str| {
        let mut json = String::new();
        for unit in serde_json::to_string(value)
            .expect("serialize text")
            .encode_utf16()
        {
            if unit > 0x7f {
                json.push_str(&format!("\\u{unit:04x}"));
            } else {
                json.push(char::from_u32(u32::from(unit)).expect("ASCII"));
            }
        }
        json
    };
    let value = quoted(&id.to_string());
    let data = match (action, description) {
        (Some("change"), Some(description)) => format!(
            r#"{{"action": "change", "value": {value}, "obj": {}, "new_value": {value}}}"#,
            quoted(description)
        ),
        (None, Some(description)) => {
            format!(r#"{{"value": {value}, "obj": {}}}"#, quoted(description))
        }
        _ => format!(r#"{{"action": "delete", "value": {value}}}"#),
    };
    match admin_html::render_fragment(
        "admin/popup_response.html",
        context! { popup_response_data => data },
    ) {
        Ok(body) => private(Html(body)),
        Err(_) => unavailable(),
    }
}

#[derive(Default)]
struct SettingEffects {
    placements: bool,
    roster: bool,
}

impl SettingEffects {
    fn for_keys<'a>(keys: impl IntoIterator<Item = &'a str>) -> Self {
        let mut effects = Self::default();
        for key in keys {
            effects.placements |= publication::VISIBILITY_KEYS.contains(&key);
            effects.roster |= key == "teams_public";
        }
        effects
    }

    async fn for_deleted_keys(db: &mut MySqlConnection, ids: &[i32]) -> Result<Self, sqlx::Error> {
        let mut query = QueryBuilder::new(
            "SELECT DISTINCT `key` FROM tab_tabsettings
             WHERE `key` IN ('teams_public', 'var_teams_visible', 'nov_teams_visible') AND id IN (",
        );
        let mut selected = query.separated(", ");
        for id in ids {
            selected.push_bind(*id);
        }
        let keys = query
            .push(")")
            .build_query_scalar::<String>()
            .fetch_all(db)
            .await?;
        Ok(Self::for_keys(keys.iter().map(String::as_str)))
    }

    async fn refresh(&self, db: &mut MySqlConnection) -> Result<(), sqlx::Error> {
        if self.placements {
            let settings = Settings::load(db, publication::VISIBILITY_KEYS).await?;
            publication::refresh_placements(db, &settings).await?;
        }
        if self.roster {
            roster_log::enabled(db).await?;
        }
        Ok(())
    }
}

const TAG_RELATIONS: &[(&str, &str, &str, &str)] = &[
    (
        "tab_team_required_room_tags",
        "Team_required_room_tags",
        "Team-roomtag relationship",
        "team-roomtag relationships",
    ),
    (
        "tab_judge_required_room_tags",
        "Judge_required_room_tags",
        "Judge-roomtag relationship",
        "judge-roomtag relationships",
    ),
    (
        "tab_room_tags",
        "Room_tags",
        "Room-roomtag relationship",
        "room-roomtag relationships",
    ),
];

struct DeletionSummary {
    counts: Vec<(&'static str, usize)>,
    objects: Vec<String>,
}

pub(super) struct AdminFormProof(Vec<u8>);

impl AdminFormProof {
    fn deletion(
        page: &Page<'_>,
        records: &Removed,
        cascade: &Cascade,
        pairings: &CascadePairings,
    ) -> Option<Self> {
        if !page.resource.cascading_deletion()
            && !page.resource.registration_config()
            && !page.resource.motion()
        {
            return None;
        }
        Self::for_session(
            page,
            "admin deletion preview",
            (
                &records.rows,
                &records.revisions,
                cascade.scope(),
                pairings.scope(),
            ),
        )
    }

    fn for_session(page: &Page<'_>, purpose: &str, state: impl serde::Serialize) -> Option<Self> {
        Self::for_target(page.headers, purpose, page.target.split('?').next()?, state)
    }

    pub(super) fn for_target(
        headers: &HeaderMap,
        purpose: &str,
        target: &str,
        state: impl serde::Serialize,
    ) -> Option<Self> {
        let session = cookie(headers, "sessionid")?;
        let statement = serde_json::to_vec(&(purpose, session, target, state))
            .expect("serializable admin confirmation");
        Some(Self(statement))
    }

    pub(super) fn verified(&self, app: &App, posted: &str) -> bool {
        hex::decode(posted)
            .is_ok_and(|tag| hmac::verify(&app.admin_notice_key, &self.0, &tag).is_ok())
    }

    pub(super) fn token(&self, app: &App) -> String {
        hex::encode(hmac::sign(&app.admin_notice_key, &self.0).as_ref())
    }
}

type Placement = (i32, Option<i32>, Option<i32>, Option<u32>);
type PublicationScope = (i32, i32, i32, Vec<Placement>);

pub(super) struct CascadePairings {
    outrounds: Option<BTreeMap<i32, board_snapshot::Snapshot>>,
    removed_outrounds: BTreeSet<i32>,
    inrounds: BTreeMap<i32, round_audit::Snapshot>,
    publications: Vec<PublicationScope>,
    deleted_round_details: Vec<(i32, i32, Option<NaiveDateTime>, String)>,
}

impl CascadePairings {
    pub(super) async fn capture(
        db: &mut MySqlConnection,
        cascade: &Cascade,
        model: &str,
        rows: &[(i32, String)],
    ) -> Result<Self, sqlx::Error> {
        let mut removed_outrounds = cascade.ids("tab_outround");
        if model == "outround" {
            removed_outrounds.extend(rows.iter().map(|(id, _)| *id));
        }
        let outrounds = if !removed_outrounds.is_empty() || cascade.touches("tab_outround_judges") {
            Some(board_snapshot::capture(db, board_snapshot::Pairings::Outround).await?)
        } else {
            None
        };
        let mut removed_rounds = cascade.ids("tab_round");
        if model == "round" {
            removed_rounds.extend(rows.iter().map(|(id, _)| *id));
        }
        let mut deleted_round_details = Vec::new();
        if !removed_rounds.is_empty() {
            let mut query = QueryBuilder::new(
                "SELECT id, pullup, result_entered_at, SHA2(rfd, 256) FROM tab_round WHERE id IN (",
            );
            let mut ids = query.separated(", ");
            for id in &removed_rounds {
                ids.push_bind(*id);
            }
            deleted_round_details = query
                .push(") ORDER BY id")
                .build_query_as()
                .fetch_all(&mut *db)
                .await?;
        }
        let mut inrounds = BTreeMap::new();
        if !removed_rounds.is_empty() || cascade.touches("tab_round_judges") {
            let mut affected = removed_rounds;
            let judge_links = cascade.ids("tab_round_judges");
            if !judge_links.is_empty() {
                let mut sql = QueryBuilder::new(
                    "SELECT DISTINCT round_id FROM tab_round_judges WHERE id IN (",
                );
                let mut selected = sql.separated(", ");
                for id in judge_links {
                    selected.push_bind(id);
                }
                let ids = sql
                    .push(")")
                    .build_query_scalar::<i32>()
                    .fetch_all(&mut *db)
                    .await?;
                affected.extend(ids);
            }
            for id in affected {
                inrounds.extend(score_audit(db, id).await?);
            }
        }
        let mut publications = Vec::new();
        if !removed_outrounds.is_empty() {
            let settings = Settings::load(db, publication::VISIBILITY_KEYS).await?;
            let affected = outrounds
                .iter()
                .flatten()
                .filter(|(id, _)| removed_outrounds.contains(id))
                .filter_map(|(_, round)| round.division.zip(round.num_teams))
                .collect::<BTreeSet<_>>();
            for (division, size) in affected {
                let key = if division == 0 {
                    publication::VISIBILITY_KEYS[0]
                } else {
                    publication::VISIBILITY_KEYS[1]
                };
                let visibility = settings.integer(key, 256);
                let placements: Vec<Placement> = sqlx::query_as(
                    "SELECT s.id, p.id, p.team_id, p.place FROM tab_publishedoutroundstage s
                     LEFT JOIN tab_publishedteamplacement p ON p.stage_id = s.id
                     WHERE s.type_of_round = ? AND s.num_teams = ? ORDER BY p.id",
                )
                .bind(division)
                .bind(size)
                .fetch_all(&mut *db)
                .await?;
                publications.push((division, size, visibility, placements));
            }
        }
        Ok(Self {
            outrounds,
            removed_outrounds,
            inrounds,
            publications,
            deleted_round_details,
        })
    }

    pub(super) fn scope(&self) -> impl serde::Serialize + '_ {
        let removed = self
            .outrounds
            .iter()
            .flatten()
            .filter(|(id, _)| self.removed_outrounds.contains(id))
            .collect::<Vec<_>>();
        (
            &self.inrounds,
            removed,
            &self.publications,
            &self.deleted_round_details,
        )
    }

    pub(super) fn blocked(&self) -> bool {
        self.outrounds.as_ref().is_some_and(|before| {
            admin_repairs::later_stage_depends_on(before, &self.removed_outrounds)
        })
    }

    pub(super) async fn record(
        self,
        db: &mut MySqlConnection,
        user: i32,
    ) -> Result<(), sqlx::Error> {
        if !self.inrounds.is_empty() {
            let mut after = BTreeMap::new();
            for id in self.inrounds.keys() {
                after.extend(score_audit(db, *id).await?);
            }
            round_audit::record(db, &self.inrounds, &after, user, "admin deletion").await?;
        }
        if let Some(outrounds) = self.outrounds {
            admin_repairs::finish_outround_changes(db, user, &outrounds, &self.removed_outrounds)
                .await?;
        }
        Ok(())
    }
}

#[derive(Default)]
struct Removed {
    rows: Vec<(i32, String)>,
    revisions: Vec<Option<String>>,
}

impl Removed {
    async fn read(
        db: &mut MySqlConnection,
        resource: &Resource,
        filter: &[i32],
        query: Option<&Query>,
        selected: Option<&BTreeSet<i32>>,
        lock: bool,
    ) -> Result<Self, sqlx::Error> {
        let list = &resource.list;
        let revision = if resource.registration_config() {
            "CAST(t.updated_at AS CHAR)"
        } else if resource.motion() {
            "CAST(JSON_ARRAY(t.updated_at, t.round_number, t.outround_type, t.num_teams, t.info_slide, t.motion_text, t.is_published) AS CHAR)"
        } else {
            "NULL"
        };
        let mut sql = QueryBuilder::new(format!(
            "SELECT t.id, {}, {revision} FROM {} WHERE TRUE",
            list.label, list.source
        ));
        filter_ids(&mut sql, filter);
        if let Some((kind, query)) = ListKind::find(resource.list.model.name).zip(query) {
            kind.filter(&mut sql, query);
        }
        if let Some(ids) = selected {
            if ids.is_empty() {
                return Ok(Self::default());
            }
            sql.push(" AND t.id IN (");
            let mut selector = sql.separated(", ");
            for id in ids {
                selector.push_bind(id);
            }
            sql.push(")");
        }
        sql.push(" ORDER BY ").push(list.ordering);
        if lock {
            sql.push(" FOR UPDATE");
        }
        let selected = sql
            .build_query_as::<(i32, String, Option<String>)>()
            .fetch_all(db)
            .await?;
        let (rows, revisions) = selected
            .into_iter()
            .map(|(id, label, update)| ((id, label), update))
            .unzip();
        Ok(Self { rows, revisions })
    }

    async fn cascade(
        &self,
        db: &mut MySqlConnection,
        resource: &Resource,
        locked: bool,
    ) -> Result<Cascade, sqlx::Error> {
        let ids: Vec<_> = self.rows.iter().map(|(id, _)| *id).collect();
        Cascade::read(db, resource.list.table(), &ids, locked).await
    }

    async fn summary(
        &self,
        db: &mut MySqlConnection,
        page: &Page<'_>,
        cascade: &Cascade,
    ) -> Result<DeletionSummary, sqlx::Error> {
        if page.resource.cascading_deletion() {
            let model = &page.resource.list.model;
            let mut counts = vec![(model.plural, self.rows.len())];
            counts.extend(cascade.counts());
            let title = admin_html::capitalized(model.singular);
            let mut seen = BTreeSet::new();
            let objects = self
                .rows
                .iter()
                .map(|(id, label)| {
                    let label = format!(
                        "{title}: <a href=\"{}{id}/change/\">{}</a>",
                        page.model_url,
                        escape(label)
                    );
                    cascade.rendered(page.resource.list.table(), *id, &label, &mut seen)
                })
                .collect();
            return Ok(DeletionSummary { counts, objects });
        }
        let mut children: BTreeMap<i32, Vec<String>> = BTreeMap::new();
        let model = &page.resource.list.model;
        let mut counts = vec![(model.plural, self.rows.len())];
        let mut relations: Vec<_> = page
            .resource
            .relations()
            .map(|(_, link)| {
                (
                    link.table,
                    link.owner,
                    link.through,
                    link.singular,
                    link.plural,
                )
            })
            .collect();
        if model.name == "roomtag" {
            relations.extend(
                TAG_RELATIONS
                    .iter()
                    .map(|&(table, through, singular, plural)| {
                        (table, "roomtag_id", through, singular, plural)
                    }),
            );
        }
        if model.name == "debater" {
            relations.push((
                "tab_rankinggroup_debaters",
                "debater_id",
                "RankingGroup_debaters",
                "Rankinggroup-debater relationship",
                "rankinggroup-debater relationships",
            ));
        }
        for (table, owner, object_name, singular, plural) in relations {
            let mut sql = QueryBuilder::new(format!(
                "SELECT id, {owner} FROM {table} WHERE {owner} IN ("
            ));
            let mut ids = sql.separated(", ");
            for (id, _) in &self.rows {
                ids.push_bind(*id);
            }
            sql.push(") ORDER BY ").push(owner);
            if let Some((_, relation)) = page
                .resource
                .relations()
                .find(|(_, link)| link.table == table && link.owner == owner)
            {
                sql.push(", ").push(relation.column);
            }
            let links = sql
                .push(", id")
                .build_query_as::<(i32, i32)>()
                .fetch_all(&mut *db)
                .await?;
            if links.is_empty() {
                continue;
            }
            counts.push((plural, links.len()));
            for (id, parent) in links {
                children.entry(parent).or_default().push(format!(
                    "		<li>{singular}: {object_name} object ({id})</li>"
                ));
            }
        }
        if model.name == "debater" {
            let (name, column) = ("roundstats", "debater_id");
            let list = admin_changelist::default_list(name).expect("admin cascade model");
            let mut sql = QueryBuilder::new(format!(
                "SELECT t.id, t.{column}, {} FROM {} WHERE t.{column} IN (",
                list.label, list.source,
            ));
            let mut ids = sql.separated(", ");
            for (id, _) in &self.rows {
                ids.push_bind(*id);
            }
            let linked = sql
                .push(") ORDER BY t.id DESC")
                .build_query_as::<(i32, i32, String)>()
                .fetch_all(&mut *db)
                .await?;
            if !linked.is_empty() {
                let count = (list.model.plural, linked.len());
                counts.insert(1, count);
            }
            let title = admin_html::capitalized(list.model.singular);
            let url = list.model.url();
            for (id, parent, label) in linked {
                let item = format!(
                    "		<li>{title}: <a href=\"{url}{id}/change/\">{}</a></li>",
                    escape(&label)
                );
                let items = children.entry(parent).or_default();
                items.insert(0, item);
            }
        }
        let title = admin_html::capitalized(model.singular);
        let objects = self
            .rows
            .iter()
            .map(|(id, label)| {
                let item = format!(
                    "	<li>{title}: <a href=\"{}{id}/change/\">{}</a>",
                    page.model_url,
                    escape(label)
                );
                if let Some(children) = children.get(id) {
                    format!("{item}\n\t<ul>\n{}\n\t</ul>\n\t</li>", children.join("\n"))
                } else {
                    format!("{item}</li>")
                }
            })
            .collect();
        Ok(DeletionSummary { counts, objects })
    }

    async fn rejection(
        &self,
        db: &mut MySqlConnection,
        resource: &Resource,
    ) -> Result<Option<&'static str>, sqlx::Error> {
        let (references, message): (&[(&str, &str)], _) = match resource.list.model.name {
            "school" => (
                &[
                    ("tab_team", "school_id"),
                    ("tab_judge_schools", "school_id"),
                    ("registration_registration", "school_id"),
                ],
                "This school has teams, affiliated judges, or registrations.",
            ),
            "debater" => (
                &[("tab_team_debaters", "debater_id")],
                "Remove this debater from their team before deleting the debater.",
            ),
            "room" => (
                &[("tab_round", "room_id")],
                "This room is assigned to a round.",
            ),
            _ => return Ok(None),
        };
        for &(table, column) in references {
            let mut sql = QueryBuilder::new(format!(
                "SELECT EXISTS(SELECT 1 FROM {table} WHERE {column} IN ("
            ));
            let mut ids = sql.separated(", ");
            for (id, _) in &self.rows {
                ids.push_bind(*id);
            }
            if sql
                .push("))")
                .build_query_scalar::<bool>()
                .fetch_one(&mut *db)
                .await?
            {
                return Ok(Some(message));
            }
        }
        Ok(None)
    }

    async fn execute(
        &self,
        db: &mut MySqlConnection,
        page: &Page<'_>,
        cascade: Cascade,
    ) -> Result<u64, sqlx::Error> {
        let mut last_log = 0;
        for (id, name) in &self.rows {
            last_log = admin_html::log_action(
                db,
                page.resource.list.model.name,
                page.user,
                *id,
                name,
                3,
                String::new(),
            )
            .await?;
        }
        let ids: Vec<_> = self.rows.iter().map(|(id, _)| *id).collect();
        let mut scores = Vec::new();
        if page.resource.list.model.name == "debater" {
            let mut sql = QueryBuilder::new(
                "SELECT DISTINCT round_id FROM tab_roundstats WHERE debater_id IN (",
            );
            let mut selected = sql.separated(", ");
            for id in &ids {
                selected.push_bind(*id);
            }
            let rounds = sql
                .push(") ORDER BY round_id")
                .build_query_scalar::<i32>()
                .fetch_all(&mut *db)
                .await?;
            for round in rounds {
                scores.push((round, score_audit(db, round).await?));
            }
        }
        let effects = match page.resource.list.model.name {
            "tabsettings" => SettingEffects::for_deleted_keys(db, &ids).await?,
            "breakingteam" => SettingEffects {
                placements: true,
                roster: false,
            },
            _ => SettingEffects::default(),
        };
        effects.refresh(db).await?;
        let mut links: Vec<_> = if page.resource.cascading_deletion() {
            Vec::new()
        } else {
            page.resource
                .relations()
                .map(|(_, link)| (link.table, link.owner))
                .collect()
        };
        match page.resource.list.model.name {
            "roomtag" => links.extend(
                TAG_RELATIONS
                    .iter()
                    .map(|&(table, _, _, _)| (table, "roomtag_id")),
            ),
            "team" | "judge" => {
                let model = page.resource.list.model.name;
                let kind = if model == "team" { "teams" } else { "judges" };
                if roster_log::enabled(db).await? {
                    for (_, name) in &self.rows {
                        roster_log::record(db, kind, &format!("Removed {model} “{name}”."), false)
                            .await?;
                    }
                }
            }
            "registration" => {
                crate::admin_registration_inlines::record_cascade_removals(db, &cascade).await?;
            }
            "debater" => links.extend([
                ("tab_roundstats", "debater_id"),
                ("tab_rankinggroup_debaters", "debater_id"),
            ]),
            "school" => {
                if roster_log::enabled(db).await? {
                    for (id, name) in &self.rows {
                        roster_log::school_removed(db, *id, name).await?;
                    }
                }
                for (table, column) in [
                    ("tab_team", "hybrid_school_id"),
                    ("tab_debater", "school_id"),
                ] {
                    let mut sql = QueryBuilder::new(format!(
                        "UPDATE {table} SET {column} = NULL WHERE {column} IN ("
                    ));
                    let mut selected = sql.separated(", ");
                    for id in &ids {
                        selected.push_bind(*id);
                    }
                    sql.push(")").build().execute(&mut *db).await?;
                }
            }
            _ => {}
        }
        cascade.delete(db).await?;
        entity_store::delete_links(db, &ids, &links).await?;
        entity_store::delete_links(db, &ids, &[(page.resource.list.table(), "id")]).await?;
        for (round, before) in scores {
            let after = score_audit(db, round).await?;
            round_audit::record(db, &before, &after, page.user, "admin scores").await?;
        }
        effects.refresh(db).await?;
        Ok(last_log)
    }
}

async fn delete(
    app: &App,
    page: Page<'_>,
    id: i32,
    post: Option<&Fields>,
) -> Result<Response, sqlx::Error> {
    let mut db = app.db.begin().await?;
    let confirm = post.is_some_and(|form| form.get("post") == "yes");
    if post.is_some() && !confirm {
        return Ok(private((
            StatusCode::BAD_REQUEST,
            "Confirm the deletion first.",
        )));
    }
    if confirm {
        lock_round_if_configured(&mut db).await?;
    }
    let records = Removed::read(&mut db, &page.resource, &[id], None, None, confirm).await?;
    let Some((_, description)) = records.rows.first() else {
        return Ok(access::found("/admin/"));
    };
    let rejection = records.rejection(&mut db, &page.resource).await?;
    let cascade = records.cascade(&mut db, &page.resource, confirm).await?;
    let permissions = cascade.unavailable_permissions(&mut db).await?;
    let pairings = CascadePairings::capture(
        &mut db,
        &cascade,
        page.resource.list.model.name,
        &records.rows,
    )
    .await?;
    let rejection = rejection.or_else(|| pairings.blocked().then_some(LATER_STAGE));
    let headers = page.headers;
    let preview = AdminFormProof::deletion(&page, &records, &cascade, &pairings);
    let stale = confirm
        && preview.as_ref().is_some_and(|preview| {
            !preview.verified(app, post.map_or("", |post| post.get("mittab_delete_scope")))
        });
    if confirm {
        if !permissions.is_empty() {
            return Ok(private((
                StatusCode::FORBIDDEN,
                "You do not have permission to delete related records.",
            )));
        }
        if let Some(message) = rejection {
            return Ok(private((StatusCode::CONFLICT, message)));
        }
    }
    if confirm && !stale {
        let log = records.execute(&mut db, &page, cascade).await?;
        pairings.record(&mut db, page.user).await?;
        db.commit().await?;
        let response = if post.is_some_and(|post| !post.all("_popup").is_empty()) {
            popup_response(Some("delete"), id, None)
        } else {
            admin_html::redirect_with_notice(app, headers, page.list_url(), log, "delete")
        };
        return Ok(response);
    }
    let summary = if rejection.is_none() && permissions.is_empty() {
        records.summary(&mut db, &page, &cascade).await?
    } else {
        DeletionSummary {
            counts: Vec::new(),
            objects: Vec::new(),
        }
    };
    let protected = rejection.map(|message| format!("\t<li>{}</li>", escape(message)));
    db.commit().await?;
    let model = &page.resource.list.model;
    let can_delete = rejection.is_none() && permissions.is_empty();
    let (title, perms_lacking) = deletion_title(model.singular, rejection, permissions);
    let delete_scope = preview
        .filter(|_| can_delete)
        .map(|preview| preview.token(app));
    let ctx = context! {
        delete_scope,
        title => if stale { "Review updated deletion" } else { &title }, media => admin_html::scripts(),
        protected, perms_lacking,
        object_url => format!("{}{id}/", page.model_url), object => description,
        deleted_objects => summary.objects.join("\n"),
        object_name => model.singular, model_count => summary.counts,
        is_popup => page.query.get("_popup").is_some(), is_popup_var => "_popup",
        to_field => page.query.get("_to_field"), to_field_var => "_to_field",
    };
    let response = page.markup("admin/delete_confirmation.html", ctx);
    Ok(if stale {
        form_conflict(response)
    } else {
        response
    })
}

async fn bulk(app: &App, page: Page<'_>, post: &Fields) -> Result<Response, sqlx::Error> {
    let filter = match ListKind::find(page.resource.list.model.name) {
        Some(kind) => kind.ids(&page.query),
        None => page.query.record_ids(&[]),
    };
    let Some(filter) = filter else {
        return Ok(access::found(format!("{}?e=1", page.model_url)));
    };
    if post.get("action") != "delete_selected" {
        return Ok(access::found(page.target));
    }
    let Ok(selected) = post
        .all("_selected_action")
        .iter()
        .map(|value| value.parse::<i32>())
        .collect::<Result<BTreeSet<_>, _>>()
    else {
        return Ok(private((
            StatusCode::BAD_REQUEST,
            "Invalid record selection.",
        )));
    };
    let confirm = post.get("post") == "yes";
    let selected = (confirm || !post.flag("select_across")).then_some(&selected);
    let mut db = app.db.begin().await?;
    if confirm {
        lock_round_if_configured(&mut db).await?;
    }
    let records = Removed::read(
        &mut db,
        &page.resource,
        &filter,
        Some(&page.query),
        selected,
        confirm,
    )
    .await?;
    if records.rows.is_empty() {
        return Ok(access::found(page.target));
    }
    let rejection = records.rejection(&mut db, &page.resource).await?;
    let cascade = records.cascade(&mut db, &page.resource, confirm).await?;
    let permissions = cascade.unavailable_permissions(&mut db).await?;
    let pairings = CascadePairings::capture(
        &mut db,
        &cascade,
        page.resource.list.model.name,
        &records.rows,
    )
    .await?;
    let rejection = rejection.or_else(|| pairings.blocked().then_some(LATER_STAGE));
    let headers = page.headers;
    let preview = AdminFormProof::deletion(&page, &records, &cascade, &pairings);
    let stale = confirm
        && preview
            .as_ref()
            .is_some_and(|preview| !preview.verified(app, post.get("mittab_delete_scope")));
    if confirm {
        if !permissions.is_empty() {
            return Ok(private((
                StatusCode::FORBIDDEN,
                "You do not have permission to delete related records.",
            )));
        }
        if let Some(message) = rejection {
            return Ok(private((StatusCode::CONFLICT, message)));
        }
    }
    if confirm && !stale {
        let log = records.execute(&mut db, &page, cascade).await?;
        pairings.record(&mut db, page.user).await?;
        db.commit().await?;
        let notice = format!("bulk-{}", records.rows.len());
        let response =
            admin_html::redirect_with_notice(app, headers, page.target.to_owned(), log, &notice);
        return Ok(response);
    }
    let summary = if rejection.is_none() && permissions.is_empty() {
        records.summary(&mut db, &page, &cascade).await?
    } else {
        DeletionSummary {
            counts: Vec::new(),
            objects: Vec::new(),
        }
    };
    let protected = rejection.map(|message| format!("\t<li>{}</li>", escape(message)));
    db.commit().await?;
    let count = records.rows.len();
    let model = &page.resource.list.model;
    let name = if count == 1 {
        model.singular
    } else {
        model.plural
    };
    let can_delete = rejection.is_none() && permissions.is_empty();
    let (title, perms_lacking) = deletion_title(name, rejection, permissions);
    let delete_scope = preview
        .filter(|_| can_delete)
        .map(|preview| preview.token(app));
    let ctx = context! {
        delete_scope,
        title => if stale { "Review updated deletion" } else { &title }, media => admin_html::scripts(),
        protected, perms_lacking,
        objects_name => name,
        model_count => summary.counts,
        deletable_objects => [summary.objects.join("\n")],
        queryset => records.rows.iter().map(|(id, _)| context! {pk => id}).collect::<Vec<_>>(),
        action_checkbox_name => "_selected_action",
    };
    let response = page.markup("admin/delete_selected_confirmation.html", ctx);
    Ok(if stale {
        form_conflict(response)
    } else {
        response
    })
}

fn deletion_title(name: &str, rejection: Option<&str>, permissions: Vec<&str>) -> (String, String) {
    let title = if rejection.is_some() || !permissions.is_empty() {
        format!("Cannot delete {name}")
    } else {
        "Are you sure?".into()
    };
    let permissions = permissions
        .into_iter()
        .map(|item| format!("\t<li>{}</li>", escape(item)))
        .collect::<Vec<_>>()
        .join("\n");
    (title, permissions)
}

async fn history(app: &App, page: Page<'_>, id: i32) -> Result<Response, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    let Some(description) = page.resource.describe(&mut db, id).await? else {
        return Ok(access::found("/admin/"));
    };
    let model = &page.resource.list.model;
    let filter = "FROM django_admin_log l JOIN django_content_type t ON l.content_type_id = t.id
        JOIN auth_user u ON l.user_id = u.id
        WHERE t.app_label = ? AND t.model = ? AND l.object_id = ?";
    let object_id = id.to_string();
    let count: i64 = sqlx::query_scalar(AssertSqlSafe(format!("SELECT COUNT(*) {filter}")))
        .bind(model.application())
        .bind(model.name)
        .bind(&object_id)
        .fetch_one(&mut *db)
        .await?;
    let count = u32::try_from(count).unwrap_or(u32::MAX);
    let page_size = admin_changelist::PER_PAGE;
    let pages = count.div_ceil(page_size).max(1);
    let number = u32::try_from(page.query.requested())
        .ok()
        .filter(|page| (1..=pages).contains(page))
        .unwrap_or(pages);
    let log = sqlx::query_as::<_,(NaiveDateTime, String, String, String)>(AssertSqlSafe(format!(
        "SELECT l.action_time, u.username, TRIM(CONCAT(u.first_name, ' ', u.last_name)), l.change_message
         {filter} ORDER BY l.action_time, l.id LIMIT ? OFFSET ?"
    )))
    .bind(model.application()).bind(model.name).bind(object_id).bind(page_size).bind((number - 1) * page_size)
    .fetch_all(&mut *db).await?;
    let action_list: Vec<_> = log
        .into_iter()
        .map(|(date, user, name, message)| {
            context! {
                action_time => admin_html::django_datetime(date),
                user => context! { get_username => user, get_full_name => name },
                get_change_message => admin_html::admin_change_message(&message),
            }
        })
        .collect();
    let ctx = context! {
        title => format!("Change history: {description}"), object => description,
        object_url => format!("{}{id}/", page.model_url),
        module_name => admin_html::capitalized(model.plural),
        action_list, count, page => number, pages, pagination_required => pages > 1,
        page_range => (1..=pages).collect::<Vec<_>>(), page_var => "p",
    };
    Ok(page.markup("admin/object_history.html", ctx))
}
