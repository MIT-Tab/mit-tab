use std::collections::BTreeSet;

use chrono::{DateTime, NaiveDate, NaiveDateTime, TimeZone};
use chrono_tz::America::New_York;
use sqlx::{AssertSqlSafe, MySql, MySqlConnection, QueryBuilder};

use crate::{
    admin_changelist::{self, DefaultList, Query},
    codes::{self, Credential},
    registration_form::Fields,
};

pub const MANAGED: &[&str] = &[
    "bye",
    "noshow",
    "checkin",
    "roomcheckin",
    "breakingteam",
    "usertournamentsetuppreference",
    "roomtag",
    "school",
    "debater",
    "room",
    "rankinggroup",
    "scratch",
    "round",
    "outround",
    "team",
    "judge",
    "tabsettings",
    "registration",
    "registrationconfig",
    "registrationchangelog",
    "registrationconfirmationemaillog",
    "infolink",
    "infotext",
    "registrationlink",
];

pub struct Resource {
    pub list: DefaultList,
    pub fields: &'static [Field],
    pub unique: &'static [Unique],
}

pub struct Field {
    pub name: &'static str,
    pub column: &'static str,
    pub label: &'static str,
    pub kind: Kind,
    pub required: bool,
    pub initial: Option<&'static str>,
    empty_value: Option<&'static str>,
    pub filter_label: Option<&'static str>,
    pub help_text: Option<&'static str>,
    pub related_widget: bool,
    pub can_delete_related: bool,
}

#[derive(Clone, Copy)]
pub enum Kind {
    Text(usize),
    HttpUrl(usize),
    Textarea,
    Json,
    EasternDateTime,
    Email(usize),
    Integer,
    PositiveSmallInteger,
    PositiveInteger,
    Experience,
    BallotCode,
    Decimal,
    Checkbox,
    Relation(Relation),
    Multiple(Multiple),
    Choice(&'static [(i32, &'static str)]),
}

#[derive(Clone, Copy)]
pub struct Relation {
    pub model: &'static str,
    pub admin_url: &'static str,
    sql: &'static str,
}

impl Relation {
    pub async fn options(
        self,
        db: &mut MySqlConnection,
    ) -> Result<Vec<(i32, String)>, sqlx::Error> {
        sqlx::query_as(self.sql).fetch_all(db).await
    }
}

#[derive(Clone, Copy)]
pub struct Multiple {
    pub relation: Relation,
    pub table: &'static str,
    pub owner: &'static str,
    pub column: &'static str,
    pub through: &'static str,
    pub singular: &'static str,
    pub plural: &'static str,
}

impl Multiple {
    async fn ids(self, db: &mut MySqlConnection, owner: i32) -> Result<BTreeSet<i32>, sqlx::Error> {
        sqlx::query_scalar(AssertSqlSafe(format!(
            "SELECT {} FROM {} WHERE {} = ?",
            self.column, self.table, self.owner,
        )))
        .bind(owner)
        .fetch_all(db)
        .await
        .map(|ids| ids.into_iter().collect())
    }

    async fn save(
        self,
        db: &mut MySqlConnection,
        owner: i32,
        value: Option<&str>,
    ) -> Result<(), sqlx::Error> {
        let before = self.ids(db, owner).await?;
        let after: BTreeSet<_> = value
            .into_iter()
            .flat_map(|value| value.split(','))
            .map(str::parse::<i32>)
            .collect::<Result<_, _>>()
            .map_err(|error| sqlx::Error::Decode(error.into()))?;
        let mut removed = before.difference(&after).peekable();
        if removed.peek().is_some() {
            let mut sql = QueryBuilder::new(format!(
                "DELETE FROM {} WHERE {} = ",
                self.table, self.owner
            ));
            sql.push_bind(owner)
                .push(format!(" AND {} IN (", self.column));
            let mut ids = sql.separated(", ");
            for id in removed {
                ids.push_bind(*id);
            }
            sql.push(")").build().execute(&mut *db).await?;
        }
        let mut added = after.difference(&before).peekable();
        if added.peek().is_some() {
            let mut sql = QueryBuilder::new(format!(
                "INSERT INTO {} ({}, {}) ",
                self.table, self.owner, self.column
            ));
            sql.push_values(added, |mut row, id| {
                row.push_bind(owner).push_bind(*id);
            })
            .build()
            .execute(&mut *db)
            .await?;
        }
        if self.table == "tab_round_judges" {
            for &judge in before.difference(&after) {
                sqlx::query(
                    "DELETE FROM tab_manualjudgeassignment WHERE round_id = ? AND judge_id = ?",
                )
                .bind(owner)
                .bind(judge)
                .execute(&mut *db)
                .await?;
            }
            for &judge in after.difference(&before) {
                sqlx::query(
                    "INSERT INTO tab_manualjudgeassignment (round_id, judge_id, created_by_id, created_at)
                     VALUES (?, ?, NULL, UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE id = id",
                ).bind(owner).bind(judge).execute(&mut *db).await?;
            }
        }
        Ok(())
    }
}

pub struct Unique {
    pub fields: &'static [usize],
    pub error_field: Option<usize>,
    pub message: &'static str,
}

const TEAM: Relation = Relation {
    model: "team",
    admin_url: "/admin/tab/team/",
    sql: "SELECT id, name FROM tab_team ORDER BY id",
};
const JUDGE: Relation = Relation {
    model: "judge",
    admin_url: "/admin/tab/judge/",
    sql: "SELECT id, name FROM tab_judge ORDER BY name",
};
const ROOM: Relation = Relation {
    model: "room",
    admin_url: "/admin/tab/room/",
    sql: "SELECT id, name FROM tab_room ORDER BY name",
};
const USER: Relation = Relation {
    model: "user",
    admin_url: "/admin/auth/user/",
    sql: "SELECT id, username FROM auth_user ORDER BY username",
};

pub(super) const SCHOOL: Relation = Relation {
    model: "school",
    admin_url: "/admin/tab/school/",
    sql: "SELECT id, name FROM tab_school ORDER BY name",
};
const DEBATER: Relation = Relation {
    model: "debater",
    admin_url: "/admin/tab/debater/",
    sql: "SELECT id, name FROM tab_debater ORDER BY name",
};
const REGISTRATION: Relation = Relation {
    model: "registration",
    admin_url: "/admin/registration/registration/",
    sql: "SELECT r.id, CONCAT(s.name, ' — ', r.email) FROM registration_registration r
          JOIN tab_school s ON s.id = r.school_id ORDER BY r.id",
};
const ROOM_TAG: Relation = Relation {
    model: "room tag",
    admin_url: "/admin/tab/roomtag/",
    sql: "SELECT id, tag FROM tab_roomtag ORDER BY id",
};
const ROOM_TAGS: Multiple = Multiple {
    relation: ROOM_TAG,
    table: "tab_room_tags",
    owner: "room_id",
    column: "roomtag_id",
    through: "Room_tags",
    singular: "Room-roomtag relationship",
    plural: "room-roomtag relationships",
};
const TEAM_DEBATERS: Multiple = Multiple {
    relation: DEBATER,
    table: "tab_team_debaters",
    owner: "team_id",
    column: "debater_id",
    through: "Team_debaters",
    singular: "Team-debater relationship",
    plural: "team-debater relationships",
};
const TEAM_TAGS: Multiple = Multiple {
    relation: ROOM_TAG,
    table: "tab_team_required_room_tags",
    owner: "team_id",
    column: "roomtag_id",
    through: "Team_required_room_tags",
    singular: "Team-roomtag relationship",
    plural: "team-roomtag relationships",
};
const JUDGE_SCHOOLS: Multiple = Multiple {
    relation: SCHOOL,
    table: "tab_judge_schools",
    owner: "judge_id",
    column: "school_id",
    through: "Judge_schools",
    singular: "Judge-school relationship",
    plural: "judge-school relationships",
};
const JUDGE_TAGS: Multiple = Multiple {
    relation: ROOM_TAG,
    table: "tab_judge_required_room_tags",
    owner: "judge_id",
    column: "roomtag_id",
    through: "Judge_required_room_tags",
    singular: "Judge-roomtag relationship",
    plural: "judge-roomtag relationships",
};
const ROUND_PANEL: Multiple = Multiple {
    relation: JUDGE,
    table: "tab_round_judges",
    owner: "round_id",
    column: "judge_id",
    through: "Round_judges",
    singular: "Round-judge relationship",
    plural: "round-judge relationships",
};
const OUTROUND_PANEL: Multiple = Multiple {
    relation: JUDGE,
    table: "tab_outround_judges",
    owner: "outround_id",
    column: "judge_id",
    through: "Outround_judges",
    singular: "Outround-judge relationship",
    plural: "outround-judge relationships",
};

const GROUP_TEAMS: Multiple = Multiple {
    relation: TEAM,
    table: "tab_rankinggroup_teams",
    owner: "rankinggroup_id",
    column: "team_id",
    through: "RankingGroup_teams",
    singular: "Rankinggroup-team relationship",
    plural: "rankinggroup-team relationships",
};
const GROUP_DEBATERS: Multiple = Multiple {
    relation: DEBATER,
    table: "tab_rankinggroup_debaters",
    owner: "rankinggroup_id",
    column: "debater_id",
    through: "RankingGroup_debaters",
    singular: "Rankinggroup-debater relationship",
    plural: "rankinggroup-debater relationships",
};

impl Field {
    pub(super) const fn new(name: &'static str, label: &'static str, kind: Kind) -> Self {
        Self {
            name,
            column: name,
            label,
            kind,
            required: true,
            initial: None,
            empty_value: None,
            filter_label: None,
            help_text: None,
            related_widget: true,
            can_delete_related: false,
        }
    }

    pub(super) const fn relation(
        name: &'static str,
        column: &'static str,
        label: &'static str,
        relation: Relation,
    ) -> Self {
        Self {
            column,
            ..Self::new(name, label, Kind::Relation(relation))
        }
    }

    pub(super) const fn optional(self) -> Self {
        Self {
            required: false,
            ..self
        }
    }

    const fn blank_text(self) -> Self {
        Self {
            empty_value: Some(""),
            ..self.optional()
        }
    }

    const fn unwrapped(self) -> Self {
        Self {
            related_widget: false,
            ..self
        }
    }

    pub(super) const fn allow_related_delete(self) -> Self {
        Self {
            can_delete_related: true,
            ..self
        }
    }

    const fn filtered(self, label: &'static str) -> Self {
        Self {
            filter_label: Some(label),
            ..self
        }
    }

    const fn help(self, text: &'static str) -> Self {
        Self {
            help_text: Some(text),
            ..self
        }
    }

    const fn initial(self, initial: &'static str) -> Self {
        Self {
            initial: Some(initial),
            ..self
        }
    }
}

const fn eastern_date(name: &'static str, label: &'static str) -> Field {
    Field::new(name, label, Kind::EasternDateTime)
        .optional()
        .help("Eastern time (EST/EDT).")
}

const fn checkbox(name: &'static str, label: &'static str, initial: &'static str) -> Field {
    Field::new(name, label, Kind::Checkbox)
        .optional()
        .initial(initial)
}

const INFO_TITLE: Field = Field::new("title", "Title", Kind::Text(80));
pub(super) const INFO_ACTIVE: Field = checkbox("is_active", "Is active", "1");
pub(super) const INFO_ORDER: Field =
    Field::new("display_order", "Display order", Kind::PositiveInteger).initial("0");

const APDA_ID: Field = Field::new("apda_id", "Apda id", Kind::Integer)
    .optional()
    .initial("-1");

const GOV_TEAM: Field = Field::relation("gov_team", "gov_team_id", "Gov team", TEAM);
const OPP_TEAM: Field = Field::relation("opp_team", "opp_team_id", "Opp team", TEAM);
const CHAIR: Field = Field::relation("chair", "chair_id", "Chair", JUDGE).unwrapped();
const PAIRING_ROOM: Field = Field::relation("room", "room_id", "Room", ROOM).optional();
const ROUND_RESULTS: &[(i32, &str)] = &[
    (0, "UNKNOWN"),
    (1, "GOV"),
    (2, "OPP"),
    (3, "GOV via Forfeit"),
    (4, "OPP via Forfeit"),
    (5, "ALL DROP"),
    (6, "ALL WIN"),
];
const OUTROUND_RESULTS: &[(i32, &str)] = &[
    (0, "UNKNOWN"),
    (1, "GOV"),
    (2, "OPP"),
    (3, "GOV via Forfeit"),
    (4, "OPP via Forfeit"),
];

const REGISTRATION_FIELD: Field = Field::relation(
    "registration",
    "registration_id",
    "Registration",
    REGISTRATION,
)
.optional();
const TIEBREAKER: Field = Field::new("tiebreaker", "Tiebreaker", Kind::Integer).optional();
const HOUSING: Field =
    Field::new("needs_crash_housing", "Needs crash housing", Kind::Checkbox).optional();
const WGM_HOUSING: Field = Field::new(
    "requests_wgm_housing",
    "Requests wgm housing",
    Kind::Checkbox,
)
.optional();
const EMAIL: Field = Field::new("email", "Email", Kind::Email(254)).optional();

const ROUND: Field = Field::new("round_number", "Round number", Kind::Integer);
const SEED: Field = Field::new("seed", "Seed", Kind::Integer).initial("-1");
const EFFECTIVE_SEED: Field =
    Field::new("effective_seed", "Effective seed", Kind::Integer).initial("-1");
const TYPE: Field = Field::new(
    "type_of_team",
    "Type of team",
    Kind::Choice(&[(0, "Varsity"), (1, "Novice")]),
)
.initial("0");

impl Resource {
    pub fn find(name: &str) -> Option<Self> {
        let (fields, unique): (&'static [Field], &'static [Unique]) = match name {
            "registration" => (
                const {
                    &[
                        Field::relation("school", "school_id", "School", SCHOOL),
                        Field::new("email", "Email", Kind::Email(254)),
                        Field::new("dietary_requests", "Dietary requests", Kind::Textarea)
                            .optional(),
                        Field::new("housing_requests", "Housing requests", Kind::Textarea)
                            .optional(),
                        Field::new("herokunator_code", "Herokunator code", Kind::Text(255))
                            .optional(),
                    ]
                },
                &[Unique {
                    fields: &[4],
                    error_field: Some(4),
                    message: "Registration with this Herokunator code already exists.",
                }],
            ),
            "registrationconfig" => (
                const {
                    &[
                        checkbox("allow_new_registrations", "Allow new registrations", "1"),
                        checkbox("allow_registration_edits", "Allow registration edits", "1"),
                        eastern_date("new_registrations_open_at", "New registrations open at"),
                        eastern_date("new_registrations_close_at", "New registrations close at"),
                        eastern_date("registration_edits_open_at", "Registration edits open at"),
                        eastern_date("registration_edits_close_at", "Registration edits close at"),
                        checkbox("crash_housing_enabled", "Crash housing enabled", "0"),
                        checkbox(
                            "team_name_changes_allowed",
                            "Team name changes allowed",
                            "0",
                        ),
                        checkbox("disc_scratches_open", "Disc scratches open", "0"),
                        Field::new(
                            "disc_scratch_quantity",
                            "Disc scratch quantity",
                            Kind::PositiveSmallInteger,
                        )
                        .initial("0"),
                    ]
                },
                &[],
            ),
            "registrationchangelog" => (&[], &[]),
            "registrationconfirmationemaillog" => (
                const { &[Field::new("email_payload", "Email payload", Kind::Json).optional()] },
                &[],
            ),
            "infotext" => (
                const {
                    &[
                        INFO_TITLE,
                        Field::new("text", "Text", Kind::Text(500)),
                        INFO_ACTIVE,
                        INFO_ORDER,
                    ]
                },
                &[],
            ),
            "infolink" | "registrationlink" => (
                const {
                    &[
                        INFO_TITLE,
                        Field::new("url", "Url", Kind::HttpUrl(500)),
                        Field::new("description", "Description", Kind::Text(160)).blank_text(),
                        INFO_ACTIVE,
                        INFO_ORDER,
                    ]
                },
                &[],
            ),
            "bye" => (
                const {
                    &[
                        Field::relation("bye_team", "bye_team_id", "Bye team", TEAM),
                        ROUND,
                    ]
                },
                &[],
            ),
            "noshow" => (
                const {
                    &[
                        Field::relation("no_show_team", "no_show_team_id", "No show team", TEAM),
                        ROUND,
                    ]
                },
                &[],
            ),
            "checkin" => (
                const { &[Field::relation("judge", "judge_id", "Judge", JUDGE), ROUND] },
                &[Unique {
                    fields: &[0, 1],
                    error_field: None,
                    message: "Check in with this Judge and Round number already exists.",
                }],
            ),
            "roomcheckin" => (
                const { &[Field::relation("room", "room_id", "Room", ROOM), ROUND] },
                &[Unique {
                    fields: &[0, 1],
                    error_field: None,
                    message: "Room check in with this Room and Round number already exists.",
                }],
            ),
            "breakingteam" => (
                const {
                    &[
                        Field::relation("team", "team_id", "Team", TEAM),
                        SEED,
                        EFFECTIVE_SEED,
                        TYPE,
                    ]
                },
                &[Unique {
                    fields: &[0],
                    error_field: Some(0),
                    message: "Breaking team with this Team already exists.",
                }],
            ),
            "usertournamentsetuppreference" => (
                const {
                    &[
                        Field::relation("user", "user_id", "User", USER),
                        Field::new(
                            "hide_tournament_todo",
                            "Hide tournament todo",
                            Kind::Checkbox,
                        )
                        .optional(),
                    ]
                },
                &[Unique {
                    fields: &[0],
                    error_field: Some(0),
                    message: "Tournament setup preference with this User already exists.",
                }],
            ),
            "roomtag" => (
                const {
                    &[
                        Field::new("tag", "Tag", Kind::Text(255)),
                        Field::new("priority", "Priority", Kind::Decimal),
                    ]
                },
                &[],
            ),
            "tabsettings" => (
                const {
                    &[
                        Field::new("key", "Key", Kind::Text(50)),
                        Field::new("value", "Value", Kind::Integer).optional(),
                        Field::new("value_string", "Value string", Kind::Text(200)).optional(),
                    ]
                },
                &[],
            ),
            "school" => (
                const { &[Field::new("name", "Name", Kind::Text(50)), APDA_ID] },
                &[Unique {
                    fields: &[0],
                    error_field: Some(0),
                    message: "School with this Name already exists.",
                }],
            ),
            "debater" => (
                const {
                    &[
                        Field::new("name", "Name", Kind::Text(30)),
                        EMAIL,
                        Field::new(
                            "novice_status",
                            "Novice status",
                            Kind::Choice(&[(0, "Varsity"), (1, "Novice")]),
                        ),
                        Field::new("needs_crash_housing", "Needs crash housing", Kind::Checkbox)
                            .optional(),
                        Field::new(
                            "requests_wgm_housing",
                            "Requests wgm housing",
                            Kind::Checkbox,
                        )
                        .optional(),
                        Field::new("qualified", "Qualified", Kind::Checkbox).optional(),
                        TIEBREAKER,
                        APDA_ID,
                        Field::relation("school", "school_id", "School", SCHOOL)
                            .optional()
                            .allow_related_delete(),
                    ]
                },
                &[
                    Unique {
                        fields: &[0],
                        error_field: Some(0),
                        message: "Debater with this Name already exists.",
                    },
                    Unique {
                        fields: &[6],
                        error_field: Some(6),
                        message: "Debater with this Tiebreaker already exists.",
                    },
                ],
            ),
            "room" => (
                const {
                    &[
                        Field::new("name", "Name", Kind::Text(30)),
                        Field::new("rank", "Rank", Kind::Decimal),
                        Field::new("tags", "Tags", Kind::Multiple(ROOM_TAGS)).optional(),
                    ]
                },
                &[Unique {
                    fields: &[0],
                    error_field: Some(0),
                    message: "Room with this Name already exists.",
                }],
            ),
            "rankinggroup" => (
                const {
                    &[
                        Field::new("name", "Name", Kind::Text(255)),
                        Field::new("teams", "Teams", Kind::Multiple(GROUP_TEAMS)).optional(),
                        Field::new("debaters", "Debaters", Kind::Multiple(GROUP_DEBATERS))
                            .optional(),
                    ]
                },
                &[Unique {
                    fields: &[0],
                    error_field: Some(0),
                    message: "Ranking group with this Name already exists.",
                }],
            ),
            "team" => (
                const {
                    &[
                        Field::new("name", "Name", Kind::Text(35)),
                        Field::relation("school", "school_id", "School", SCHOOL),
                        Field::relation(
                            "hybrid_school",
                            "hybrid_school_id",
                            "Hybrid school",
                            SCHOOL,
                        )
                        .optional()
                        .allow_related_delete(),
                        Field::new("debaters", "Debaters", Kind::Multiple(TEAM_DEBATERS))
                            .filtered("debaters"),
                        REGISTRATION_FIELD,
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
                        Field::new("checked_in", "Checked in", Kind::Checkbox)
                            .optional()
                            .initial("1"),
                        Field::new(
                            "required_room_tags",
                            "Required room tags",
                            Kind::Multiple(TEAM_TAGS),
                        )
                        .optional(),
                        Field::new(
                            "break_preference",
                            "Break preference",
                            Kind::Choice(&[(0, "Varsity"), (1, "Novice")]),
                        )
                        .initial("0"),
                        TIEBREAKER,
                        Field::new("ranking_public", "Ranking public", Kind::Checkbox)
                            .optional()
                            .initial("1"),
                    ]
                },
                &[
                    Unique {
                        fields: &[0],
                        error_field: Some(0),
                        message: "Team with this Name already exists.",
                    },
                    Unique {
                        fields: &[9],
                        error_field: Some(9),
                        message: "Team with this Tiebreaker already exists.",
                    },
                ],
            ),
            "judge" => {
                (
                    const {
                        &[
                        Field::new("name", "Name", Kind::Text(30)),
                        Field::new("rank", "Rank", Kind::Decimal),
                        Field::new("reported_experience", "Reported experience", Kind::Experience).optional(),
                        HOUSING, WGM_HOUSING,
                        Field::new("schools", "Schools", Kind::Multiple(JUDGE_SCHOOLS)).filtered("schools"),
                        EMAIL,
                        REGISTRATION_FIELD,
                        Field::new("ballot_code", "Ballot code", Kind::BallotCode).optional(),
                        Field::new("is_dino", "Is dino", Kind::Checkbox).optional(),
                        Field::new("wing_only", "Wing only", Kind::Checkbox).optional(),
                        Field::new("request_wing", "Request wing", Kind::Checkbox).optional()
                            .help("Prioritize giving this judge a wing when they chair an inround."),
                        Field::new("no_wings", "No wings", Kind::Checkbox).optional()
                            .help("Deprioritize giving this judge a wing when they chair an inround."),
                        Field::new("required_room_tags", "Required room tags", Kind::Multiple(JUDGE_TAGS))
                            .optional().filtered("required room tags"),
                    ]
                    },
                    &[
                        Unique {
                            fields: &[0],
                            error_field: Some(0),
                            message: "Judge with this Name already exists.",
                        },
                        Unique {
                            fields: &[8],
                            error_field: Some(8),
                            message: "Judge with this Ballot code already exists.",
                        },
                    ],
                )
            }
            "round" => (
                const {
                    &[
                        ROUND,
                        GOV_TEAM,
                        OPP_TEAM,
                        CHAIR,
                        Field::new("judges", "Judges", Kind::Multiple(ROUND_PANEL))
                            .optional()
                            .filtered("judges"),
                        Field::new(
                            "pullup",
                            "Pullup",
                            Kind::Choice(&[(0, "NONE"), (1, "GOV"), (2, "OPP")]),
                        )
                        .initial("0"),
                        PAIRING_ROOM.allow_related_delete(),
                        Field::new("victor", "Victor", Kind::Choice(ROUND_RESULTS)).initial("0"),
                        Field::new("rfd", "Rfd", Kind::Textarea).optional(),
                    ]
                },
                &[],
            ),
            "outround" => (
                const {
                    &[
                        Field::new("num_teams", "Num teams", Kind::Integer),
                        Field::new(
                            "type_of_round",
                            "Type of round",
                            Kind::Choice(&[(0, "Varsity"), (1, "Novice")]),
                        )
                        .initial("0"),
                        GOV_TEAM,
                        OPP_TEAM,
                        CHAIR,
                        Field::new("judges", "Judges", Kind::Multiple(OUTROUND_PANEL))
                            .optional()
                            .filtered("judges"),
                        PAIRING_ROOM,
                        Field::new("victor", "Victor", Kind::Choice(OUTROUND_RESULTS)).initial("0"),
                        Field::new("sidelock", "Sidelock", Kind::Checkbox).optional(),
                        Field::new(
                            "choice",
                            "Choice",
                            Kind::Choice(&[(0, "No"), (1, "Gov"), (2, "Opp")]),
                        )
                        .initial("0"),
                    ]
                },
                &[],
            ),
            "scratch" => (
                const {
                    &[
                        Field::relation("judge", "judge_id", "Judge", JUDGE),
                        Field::relation("team", "team_id", "Team", TEAM),
                        Field::new(
                            "scratch_type",
                            "Scratch type",
                            Kind::Choice(&[(0, "Discretionary Scratch"), (1, "Tab Scratch")]),
                        ),
                    ]
                },
                &[Unique {
                    fields: &[0, 1],
                    error_field: None,
                    message: "Scratch with this Judge and Team already exists.",
                }],
            ),
            _ => return None,
        };
        Some(Self {
            list: admin_changelist::default_list(name)?,
            fields,
            unique,
        })
    }

    pub fn value<'a>(&self, name: &str, values: &'a [Option<String>]) -> Option<&'a str> {
        self.fields
            .iter()
            .zip(values)
            .find(|(field, _)| field.name == name)
            .and_then(|(_, value)| value.as_deref())
    }

    pub fn registration(&self) -> bool {
        self.list.model.name == "registration"
    }

    pub fn registration_config(&self) -> bool {
        self.list.model.name == "registrationconfig"
    }

    pub fn public_information(&self) -> bool {
        matches!(
            self.list.model.name,
            "infolink" | "infotext" | "registrationlink"
        )
    }

    pub fn pairing(&self) -> bool {
        matches!(self.list.model.name, "round" | "outround")
    }

    pub fn cascading_deletion(&self) -> bool {
        self.pairing()
            || self.registration()
            || matches!(self.list.model.name, "team" | "judge" | "room")
    }

    pub fn attributed(&self) -> bool {
        matches!(self.list.model.name, "scratch" | "team" | "judge")
    }

    pub fn relations(&self) -> impl Iterator<Item = (&Field, Multiple)> {
        self.fields.iter().filter_map(|field| match field.kind {
            Kind::Multiple(link) => Some((field, link)),
            _ => None,
        })
    }

    pub async fn describe(
        &self,
        db: &mut MySqlConnection,
        id: i32,
    ) -> Result<Option<String>, sqlx::Error> {
        sqlx::query_scalar(AssertSqlSafe(format!(
            "SELECT {} FROM {} WHERE t.id = ?",
            self.list.label, self.list.source,
        )))
        .bind(id)
        .fetch_optional(db)
        .await
    }

    pub async fn load(
        &self,
        db: &mut MySqlConnection,
        id: i32,
        lock: bool,
    ) -> Result<Option<Vec<Option<String>>>, sqlx::Error> {
        let values = self
            .fields
            .iter()
            .map(|field| match field.kind {
                Kind::Multiple(_) => "NULL".into(),
                _ => format!("CAST(`{}` AS CHAR)", field.column),
            })
            .chain(
                (self.registration_config() || self.public_information())
                    .then(|| "CAST(updated_at AS CHAR)".into()),
            )
            .collect::<Vec<_>>()
            .join(", ");
        let suffix = if lock { " FOR UPDATE" } else { "" };
        let raw: Option<String> = sqlx::query_scalar(AssertSqlSafe(format!(
            "SELECT CAST(JSON_ARRAY({values}) AS CHAR) FROM {} WHERE id = ?{suffix}",
            self.list.table(),
        )))
        .bind(id)
        .fetch_optional(&mut *db)
        .await?;
        let Some(raw) = raw else {
            return Ok(None);
        };
        let mut values: Vec<Option<String>> =
            serde_json::from_str(&raw).map_err(|error| sqlx::Error::Decode(error.into()))?;
        for (index, field) in self.fields.iter().enumerate() {
            if let Kind::Multiple(link) = field.kind {
                values[index] = join_ids(link.ids(db, id).await?);
            }
        }
        Ok(Some(values))
    }

    async fn duplicate(
        &self,
        db: &mut MySqlConnection,
        id: Option<i32>,
        values: &[Option<String>],
        unique: &Unique,
    ) -> Result<bool, sqlx::Error> {
        let mut sql = QueryBuilder::new(format!(
            "SELECT EXISTS(SELECT 1 FROM {} WHERE TRUE",
            self.list.table()
        ));
        for index in unique.fields {
            let column = self.fields[*index].column;
            sql.push(format!(" AND `{column}` = "))
                .push_bind(&values[*index]);
        }
        if let Some(id) = id {
            sql.push(" AND id <> ").push_bind(id);
        }
        sql.push(")").build_query_scalar().fetch_one(db).await
    }

    pub async fn save(
        &self,
        db: &mut MySqlConnection,
        id: Option<i32>,
        values: &[Option<String>],
        user: i32,
        initial: Option<&[Option<String>]>,
    ) -> Result<i32, sqlx::Error> {
        if self.fields.is_empty() {
            return id.ok_or(sqlx::Error::RowNotFound);
        }
        let mut sql = if id.is_some() {
            QueryBuilder::<MySql>::new(format!("UPDATE {} SET ", self.list.table()))
        } else {
            QueryBuilder::<MySql>::new(format!("INSERT INTO {} SET ", self.list.table()))
        };
        let mut columns = sql.separated(", ");
        let model = self.list.model.name;
        let coin = if matches!(model, "team" | "debater") {
            let submitted = self.value("tiebreaker", values);
            let unchanged = id.is_some()
                && submitted.is_some_and(|submitted| {
                    initial.and_then(|old| self.value("tiebreaker", old)) == Some(submitted)
                });
            if unchanged || submitted.is_none_or(|value| value == "0") {
                Some(codes::coin(db, model == "team").await?.to_string())
            } else {
                None
            }
        } else {
            None
        };
        let team_code = if model == "team" {
            let existing: Option<String> = if let Some(id) = id {
                sqlx::query_scalar("SELECT team_code FROM tab_team WHERE id = ?")
                    .bind(id)
                    .fetch_one(&mut *db)
                    .await?
            } else {
                None
            };
            if existing.as_deref().is_none_or(str::is_empty) {
                Some(codes::unique(db, Credential::Team).await?)
            } else {
                None
            }
        } else {
            None
        };
        let ballot_code = if model == "judge" && self.value("ballot_code", values).is_none() {
            Some(codes::unique(db, Credential::Judge).await?)
        } else {
            None
        };
        let registration_code =
            if self.registration() && self.value("herokunator_code", values).is_none() {
                Some(codes::unique(db, Credential::Registration).await?)
            } else {
                None
            };
        for (field, value) in self.fields.iter().zip(values) {
            if !matches!(field.kind, Kind::Multiple(_)) {
                let value = match field.name {
                    "tiebreaker" => coin.as_ref().or(value.as_ref()),
                    "ballot_code" => ballot_code.as_ref().or(value.as_ref()),
                    "herokunator_code" => registration_code.as_ref().or(value.as_ref()),
                    _ => value.as_ref(),
                };
                columns
                    .push(format!("`{}` = ", field.column))
                    .push_bind_unseparated(value);
            }
        }
        if self.registration_config() {
            columns.push("updated_at = UTC_TIMESTAMP(6)");
            if id.is_none() {
                columns.push("id = 1");
            }
        }
        if self.registration() || self.public_information() {
            columns.push("updated_at = UTC_TIMESTAMP(6)");
            if id.is_none() {
                columns.push("created_at = UTC_TIMESTAMP(6)");
            }
        }
        if let Some(code) = team_code.as_ref() {
            columns.push("team_code = ").push_bind_unseparated(code);
        }
        if id.is_none() && self.attributed() {
            columns.push("created_by_id = ").push_bind_unseparated(user);
            columns.push("created_at = UTC_TIMESTAMP(6)");
        }
        if model == "round" {
            if self.value("victor", values) == Some("0") {
                columns.push("result_entered_at = NULL");
            } else if initial
                .and_then(|row| self.value("victor", row))
                .unwrap_or("0")
                == "0"
            {
                columns.push("result_entered_at = UTC_TIMESTAMP(6)");
            }
            sqlx::query(
                "DELETE FROM tab_noshow WHERE round_number = ? AND no_show_team_id IN (?, ?)",
            )
            .bind(self.value("round_number", values))
            .bind(self.value("gov_team", values))
            .bind(self.value("opp_team", values))
            .execute(&mut *db)
            .await?;
        }
        if let Some(id) = id {
            sql.push(" WHERE id = ").push_bind(id);
        }
        let result = sql.build().execute(&mut *db).await?;
        let saved = match id {
            Some(id) => id,
            None if self.registration_config() => 1,
            None => i32::try_from(result.last_insert_id())
                .map_err(|error| sqlx::Error::Decode(error.into()))?,
        };
        for (field, value) in self.fields.iter().zip(values) {
            if let Kind::Multiple(link) = field.kind {
                link.save(db, saved, value.as_deref()).await?;
            }
        }
        Ok(saved)
    }
}

fn join_ids(ids: BTreeSet<i32>) -> Option<String> {
    (!ids.is_empty()).then(|| ids.iter().map(i32::to_string).collect::<Vec<_>>().join(","))
}

pub struct Form {
    pub fields: Vec<BoundField>,
    pub readonly: Vec<minijinja::Value>,
    pub can_add: bool,
    pub non_field_error: Option<&'static str>,
    pub pairing_revision: Option<String>,
    pub resource_revision: Option<(&'static str, String)>,
    pub inlines: Option<crate::admin_registration_inlines::Inlines>,
}

pub struct BoundField {
    pub field: &'static Field,
    pub value: Option<String>,
    pub initial_json: Option<String>,
    pub options: Vec<(i32, String)>,
    pub selected: Vec<String>,
    pub error: Option<String>,
}

impl Form {
    pub async fn load(
        resource: &Resource,
        db: &mut MySqlConnection,
        initial: Option<&[Option<String>]>,
        post: Option<&Fields>,
        query: &Query,
    ) -> Result<Self, sqlx::Error> {
        let mut fields = Vec::new();
        for (index, field) in resource.fields.iter().enumerate() {
            let stored = initial.and_then(|row| row[index].as_deref());
            let value = if let Some(post) = post {
                match field.kind {
                    Kind::Checkbox => Some(if post.flag(field.name) { "1" } else { "0" }.into()),
                    _ => post.all(field.name).last().cloned(),
                }
            } else if initial.is_some() {
                match field.kind {
                    Kind::EasternDateTime => eastern_date_input(stored),
                    _ => stored.map(str::to_owned),
                }
            } else {
                query.get(field.name).or(field.initial).map(str::to_owned)
            };
            let options = match field.kind {
                Kind::Relation(relation) => relation.options(db).await?,
                Kind::Multiple(link) => link.relation.options(db).await?,
                Kind::Choice(choices) => choices
                    .iter()
                    .map(|&(id, text)| (id, text.to_owned()))
                    .collect(),
                _ => Vec::new(),
            };
            let selected = match (field.kind, post) {
                (Kind::Multiple(_), Some(post)) => post.all(field.name).to_vec(),
                (Kind::Multiple(_), None) => value
                    .iter()
                    .flat_map(|value| value.split(',').map(str::to_owned))
                    .collect(),
                _ => value.iter().cloned().collect(),
            };
            let initial_json = matches!(field.kind, Kind::Json)
                .then(|| {
                    if let Some(post) = post {
                        post.all(&format!("initial-{}", field.name)).last().cloned()
                    } else {
                        stored.map(str::to_owned)
                    }
                })
                .flatten();
            fields.push(BoundField {
                field,
                value,
                initial_json,
                options,
                selected,
                error: None,
            });
        }
        Ok(Self {
            fields,
            readonly: Vec::new(),
            can_add: true,
            non_field_error: None,
            pairing_revision: None,
            resource_revision: None,
            inlines: None,
        })
    }

    pub async fn clean(
        &mut self,
        resource: &Resource,
        db: &mut MySqlConnection,
        id: Option<i32>,
        initial: Option<&[Option<String>]>,
    ) -> Result<Option<Vec<Option<String>>>, sqlx::Error> {
        let values: Vec<_> = self
            .fields
            .iter_mut()
            .enumerate()
            .map(|(index, field)| {
                let stored = initial.and_then(|row| row[index].as_deref());
                match field.field.kind {
                    Kind::EasternDateTime if eastern_date_input(stored) == field.value => {
                        stored.map(str::to_owned)
                    }
                    Kind::Json if field.value.is_none() => stored.map(str::to_owned),
                    _ => field.clean(),
                }
            })
            .collect();
        if resource.pairing()
            && let Some(chair) = resource.value("chair", &values)
            && self
                .fields
                .iter()
                .find(|field| field.field.name == "judges")
                .is_some_and(|field| field.error.is_none())
            && !resource
                .value("judges", &values)
                .unwrap_or_default()
                .split(',')
                .any(|judge| judge == chair)
        {
            self.non_field_error = Some("Chair must be a judge in the round");
        }
        self.check_duplicates(resource, db, id, &values).await?;
        if self.non_field_error.is_some() || self.fields.iter().any(|field| field.error.is_some()) {
            return Ok(None);
        }
        Ok(Some(values))
    }

    pub async fn check_duplicates(
        &mut self,
        resource: &Resource,
        db: &mut MySqlConnection,
        id: Option<i32>,
        values: &[Option<String>],
    ) -> Result<bool, sqlx::Error> {
        let mut duplicate = false;
        for unique in resource.unique {
            if unique.fields.iter().all(|&index| values[index].is_some())
                && resource.duplicate(db, id, values, unique).await?
            {
                duplicate = true;
                if let Some(index) = unique.error_field {
                    self.fields[index].error = Some(unique.message.into());
                } else {
                    self.non_field_error = Some(unique.message);
                }
            }
        }
        Ok(duplicate)
    }
}

impl BoundField {
    pub(super) fn clean(&mut self) -> Option<String> {
        let raw = self.value.as_deref().unwrap_or_default().trim();
        let invalid = |error: &mut Option<String>, message: &str| {
            *error = Some(message.into());
            None
        };
        if matches!(self.field.kind, Kind::Multiple(_)) {
            if self.field.required && self.selected.is_empty() {
                return invalid(&mut self.error, "This field is required.");
            }
            let mut ids = BTreeSet::new();
            for value in &self.selected {
                let Ok(id) = value.parse::<i32>() else {
                    return invalid(&mut self.error, &format!("“{value}” is not a valid value."));
                };
                if !self.options.iter().any(|&(option, _)| option == id) {
                    return invalid(
                        &mut self.error,
                        &format!(
                            "Select a valid choice. {value} is not one of the available choices."
                        ),
                    );
                }
                ids.insert(id);
            }
            return join_ids(ids);
        }
        if matches!(self.field.kind, Kind::Textarea) {
            return if raw.contains('\0') {
                invalid(&mut self.error, "Null characters are not allowed.")
            } else {
                Some(raw.into())
            };
        }
        if matches!(self.field.kind, Kind::Json) {
            return if serde_json::from_str::<serde_json::Value>(raw).is_ok() {
                Some(raw.into())
            } else {
                invalid(&mut self.error, "Enter a valid JSON.")
            };
        }
        if raw.is_empty() {
            return if self.field.required {
                invalid(&mut self.error, "This field is required.")
            } else {
                self.field.empty_value.map(str::to_owned)
            };
        }
        match self.field.kind {
            Kind::Text(max) | Kind::Email(max) | Kind::HttpUrl(max) => {
                if matches!(self.field.kind, Kind::Email(_)) && !valid_email(raw) {
                    return invalid(&mut self.error, "Enter a valid email address.");
                }
                let len = raw.chars().count();
                if len > max {
                    return invalid(
                        &mut self.error,
                        &format!("Ensure this value has at most {max} characters (it has {len})."),
                    );
                }
                if raw.contains('\0') {
                    return invalid(&mut self.error, "Null characters are not allowed.");
                }
                if matches!(self.field.kind, Kind::HttpUrl(_))
                    && !crate::registration_admin::valid_http_url(raw)
                {
                    return invalid(&mut self.error, "Enter a valid URL.");
                }
                Some(raw.into())
            }
            Kind::BallotCode => {
                let len = raw.chars().count();
                if len > 30 {
                    return invalid(
                        &mut self.error,
                        &format!("Ensure this value has at most 30 characters (it has {len})."),
                    );
                }
                if !crate::judge_portal::valid_code(raw) {
                    return invalid(
                        &mut self.error,
                        "Ballot code must be legacy alphanumeric text or a hyphenated word-word code with an optional alphanumeric token.",
                    );
                }
                Some(raw.into())
            }
            Kind::EasternDateTime => match eastern_datetime(raw) {
                Ok(date) => Some(date.format("%Y-%m-%d %H:%M:%S%.6f").to_string()),
                Err(message) => invalid(&mut self.error, &message),
            },
            Kind::Integer
            | Kind::Experience
            | Kind::PositiveSmallInteger
            | Kind::PositiveInteger => {
                let integer = if let Some((number, fraction)) = raw.split_once('.')
                    && fraction.bytes().all(|digit| digit == b'0')
                {
                    number
                } else {
                    raw
                };
                match integer.parse::<i128>() {
                    Ok(value)
                        if matches!(
                            self.field.kind,
                            Kind::Experience | Kind::PositiveSmallInteger | Kind::PositiveInteger
                        ) && value < 0 =>
                    {
                        invalid(
                            &mut self.error,
                            "Ensure this value is greater than or equal to 0.",
                        )
                    }
                    Ok(value) if matches!(self.field.kind, Kind::Experience) && value > 10 => {
                        invalid(
                            &mut self.error,
                            "Ensure this value is less than or equal to 10.",
                        )
                    }
                    Ok(value)
                        if matches!(self.field.kind, Kind::PositiveSmallInteger)
                            && value > 65535 =>
                    {
                        invalid(
                            &mut self.error,
                            "Ensure this value is less than or equal to 65535.",
                        )
                    }
                    Ok(value) if matches!(self.field.kind, Kind::PositiveInteger) => {
                        match u32::try_from(value) {
                            Ok(number) => Some(number.to_string()),
                            Err(_) => invalid(
                                &mut self.error,
                                "Ensure this value is less than or equal to 4294967295.",
                            ),
                        }
                    }
                    Ok(value) if value < i128::from(i32::MIN) => invalid(
                        &mut self.error,
                        "Ensure this value is greater than or equal to -2147483648.",
                    ),
                    Ok(value) if value > i128::from(i32::MAX) => invalid(
                        &mut self.error,
                        "Ensure this value is less than or equal to 2147483647.",
                    ),
                    Ok(value) => Some(value.to_string()),
                    Err(_) => invalid(&mut self.error, "Enter a whole number."),
                }
            }
            Kind::Decimal => match priority(raw) {
                Ok(value) => Some(value),
                Err(reason) => invalid(&mut self.error, reason),
            },
            Kind::Checkbox => Some(raw.into()),
            Kind::Multiple(_) | Kind::Textarea | Kind::Json => {
                unreachable!("Field was cleaned above")
            }
            Kind::Relation(_) | Kind::Choice(_) => {
                let id = raw.parse::<i32>().ok();
                if self.options.iter().any(|&(option, _)| {
                    Some(option) == id
                        && (!matches!(self.field.kind, Kind::Choice(_))
                            || option.to_string() == raw)
                }) {
                    id.map(|id| id.to_string())
                } else if matches!(self.field.kind, Kind::Choice(_)) {
                    invalid(
                        &mut self.error,
                        &format!(
                            "Select a valid choice. {raw} is not one of the available choices."
                        ),
                    )
                } else {
                    invalid(
                        &mut self.error,
                        "Select a valid choice. That choice is not one of the available choices.",
                    )
                }
            }
        }
    }
}

fn eastern_date_input(stored: Option<&str>) -> Option<String> {
    stored
        .and_then(crate::admin_registration::parse_db_date)
        .map(|date| {
            New_York
                .from_utc_datetime(&date)
                .format("%Y-%m-%dT%H:%M")
                .to_string()
        })
}

fn eastern_datetime(raw: &str) -> Result<NaiveDateTime, String> {
    if let Ok(date) = DateTime::parse_from_rfc3339(raw) {
        return Ok(date.naive_utc());
    }
    let local = [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%d %H:%M",
    ]
    .into_iter()
    .find_map(|format| NaiveDateTime::parse_from_str(raw, format).ok())
    .or_else(|| {
        NaiveDate::parse_from_str(raw, "%Y-%m-%d")
            .ok()?
            .and_hms_opt(0, 0, 0)
    })
    .ok_or_else(|| "Enter a valid date/time.".to_owned())?;
    New_York.from_local_datetime(&local).single().map(|date| date.naive_utc()).ok_or_else(|| {
        format!("{local} couldn’t be interpreted in time zone America/New_York; it may be ambiguous or it may not exist.")
    })
}

pub(super) fn valid_email(value: &str) -> bool {
    let Some((local, domain)) = value.rsplit_once('@') else {
        return false;
    };
    if value.chars().count() > 320 || !valid_email_local(local) {
        return false;
    }
    if domain == "localhost" {
        return true;
    }
    if let Some(ip) = domain.strip_prefix('[').and_then(|ip| ip.strip_suffix(']')) {
        return ip
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() || matches!(byte, b':' | b'.'))
            && ip.parse::<std::net::IpAddr>().is_ok();
    }
    if valid_email_domain(domain) {
        return true;
    }
    !domain.is_ascii()
        && matches!(url::Host::parse(domain), Ok(url::Host::Domain(punycode)) if valid_email_domain(&punycode))
}

fn valid_email_local(local: &str) -> bool {
    if let Some(quoted) = local
        .strip_prefix('"')
        .and_then(|body| body.strip_suffix('"'))
    {
        let mut chars = quoted.chars();
        while let Some(ch) = chars.next() {
            if ch == '\\' {
                if !chars.next().is_some_and(|escaped| matches!(escaped, '\x01'..='\x09' | '\x0b'..='\x0c' | '\x0e'..='\x7f')) {
                    return false;
                }
            } else if !matches!(ch, '\x01'..='\x08' | '\x0b'..='\x0c' | '\x0e'..='\x1f' | '!' | '#'..='[' | ']'..='\x7f')
            {
                return false;
            }
        }
        return true;
    }
    local.split('.').all(|part| {
        !part.is_empty()
            && part
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || "-!#$%&'*+/=?^_`{}|~İıſK".contains(ch))
    })
}

fn valid_email_domain(domain: &str) -> bool {
    let Some((host, ending)) = domain.rsplit_once('.') else {
        return false;
    };
    let valid_chars = |ch: char| ch.is_ascii_alphanumeric() || "-İıſK".contains(ch);
    (2..=63).contains(&ending.chars().count())
        && !ending.ends_with('-')
        && ending.chars().all(valid_chars)
        && host.split('.').all(|label| {
            (1..=63).contains(&label.chars().count())
                && !label.starts_with('-')
                && !label.ends_with('-')
                && label.chars().all(valid_chars)
        })
}

fn priority(raw: &str) -> Result<String, &'static str> {
    const INVALID_NUMBER: &str = "Enter a number.";
    let (negative, raw) = match raw.as_bytes().first() {
        Some(b'-') => (true, &raw[1..]),
        Some(b'+') => (false, &raw[1..]),
        _ => (false, raw),
    };
    let (mantissa, exponent) = if let Some((coefficient, exponent)) = raw.split_once(['e', 'E']) {
        (
            coefficient,
            i64::from(exponent.parse::<i32>().map_err(|_| INVALID_NUMBER)?),
        )
    } else {
        (raw, 0)
    };
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    if whole.is_empty() && fraction.is_empty()
        || !whole
            .bytes()
            .chain(fraction.bytes())
            .all(|c| c.is_ascii_digit())
    {
        return Err(INVALID_NUMBER);
    }
    let digits = format!("{whole}{fraction}");
    let coefficient = digits.trim_start_matches('0');
    let exponent = exponent - fraction.len() as i64;
    let decimals = (-exponent).max(0);
    let digits = if exponent >= 0 && !coefficient.is_empty() {
        coefficient.len() as i64 + exponent
    } else {
        (coefficient.len() as i64).max(1).max(decimals)
    };
    if digits > 4 {
        return Err("Ensure that there are no more than 4 digits in total.");
    }
    if decimals > 2 {
        return Err("Ensure that there are no more than 2 decimal places.");
    }
    if digits - decimals > 2 {
        return Err("Ensure that there are no more than 2 digits before the decimal point.");
    }
    let cents = if coefficient.is_empty() {
        0
    } else {
        coefficient.parse::<u32>().map_err(|_| INVALID_NUMBER)? * 10_u32.pow((exponent + 2) as u32)
    };
    let sign = if negative && cents != 0 { "-" } else { "" };
    Ok(format!("{sign}{}.{:02}", cents / 100, cents % 100))
}
