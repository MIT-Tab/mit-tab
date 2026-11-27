use std::{
    collections::HashMap, fs::OpenOptions, net::IpAddr, os::unix::fs::OpenOptionsExt,
    path::PathBuf, time::Duration,
};

use mittab_tabulation::{
    ballot_entry::ROLES, ballots::RoundFilter, legacy_random::LegacyRandom, rankings::Stat,
};
use reqwest::{Client, StatusCode, Url, header, redirect};
use ring::rand::{SecureRandom, SystemRandom};
use sqlx::MySqlPool;

use crate::{
    ballot_entry::{self, WriteBallot},
    judge_portal, passwords,
    public_rankings::policy,
    settings::Settings,
    speakers,
    tournament_data::TournamentData,
    tournament_maintenance,
};

type CsvRows = Vec<Vec<String>>;

struct Export {
    team: CsvRows,
    novice_team: CsvRows,
    speaker: CsvRows,
    novice_speaker: CsvRows,
}

const TEAM_COLUMNS: &[&str] = &[
    "Team Name",
    "School",
    "Hyrbid School",
    "Debater 1",
    "Debater 2",
    "Wins",
    "Speaks",
    "Ranks",
];
const SPEAKER_COLUMNS: &[&str] = &["Name", "School", "Speaks", "Ranks"];

pub async fn export_stats(pool: &MySqlPool, args: &[String]) -> Result<(), &'static str> {
    let mut options = [
        ("--root", "."),
        ("--team-file", "teams.csv"),
        ("--nov-team-file", "nov-teams.csv"),
        ("--debater-file", "debaters.csv"),
        ("--nov-debater-file", "nov-debaters.csv"),
    ];
    for pair in args.chunks(2) {
        let [key, value] = pair else {
            return Err("Each export-stats option requires a value.");
        };
        let Some((_, setting)) = options.iter_mut().find(|(name, _)| name == key) else {
            return Err("Unknown export-stats option.");
        };
        if value.is_empty() {
            return Err("Export paths cannot be empty.");
        }
        *setting = value;
    }
    let export = stats(pool)
        .await
        .map_err(|_| "Could not calculate tournament statistics.")?;
    let directory = PathBuf::from(options[0].1);
    let reports = [
        (options[1].1.to_owned(), TEAM_COLUMNS, export.team),
        (options[2].1.to_owned(), TEAM_COLUMNS, export.novice_team),
        (options[3].1.to_owned(), SPEAKER_COLUMNS, export.speaker),
        (
            options[4].1.to_owned(),
            SPEAKER_COLUMNS,
            export.novice_speaker,
        ),
    ];
    tokio::task::spawn_blocking(move || write_reports(directory, reports))
        .await
        .map_err(|_| "Could not write the statistics exports.")?
}

type TeamInfo = (i32, String, String, Option<String>);
type SpeakerInfo = (i32, String, Option<String>);

async fn stats(pool: &MySqlPool) -> Result<Export, sqlx::Error> {
    let mut db = pool.begin().await?;
    let settings = Settings::load(&mut db, speakers::SETTINGS).await?;
    let teams = TournamentData::load(&mut db)
        .await?
        .scores(
            policy(&settings),
            RoundFilter {
                exclude: None,
                through: None,
            },
        )
        .map_err(|message| sqlx::Error::Protocol(message.into()))?;
    let speakers = speakers::ordered_speaker_rows(&mut db, &settings).await?;
    let team_names: HashMap<_, _> = sqlx::query_as::<_, TeamInfo>(
        "SELECT t.id, t.name, s.name, h.name FROM tab_team t JOIN tab_school s ON s.id = t.school_id
         LEFT JOIN tab_school h ON h.id = t.hybrid_school_id",
    ).fetch_all(&mut *db).await?.into_iter().map(|(id, name, school, hybrid)| (i64::from(id), (name, school, hybrid))).collect();
    let speaker_names: HashMap<_, _> = sqlx::query_as::<_, SpeakerInfo>(
        "SELECT d.id, d.name, t.name FROM tab_debater d LEFT JOIN tab_team t
         ON t.id = (SELECT MIN(m.team_id) FROM tab_team_debaters m WHERE m.debater_id = d.id)",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(id, name, team)| (id, (name, team)))
    .collect();
    let mut people: HashMap<i64, Vec<(String, bool)>> = HashMap::new();
    for (team, name, novice) in sqlx::query_as::<_, (i32, String, i32)>(
        "SELECT m.team_id, d.name, d.novice_status FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id ORDER BY d.name, d.id",
    ).fetch_all(&mut *db).await? {
        people.entry(i64::from(team)).or_default().push((name, novice == 1));
    }
    db.commit().await?;
    let mut exports = Export {
        team: Vec::new(),
        novice_team: Vec::new(),
        speaker: Vec::new(),
        novice_speaker: Vec::new(),
    };
    for row in teams {
        let (name, school, hybrid) = &team_names[&row.id];
        let members = people.get(&row.id).map_or(&[][..], Vec::as_slice);
        let first = members.first().map_or("", |(name, _)| name.as_str());
        let last = members
            .get(1..)
            .and_then(|rest| rest.last())
            .map_or("", |(name, _)| name.as_str());
        let cells = vec![
            name.clone(),
            school.clone(),
            hybrid.clone().unwrap_or_default(),
            first.into(),
            last.into(),
            (row.score.get(Stat::Wins) as i32).to_string(),
            row.score.get(Stat::Speaks).to_string(),
            row.score.get(Stat::Ranks).to_string(),
        ];
        if members.iter().all(|(_, novice)| *novice) {
            exports.novice_team.push(cells.clone());
        }
        exports.team.push(cells);
    }
    for row in speakers {
        let (name, team) = &speaker_names[&row.debater.id];
        let cells = vec![
            name.clone(),
            team.clone().unwrap_or_default(),
            row.score.get(Stat::Speaks).to_string(),
            row.score.get(Stat::Ranks).to_string(),
        ];
        if row.debater.novice_status == 1 {
            exports.novice_speaker.push(cells.clone());
        }
        exports.speaker.push(cells);
    }
    Ok(exports)
}

type CsvReport = (String, &'static [&'static str], CsvRows);

fn write_reports(directory: PathBuf, reports: [CsvReport; 4]) -> Result<(), &'static str> {
    let error = "Could not write the statistics exports.";
    let paths = reports.each_ref().map(|(name, _, _)| directory.join(name));
    for (index, path) in paths.iter().enumerate() {
        if paths[..index].contains(path) || path.file_name().is_none() || path.is_dir() {
            return Err("Choose a distinct output file for each report.");
        }
    }
    struct Staged(PathBuf);
    impl Drop for Staged {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    std::fs::create_dir_all(directory).map_err(|_| error)?;
    let mut staged = Vec::new();
    for (path, (_, headers, rows)) in paths.iter().zip(reports) {
        let temporary =
            path.with_file_name(format!(".mittab-stats-{}", passwords::random_secret()?));
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)
            .map_err(|_| error)?;
        let temporary = Staged(temporary);
        let mut writer = csv::WriterBuilder::new()
            .terminator(csv::Terminator::CRLF)
            .from_writer(file);
        writer.write_record(headers).map_err(|_| error)?;
        for row in rows {
            writer.write_record(row).map_err(|_| error)?;
        }
        writer.flush().map_err(|_| error)?;
        staged.push(temporary);
    }
    for (source, destination) in staged.iter().zip(paths) {
        std::fs::rename(&source.0, destination).map_err(|_| error)?;
    }
    Ok(())
}

pub async fn load_test(pool: &MySqlPool, args: &[String]) -> Result<(), &'static str> {
    if !tournament_maintenance::simulation_enabled() {
        return Err("Load testing is available for development and test deployments.");
    }
    let mut host = "127.0.0.1:8000";
    let mut connections = 10;
    for option in args.chunks(2) {
        let [name, value] = option else {
            return Err("Each load-test option requires a value.");
        };
        match name.as_str() {
            "--host" => host = value,
            "--connections" => {
                connections = value
                    .parse::<usize>()
                    .ok()
                    .filter(|count| (1..=50).contains(count))
                    .ok_or("Connections must be between 1 and 50.")?;
            }
            _ => return Err("Unknown load-test option."),
        }
    }
    let address = local_address(host)?;
    let client = Client::builder()
        .timeout(Duration::from_secs(10))
        .redirect(redirect::Policy::none())
        .no_proxy()
        .build()
        .map_err(|_| "Could not start the load-test HTTP client.")?;
    let ballots = load_ballots(pool)
        .await
        .map_err(|_| "Could not load outstanding ballots.")?;
    let attempted = ballots.len();
    let mut failed = 0;
    let mut ballots = ballots.into_iter();
    let mut pending = tokio::task::JoinSet::new();
    loop {
        while pending.len() < connections {
            let Some(ballot) = ballots.next() else { break };
            let client = client.clone();
            let address = address.clone();
            pending.spawn(async move { send_ballot(&client, &address, ballot).await });
        }
        let Some(result) = pending.join_next().await else {
            break;
        };
        if !matches!(result, Ok(Some(()))) {
            failed += 1;
        }
    }
    eprintln!(
        "Load test submitted {} of {attempted} ballots.",
        attempted - failed
    );
    if failed == 0 {
        Ok(())
    } else {
        Err(
            "Some ballots could not be submitted. Check that pairings are released and chairs have access codes.",
        )
    }
}

fn local_address(host: &str) -> Result<Url, &'static str> {
    let err = "Load testing requires a loopback HTTP address.";
    let mut address = Url::parse(&format!("http://{host}")).map_err(|_| err)?;
    if address.host_str() == Some("localhost") {
        address.set_host(Some("127.0.0.1")).map_err(|_| err)?;
    }
    let ip = address
        .host_str()
        .and_then(|host| host.trim_matches(['[', ']']).parse::<IpAddr>().ok())
        .ok_or(err)?;
    if !ip.is_loopback()
        || address.path() != "/"
        || address.query().is_some()
        || address.fragment().is_some()
        || !address.username().is_empty()
        || address.password().is_some()
    {
        return Err(err);
    }
    Ok(address)
}

struct LoadBallot {
    code: String,
    fields: Vec<(String, String)>,
}

async fn load_ballots(pool: &MySqlPool) -> Result<Vec<LoadBallot>, sqlx::Error> {
    let mut db = pool.begin().await?;
    let current = Settings::load(&mut db, &["cur_round"])
        .await?
        .integer("cur_round", 1);
    let rows = sqlx::query_as::<_, (i32, String)>(
        "SELECT r.id, COALESCE(j.ballot_code, '') FROM tab_round r
         LEFT JOIN tab_judge j ON j.id = r.chair_id WHERE r.round_number = ? AND r.victor = 0 ORDER BY r.id",
    ).bind(current.saturating_sub(1)).fetch_all(&mut *db).await?;
    let mut seed = [0; 8];
    SystemRandom::new()
        .fill(&mut seed)
        .map_err(|_| sqlx::Error::Protocol("No entropy available".into()))?;
    let mut rng = LegacyRandom::seeded(u64::from_ne_bytes(seed));
    let mut ballots = Vec::new();
    for (id, code) in rows {
        let debate = ballot_entry::load(&mut db, id)
            .await?
            .ok_or(sqlx::Error::RowNotFound)?;
        let ballot = ballot_entry::simulate(&debate, &mut rng)
            .map_err(|message| sqlx::Error::Protocol(message.into()))?;
        let WriteBallot::Scores(winner, scores) = ballot else {
            continue;
        };
        let mut fields = vec![
            ("ballot_code".into(), code.clone()),
            ("round_instance".into(), id.to_string()),
            ("winner".into(), winner.to_string()),
        ];
        for (role, debater, speaks, rank) in scores {
            let role = ROLES[role];
            fields.extend([
                (format!("{role}_debater"), debater.to_string()),
                (format!("{role}_speaks"), speaks),
                (format!("{role}_ranks"), rank.to_string()),
            ]);
        }
        ballots.push(LoadBallot { code, fields });
    }
    db.commit().await?;
    Ok(ballots)
}

async fn send_ballot(client: &Client, target: &Url, mut ballot: LoadBallot) -> Option<()> {
    if !judge_portal::valid_code(&ballot.code) {
        return None;
    }
    let address = target
        .join(&format!("public/e-ballots/{}/current/", ballot.code))
        .ok()?;
    let form = client.get(address.clone()).send().await.ok()?;
    if form.status() != StatusCode::OK {
        return None;
    }
    let csrf = form
        .headers()
        .get_all(header::SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .filter_map(|cookie| cookie.split(';').next())
        .find_map(|cookie| cookie.strip_prefix("csrftoken="))?
        .to_owned();
    ballot
        .fields
        .push(("csrfmiddlewaretoken".into(), csrf.clone()));
    let encoded = url::form_urlencoded::Serializer::new(String::new())
        .extend_pairs(ballot.fields)
        .finish();
    let reply = client
        .post(address)
        .header(header::COOKIE, format!("csrftoken={csrf}"))
        .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
        .body(encoded)
        .send()
        .await
        .ok()?;
    let submitted = reply.headers().get(header::LOCATION)?.to_str().ok()?;
    (reply.status() == StatusCode::FOUND && submitted.ends_with("/submitted/")).then_some(())
}
