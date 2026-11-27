use std::collections::{BTreeMap, BTreeSet};

use sqlx::{MySqlConnection, QueryBuilder};

use crate::{
    admin_changelist, admin_html, admin_model_data::email_log_revision, entity_store::delete_links,
    legacy_html::escape,
};

#[derive(Clone, Copy)]
enum Model {
    Registered(&'static str),
    JudgeExpectedCheckIn,
    LogEntry,
    Other {
        table: &'static str,
        class: &'static str,
        singular: &'static str,
        plural: &'static str,
    },
}

impl Model {
    const fn other(
        table: &'static str,
        class: &'static str,
        singular: &'static str,
        plural: &'static str,
    ) -> Self {
        Self::Other {
            table,
            class,
            singular,
            plural,
        }
    }

    fn description(self) -> Description {
        match self {
            Model::Registered(name) => {
                let list =
                    admin_changelist::default_list(name).expect("cascade model with admin link");
                Description {
                    table: list.table(),
                    source: list.source.into(),
                    label: list.label.into(),
                    singular: list.model.singular,
                    plural: list.model.plural,
                    url: Some(list.model.url()),
                }
            }
            Model::JudgeExpectedCheckIn => Description {
                table: "tab_judgeexpectedcheckin",
                source: "tab_judgeexpectedcheckin t JOIN tab_judge j ON j.id = t.judge_id".into(),
                label: "CONCAT('Judge ', j.name, ' is expected for round ', t.round_number)".into(),
                singular: "judge expected check in",
                plural: "judge expected check ins",
                url: None,
            },
            Model::LogEntry => Description {
                table: "django_admin_log",
                source: "django_admin_log t".into(),
                label: "CAST(JSON_ARRAY(t.action_flag, t.object_repr, t.change_message) AS CHAR)"
                    .into(),
                singular: "log entry",
                plural: "log entries",
                url: None,
            },
            Model::Other {
                table,
                class,
                singular,
                plural,
            } => Description {
                table,
                source: format!("{table} t"),
                label: format!("CONCAT('{class} object (', t.id, ')')"),
                singular,
                plural,
                url: None,
            },
        }
    }
}

struct Description {
    table: &'static str,
    source: String,
    label: String,
    singular: &'static str,
    plural: &'static str,
    url: Option<String>,
}

use Model::Registered;

const ROUND_JUDGES: Model = Model::other(
    "tab_round_judges",
    "Round_judges",
    "round-judge relationship",
    "round-judge relationships",
);
const OUTROUND_JUDGES: Model = Model::other(
    "tab_outround_judges",
    "Outround_judges",
    "outround-judge relationship",
    "outround-judge relationships",
);
const MANUAL_ASSIGNMENT: Model = Model::other(
    "tab_manualjudgeassignment",
    "ManualJudgeAssignment",
    "manual judge assignment",
    "manual judge assignments",
);

fn children(table: &str) -> &'static [(Model, &'static str)] {
    match table {
        "registration_registration" => &[
            (Registered("team"), "registration_id"),
            (Registered("judge"), "registration_id"),
            (
                Registered("registrationconfirmationemaillog"),
                "registration_id",
            ),
        ],
        "auth_user" => {
            const {
                &[
                    (Model::LogEntry, "user_id"),
                    (
                        Model::other(
                            "auth_user_groups",
                            "User_groups",
                            "user-group relationship",
                            "user-group relationships",
                        ),
                        "user_id",
                    ),
                    (
                        Model::other(
                            "auth_user_user_permissions",
                            "User_user_permissions",
                            "user-permission relationship",
                            "user-permission relationships",
                        ),
                        "user_id",
                    ),
                    (Registered("usertournamentsetuppreference"), "user_id"),
                ]
            }
        }
        "auth_group" => {
            const {
                &[
                    (
                        Model::other(
                            "auth_group_permissions",
                            "Group_permissions",
                            "group-permission relationship",
                            "group-permission relationships",
                        ),
                        "group_id",
                    ),
                    (
                        Model::other(
                            "auth_user_groups",
                            "User_groups",
                            "user-group relationship",
                            "user-group relationships",
                        ),
                        "group_id",
                    ),
                ]
            }
        }
        "tab_team" => {
            const {
                &[
                    (
                        Model::other(
                            "tab_team_debaters",
                            "Team_debaters",
                            "team-debater relationship",
                            "team-debater relationships",
                        ),
                        "team_id",
                    ),
                    (
                        Model::other(
                            "tab_team_required_room_tags",
                            "Team_required_room_tags",
                            "team-roomtag relationship",
                            "team-roomtag relationships",
                        ),
                        "team_id",
                    ),
                    (Registered("breakingteam"), "team_id"),
                    (
                        Model::other(
                            "tab_teamcodeemaillog",
                            "TeamCodeEmailLog",
                            "team code email log",
                            "team code email logs",
                        ),
                        "team_id",
                    ),
                    (Registered("scratch"), "team_id"),
                    (Registered("outround"), "gov_team_id"),
                    (Registered("outround"), "opp_team_id"),
                    (
                        Model::other(
                            "tab_publishedteamplacement",
                            "PublishedTeamPlacement",
                            "published team placement",
                            "published team placements",
                        ),
                        "team_id",
                    ),
                    (Registered("outroundchallenge"), "challenger_id"),
                    (Registered("outroundchallenge"), "challenged_id"),
                    (Registered("round"), "gov_team_id"),
                    (Registered("round"), "opp_team_id"),
                    (Registered("bye"), "bye_team_id"),
                    (Registered("noshow"), "no_show_team_id"),
                    (
                        Model::other(
                            "tab_rankinggroup_teams",
                            "RankingGroup_teams",
                            "rankinggroup-team relationship",
                            "rankinggroup-team relationships",
                        ),
                        "team_id",
                    ),
                ]
            }
        }
        "tab_judge" => {
            const {
                &[
                    (
                        Model::other(
                            "tab_judge_schools",
                            "Judge_schools",
                            "judge-school relationship",
                            "judge-school relationships",
                        ),
                        "judge_id",
                    ),
                    (
                        Model::other(
                            "tab_judge_required_room_tags",
                            "Judge_required_room_tags",
                            "judge-roomtag relationship",
                            "judge-roomtag relationships",
                        ),
                        "judge_id",
                    ),
                    (Registered("judgecodeemaillog"), "judge_id"),
                    (Registered("scratch"), "judge_id"),
                    (OUTROUND_JUDGES, "judge_id"),
                    (Registered("outround"), "chair_id"),
                    (ROUND_JUDGES, "judge_id"),
                    (Registered("round"), "chair_id"),
                    (MANUAL_ASSIGNMENT, "judge_id"),
                    (Registered("checkin"), "judge_id"),
                    (Model::JudgeExpectedCheckIn, "judge_id"),
                ]
            }
        }
        "tab_round" => &[
            (Registered("writtenrfdemaillog"), "round_id"),
            (ROUND_JUDGES, "round_id"),
            (MANUAL_ASSIGNMENT, "round_id"),
            (Registered("roundstats"), "round_id"),
        ],
        "tab_room" => {
            const {
                &[
                    (
                        Model::other(
                            "tab_room_tags",
                            "Room_tags",
                            "room-roomtag relationship",
                            "room-roomtag relationships",
                        ),
                        "room_id",
                    ),
                    (Registered("outround"), "room_id"),
                    (Registered("roomcheckin"), "room_id"),
                ]
            }
        }
        "tab_outround" => &[(OUTROUND_JUDGES, "outround_id")],
        _ => &[],
    }
}

type Key = (&'static str, i32);

struct RelatedModel {
    table: &'static str,
    plural: &'static str,
    ids: BTreeSet<i32>,
    depth: usize,
}

#[derive(Default)]
pub struct Cascade {
    related: Vec<RelatedModel>,
    objects: BTreeMap<Key, String>,
    edges: BTreeMap<Key, Vec<Key>>,
    registration_log_links: Vec<(i32, i32)>,
    email_log_revisions: BTreeMap<Key, String>,
}

impl Cascade {
    pub async fn read(
        db: &mut MySqlConnection,
        table: &'static str,
        ids: &[i32],
        locked: bool,
    ) -> Result<Self, sqlx::Error> {
        Self::read_roots(db, &[(table, ids)], locked).await
    }

    pub async fn read_roots(
        db: &mut MySqlConnection,
        roots: &[(&'static str, &[i32])],
        locked: bool,
    ) -> Result<Self, sqlx::Error> {
        let mut result = Self::default();
        for &(table, ids) in roots {
            result.collect(db, table, ids, locked, 1).await?;
            if table == "registration_registration" && !ids.is_empty() {
                let mut sql = QueryBuilder::new(
                    "SELECT id, registration_id FROM registration_registrationchangelog WHERE registration_id IN (",
                );
                let mut selected = sql.separated(", ");
                for id in ids {
                    selected.push_bind(*id);
                }
                sql.push(") ORDER BY id");
                if locked {
                    sql.push(" FOR UPDATE");
                }
                result.registration_log_links = sql.build_query_as().fetch_all(&mut *db).await?;
            }
        }
        Ok(result)
    }

    async fn collect(
        &mut self,
        db: &mut MySqlConnection,
        parent: &'static str,
        ids: &[i32],
        locked: bool,
        depth: usize,
    ) -> Result<(), sqlx::Error> {
        if ids.is_empty() {
            return Ok(());
        }
        for &(child, column) in children(parent) {
            let model = child.description();
            let revision = match child {
                Registered(name) => email_log_revision(name),
                _ => None,
            }
            .unwrap_or("NULL");
            let mut sql = QueryBuilder::new(format!(
                "SELECT t.id, t.{column}, {}, {revision} FROM {} WHERE t.{column} IN (",
                model.label, model.source
            ));
            let mut selected = sql.separated(", ");
            for id in ids {
                selected.push_bind(*id);
            }
            let related_order = match (model.table, column) {
                ("tab_team_debaters", "team_id") => Some("debater_id"),
                ("auth_user_groups", "user_id") => Some("group_id"),
                ("auth_user_user_permissions", "user_id")
                | ("auth_group_permissions", "group_id") => Some("permission_id"),
                ("tab_team_required_room_tags", "team_id")
                | ("tab_room_tags", "room_id")
                | ("tab_judge_required_room_tags", "judge_id") => Some("roomtag_id"),
                ("tab_judge_schools", "judge_id") => Some("school_id"),
                ("tab_judge", "registration_id") => Some("name"),
                ("registration_registrationconfirmationemaillog", "registration_id") => {
                    Some("sent_at DESC")
                }
                ("tab_round_judges", "round_id") | ("tab_outround_judges", "outround_id") => {
                    Some("judge_id")
                }
                _ => None,
            };
            sql.push(") ORDER BY t.").push(column);
            if let Some(related) = related_order {
                sql.push(", t.").push(related);
            }
            sql.push(", t.id");
            if locked {
                sql.push(" FOR UPDATE");
            }
            let rows = sql
                .build_query_as::<(i32, i32, String, Option<String>)>()
                .fetch_all(&mut *db)
                .await?;
            if rows.is_empty() {
                continue;
            }
            let index = self
                .related
                .iter()
                .position(|entry| entry.table == model.table)
                .unwrap_or_else(|| {
                    self.related.push(RelatedModel {
                        table: model.table,
                        plural: model.plural,
                        ids: BTreeSet::new(),
                        depth,
                    });
                    self.related.len() - 1
                });
            let recorded = &mut self.related[index];
            recorded.depth = recorded.depth.max(depth);
            let title = admin_html::capitalized(model.singular);
            let mut newly_found = Vec::new();
            for (id, owner, label, revision) in rows {
                let key = (model.table, id);
                if let Some(revision) = revision {
                    self.email_log_revisions.insert(key, revision);
                }
                self.edges.entry((parent, owner)).or_default().push(key);
                if recorded.ids.insert(id) {
                    let label = if matches!(child, Model::LogEntry) {
                        let (action, object, message): (u8, String, String) =
                            serde_json::from_str(&label)
                                .map_err(|error| sqlx::Error::Decode(error.into()))?;
                        match action {
                            1 => format!("Added “{object}”."),
                            2 => format!(
                                "Changed “{object}” — {}",
                                admin_html::admin_change_message(&message)
                            ),
                            3 => format!("Deleted “{object}.”"),
                            _ => format!("LogEntry Object ({id})"),
                        }
                    } else {
                        label
                    };
                    let label = escape(&label);
                    let item = if let Some(url) = &model.url {
                        format!("{title}: <a href=\"{url}{id}/change/\">{label}</a>")
                    } else {
                        format!("{title}: {label}")
                    };
                    self.objects.insert(key, item);
                    newly_found.push(id);
                }
            }
            if !children(model.table).is_empty() {
                Box::pin(self.collect(db, model.table, &newly_found, locked, depth + 1)).await?;
            }
        }
        Ok(())
    }

    pub async fn unavailable_permissions(
        &self,
        db: &mut MySqlConnection,
    ) -> Result<Vec<&'static str>, sqlx::Error> {
        let mut blocked = Vec::new();
        for model in &self.related {
            match model.table {
                "tab_manualjudgeassignment" => blocked.push("manual judge assignment"),
                "tab_outroundchallenge"
                    if !admin_html::model_enabled(db, "tab", "outroundchallenge").await? =>
                {
                    blocked.push("outround challenge");
                }
                _ => {}
            }
        }
        blocked.sort_unstable();
        Ok(blocked)
    }

    pub fn ids(&self, table: &str) -> BTreeSet<i32> {
        self.related
            .iter()
            .find(|model| model.table == table)
            .map(|model| model.ids.clone())
            .unwrap_or_default()
    }

    pub fn touches(&self, table: &str) -> bool {
        self.related.iter().any(|model| model.table == table)
    }

    pub fn scope(&self) -> impl serde::Serialize + '_ {
        (
            self.objects.iter().collect::<Vec<_>>(),
            self.edges.iter().collect::<Vec<_>>(),
            &self.registration_log_links,
            self.email_log_revisions.iter().collect::<Vec<_>>(),
        )
    }

    pub fn counts(&self) -> impl Iterator<Item = (&'static str, usize)> {
        self.related
            .iter()
            .map(|model| (model.plural, model.ids.len()))
    }

    pub fn rendered(
        &self,
        table: &'static str,
        root: i32,
        label: &str,
        seen: &mut BTreeSet<Key>,
    ) -> String {
        self.render_node((table, root), label, 1, seen)
    }

    fn render_node(&self, key: Key, label: &str, depth: usize, seen: &mut BTreeSet<Key>) -> String {
        if !seen.insert(key) {
            return String::new();
        }
        let indent = "\t".repeat(depth);
        let mut output = format!("{indent}<li>{label}");
        let children: Vec<_> = self
            .edges
            .get(&key)
            .into_iter()
            .flatten()
            .map(|child| self.render_node(*child, &self.objects[child], depth + 1, seen))
            .filter(|rendered| !rendered.is_empty())
            .collect();
        if !children.is_empty() {
            output.push_str(&format!(
                "\n{indent}<ul>\n{}\n{indent}</ul>\n{indent}",
                children.join("\n")
            ));
        }
        output.push_str("</li>");
        output
    }

    pub async fn delete(self, db: &mut MySqlConnection) -> Result<(), sqlx::Error> {
        for (id, parent) in self.registration_log_links {
            sqlx::query(
                "UPDATE registration_registrationchangelog
                SET registration_id = NULL WHERE id = ? AND registration_id = ?",
            )
            .bind(id)
            .bind(parent)
            .execute(&mut *db)
            .await?;
        }
        let mut related = self.related;
        related.sort_by_key(|model| std::cmp::Reverse(model.depth));
        for model in related {
            let ids: Vec<_> = model.ids.into_iter().collect();
            delete_links(db, &ids, &[(model.table, "id")]).await?;
        }
        Ok(())
    }
}
