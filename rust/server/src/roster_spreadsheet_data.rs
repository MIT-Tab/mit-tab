use std::collections::{BTreeMap, BTreeSet};

use sqlx::{Acquire, MySqlConnection, MySqlPool};

use crate::{
    registration_form::{Fields, length},
    roster_spreadsheet_layout::{self as layout, Roster},
    settings::Settings,
    spreadsheets::{self, Format},
    staff_pairing::lock_round,
    staff_roster_data::{self as roster, Edit, Error, Kind},
};

pub struct Sheet {
    pub headers: Vec<String>,
    pub rows: Vec<Vec<String>>,
}

pub async fn export(db: &mut MySqlConnection, roster: Roster) -> Result<Sheet, sqlx::Error> {
    let mut headers: Vec<String> = roster
        .headers()
        .iter()
        .map(|&header| header.to_owned())
        .collect();
    let rows = match roster {
        Roster::Teams => teams(db).await?,
        Roster::Judges => judges(db, &mut headers).await?,
        Roster::Rooms => sqlx::query_as::<_, (String, String)>("SELECT name, CAST(`rank` AS CHAR) FROM tab_room ORDER BY name, id")
            .fetch_all(db).await?.into_iter().map(|(name, rank)| vec![name, rank]).collect(),
        Roster::Scratches => sqlx::query_as::<_, (String, String, String)>(
            "SELECT t.name, j.name, IF(x.scratch_type = 0, 'team', 'tab') FROM tab_scratch x
             JOIN tab_team t ON t.id = x.team_id JOIN tab_judge j ON j.id = x.judge_id ORDER BY x.id",
        ).fetch_all(db).await?.into_iter().map(|(team, judge, kind)| vec![team, judge, kind]).collect(),
    };
    Ok(Sheet { headers, rows })
}

async fn teams(db: &mut MySqlConnection) -> Result<Vec<Vec<String>>, sqlx::Error> {
    let mut debaters: BTreeMap<i32, Vec<[String; 4]>> = BTreeMap::new();
    let rows = sqlx::query_as::<_, (i32, String, i32, Option<String>, Option<i32>)>(
        "SELECT m.team_id, d.name, d.novice_status, d.email, d.apda_id
         FROM tab_team_debaters m JOIN tab_debater d ON d.id = m.debater_id ORDER BY m.team_id, d.name, d.id",
    ).fetch_all(&mut *db).await?;
    for (team, name, status, email, apda_id) in rows {
        let status = if status == 1 { "Novice" } else { "Varsity" };
        debaters.entry(team).or_default().push([
            name,
            status.into(),
            email.unwrap_or_default(),
            apda_id.map(|id| id.to_string()).unwrap_or_default(),
        ]);
    }
    Ok(
        sqlx::query_as::<_, (i32, String, String, Option<String>, i32)>(
            "SELECT t.id, t.name, s.name, h.name, t.seed FROM tab_team t
         JOIN tab_school s ON s.id = t.school_id LEFT JOIN tab_school h ON h.id = t.hybrid_school_id
         ORDER BY t.name, t.id",
        )
        .fetch_all(db)
        .await?
        .into_iter()
        .map(|(id, name, school, hybrid, seed)| {
            let seed = match seed {
                1 => "Free Seed",
                2 => "Half Seed",
                3 => "Full Seed",
                _ => "Unseeded",
            };
            let mut row = vec![name, school, hybrid.unwrap_or_default(), seed.into()];
            for member in debaters.remove(&id).unwrap_or_default().into_iter().take(2) {
                row.extend(member);
            }
            row.resize(12, String::new());
            row
        })
        .collect(),
    )
}

async fn judges(
    db: &mut MySqlConnection,
    headers: &mut Vec<String>,
) -> Result<Vec<Vec<String>>, sqlx::Error> {
    let mut schools: BTreeMap<i32, Vec<String>> = BTreeMap::new();
    for (id, school) in sqlx::query_as::<_, (i32, String)>(
        "SELECT a.judge_id, s.name FROM tab_judge_schools a JOIN tab_school s ON s.id = a.school_id
         ORDER BY a.judge_id, s.name, s.id",
    )
    .fetch_all(&mut *db)
    .await?
    {
        schools.entry(id).or_default().push(school);
    }
    let width = schools.values().map(Vec::len).max().unwrap_or_default();
    headers.extend((1..=width).map(|index| format!("School {index}")));
    let round_count = rounds(db).await?;
    headers.extend((1..=round_count).map(|round| format!("Round {round} expected")));
    let expected: BTreeSet<_> = sqlx::query_as::<_, (i32, i32)>(
        "SELECT judge_id, round_number FROM tab_judgeexpectedcheckin",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .collect();
    Ok(sqlx::query_as::<_, (i32, String, Option<String>, String)>(
        "SELECT id, name, email, CAST(`rank` AS CHAR) FROM tab_judge ORDER BY name, id",
    )
    .fetch_all(db)
    .await?
    .into_iter()
    .map(|(id, name, email, rank)| {
        let mut row = vec![name, email.unwrap_or_default(), rank];
        row.extend(schools.remove(&id).unwrap_or_default());
        row.resize(3 + width, String::new());
        row.extend((1..=round_count).map(|round| {
            if expected.contains(&(id, round as i32)) {
                "yes".into()
            } else {
                String::new()
            }
        }));
        row
    })
    .collect())
}

async fn rounds(db: &mut MySqlConnection) -> Result<usize, sqlx::Error> {
    Ok(Settings::load(db, &["tot_rounds"])
        .await?
        .integer("tot_rounds", 5)
        .clamp(0, 50) as usize)
}

pub async fn import(
    pool: &MySqlPool,
    kind: Roster,
    format: Format,
    input: Vec<u8>,
    user: i32,
) -> Result<usize, Vec<String>> {
    let raw = tokio::task::spawn_blocking(move || spreadsheets::read(format, &input))
        .await
        .map_err(|_| vec!["Unable to read the spreadsheet.".into()])?
        .map_err(|error| vec![error.into()])?;
    let mut tx = pool.begin().await.map_err(|_| database())?;
    lock_round(&mut tx).await.map_err(|_| database())?;
    let rounds = if kind == Roster::Judges {
        rounds(&mut tx).await.map_err(|_| database())?
    } else {
        0
    };
    let decoded = layout::configure(kind, raw, rounds).map_err(|error| vec![error])?;
    let mut errors = Vec::new();
    let len = decoded.rows.len();
    for (number, row) in decoded.rows {
        let mut position = tx.begin().await.map_err(|_| database())?;
        match import_row(
            &mut position,
            kind,
            &row,
            user,
            rounds,
            decoded.named_headers,
        )
        .await
        {
            Ok(()) => position.commit().await.map_err(|_| database())?,
            Err(error) => {
                position.rollback().await.map_err(|_| database())?;
                let error = match error {
                    Error::Invalid(error) => error,
                    Error::Missing => "A referenced roster entry was removed.",
                    Error::Database => "The row could not be saved.",
                };
                errors.push(format!("Row {number}: {error}"));
                if errors.len() == 100 {
                    break;
                }
            }
        }
    }
    if !errors.is_empty() {
        tx.rollback().await.map_err(|_| database())?;
        return Err(errors);
    }
    tx.commit().await.map_err(|_| database())?;
    Ok(len)
}

fn database() -> Vec<String> {
    vec!["The roster could not be imported.".into()]
}

async fn import_row(
    db: &mut MySqlConnection,
    kind: Roster,
    row: &[String],
    user: i32,
    rounds: usize,
    named: bool,
) -> Result<(), Error> {
    match kind {
        Roster::Teams => import_team(db, row, user).await,
        Roster::Judges => import_judge(db, row, user, rounds, named).await,
        Roster::Rooms => {
            let mut fields = Fields::default();
            fields.push("name", &row[0]);
            let rank =
                layout::finite(&row[1]).ok_or(Error::Invalid("Rank must be a finite number."))?;
            fields.push("rank", rank.to_string());
            create(db, Kind::Room, &fields, user).await?;
            Ok(())
        }
        Roster::Scratches => import_scratch(db, row, user).await,
    }
}

async fn create(
    db: &mut MySqlConnection,
    kind: Kind,
    fields: &Fields,
    user: i32,
) -> Result<i32, Error> {
    roster::save_on(
        db,
        kind,
        Edit {
            id: None,
            user,
            checkins: false,
            public_log: false,
            fields,
        },
    )
    .await
}

async fn school(db: &mut MySqlConnection, name: &str) -> Result<i32, Error> {
    if let Some(id) = sqlx::query_scalar("SELECT id FROM tab_school WHERE name = ?")
        .bind(name)
        .fetch_optional(&mut *db)
        .await?
    {
        return Ok(id);
    }
    if name.is_empty() || !length(name, 50) || name.chars().any(char::is_control) {
        return Err(Error::Invalid(
            "Enter a valid school name of at most 50 characters.",
        ));
    }
    Ok(
        sqlx::query("INSERT INTO tab_school (name, apda_id) VALUES (?, -1)")
            .bind(name)
            .execute(db)
            .await?
            .last_insert_id() as i32,
    )
}

async fn import_team(db: &mut MySqlConnection, row: &[String], user: i32) -> Result<(), Error> {
    let school_id = school(db, &row[1]).await?;
    let seed = layout::seed(&row[3]).ok_or(Error::Invalid(
        "Seed must be full, half, free, or unseeded.",
    ))?;
    let mut draft = Fields::default();
    draft.push("name", &row[0]);
    draft.push("school", school_id);
    if !row[2].is_empty() {
        draft.push("hybrid_school", school(db, &row[2]).await?);
    }
    draft.push("seed", seed);
    draft.push("ranking_public", "on");
    draft.push("checked_in", "on");
    for base in [4, 8] {
        let status = layout::novice(&row[base + 1])
            .ok_or(Error::Invalid("Debater status must be novice or varsity."))?;
        let mut speaker = Fields::default();
        speaker.push("name", &row[base]);
        speaker.push("novice_status", status);
        speaker.push("email", &row[base + 2]);
        let apda = &row[base + 3];
        speaker.push("apda_id", if apda.is_empty() { "-1" } else { apda });
        draft.push("debaters", create(db, Kind::Debater, &speaker, user).await?);
    }
    create(db, Kind::Team, &draft, user).await?;
    Ok(())
}

async fn import_judge(
    db: &mut MySqlConnection,
    row: &[String],
    user: i32,
    rounds: usize,
    named: bool,
) -> Result<(), Error> {
    let rank =
        layout::finite(&row[2]).ok_or(Error::Invalid("Calibration must be a finite number."))?;
    let split = if rounds > 0
        && row.len() >= 3 + rounds
        && (named
            || row[row.len() - rounds..]
                .iter()
                .all(|field| layout::expected(field).is_some()))
    {
        row.len() - rounds
    } else {
        row.len()
    };
    let affiliated = &row[3..split];
    if affiliated.iter().all(|field| field.is_empty()) {
        return Err(Error::Invalid("Each judge must have at least one school."));
    }
    let mut draft = Fields::default();
    draft.push("name", &row[0]);
    draft.push("email", &row[1]);
    draft.push("rank", format!("{rank:.2}"));
    for name in affiliated.iter().filter(|name| !name.is_empty()) {
        draft.push("schools", school(db, name).await?);
    }
    let judge = create(db, Kind::Judge, &draft, user).await?;
    for (index, flag) in row[split..].iter().enumerate() {
        let available = layout::expected(flag).ok_or(Error::Invalid(
            "Use yes, no, or a blank cell for expected availability.",
        ))?;
        if available {
            sqlx::query(
                "INSERT INTO tab_judgeexpectedcheckin (judge_id, round_number) VALUES (?, ?)",
            )
            .bind(judge)
            .bind(index as i32 + 1)
            .execute(&mut *db)
            .await?;
        }
    }
    Ok(())
}

async fn import_scratch(db: &mut MySqlConnection, row: &[String], user: i32) -> Result<(), Error> {
    let kind =
        layout::scratch(&row[2]).ok_or(Error::Invalid("Scratch type must be team or tab."))?;
    let team: i32 = sqlx::query_scalar("SELECT id FROM tab_team WHERE name = ?")
        .bind(&row[0])
        .fetch_optional(&mut *db)
        .await?
        .ok_or(Error::Invalid("The team does not exist."))?;
    let judge: i32 = sqlx::query_scalar("SELECT id FROM tab_judge WHERE name = ?")
        .bind(&row[1])
        .fetch_optional(&mut *db)
        .await?
        .ok_or(Error::Invalid("The judge does not exist."))?;
    let record = sqlx::query(
        "INSERT INTO tab_scratch (team_id, judge_id, scratch_type, created_by_id, created_at)
         VALUES (?, ?, ?, ?, UTC_TIMESTAMP(6))",
    )
    .bind(team)
    .bind(judge)
    .bind(kind)
    .bind(user)
    .execute(&mut *db)
    .await?;
    crate::staff_scratches::audit(
        db,
        record.last_insert_id() as i32,
        user,
        &row[0],
        &row[1],
        kind,
        "create",
    )
    .await?;
    Ok(())
}
