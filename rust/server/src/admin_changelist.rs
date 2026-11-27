use std::collections::BTreeMap;

use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use minijinja::{Value, context};
use sqlx::{MySql, QueryBuilder};

use crate::{App, access, admin_html, legacy_html, legacy_session::Actor, private, unavailable};

pub const PER_PAGE: u32 = 100;
const MAX_SHOW_ALL: u32 = 200;

pub struct Model {
    pub name: &'static str,
    pub singular: &'static str,
    pub plural: &'static str,
}

pub const SCORES: Model = Model {
    name: "roundstats",
    singular: "round stats",
    plural: "round stats",
};

impl Model {
    pub fn url(&self) -> String {
        format!("/admin/{}/{}/", self.application(), self.name)
    }

    pub fn application(&self) -> &'static str {
        application(self.name)
    }

    pub fn opts(&self) -> Value {
        let app = self.application();
        context! {
            app_label => app,
            model_name => self.name,
            verbose_name => self.singular,
            verbose_name_plural => self.plural,
            app_config => context! { verbose_name => match app {
                "auth" => "Authentication and Authorization",
                "registration" => "Registration",
                _ => "MIT Tab",
            } },
        }
    }
}

pub(super) fn application(model: &str) -> &'static str {
    match model {
        "user" | "group" => "auth",
        "registration"
        | "registrationconfig"
        | "registrationchangelog"
        | "registrationconfirmationemaillog"
        | "infolink"
        | "infotext"
        | "registrationlink" => "registration",
        _ => "tab",
    }
}

pub(super) struct DefaultList {
    pub model: Model,
    pub source: &'static str,
    pub label: &'static str,
    pub ordering: &'static str,
}

impl DefaultList {
    pub fn table(&self) -> &'static str {
        self.source
            .split_once(' ')
            .map_or(self.source, |(table, _)| table)
    }
}

macro_rules! motion_round_display {
    () => {
        "CASE
            WHEN t.round_number IS NOT NULL THEN CONCAT('Round ', t.round_number)
            WHEN t.num_teams IS NULL THEN 'Unknown Round'
            ELSE CONCAT(IF(t.outround_type = 0, 'Varsity ', 'Novice '),
                CASE t.num_teams
                    WHEN 2 THEN 'Finals' WHEN 4 THEN 'Semifinals' WHEN 8 THEN 'Quarterfinals'
                    WHEN 16 THEN 'Octofinals' WHEN 32 THEN 'Double Octofinals'
                    WHEN 64 THEN 'Triple Octofinals' ELSE CONCAT('Round of ', t.num_teams)
                END)
            END"
    };
}

pub(super) const MOTION_ROUND_DISPLAY: &str = motion_round_display!();

pub(super) fn default_list(name: &str) -> Option<DefaultList> {
    let (name, singular, plural, source, label, ordering) = match name {
        "registration" => (
            "registration",
            "registration",
            "registrations",
            "registration_registration t JOIN tab_school s ON s.id = t.school_id",
            "CONCAT(s.name, ' — ', t.email)",
            "t.id DESC",
        ),
        "registrationconfig" => (
            "registrationconfig",
            "registration config",
            "registration configs",
            "registration_registrationconfig t",
            "CONCAT('RegistrationConfig object (', t.id, ')')",
            "t.id DESC",
        ),
        "registrationchangelog" => (
            "registrationchangelog",
            "registration change log",
            "registration change logs",
            "registration_registrationchangelog t",
            "CONCAT(CASE t.action WHEN 'created' THEN 'Created' WHEN 'updated' THEN 'Updated'
                WHEN 'deleted' THEN 'Deleted' ELSE t.action END, ' ', t.registration_code)",
            "t.created_at DESC, t.id DESC",
        ),
        "registrationconfirmationemaillog" => (
            "registrationconfirmationemaillog",
            "registration confirmation email log",
            "registration confirmation email logs",
            "registration_registrationconfirmationemaillog t
                JOIN registration_registration r ON t.registration_id = r.id JOIN tab_school s ON r.school_id = s.id",
            "CONCAT('Registration confirmation ', IF(t.successful, 'sent', 'failed'), ': ', t.email)",
            "t.sent_at DESC, t.id DESC",
        ),
        "motion" => (
            "motion", "motion", "motions", "tab_motion t",
            concat!("CONCAT('Motion for ', ", motion_round_display!(), ", ': ', LEFT(t.motion_text, 50), '...')"),
            "t.round_number, t.outround_type, t.num_teams DESC, t.id DESC",
        ),
        "infolink" => (
            "infolink", "info link", "info links", "registration_infolink t",
            "t.title", "t.display_order, t.created_at, t.id DESC",
        ),
        "infotext" => (
            "infotext", "info text", "info texts", "registration_infotext t",
            "t.title", "t.display_order, t.created_at, t.id DESC",
        ),
        "registrationlink" => (
            "registrationlink", "registration link", "registration links", "registration_registrationlink t",
            "t.title", "t.display_order, t.created_at, t.id DESC",
        ),
        "school" => (
            "school",
            "school",
            "schools",
            "tab_school t",
            "t.name",
            "t.name, t.id DESC",
        ),
        "debater" => (
            "debater",
            "debater",
            "debaters",
            "tab_debater t",
            "t.name",
            "t.name, t.id DESC",
        ),
        "team" => ("team", "team", "teams", "tab_team t", "t.name", "t.id"),
        "judge" => (
            "judge",
            "judge",
            "judges",
            "tab_judge t",
            "t.name",
            "t.name, t.id DESC",
        ),
        "room" => (
            "room",
            "room",
            "rooms",
            "tab_room t",
            "t.name",
            "t.name, t.id DESC",
        ),
        "roomtag" => (
            "roomtag",
            "room tag",
            "room tags",
            "tab_roomtag t",
            "t.tag",
            "t.id DESC",
        ),
        "rankinggroup" => (
            "rankinggroup",
            "ranking group",
            "ranking groups",
            "tab_rankinggroup t",
            "t.name",
            "t.id DESC",
        ),
        "roundstats" => (
            SCORES.name,
            SCORES.singular,
            SCORES.plural,
            "tab_roundstats t JOIN tab_debater d ON d.id = t.debater_id JOIN tab_round r ON r.id = t.round_id",
            "CONCAT('Results for ', d.name, ' in round ', r.round_number)",
            "t.id DESC",
        ),
        "round" => (
            "round",
            "round",
            "rounds",
            "tab_round t JOIN tab_team g ON t.gov_team_id = g.id JOIN tab_team o ON t.opp_team_id = o.id",
            "CONCAT('Round ', t.round_number, ' between ', g.name, ' and ', o.name)",
            "t.id DESC",
        ),
        "outround" => (
            "outround",
            "outround",
            "outrounds",
            "tab_outround t JOIN tab_team g ON t.gov_team_id = g.id JOIN tab_team o ON t.opp_team_id = o.id",
            "CONCAT('Outround ', t.num_teams, ' between ', g.name, ' and ', o.name)",
            "t.id DESC",
        ),
        "scratch" => (
            "scratch",
            "scratch",
            "scratches",
            "tab_scratch t JOIN tab_team team ON t.team_id = team.id JOIN tab_judge j ON t.judge_id = j.id",
            "CONCAT(team.name, IF(t.scratch_type = 0, ' <=Team=> ', ' <=Tab=> '), j.name)",
            "t.id DESC",
        ),
        "checkin" => (
            "checkin",
            "check in",
            "check ins",
            "tab_checkin t JOIN tab_judge j ON t.judge_id = j.id",
            "CONCAT('Judge ', j.name, ' is checked in for round ', t.round_number)",
            "t.id DESC",
        ),
        "roomcheckin" => (
            "roomcheckin",
            "room check in",
            "room check ins",
            "tab_roomcheckin t JOIN tab_room r ON t.room_id = r.id",
            "CONCAT('Room ', r.name, ' is checked in for round ', t.round_number)",
            "t.id DESC",
        ),
        "bye" => (
            "bye",
            "bye",
            "byes",
            "tab_bye t JOIN tab_team team ON t.bye_team_id = team.id",
            "CONCAT('Bye in round ', t.round_number, ' for ', team.name)",
            "t.id DESC",
        ),
        "noshow" => (
            "noshow",
            "no show",
            "no shows",
            "tab_noshow t JOIN tab_team team ON t.no_show_team_id = team.id",
            "CONCAT(team.name, ' was no-show for round ', t.round_number)",
            "t.id DESC",
        ),
        "breakingteam" => (
            "breakingteam",
            "breaking team",
            "breaking teams",
            "tab_breakingteam t",
            "CONCAT('BreakingTeam object (', t.id, ')')",
            "t.id DESC",
        ),
        "tabsettings" => (
            "tabsettings",
            "tab settings",
            "tab settings",
            "tab_tabsettings t",
            "CONCAT(t.`key`, ' => ', COALESCE(t.value_string, CAST(t.value AS CHAR), 'None'))",
            "t.id DESC",
        ),
        "usertournamentsetuppreference" => (
            "usertournamentsetuppreference",
            "tournament setup preference",
            "tournament setup preferences",
            "tab_usertournamentsetuppreference t JOIN auth_user u ON t.user_id = u.id",
            "CONCAT(u.username, ' hide checklist: ', IF(t.hide_tournament_todo, 'True', 'False'))",
            "t.id DESC",
        ),
        "judgecodeemaillog" => (
            "judgecodeemaillog",
            "judge code email log",
            "judge code email logs",
            "tab_judgecodeemaillog t",
            "CONCAT('JudgeCodeEmailLog object (', t.id, ')')",
            "t.id DESC",
        ),
        "writtenrfdemaillog" => (
            "writtenrfdemaillog",
            "written rfd email log",
            "written rfd email logs",
            "tab_writtenrfdemaillog t",
            "CONCAT('WrittenRFDEmailLog object (', t.id, ')')",
            "t.id DESC",
        ),
        "outroundchallenge" => (
            "outroundchallenge",
            "outround challenge",
            "outround challenges",
            "tab_outroundchallenge t JOIN tab_team challenger ON t.challenger_id = challenger.id
             JOIN tab_team challenged ON t.challenged_id = challenged.id",
            "CONCAT(IF(t.type_of_round = 0, 'Varsity', 'Novice'), ' Ro', t.num_teams, ': ', challenger.name, ' challenged ', challenged.name)",
            "t.id DESC",
        ),
        _ => return None,
    };
    Some(DefaultList {
        model: Model {
            name,
            singular,
            plural,
        },
        source,
        label,
        ordering,
    })
}

pub struct Query {
    raw: String,
    pub params: BTreeMap<String, String>,
    page: i64,
}

impl Query {
    pub fn new(raw: Option<String>) -> Self {
        let raw = raw.unwrap_or_default();
        let mut params: BTreeMap<_, _> = url::form_urlencoded::parse(raw.as_bytes())
            .into_owned()
            .collect();
        let page = params
            .remove("p")
            .and_then(|page| page.trim().parse().ok())
            .unwrap_or(1);
        params.remove("e");
        Self { raw, params, page }
    }

    pub fn request_url(&self, path: &str) -> String {
        if self.raw.is_empty() {
            path.into()
        } else {
            format!("{path}?{}", self.raw)
        }
    }

    pub fn requested(&self) -> i64 {
        self.page
    }

    pub fn get(&self, field: &str) -> Option<&str> {
        self.params.get(field).map(String::as_str)
    }

    pub fn record_ids(&self, filters: &[&str]) -> Option<Vec<i32>> {
        let mut ids = Vec::new();
        for (key, value) in &self.params {
            match key.as_str() {
                "id" | "id__exact" | "pk" | "pk__exact" => ids.push(value.trim().parse().ok()?),
                "all" | "o" | "q" | "_popup" | "_to_field" => {}
                filter if filters.contains(&filter) => {}
                _ => return None,
            }
        }
        Some(ids)
    }

    pub fn ordered_params(&self) -> Vec<(String, &str)> {
        let mut seen = std::collections::HashSet::new();
        url::form_urlencoded::parse(self.raw.as_bytes())
            .filter_map(|(key, _)| {
                let value = self.get(&key)?;
                seen.insert(key.to_string())
                    .then(|| (key.into_owned(), value))
            })
            .collect()
    }

    pub fn suffix(&self) -> String {
        let mut serializer = url::form_urlencoded::Serializer::new(String::new());
        for (key, value) in url::form_urlencoded::parse(self.raw.as_bytes()) {
            serializer.append_pair(&key, &value);
        }
        admin_html::preserved_filters(&serializer.finish())
    }

    pub(super) fn add_suffix(&self) -> String {
        let mut suffix = self.suffix();
        if self.get("_popup").is_some() {
            suffix.push_str("&_popup=1");
        }
        if let Some(field) = self.get("_to_field").filter(|field| !field.is_empty()) {
            suffix.push_str("&_to_field=");
            suffix.push_str(field);
        }
        suffix
    }

    pub fn url(&self, key: &str, value: &str) -> String {
        let mut params = self.params.clone();
        params.insert(key.to_owned(), value.to_owned());
        Self::encode(params)
    }

    pub fn encode(params: BTreeMap<String, String>) -> String {
        format!(
            "?{}",
            url::form_urlencoded::Serializer::new(String::new())
                .extend_pairs(params)
                .finish()
        )
    }
}

pub fn filter_ids(statement: &mut QueryBuilder<MySql>, ids: &[i32]) {
    for id in ids {
        statement.push(" AND t.id = ").push_bind(*id);
    }
}

pub struct Page {
    pub number: u32,
    pub count: u32,
    total_pages: u32,
    show_all: bool,
}

impl Page {
    pub fn new(query: &Query, count: i64) -> Option<Self> {
        let count = u32::try_from(count).unwrap_or(u32::MAX);
        let total_pages = count.div_ceil(PER_PAGE).max(1);
        let show_all = query.get("all").is_some() && count <= MAX_SHOW_ALL;
        let number = if show_all || total_pages == 1 {
            1
        } else {
            u32::try_from(query.page)
                .ok()
                .filter(|number| (1..=total_pages).contains(number))?
        };
        Some(Self {
            number,
            count,
            total_pages,
            show_all,
        })
    }

    pub fn window(&self) -> (u32, u32) {
        if self.show_all {
            (self.count, 0)
        } else {
            (PER_PAGE, (self.number - 1) * PER_PAGE)
        }
    }

    pub fn context(&self, query: &Query) -> Value {
        context! {
            pagination_required => !self.show_all && self.total_pages > 1,
            page_range => admin_html::paginator_links(self.number, self.total_pages, |number| query.url("p", &number.to_string())),
            show_all_url => (!self.show_all && self.total_pages > 1 && self.count <= MAX_SHOW_ALL)
                .then(|| query.url("all", "")),
        }
    }
}

pub async fn get(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    application: &str,
    model: &str,
    raw: Option<String>,
) -> Option<Response> {
    if let Some(kind) = crate::admin_model_list::ListKind::find(model)
        && application == self::application(model)
    {
        return Some(
            crate::admin_model_list::list(app, actor, headers, kind, Query::new(raw), None).await,
        );
    }
    if application != "tab" {
        return None;
    }
    let config = default_list(model)?;
    Some(default_page(app, actor, headers, config, Query::new(raw)).await)
}

async fn default_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    config: DefaultList,
    query: Query,
) -> Response {
    let model = &config.model;
    let path = model.url();
    let shell = match admin_html::Shell::load(app, actor, headers, &path).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    let result = async {
        let mut db = app.db.begin().await?;
        if !admin_html::model_enabled(&mut db, "tab", model.name).await? {
            return Ok(private((StatusCode::FORBIDDEN, "Access denied.")));
        }
        let Some(ids) = query.record_ids(&[]) else {
            return Ok(access::found(format!("{path}?e=1")));
        };
        let table = config.table();
        let mut total = QueryBuilder::new(format!("SELECT COUNT(*) FROM {table} t WHERE TRUE"));
        filter_ids(&mut total, &ids);
        let count = total
            .build_query_scalar::<i64>()
            .fetch_one(&mut *db)
            .await?;
        let has_actions = if ids.is_empty() {
            count > 0
        } else {
            QueryBuilder::<MySql>::new(format!("SELECT EXISTS(SELECT 1 FROM {table})"))
                .build_query_scalar::<bool>()
                .fetch_one(&mut *db)
                .await?
        };
        let Some(page) = Page::new(&query, count) else {
            return Ok(access::found(format!("{path}?e=1")));
        };
        if query
            .get("_to_field")
            .is_some_and(|name| !matches!(name, "" | "id"))
        {
            return Ok(private((
                StatusCode::BAD_REQUEST,
                "Invalid related object field.",
            )));
        }
        let order = if query.get("o").is_some() {
            "t.id DESC"
        } else {
            config.ordering
        };
        let (limit, offset) = page.window();
        let mut selection = QueryBuilder::new(format!(
            "SELECT t.id, {} FROM {} WHERE TRUE",
            config.label, config.source,
        ));
        filter_ids(&mut selection, &ids);
        selection
            .push(" ORDER BY ")
            .push(order)
            .push(" LIMIT ")
            .push_bind(limit)
            .push(" OFFSET ")
            .push_bind(offset);
        let rows = selection
            .build_query_as::<(i32, String)>()
            .fetch_all(&mut *db)
            .await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(render_default(
            shell,
            headers,
            model,
            &query,
            &page,
            rows,
            has_actions,
        ))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

fn render_default(
    shell: admin_html::Shell,
    headers: &HeaderMap,
    model: &Model,
    query: &Query,
    page: &Page,
    rows: Vec<(i32, String)>,
    has_actions: bool,
) -> Response {
    let path = model.url();
    let popup = query.get("_popup").is_some();
    let suffix = query.suffix();
    let opts = model.opts();
    let count = page.count;
    let title = if popup {
        format!("Select {}", model.singular)
    } else {
        format!("Select {} to change", model.singular)
    };
    let mut name = model.singular.chars();
    let label = match name.next() {
        Some(first) => format!("{}{}", first.to_uppercase(), name.as_str()),
        None => String::new(),
    };
    let mut headers_context = Vec::new();
    if !popup {
        headers_context.push(context! {
            class_attrib => r#" class="action-checkbox-column""#,
            text => r#"<input type="checkbox" id="action-toggle">"#,
        });
    }
    headers_context.push(context! { class_attrib => r#" class="column-__str__""#, text => label });
    let results: Vec<_> = rows.iter().map(|(id, label)| {
        let opener = if popup { format!(r#" data-popup-opener="{id}""#) } else { String::new() };
        let mut cells = Vec::new();
        if !popup {
            cells.push(format!(r#"<td class="action-checkbox"><input type="checkbox" name="_selected_action" value="{id}" class="action-select"></td>"#));
        }
        cells.push(format!(r#"<th class="field-__str__"><a href="{path}{id}/change/{}"{opener}>{}</a></th>"#,
            legacy_html::escape(&suffix), legacy_html::escape(label)));
        cells
    }).collect();
    let initial = format!(
        r#"<select name="action" required>
  <option value="" selected>---------</option>

  <option value="delete_selected">Delete selected {}</option>

</select>"#,
        model.plural
    );
    let pager = page.context(query);
    let body = context! {
        title, opts => &opts, is_popup => popup,
        cl => context! { opts => &opts, show_admin_actions => has_actions, result_count => count, result_list => &rows },
        app_url => "/admin/tab/",
        model_url => path,
        filter_suffix => query.add_suffix(),
        module_name => model.plural,
        has_add_permission => true,
        actions_on_top => true,
        action_form => !popup,
        action_fields => [
            context! { label => "Action:", widget => initial },
            context! { widget => r#"<input type="hidden" name="select_across" value="0" class="select-across">"# },
        ],
        actions_selection_counter => true,
        selection_note => format!("0 of {} selected", rows.len()),
        selection_note_all => format!("All {count} selected"),
        search_form => "\n\n",
        media => context! { js => admin_html::scripts() },
        result_headers => headers_context,
        results,
        ..pager,
    };
    shell.render("admin/change_list.html", body, headers)
}
