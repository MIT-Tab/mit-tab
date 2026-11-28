use std::collections::{BTreeMap, BTreeSet};

use chrono::NaiveDateTime;
use minijinja::{Value, context};
use serde::Serialize;
use serde_json::json;
use sqlx::{AssertSqlSafe, MySqlConnection};

use crate::{
    admin_cascade::Cascade,
    admin_html::django_datetime,
    admin_model_data::{BoundField, Field, Kind, SCHOOL},
    admin_model_forms::{CascadePairings, error_list, render_widget},
    admin_repairs::LATER_STAGE,
    codes::{self, Credential},
    entity_store::delete_links,
    legacy_html::escape,
    registration_form::Fields,
    roster_log,
};

type InlineValues = [Option<String>; 4];

const TEAM: &[Field] = &[
    Field::new("name", "Name", Kind::Text(35)),
    Field::new(
        "seed",
        "Seed",
        Kind::Choice(&[
            (0, "Unseeded"),
            (1, "Free Seed"),
            (2, "Half Seed"),
            (3, "Full Seed"),
        ]),
    ),
    Field::relation("school", "school_id", "School", SCHOOL),
    Field::relation("hybrid_school", "hybrid_school_id", "Hybrid school", SCHOOL)
        .optional()
        .allow_related_delete(),
];
const JUDGE: &[Field] = &[
    Field::new("name", "Name", Kind::Text(30)),
    Field::new(
        "reported_experience",
        "Reported experience",
        Kind::Experience,
    )
    .optional(),
    Field::new("rank", "Rank", Kind::Decimal),
    Field::new("email", "Email", Kind::Email(254)).optional(),
];

#[derive(Clone, Copy)]
enum Member {
    Team,
    Judge,
}

impl Member {
    fn name(self) -> &'static str {
        match self {
            Self::Team => "team",
            Self::Judge => "judge",
        }
    }

    fn plural(self) -> &'static str {
        match self {
            Self::Team => "teams",
            Self::Judge => "judges",
        }
    }

    fn table(self) -> &'static str {
        match self {
            Self::Team => "tab_team",
            Self::Judge => "tab_judge",
        }
    }

    fn fields(self) -> &'static [Field] {
        match self {
            Self::Team => TEAM,
            Self::Judge => JUDGE,
        }
    }

    async fn read(
        self,
        db: &mut MySqlConnection,
        registration: Option<i32>,
        lock: bool,
    ) -> Result<Vec<Stored>, sqlx::Error> {
        let Some(registration) = registration else {
            return Ok(Vec::new());
        };
        let (columns, order) = match self {
            Self::Team => (
                "id, name, CAST(seed AS CHAR), CAST(school_id AS CHAR), CAST(hybrid_school_id AS CHAR)",
                "id",
            ),
            Self::Judge => (
                "id, name, CAST(reported_experience AS CHAR), CAST(`rank` AS CHAR), email",
                "name",
            ),
        };
        let lock = if lock { " FOR UPDATE" } else { "" };
        let rows =
            sqlx::query_as::<_, (i32, String, Option<String>, Option<String>, Option<String>)>(
                AssertSqlSafe(format!(
                    "SELECT {columns} FROM {} WHERE registration_id = ? ORDER BY {order}{lock}",
                    self.table()
                )),
            )
            .bind(registration)
            .fetch_all(db)
            .await?;
        Ok(rows
            .into_iter()
            .map(|(id, name, a, b, c)| Stored {
                id,
                values: [Some(name), a, b, c],
            })
            .collect())
    }

    async fn update(
        self,
        db: &mut MySqlConnection,
        parent: i32,
        id: Option<i32>,
        values: &InlineValues,
    ) -> Result<(), sqlx::Error> {
        let [name, first, second, third] = values;
        match (self, id) {
            (Self::Team, Some(id)) => {
                let coin = codes::coin(db, true).await?;
                let old_code: Option<String> =
                    sqlx::query_scalar("SELECT team_code FROM tab_team WHERE id = ?")
                        .bind(id)
                        .fetch_one(&mut *db)
                        .await?;
                let code = match old_code.filter(|code| !code.is_empty()) {
                    Some(code) => code,
                    None => codes::unique(db, Credential::Team).await?,
                };
                sqlx::query("UPDATE tab_team SET name = ?, seed = ?, school_id = ?,
                    hybrid_school_id = ?, tiebreaker = ?, team_code = ? WHERE id = ? AND registration_id = ?")
                    .bind(name).bind(first).bind(second).bind(third).bind(coin).bind(code)
                    .bind(id).bind(parent).execute(db).await?;
            }
            (Self::Team, None) => {
                let coin = codes::coin(db, true).await?;
                let code = codes::unique(db, Credential::Team).await?;
                sqlx::query("INSERT INTO tab_team (name, seed, school_id, hybrid_school_id, registration_id,
                    tiebreaker, team_code, checked_in, break_preference, ranking_public, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 1, UTC_TIMESTAMP(6))")
                    .bind(name).bind(first).bind(second).bind(third).bind(parent)
                    .bind(coin).bind(code).execute(db).await?;
            }
            (Self::Judge, Some(id)) => {
                let old_code: Option<String> =
                    sqlx::query_scalar("SELECT ballot_code FROM tab_judge WHERE id = ?")
                        .bind(id)
                        .fetch_one(&mut *db)
                        .await?;
                let code = match old_code.filter(|code| !code.is_empty()) {
                    Some(code) => code,
                    None => codes::unique(db, Credential::Judge).await?,
                };
                sqlx::query(
                    "UPDATE tab_judge SET name = ?, reported_experience = ?, `rank` = ?, email = ?,
                    ballot_code = ? WHERE id = ? AND registration_id = ?",
                )
                .bind(name)
                .bind(first)
                .bind(second)
                .bind(third)
                .bind(code)
                .bind(id)
                .bind(parent)
                .execute(db)
                .await?;
            }
            (Self::Judge, None) => {
                let code = codes::unique(db, Credential::Judge).await?;
                sqlx::query("INSERT INTO tab_judge (name, reported_experience, `rank`, email, registration_id, ballot_code,
                    needs_crash_housing, requests_wgm_housing, is_dino, wing_only, request_wing, no_wings, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, UTC_TIMESTAMP(6))")
                    .bind(name).bind(first).bind(second).bind(third).bind(parent).bind(code)
                    .execute(db).await?;
            }
        }
        Ok(())
    }
}

#[derive(Serialize)]
struct Stored {
    id: i32,
    values: InlineValues,
}

impl Stored {
    fn name(&self) -> &str {
        self.values[0].as_deref().unwrap_or_default()
    }
}

struct Row {
    id: Option<i32>,
    parent: Option<i32>,
    original: Option<String>,
    fields: Vec<BoundField>,
    delete: bool,
    error: Option<&'static str>,
    save: Option<InlineValues>,
}

struct Roster {
    kind: Member,
    stored: Vec<Stored>,
    rows: Vec<Row>,
    error: Option<&'static str>,
}

impl Roster {
    async fn load(
        db: &mut MySqlConnection,
        parent: Option<i32>,
        kind: Member,
        lock: bool,
    ) -> Result<Self, sqlx::Error> {
        Ok(Self {
            kind,
            stored: kind.read(db, parent, lock).await?,
            rows: Vec::new(),
            error: None,
        })
    }

    async fn bind(
        &mut self,
        post: Option<&Fields>,
        db: &mut MySqlConnection,
        parent: Option<i32>,
        schools: &[(i32, String)],
    ) -> Result<(), sqlx::Error> {
        let prefix = self.kind.plural();
        let initial = self.stored.len();
        let count = match post {
            None => initial,
            Some(post) => match (
                post.get(&format!("{prefix}-TOTAL_FORMS")).parse::<usize>(),
                post.get(&format!("{prefix}-INITIAL_FORMS"))
                    .parse::<usize>(),
            ) {
                (Ok(count), Ok(declared))
                    if declared == initial && (initial..=initial.max(1000)).contains(&count) =>
                {
                    count
                }
                _ => {
                    self.error = Some("Management form data is missing or has been tampered with.");
                    initial
                }
            },
        };
        let post = post.filter(|_| self.error.is_none());
        let mut existing = self
            .stored
            .iter()
            .map(|row| (row.id, row))
            .collect::<BTreeMap<_, _>>();
        let mut names = BTreeSet::new();
        for index in 0..count {
            let name = |field: &str| format!("{prefix}-{index}-{field}");
            let stored = match (index < initial, post) {
                (true, Some(post)) => post
                    .get(&name("id"))
                    .parse::<i32>()
                    .ok()
                    .and_then(|id| existing.remove(&id)),
                (true, None) => Some(&self.stored[index]),
                (false, _) => None,
            };
            if let Some(post) = post {
                let bad_id = if index < initial {
                    stored.is_none()
                } else {
                    !post.get(&name("id")).is_empty()
                };
                let supplied_parent = post.get(&name("registration"));
                if bad_id
                    || (!supplied_parent.is_empty()
                        && supplied_parent.parse::<i32>().ok() != parent)
                {
                    self.error = Some("The submitted rows do not match this registration.");
                }
            }
            let id = stored.map(|row| row.id);
            let original = stored.map(|row| row.name().to_owned());
            let mut fields = self.fields(schools, |position, field| {
                if let Some(post) = post {
                    post.all(&name(field)).last().cloned()
                } else {
                    stored.and_then(|row| row.values[position].clone())
                }
            });
            let delete = post.is_some_and(|post| post.flag(&name("DELETE")));
            let mut save = None;
            let mut error = None;
            if post.is_some()
                && !delete
                && (id.is_some()
                    || fields.iter().any(|field| {
                        field
                            .value
                            .as_ref()
                            .is_some_and(|text| !text.trim().is_empty())
                    }))
            {
                let values: InlineValues = std::array::from_fn(|index| fields[index].clean());
                if let Some(name) = &values[0] {
                    let sql = format!(
                        "SELECT EXISTS(SELECT 1 FROM {} WHERE name = ? AND (? IS NULL OR id <> ?))",
                        self.kind.table()
                    );
                    let duplicate: bool = sqlx::query_scalar(AssertSqlSafe(sql))
                        .bind(name)
                        .bind(id)
                        .bind(id)
                        .fetch_one(&mut *db)
                        .await?;
                    if duplicate {
                        let label = match self.kind {
                            Member::Team => "Team",
                            Member::Judge => "Judge",
                        };
                        fields[0].error = Some(format!("{label} with this Name already exists."));
                    } else if !names.insert(name.to_lowercase()) {
                        self.error = Some("Please correct the duplicate data for name.");
                        error = Some("Please correct the duplicate values below.");
                    }
                }
                if fields.iter().all(|field| field.error.is_none()) {
                    save = Some(values);
                }
            }
            self.rows.push(Row {
                id,
                parent: post.map_or(parent, |post| post.get(&name("registration")).parse().ok()),
                original,
                fields,
                delete,
                error,
                save,
            });
        }
        self.rows.push(Row {
            id: None,
            parent,
            original: None,
            fields: self.fields(schools, |_, _| None),
            delete: false,
            error: None,
            save: None,
        });
        Ok(())
    }

    fn fields(
        &self,
        schools: &[(i32, String)],
        value: impl Fn(usize, &str) -> Option<String>,
    ) -> Vec<BoundField> {
        self.kind
            .fields()
            .iter()
            .enumerate()
            .map(|(index, field)| {
                let value = value(index, field.name);
                let options = match field.kind {
                    Kind::Choice(choices) => choices
                        .iter()
                        .map(|&(id, text)| (id, text.to_owned()))
                        .collect(),
                    Kind::Relation(_) => schools.to_vec(),
                    _ => Vec::new(),
                };
                BoundField {
                    field,
                    selected: value.iter().cloned().collect(),
                    value,
                    initial_json: None,
                    options,
                    error: None,
                }
            })
            .collect()
    }

    fn errors(&self) -> usize {
        usize::from(self.error.is_some())
            + self
                .rows
                .iter()
                .map(|row| {
                    usize::from(row.error.is_some())
                        + row
                            .fields
                            .iter()
                            .filter(|field| field.error.is_some())
                            .count()
                })
                .sum::<usize>()
    }

    fn markup(&self) -> Result<Value, minijinja::Error> {
        let label = if matches!(self.kind, Member::Team) {
            "Team"
        } else {
            "Judge"
        };
        let plural = if matches!(self.kind, Member::Team) {
            "Teams"
        } else {
            "Judges"
        };
        let prefix = self.kind.plural();
        let columns = self.kind.fields().iter()
            .map(|field| context! {name => field.name, label => field.label, required => field.required})
            .collect();
        let count = self.rows.len().saturating_sub(1);
        let rows = self
            .rows
            .iter()
            .enumerate()
            .map(|(index, row)| {
                let index = if index == count {
                    "__prefix__".into()
                } else {
                    index.to_string()
                };
                let row_prefix = format!("{prefix}-{index}");
                let lines = row
                    .fields
                    .iter()
                    .map(|field| {
                        let name = field.field.name;
                        let widget = render_widget(field, &format!("{row_prefix}-{name}"), false)?;
                        let errors = field
                            .error
                            .as_deref()
                            .map_or_else(String::new, |error| error_list(error, false));
                        Ok(context! {fields => [context! {
                            field => context! { name }, widget, errors,
                        }]})
                    })
                    .collect::<Result<Vec<_>, minijinja::Error>>()?;
                Ok(inline_row(
                    row.parent,
                    &row_prefix,
                    row.id,
                    row.original.as_deref(),
                    row.delete,
                    row.error,
                    lines,
                ))
            })
            .collect::<Result<Vec<_>, minijinja::Error>>()?;
        Ok(formset(
            prefix,
            (label, plural),
            columns,
            rows,
            (count, self.stored.len()),
            true,
            self.error,
        ))
    }

    fn deleted(&self) -> Vec<i32> {
        self.rows
            .iter()
            .filter(|row| row.delete)
            .filter_map(|row| row.id)
            .collect()
    }

    async fn save(
        &self,
        db: &mut MySqlConnection,
        parent: i32,
        log_roster: bool,
        events: &mut Vec<serde_json::Value>,
    ) -> Result<(), sqlx::Error> {
        let model = self.kind.name();
        let plural = self.kind.plural();
        let stored: BTreeMap<_, _> = self.stored.iter().map(|row| (row.id, row)).collect();
        let mut additions = Vec::new();
        let mut edits = Vec::new();
        let mut removals = Vec::new();
        for row in &self.rows {
            let old = row.id.and_then(|id| stored.get(&id).copied());
            if row.delete {
                if let Some(old) = old {
                    if log_roster {
                        roster_log::record(
                            db,
                            plural,
                            &format!("Removed {model} “{}”.", old.name()),
                            false,
                        )
                        .await?;
                    }
                    removals.push(json!({"deleted": {"name": model, "object": old.name()}}));
                }
                continue;
            }
            let Some(values) = &row.save else {
                continue;
            };
            let name = values[0].as_deref().unwrap_or_default();
            if let Some(old) = old {
                let changes = self
                    .kind
                    .fields()
                    .iter()
                    .zip(old.values.iter().zip(values))
                    .filter_map(|(field, (before, after))| {
                        let equal = match field.kind {
                            Kind::Decimal => {
                                before.as_deref().and_then(|v| v.parse::<f64>().ok())
                                    == after.as_deref().and_then(|v| v.parse::<f64>().ok())
                            }
                            _ => before == after,
                        };
                        (!equal).then_some(field.label)
                    })
                    .collect::<Vec<_>>();
                if changes.is_empty() {
                    continue;
                }
                self.kind.update(db, parent, row.id, values).await?;
                if log_roster {
                    match self.kind {
                        Member::Team => {
                            for index in [2, 3] {
                                if old.values[index] != values[index] {
                                    let (before, after) = roster_log::affiliation_names(
                                        db,
                                        old.values[index].as_deref(),
                                        values[index].as_deref(),
                                    )
                                    .await?;
                                    roster_log::record(
                                        db,
                                        plural,
                                        &format!(
                                            "Changed affiliation for “{name}”: {before} → {after}."
                                        ),
                                        false,
                                    )
                                    .await?;
                                }
                            }
                        }
                        Member::Judge if old.name() != name => {
                            roster_log::record(
                                db,
                                plural,
                                &format!("Changed name for “{name}”: {} → {name}.", old.name()),
                                false,
                            )
                            .await?;
                        }
                        _ => {}
                    }
                }
                edits.push(json!({"changed": {"name": model, "object": name, "fields": changes}}));
            } else {
                self.kind.update(db, parent, None, values).await?;
                if log_roster {
                    roster_log::record(db, plural, &format!("Added {model} “{name}”."), false)
                        .await?;
                }
                additions.push(json!({"added": {"name": model, "object": name}}));
            }
        }
        events.extend(additions);
        events.extend(edits);
        events.extend(removals);
        Ok(())
    }
}

fn readonly_text(text: &str) -> String {
    escape(&text.replace("\r\n", "\n").replace('\r', "\n")).replace('\n', "<br>")
}

struct Readonly {
    prefix: &'static str,
    titles: (&'static str, &'static str),
    fields: &'static [(&'static str, &'static str)],
    rows: Vec<(i32, String, Vec<String>)>,
}

impl Readonly {
    fn markup(&self, parent: Option<i32>) -> Value {
        let columns = self
            .fields
            .iter()
            .map(|&(name, label)| context! {name, label})
            .collect();
        let rows = self
            .rows
            .iter()
            .enumerate()
            .map(|(index, (id, original, content))| {
                let lines = self.fields.iter().zip(content).map(|(&(name, _), contents)| context! {
                fields => [context! { field => context! { name }, is_readonly => true, contents }],
            }).collect();
                inline_row(
                    parent,
                    &format!("{}-{index}", self.prefix),
                    Some(*id),
                    Some(original),
                    false,
                    None,
                    lines,
                )
            })
            .collect();
        let count = self.rows.len();
        formset(
            self.prefix,
            self.titles,
            columns,
            rows,
            (count, count),
            false,
            None,
        )
    }
}

pub struct Inlines {
    teams: Roster,
    judges: Roster,
    email_logs: Readonly,
    change_logs: Readonly,
    parent: Option<i32>,
    pub token: Option<String>,
    pub removal_token: Option<String>,
    removal: Option<(Cascade, CascadePairings)>,
}

impl Inlines {
    pub async fn load(
        db: &mut MySqlConnection,
        parent: Option<i32>,
        lock: bool,
    ) -> Result<Self, sqlx::Error> {
        let teams = Roster::load(db, parent, Member::Team, lock).await?;
        let judges = Roster::load(db, parent, Member::Judge, lock).await?;
        let email_logs = Readonly {
            prefix: "confirmation_email_logs",
            titles: (
                "Registration confirmation email log",
                "Registration confirmation email logs",
            ),
            fields: &[
                ("sent_at", "Sent at"),
                ("email", "Email"),
                ("successful", "Successful"),
                ("error_message", "Error message"),
            ],
            rows: sqlx::query_as::<_, (i32, NaiveDateTime, String, bool, String)>(
                "SELECT id, sent_at, email, successful, error_message
                 FROM registration_registrationconfirmationemaillog WHERE registration_id = ?
                 ORDER BY sent_at DESC, id DESC",
            )
            .bind(parent)
            .fetch_all(&mut *db)
            .await?
            .into_iter()
            .map(|(id, time, email, success, error)| {
                let (status, icon, alt) = if success {
                    ("sent", "yes", "True")
                } else {
                    ("failed", "no", "False")
                };
                let icon = format!(r#"<img src="/static/admin/img/icon-{icon}.svg" alt="{alt}">"#);
                let label = format!("Registration confirmation {status}: {email}");
                (
                    id,
                    label,
                    vec![
                        django_datetime(time),
                        escape(&email),
                        icon,
                        readonly_text(&error),
                    ],
                )
            })
            .collect(),
        };
        let change_logs = Readonly {
            prefix: "change_logs",
            titles: ("Registration change log", "Registration change logs"),
            fields: &[("created_at", "Created at"), ("action", "Action"), ("summary", "Summary"),
                ("registration_code", "Registration code"), ("school_name", "School name"), ("email", "Email")],
            rows: sqlx::query_as::<_, (i32, NaiveDateTime, String, String, String, String, String)>(
                "SELECT id, created_at, action, summary, registration_code, school_name, email
                 FROM registration_registrationchangelog WHERE registration_id = ? ORDER BY created_at DESC, id DESC",
            ).bind(parent).fetch_all(&mut *db).await?.into_iter().map(|(id, time, action, summary, code, school, email)| {
                let action = match action.as_str() {
                    "created" => "Created", "updated" => "Updated", "deleted" => "Deleted", _ => &action,
                };
                (id, format!("{action} {code}"), vec![django_datetime(time), escape(action), readonly_text(&summary),
                    escape(&code), escape(&school), escape(&email)])
            }).collect(),
        };
        Ok(Self {
            teams,
            judges,
            email_logs,
            change_logs,
            parent,
            token: None,
            removal_token: None,
            removal: None,
        })
    }

    pub fn revision(&self) -> impl Serialize + '_ {
        (&self.teams.stored, &self.judges.stored)
    }

    pub fn requests_removal(&self, post: &Fields) -> bool {
        [&self.teams, &self.judges].iter().any(|roster| {
            let prefix = roster.kind.plural();
            (0..roster.stored.len()).any(|index| post.flag(&format!("{prefix}-{index}-DELETE")))
        })
    }

    pub async fn deletion_scope(
        &self,
        db: &mut MySqlConnection,
        lock: bool,
    ) -> Result<(Cascade, CascadePairings), sqlx::Error> {
        let teams: Vec<_> = self.teams.stored.iter().map(|row| row.id).collect();
        let judges: Vec<_> = self.judges.stored.iter().map(|row| row.id).collect();
        let cascade =
            Cascade::read_roots(db, &[("tab_team", &teams), ("tab_judge", &judges)], lock).await?;
        let pairings = CascadePairings::capture(db, &cascade, "registration", &[]).await?;
        Ok((cascade, pairings))
    }

    pub async fn bind(
        &mut self,
        post: Option<&Fields>,
        db: &mut MySqlConnection,
    ) -> Result<(), sqlx::Error> {
        let schools = SCHOOL.options(db).await?;
        self.teams.bind(post, db, self.parent, &schools).await?;
        self.judges.bind(post, db, self.parent, &schools).await?;
        let teams = self.teams.deleted();
        let judges = self.judges.deleted();
        if self.valid() && (!teams.is_empty() || !judges.is_empty()) {
            let cascade =
                Cascade::read_roots(db, &[("tab_team", &teams), ("tab_judge", &judges)], true)
                    .await?;
            let pairings = CascadePairings::capture(db, &cascade, "registration", &[]).await?;
            if !cascade.unavailable_permissions(db).await?.is_empty() {
                self.teams.error = Some("A related record requires separate deletion permission.");
            } else if pairings.blocked() {
                self.teams.error = Some(LATER_STAGE);
            } else {
                self.removal = Some((cascade, pairings));
            }
        }
        Ok(())
    }

    pub fn valid(&self) -> bool {
        self.error_count() == 0
    }

    pub fn error_count(&self) -> usize {
        self.teams.errors() + self.judges.errors()
    }

    pub fn markup(&self) -> Result<Vec<Value>, minijinja::Error> {
        Ok(vec![
            self.teams.markup()?,
            self.judges.markup()?,
            self.email_logs.markup(self.parent),
            self.change_logs.markup(self.parent),
        ])
    }

    pub async fn save(
        &mut self,
        db: &mut MySqlConnection,
        parent: i32,
        user: i32,
    ) -> Result<Vec<serde_json::Value>, sqlx::Error> {
        let log_roster = roster_log::enabled(db).await?;
        let mut events = Vec::new();
        self.teams.save(db, parent, log_roster, &mut events).await?;
        self.judges
            .save(db, parent, log_roster, &mut events)
            .await?;
        if let Some((cascade, pairings)) = self.removal.take() {
            cascade.delete(db).await?;
            delete_links(db, &self.teams.deleted(), &[("tab_team", "id")]).await?;
            delete_links(db, &self.judges.deleted(), &[("tab_judge", "id")]).await?;
            pairings.record(db, user).await?;
        }
        Ok(events)
    }
}

pub(super) async fn record_cascade_removals(
    db: &mut MySqlConnection,
    cascade: &Cascade,
) -> Result<(), sqlx::Error> {
    if !roster_log::enabled(db).await? {
        return Ok(());
    }
    for kind in [Member::Team, Member::Judge] {
        let sql = format!("SELECT name FROM {} WHERE id = ?", kind.table());
        for id in cascade.ids(kind.table()) {
            let name: String = sqlx::query_scalar(AssertSqlSafe(sql.as_str()))
                .bind(id)
                .fetch_one(&mut *db)
                .await?;
            roster_log::record(
                db,
                kind.plural(),
                &format!("Removed {} “{name}”.", kind.name()),
                false,
            )
            .await?;
        }
    }
    Ok(())
}

fn hidden(name: &str, value: Option<impl std::fmt::Display>) -> String {
    let value = value.map_or_else(String::new, |value| format!(r#" value="{value}""#));
    format!(r#"<input type="hidden" name="{name}"{value} id="id_{name}">"#)
}

fn inline_row(
    parent: Option<i32>,
    prefix: &str,
    id: Option<i32>,
    original: Option<&str>,
    delete: bool,
    error: Option<&str>,
    lines: Vec<Value>,
) -> Value {
    let checked = if delete { " checked" } else { "" };
    let field = format!(
        r#"<input type="checkbox" name="{prefix}-DELETE" id="id_{prefix}-DELETE"{checked}>"#
    );
    context! {
        original,
        form => context! { non_field_errors => error.map(|message| error_list(message, false)) },
        needs_explicit_pk_field => true,
        pk_field => context! { field => hidden(&format!("{prefix}-id"), id) },
        fk_field => context! { field => hidden(&format!("{prefix}-registration"), parent) },
        deletion_field => context! { field },
        fieldsets => [context! { lines }],
    }
}

fn formset(
    prefix: &str,
    (label, plural): (&str, &str),
    fields: Vec<Value>,
    rows: Vec<Value>,
    (total, initial): (usize, usize),
    editable: bool,
    error: Option<&str>,
) -> Value {
    let max = if editable { 1000 } else { 0 };
    let management_form = [
        ("TOTAL_FORMS", total),
        ("INITIAL_FORMS", initial),
        ("MIN_NUM_FORMS", 0),
        ("MAX_NUM_FORMS", max),
    ]
    .map(|(name, value)| hidden(&format!("{prefix}-{name}"), Some(value)))
    .join("");
    let inline_formset_data = format!(
        r##"{{"name": "#{prefix}", "options": {{"prefix": "{prefix}", "addText": "Add another {label}", "deleteText": "Remove"}}}}"##
    );
    context! {
        classes => "",
        opts => context! { template => "admin/edit_inline/tabular.html", verbose_name => label, verbose_name_plural => plural },
        formset => context! {
            prefix, management_form, max_num => max, can_delete => editable,
            non_form_errors => error.map_or_else(String::new, |message| {
                format!("<ul class=\"errorlist nonform\"><li>{}</li></ul>", escape(message))
            }),
        },
        inline_formset_data,
        has_add_permission => editable, has_delete_permission => editable,
        cell_count => fields.len() + 1 + usize::from(editable),
        fields, rows,
    }
}
