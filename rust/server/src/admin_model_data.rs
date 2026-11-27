use sqlx::{AssertSqlSafe, MySql, MySqlConnection, QueryBuilder};

use crate::{
    admin_changelist::{self, DefaultList},
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
    "tabsettings",
];

pub struct Resource {
    pub list: DefaultList,
    pub fields: &'static [Field],
    pub unique: Option<Unique>,
}

pub struct Field {
    pub name: &'static str,
    pub column: &'static str,
    pub label: &'static str,
    pub kind: Kind,
    pub required: bool,
    pub initial: Option<&'static str>,
}

#[derive(Clone, Copy)]
pub enum Kind {
    Text(usize),
    Integer,
    Decimal,
    Checkbox,
    Relation(Relation),
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

impl Field {
    const fn new(name: &'static str, label: &'static str, kind: Kind) -> Self {
        Self {
            name,
            column: name,
            label,
            kind,
            required: true,
            initial: None,
        }
    }

    const fn relation(
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

    const fn optional(self) -> Self {
        Self {
            required: false,
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
        let (fields, unique): (&'static [Field], _) = match name {
            "bye" => (
                const {
                    &[
                        Field::relation("bye_team", "bye_team_id", "Bye team", TEAM),
                        ROUND,
                    ]
                },
                None,
            ),
            "noshow" => (
                const {
                    &[
                        Field::relation("no_show_team", "no_show_team_id", "No show team", TEAM),
                        ROUND,
                    ]
                },
                None,
            ),
            "checkin" => (
                const { &[Field::relation("judge", "judge_id", "Judge", JUDGE), ROUND] },
                Some(Unique {
                    fields: &[0, 1],
                    error_field: None,
                    message: "Check in with this Judge and Round number already exists.",
                }),
            ),
            "roomcheckin" => (
                const { &[Field::relation("room", "room_id", "Room", ROOM), ROUND] },
                Some(Unique {
                    fields: &[0, 1],
                    error_field: None,
                    message: "Room check in with this Room and Round number already exists.",
                }),
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
                Some(Unique {
                    fields: &[0],
                    error_field: Some(0),
                    message: "Breaking team with this Team already exists.",
                }),
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
                Some(Unique {
                    fields: &[0],
                    error_field: Some(0),
                    message: "Tournament setup preference with this User already exists.",
                }),
            ),
            "roomtag" => (
                const {
                    &[
                        Field::new("tag", "Tag", Kind::Text(255)),
                        Field::new("priority", "Priority", Kind::Decimal),
                    ]
                },
                None,
            ),
            "tabsettings" => (
                const {
                    &[
                        Field::new("key", "Key", Kind::Text(50)),
                        Field::new("value", "Value", Kind::Integer).optional(),
                        Field::new("value_string", "Value string", Kind::Text(200)).optional(),
                    ]
                },
                None,
            ),
            _ => return None,
        };
        Some(Self {
            list: admin_changelist::default_list(name)?,
            fields,
            unique,
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
            .map(|field| format!("CAST(`{}` AS CHAR)", field.column))
            .collect::<Vec<_>>()
            .join(", ");
        let suffix = if lock { " FOR UPDATE" } else { "" };
        let raw: Option<String> = sqlx::query_scalar(AssertSqlSafe(format!(
            "SELECT CAST(JSON_ARRAY({values}) AS CHAR) FROM {} WHERE id = ?{suffix}",
            self.list.table(),
        )))
        .bind(id)
        .fetch_optional(db)
        .await?;
        raw.map(|raw| serde_json::from_str(&raw).map_err(|error| sqlx::Error::Decode(error.into())))
            .transpose()
    }

    pub async fn duplicate(
        &self,
        db: &mut MySqlConnection,
        id: Option<i32>,
        values: &[Option<String>],
    ) -> Result<bool, sqlx::Error> {
        let Some(unique) = &self.unique else {
            return Ok(false);
        };
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
    ) -> Result<i32, sqlx::Error> {
        if let Some(id) = id {
            let mut sql = QueryBuilder::<MySql>::new(format!("UPDATE {} SET ", self.list.table()));
            let mut columns = sql.separated(", ");
            for (field, value) in self.fields.iter().zip(values) {
                columns
                    .push(format!("`{}` = ", field.column))
                    .push_bind_unseparated(value);
            }
            sql.push(" WHERE id = ")
                .push_bind(id)
                .build()
                .execute(db)
                .await?;
            return Ok(id);
        }
        let columns = self
            .fields
            .iter()
            .map(|field| format!("`{}`", field.column))
            .collect::<Vec<_>>()
            .join(", ");
        let mut sql = QueryBuilder::<MySql>::new(format!(
            "INSERT INTO {} ({columns}) VALUES (",
            self.list.table()
        ));
        let mut columns = sql.separated(", ");
        for value in values {
            columns.push_bind(value);
        }
        let result = sql.push(")").build().execute(db).await?;
        i32::try_from(result.last_insert_id()).map_err(|error| sqlx::Error::Decode(error.into()))
    }
}

pub struct Form {
    pub fields: Vec<BoundField>,
    pub non_field_error: Option<&'static str>,
}

pub struct BoundField {
    pub field: &'static Field,
    pub value: Option<String>,
    pub options: Vec<(i32, String)>,
    pub error: Option<String>,
}

impl Form {
    pub async fn load(
        resource: &Resource,
        db: &mut MySqlConnection,
        initial: Option<&[Option<String>]>,
        post: Option<&Fields>,
    ) -> Result<Self, sqlx::Error> {
        let mut fields = Vec::new();
        for (index, field) in resource.fields.iter().enumerate() {
            let value = if let Some(post) = post {
                match field.kind {
                    Kind::Checkbox => Some(if post.flag(field.name) { "1" } else { "0" }.into()),
                    _ => post.all(field.name).last().cloned(),
                }
            } else if let Some(initial) = initial {
                initial[index].clone()
            } else {
                field.initial.map(str::to_owned)
            };
            let options = match field.kind {
                Kind::Relation(relation) => relation.options(db).await?,
                Kind::Choice(choices) => choices
                    .iter()
                    .map(|&(id, text)| (id, text.to_owned()))
                    .collect(),
                _ => Vec::new(),
            };
            fields.push(BoundField {
                field,
                value,
                options,
                error: None,
            });
        }
        Ok(Self {
            fields,
            non_field_error: None,
        })
    }

    pub async fn clean(
        &mut self,
        resource: &Resource,
        db: &mut MySqlConnection,
        id: Option<i32>,
    ) -> Result<Option<Vec<Option<String>>>, sqlx::Error> {
        let values: Vec<_> = self.fields.iter_mut().map(BoundField::clean).collect();
        if self.fields.iter().any(|field| field.error.is_some()) {
            return Ok(None);
        }
        if resource.duplicate(db, id, &values).await? {
            self.mark_duplicate(resource);
            return Ok(None);
        }
        Ok(Some(values))
    }

    pub fn mark_duplicate(&mut self, resource: &Resource) {
        if let Some(unique) = &resource.unique {
            if let Some(index) = unique.error_field {
                self.fields[index].error = Some(unique.message.into());
            } else {
                self.non_field_error = Some(unique.message);
            }
        }
    }
}

impl BoundField {
    fn clean(&mut self) -> Option<String> {
        let raw = self.value.as_deref().unwrap_or_default().trim();
        let invalid = |error: &mut Option<String>, message: &str| {
            *error = Some(message.into());
            None
        };
        if raw.is_empty() {
            return if self.field.required {
                invalid(&mut self.error, "This field is required.")
            } else {
                None
            };
        }
        match self.field.kind {
            Kind::Text(max) => {
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
                Some(raw.into())
            }
            Kind::Integer => {
                let integer = if let Some((number, fraction)) = raw.split_once('.')
                    && fraction.bytes().all(|digit| digit == b'0')
                {
                    number
                } else {
                    raw
                };
                match integer.parse::<i128>() {
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
            Kind::Relation(_) | Kind::Choice(_) => {
                let id = raw.parse::<i32>().ok();
                if self.options.iter().any(|&(option, _)| Some(option) == id) {
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
