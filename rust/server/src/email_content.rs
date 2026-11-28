use std::collections::BTreeMap;

use minijinja::context;
use serde::Serialize;
use sqlx::{MySqlConnection, Row};

use crate::email::Email;

pub fn portal_path(section: &str, code: &str) -> Option<String> {
    let valid = match section {
        "registration" => crate::registration_views::valid_code(code),
        "team_portal" => crate::team_portal::valid_code(code),
        _ => false,
    };
    valid.then(|| format!("/{section}/{code}/"))
}

pub fn team(
    address: &str,
    name: &str,
    code: &str,
    debaters: &[String],
    tournament: &str,
    portal_url: &str,
) -> Result<Email, sqlx::Error> {
    let debater_names = debaters.join(", ");
    Email::templated(
        address.into(),
        format!("Your team portal for {tournament}"),
        format!(
            "Hello,\n\nYour team has been registered for {tournament}. You can open your team portal at the link below to manage team-specific actions, including judge scratches when they are open.\n\nTeam: {name}\nDebaters: {debater_names}\nTeam code: {code}\nTeam portal: {portal_url}\n\nThanks,\nThe Tab Team",
        ),
        "emails/team_portal.html",
        "Open your team portal for tournament actions.",
        context! { team_name => name, team_code => code, tournament_name => tournament, debater_names, portal_url },
    )
}

pub fn judge(
    address: &str,
    name: &str,
    code: &str,
    rounds: &[i32],
    origin: &str,
    tournament: &str,
    registration: bool,
) -> Result<Email, sqlx::Error> {
    let availability = availability(rounds);
    let portal_search_url = format!("{origin}/public/e-ballots/");
    let portal_url = format!("{portal_search_url}{code}/");
    let confirmation = if registration {
        format!("You have been registered as a judge for {tournament}.\n\n")
    } else {
        String::new()
    };
    Email::templated(
        address.into(),
        format!("Your judge code for {tournament}"),
        format!(
            "Hi {name},\n\n{confirmation}Your judge code for {tournament} is: {code}\nYour current availability is: {availability}.\n\nUse this code to open the judge portal, update availability, and submit ballots at {portal_url}. You can also search by judge code at {portal_search_url}.\n\nThanks,\nThe Tab Team",
        ),
        "emails/judge_ballot_code.html",
        &format!("Your judge code for {tournament} is {code}."),
        context! {
            judge_name => name, tournament_name => tournament, ballot_code => code, availability,
            portal_url, portal_search_url, include_registration_confirmation => registration,
        },
    )
}

pub fn written(
    address: &str,
    round: i32,
    teams: [&str; 2],
    winner: &str,
    chair: &str,
    rfd: &str,
    tournament: &str,
) -> Result<Email, sqlx::Error> {
    let [gov, opp] = teams;
    Email::templated(
        address.into(),
        format!("Round {round} decision for {tournament}"),
        format!(
            "Hello,\n\nThe judge for your Round {round} debate at {tournament} has submitted a written reason for decision. It is included below.\n\nTournament: {tournament}\nRound: {round}\nGovernment: {gov}\nOpposition: {opp}\nWinner: {winner}\nJudge: {chair}\n\nReason for decision:\n{rfd}\n\nThanks,\nThe Tab Team",
        ),
        "emails/written_rfd.html",
        &format!("Written reason for decision from {chair} for Round {round}."),
        context! {
            tournament_name => tournament, round_number => round, gov_team => gov, opp_team => opp,
            winner_name => winner, judge_name => chair, rfd_text => rfd,
        },
    )
}

pub struct Registration {
    pub id: i32,
    pub school: String,
    pub code: String,
    pub email: String,
    dietary: String,
    housing: String,
    teams: Vec<RegistrationTeam>,
    judges: Vec<RegistrationJudge>,
}

struct RegistrationTeam {
    name: String,
    school: String,
    seed: String,
    debaters: Vec<RegistrationDebater>,
}

#[derive(Serialize)]
struct RegistrationDebater {
    name: String,
    school_name: String,
    status: &'static str,
    apda_id: String,
    qualified: &'static str,
}

struct RegistrationJudge {
    id: i32,
    name: String,
    experience: String,
}

type Links = Vec<(String, String, String)>;

pub struct Confirmations {
    pub registrations: Vec<Registration>,
    links: Links,
    judge_schools: BTreeMap<i32, Vec<String>>,
    judge_rounds: BTreeMap<i32, Vec<i32>>,
}

impl Confirmations {
    pub async fn load(
        db: &mut MySqlConnection,
        registration: Option<i32>,
    ) -> Result<Self, sqlx::Error> {
        let rows = sqlx::query(
            "SELECT r.id, s.name, r.herokunator_code, r.email, r.dietary_requests, r.housing_requests
             FROM registration_registration r JOIN tab_school s ON s.id = r.school_id
             WHERE (? IS NULL OR r.id = ?) ORDER BY s.name, r.created_at DESC, r.id",
        )
        .bind(registration).bind(registration).fetch_all(&mut *db).await?;
        let links = sqlx::query_as::<_, (String, String, String)>(
            "SELECT title, url, description FROM registration_registrationlink WHERE is_active
             ORDER BY display_order, created_at, id",
        )
        .fetch_all(&mut *db)
        .await?
        .into_iter()
        .filter(|(_, url, _)| {
            url::Url::parse(url).is_ok_and(|url| matches!(url.scheme(), "http" | "https"))
        })
        .collect();

        let mut members: BTreeMap<i32, Vec<RegistrationDebater>> = BTreeMap::new();
        for row in sqlx::query(
            "SELECT m.team_id, d.name, COALESCE(s.name, 'No school') AS school, d.novice_status,
                    d.apda_id, d.qualified
             FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id
              JOIN tab_team t ON t.id = m.team_id LEFT JOIN tab_school s ON s.id = d.school_id
             WHERE t.registration_id IS NOT NULL AND (? IS NULL OR t.registration_id = ?)
             ORDER BY d.name, d.id",
        )
        .bind(registration)
        .bind(registration)
        .fetch_all(&mut *db)
        .await?
        {
            let status = match row.try_get::<i32, _>("novice_status")? {
                0 => "Varsity",
                1 => "Novice",
                _ => "Unknown",
            };
            let apda_id = row
                .try_get::<Option<i32>, _>("apda_id")?
                .filter(|&id| id != -1)
                .map_or_else(|| "None".into(), |id| id.to_string());
            let qualified = if row.try_get("qualified")? {
                "yes"
            } else {
                "no"
            };
            members
                .entry(row.try_get("team_id")?)
                .or_default()
                .push(RegistrationDebater {
                    name: row.try_get("name")?,
                    school_name: row.try_get("school")?,
                    status,
                    apda_id,
                    qualified,
                });
        }

        let mut teams: BTreeMap<i32, Vec<RegistrationTeam>> = BTreeMap::new();
        for (registration_id, id, name, school, seed) in sqlx::query_as::<_, (i32, i32, String, String, i32)>(
            "SELECT t.registration_id, t.id, t.name, s.name, t.seed
             FROM tab_team t JOIN tab_school s ON s.id = t.school_id
             WHERE t.registration_id IS NOT NULL AND (? IS NULL OR t.registration_id = ?) ORDER BY t.id",
        )
        .bind(registration).bind(registration).fetch_all(&mut *db).await? {
            let seed = match seed {
                0 => "Unseeded".into(),
                1 => "Free Seed".into(),
                2 => "Half Seed".into(),
                3 => "Full Seed".into(),
                value => value.to_string(),
            };
            teams.entry(registration_id).or_default().push(RegistrationTeam {
                name, school, seed, debaters: members.remove(&id).unwrap_or_default(),
            });
        }
        let mut judges: BTreeMap<i32, Vec<RegistrationJudge>> = BTreeMap::new();
        for (registration_id, id, name, experience) in sqlx::query_as::<_, (i32, i32, String, String)>(
            "SELECT registration_id, id, name, COALESCE(CAST(reported_experience AS CHAR), CAST(j.rank AS CHAR)) AS experience
             FROM tab_judge j WHERE registration_id IS NOT NULL AND (? IS NULL OR registration_id = ?)
             ORDER BY name, id",
        )
        .bind(registration).bind(registration).fetch_all(&mut *db).await? {
            judges.entry(registration_id).or_default().push(RegistrationJudge { id, name, experience });
        }

        let mut judge_schools: BTreeMap<i32, Vec<String>> = BTreeMap::new();
        for (id, school) in sqlx::query_as::<_, (i32, String)>(
            "SELECT j.id, s.name FROM tab_judge j JOIN tab_judge_schools m ON m.judge_id = j.id
              JOIN tab_school s ON s.id = m.school_id
             WHERE j.registration_id IS NOT NULL AND (? IS NULL OR j.registration_id = ?) ORDER BY s.name, s.id",
        )
        .bind(registration).bind(registration).fetch_all(&mut *db).await? {
            judge_schools.entry(id).or_default().push(school);
        }
        let mut judge_rounds: BTreeMap<i32, Vec<i32>> = BTreeMap::new();
        for (id, round) in sqlx::query_as::<_, (i32, i32)>(
            "SELECT j.id, c.round_number FROM tab_judge j JOIN tab_judgeexpectedcheckin c ON c.judge_id = j.id
             WHERE j.registration_id IS NOT NULL AND (? IS NULL OR j.registration_id = ?) ORDER BY c.round_number",
        )
        .bind(registration).bind(registration).fetch_all(db).await? {
            judge_rounds.entry(id).or_default().push(round);
        }
        let registrations = rows
            .into_iter()
            .map(|row| {
                let id = row.try_get("id")?;
                Ok(Registration {
                    id,
                    school: row.try_get("name")?,
                    code: row.try_get("herokunator_code")?,
                    email: row.try_get("email")?,
                    dietary: row.try_get("dietary_requests")?,
                    housing: row.try_get("housing_requests")?,
                    teams: teams.remove(&id).unwrap_or_default(),
                    judges: judges.remove(&id).unwrap_or_default(),
                })
            })
            .collect::<Result<_, sqlx::Error>>()?;
        Ok(Self {
            registrations,
            links,
            judge_schools,
            judge_rounds,
        })
    }

    pub fn email(
        &self,
        registration: &Registration,
        origin: &str,
        tournament: &str,
    ) -> Result<Option<Email>, sqlx::Error> {
        let Registration {
            school,
            code,
            email,
            dietary,
            housing,
            ..
        } = registration;
        let Some(path) = portal_path("registration", code).filter(|_| !email.is_empty()) else {
            return Ok(None);
        };
        let edit_url = format!("{origin}{path}");
        let mut text = format!(
            "Hi {school},\n\nYour registration for {tournament} has been received. A summary of the teams and judges you registered is included below for your records.\n\nEdit your registration any time before the tournament: {edit_url}\nRegistration code: {code}\n\n",
        );
        let links: Vec<_> = self
            .links
            .iter()
            .map(|(title, url, description)| {
                context! { title, url, description }
            })
            .collect();
        if !self.links.is_empty() {
            text.push_str("A few more things to complete:\n");
            for (title, url, description) in &self.links {
                let description = if description.is_empty() {
                    String::new()
                } else {
                    format!(" ({description})")
                };
                text.push_str(&format!("- {title}: {url}{description}\n"));
            }
            text.push('\n');
        }
        let present = |value: &str| {
            if value.is_empty() {
                "None".into()
            } else {
                value.to_owned()
            }
        };
        text.push_str(&format!(
            "School and contact\n  School: {school}\n  Email:  {email}\n  Dietary requests: {}\n  Other housing requests: {}\n\nTeams\n",
            present(dietary), present(housing),
        ));
        if registration.teams.is_empty() {
            text.push_str("  None\n");
        }
        let teams: Vec<_> = registration.teams.iter().map(|team| {
            let RegistrationTeam { name, school, seed, debaters, .. } = team;
            text.push_str(&format!("- {name}\n  School protection: {school}\n  Seed: {seed}\n  Debaters:\n"));
            for debater in debaters {
                let RegistrationDebater { name, school_name, status, apda_id, qualified } = debater;
                text.push_str(&format!("    - {name} ({school_name}; {status}; APDA ID: {apda_id}; Qualified: {qualified})\n"));
            }
            context! { name, school_name => school, seed, debaters }
        }).collect();

        text.push_str("\nJudges\n");
        if registration.judges.is_empty() {
            text.push_str("  None\n");
        }
        let judges: Vec<_> = registration.judges.iter().map(|judge| {
            let RegistrationJudge { id, name, experience } = judge;
            let schools = self.judge_schools.get(id).map_or_else(|| school.clone(), |schools| schools.join(", "));
            let availability = availability(self.judge_rounds.get(id).map_or(&[], |rounds| rounds.as_slice()));
            text.push_str(&format!("- {name}\n  Self-reported experience: {experience}\n  Schools: {schools}\n  Availability: {availability}\n"));
            context! { name, rank => experience, schools, availability }
        }).collect();
        text.push_str("\nThanks,\nThe Tab Team");
        Email::templated(
            email.clone(),
            format!("Registration confirmed: {tournament}"),
            text,
            "emails/registration_confirmation.html",
            &format!("Confirmation code {code}. You can edit your registration any time before the tournament."),
            context! {
                school_name => school, contact_email => email, dietary_requests => dietary,
                housing_requests => housing, tournament_name => tournament, edit_url,
                registration_code => code, followup_links => links, teams, judges,
            },
        ).map(Some)
    }
}

fn availability(rounds: &[i32]) -> String {
    if rounds.is_empty() {
        return "None selected".into();
    }
    rounds
        .iter()
        .map(|round| {
            if *round == 0 {
                "Outrounds".into()
            } else {
                format!("Round {round}")
            }
        })
        .collect::<Vec<_>>()
        .join(", ")
}
