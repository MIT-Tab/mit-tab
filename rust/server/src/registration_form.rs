use std::{
    collections::{BTreeMap, BTreeSet},
    num::ParseIntError,
};

use serde::Serialize;
use url::form_urlencoded;

pub const MAX_TEAMS: usize = 200;
pub const MAX_JUDGES: usize = 1_000;

pub fn integer_input(raw: &str) -> Result<i32, ParseIntError> {
    let whole = raw
        .rsplit_once('.')
        .filter(|(_, digits)| digits.bytes().all(|digit| digit == b'0'))
        .map_or(raw, |(whole, _)| whole);
    whole.parse()
}

#[derive(Default)]
pub struct Fields(BTreeMap<String, Vec<String>>);

impl Fields {
    pub fn parse(body: &[u8]) -> Self {
        let mut fields = Self::default();
        for (name, value) in form_urlencoded::parse(body) {
            fields
                .0
                .entry(name.into_owned())
                .or_default()
                .push(value.into_owned());
        }
        fields
    }

    pub fn push(&mut self, name: impl Into<String>, value: impl ToString) {
        self.0
            .entry(name.into())
            .or_default()
            .push(value.to_string());
    }

    pub fn get(&self, key: &str) -> &str {
        self.all(key)
            .last()
            .map(|value| value.trim())
            .unwrap_or_default()
    }

    pub fn all(&self, key: &str) -> &[String] {
        self.0.get(key).map(Vec::as_slice).unwrap_or_default()
    }

    pub(super) fn into_last_values(self) -> impl Iterator<Item = (String, String)> {
        self.0
            .into_iter()
            .filter_map(|(name, mut values)| values.pop().map(|value| (name, value)))
    }

    pub fn flag(&self, key: &str) -> bool {
        matches!(
            self.get(key).to_ascii_lowercase().as_str(),
            "true" | "on" | "1"
        )
    }
}

#[derive(Clone, Default, Serialize)]
pub struct SchoolField {
    pub value: String,
    pub name: String,
}

#[derive(Default, Serialize)]
pub struct Member {
    pub id: Option<i32>,
    pub name: String,
    pub email: String,
    pub use_private_email: bool,
    pub apda_id: Option<i32>,
    pub novice_status: i32,
    pub qualified: bool,
    pub needs_crash_housing: bool,
    pub requests_wgm_housing: bool,
    pub school: SchoolField,
}

#[derive(Default, Serialize)]
pub struct Team {
    pub id: Option<i32>,
    pub name: String,
    pub seed: i32,
    pub second_primary: bool,
    pub members: [Member; 2],
    pub extra_member_names: Vec<String>,
}

#[derive(Default, Serialize)]
pub struct Judge {
    pub id: Option<i32>,
    pub name: String,
    pub email: String,
    pub experience: u16,
    pub experience_display: Option<String>,
    pub needs_crash_housing: bool,
    pub requests_wgm_housing: bool,
    pub schools: Vec<SchoolField>,
    pub rounds: Vec<i32>,
}

#[derive(Default, Serialize)]
pub struct Registration {
    pub id: Option<i32>,
    pub revision: String,
    pub school: SchoolField,
    pub email: String,
    pub dietary_requests: String,
    pub housing_requests: String,
    pub teams: Vec<Team>,
    pub judges: Vec<Judge>,
    pub deleted_teams: BTreeSet<i32>,
    pub deleted_judges: BTreeSet<i32>,
}

impl Registration {
    pub fn from_form(fields: &Fields, last_round: i32) -> Option<Self> {
        let mut registration = Self {
            revision: fields.get("revision").into(),
            school: school(fields, "school"),
            email: fields.get("email").into(),
            dietary_requests: fields.get("dietary_requests").into(),
            housing_requests: fields.get("housing_requests").into(),
            ..Self::default()
        };
        for i in 0..count(fields, "teams-TOTAL_FORMS", MAX_TEAMS)? {
            let p = format!("teams-{i}-");
            if fields.flag(&format!("{p}DELETE")) {
                if let Some(id) = number(fields.get(&format!("{p}team_id")))?
                    && !registration.deleted_teams.insert(id)
                {
                    return None;
                }
                continue;
            }
            let member = |key: &str| {
                let p = format!("{p}{key}_");
                let read = |field| fields.get(&format!("{p}{field}"));
                let flag = |field| fields.flag(&format!("{p}{field}"));
                let wgm = flag("requests_wgm_housing");
                Some(Member {
                    id: number(read("id"))?,
                    name: read("name").into(),
                    email: if flag("use_private_email") && read("email").contains("***") {
                        String::new()
                    } else {
                        read("email").into()
                    },
                    use_private_email: flag("use_private_email"),
                    apda_id: number(read("apda_id"))?,
                    novice_status: number(read("novice_status"))?.unwrap_or_default(),
                    qualified: flag("qualified"),
                    needs_crash_housing: wgm || flag("needs_crash_housing"),
                    requests_wgm_housing: wgm,
                    school: school(fields, &format!("{p}school")),
                })
            };
            let read = |field| fields.get(&format!("{p}{field}"));
            let team = Team {
                id: number(read("team_id"))?,
                name: read("name").into(),
                seed: number(read("seed_choice"))?.unwrap_or_default(),
                second_primary: match read("team_school_source") {
                    "debater_two" => true,
                    "" | "debater_one" => false,
                    _ => return None,
                },
                members: [member("debater_one")?, member("debater_two")?],
                extra_member_names: Vec::new(),
            };
            if team.id.is_some()
                || !team.name.is_empty()
                || team
                    .members
                    .iter()
                    .any(|member| !member.name.is_empty() || !member.email.is_empty())
            {
                registration.teams.push(team);
            }
        }
        for i in 0..count(fields, "judges-TOTAL_FORMS", MAX_JUDGES)? {
            let p = format!("judges-{i}-");
            let read = |field| fields.get(&format!("{p}{field}"));
            let flag = |field| fields.flag(&format!("{p}{field}"));
            if flag("DELETE") {
                if let Some(id) = number(read("judge_id"))?
                    && !registration.deleted_judges.insert(id)
                {
                    return None;
                }
                continue;
            }
            let id = number(read("judge_id"))?;
            let rounds = (0..=last_round)
                .filter(|&round| {
                    if round == 0 {
                        flag("availability_outround")
                    } else {
                        fields.flag(&format!("{p}availability_round_{round}"))
                    }
                })
                .collect::<Vec<_>>();
            if id.is_none()
                && ["name", "email", "experience"]
                    .iter()
                    .all(|field| read(field).is_empty())
                && rounds.is_empty()
                && fields.all(&format!("{p}schools")).is_empty()
            {
                continue;
            }
            let wgm = flag("requests_wgm_housing");
            let schools = fields
                .all(&format!("{p}schools"))
                .iter()
                .map(|code| SchoolField {
                    value: code.trim().into(),
                    name: String::new(),
                })
                .collect();
            registration.judges.push(Judge {
                id,
                name: read("name").into(),
                email: read("email").into(),
                experience: integer_input(read("experience")).ok()?.try_into().ok()?,
                experience_display: None,
                needs_crash_housing: wgm || flag("needs_crash_housing"),
                requests_wgm_housing: wgm,
                schools,
                rounds,
            });
        }
        Some(registration)
    }

    pub fn preserve_private_contacts(&mut self, stored: &Self) {
        for member in self.teams.iter_mut().flat_map(|team| &mut team.members) {
            if member.email.is_empty()
                && member.id.is_some()
                && stored
                    .teams
                    .iter()
                    .flat_map(|team| &team.members)
                    .any(|existing| {
                        existing.use_private_email
                            && existing.id == member.id
                            && existing.apda_id == member.apda_id
                            && existing.school.value == member.school.value
                    })
            {
                member.use_private_email = true;
            }
        }
    }

    pub fn validate(&self) -> Result<(), &'static str> {
        if self.school.value.is_empty()
            || !valid_email(&self.email)
            || !length(&self.dietary_requests, 2000)
            || !length(&self.housing_requests, 2000)
        {
            return Err(
                "Select a school, enter a valid contact email, and limit requests to 2,000 characters.",
            );
        }
        if self.teams.is_empty() && self.judges.is_empty() {
            return Err("Add at least one team or one judge");
        }
        if self.teams.iter().filter(|team| team.seed == 1).count() > 1 {
            return Err("Select at most one free seed");
        }
        let mut teams = BTreeSet::new();
        let mut members = BTreeSet::new();
        for team in &self.teams {
            if !name(&team.name) || !(0..=3).contains(&team.seed) || !unique(&mut teams, team.id) {
                return Err("Enter a valid team name and seed for each team.");
            }
            for member in &team.members {
                if !name(&member.name)
                    || member.school.value.is_empty()
                    || !matches!(member.novice_status, 0 | 1)
                    || !unique(&mut members, member.id)
                {
                    return Err("Each team needs two debaters with a name, school, and division.");
                }
                if !(valid_email(&member.email)
                    || (member.email.is_empty()
                        && member.use_private_email
                        && member.apda_id.is_some_and(|id| id > 0)))
                {
                    return Err("Each debater needs an email");
                }
            }
        }
        let mut judges = BTreeSet::new();
        if self.judges.iter().any(|judge| {
            !name(&judge.name)
                || !valid_email(&judge.email)
                || judge.experience > 10
                || !unique(&mut judges, judge.id)
        }) {
            return Err("Enter a valid name, email, and years of experience for each judge.");
        }
        Ok(())
    }
}

fn number(value: &str) -> Option<Option<i32>> {
    if value.is_empty() {
        Some(None)
    } else {
        integer_input(value).ok().map(Some)
    }
}

fn count(fields: &Fields, key: &str, maximum: usize) -> Option<usize> {
    fields
        .get(key)
        .parse()
        .ok()
        .filter(|count| *count <= maximum)
}

fn school(fields: &Fields, key: &str) -> SchoolField {
    SchoolField {
        value: fields.get(key).into(),
        name: fields.get(&format!("{key}_name")).into(),
    }
}

fn unique(ids: &mut BTreeSet<i32>, id: Option<i32>) -> bool {
    id.is_none_or(|id| id > 0 && ids.insert(id))
}

fn name(value: &str) -> bool {
    !value.is_empty() && length(value, 30) && !value.chars().any(char::is_control)
}

pub fn length(value: &str, limit: usize) -> bool {
    value.chars().count() <= limit
}

pub fn valid_email(value: &str) -> bool {
    let Some((local, host)) = value.rsplit_once('@') else {
        return false;
    };
    length(value, 254)
        && !local.is_empty()
        && !local.contains('@')
        && !local.starts_with('.')
        && !local.ends_with('.')
        && !local.contains("..")
        && !value
            .chars()
            .any(|ch| ch.is_whitespace() || ch.is_control() || "<>,;".contains(ch))
        && (host == "localhost"
            || (host.contains('.')
                && host.split('.').all(|label| {
                    !label.is_empty()
                        && !label.starts_with('-')
                        && !label.ends_with('-')
                        && label.chars().all(|ch| ch.is_alphanumeric() || ch == '-')
                })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registration_form_preserves_repeated_school_values_and_skips_blank_rows() {
        let values = Fields::parse(b"teams-TOTAL_FORMS=1&judges-TOTAL_FORMS=1&judges-0-name=Judge&judges-0-email=j%40a.test&judges-0-experience=2&judges-0-schools=pk%3A1&judges-0-schools=pk%3A2&judges-0-availability_outround=on");
        let registration = Registration::from_form(&values, 4).unwrap();
        assert!(registration.teams.is_empty());
        let judge = &registration.judges[0];
        assert_eq!(
            judge
                .schools
                .iter()
                .map(|school| school.value.as_str())
                .collect::<Vec<_>>(),
            ["pk:1", "pk:2"]
        );
        assert_eq!(judge.rounds, [0]);
    }
}
