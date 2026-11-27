use serde::Deserialize;
use serde_json::Value;
use sqlx::MySqlConnection;

use crate::settings::{self, SettingValue};

const FIXTURE: &[u8] = include_bytes!("../../../mittab/apps/tab/fixtures/testing_db.json");

#[derive(Deserialize)]
struct Record {
    model: String,
    pk: i32,
    fields: Value,
}

impl Record {
    fn number(&self, key: &str) -> Option<i64> {
        self.fields[key].as_i64()
    }

    fn text(&self, key: &str) -> Option<&str> {
        self.fields[key].as_str()
    }

    fn flag(&self, key: &str) -> bool {
        self.fields[key].as_bool().unwrap_or(false)
    }

    fn ids(&self, key: &str) -> impl Iterator<Item = i64> + '_ {
        self.fields[key]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_i64)
    }
}

pub async fn load(db: &mut MySqlConnection) -> Result<(), &'static str> {
    let records: Vec<Record> =
        serde_json::from_slice(FIXTURE).map_err(|_| "Could not read development fixture")?;
    apply(db, &records).await.map_err(|error| {
        let code = error.as_database_error().and_then(|error| error.code());
        eprintln!("Failed to load development fixture (database code {code:?})");
        "Could not load the development fixture"
    })
}

async fn apply(db: &mut MySqlConnection, rows: &[Record]) -> Result<(), sqlx::Error> {
    // The deployed development fixture contains historical account hashes.
    // Keep the initialized credentials for tab, entry, and Board.
    for row in rows.iter().filter(|row| row.model == "tab.tabsettings") {
        if let (Some(name), Some(value)) = (row.text("key"), row.number("value")) {
            settings::put(db, name, SettingValue::Number(value as i32)).await?;
        }
    }
    for row in rows.iter().filter(|row| row.model == "tab.school") {
        sqlx::query("INSERT INTO tab_school (id, name, apda_id) VALUES (?, ?, -1)")
            .bind(row.pk)
            .bind(row.text("name"))
            .execute(&mut *db)
            .await?;
    }
    for row in rows.iter().filter(|row| row.model == "tab.debater") {
        sqlx::query(
            "INSERT INTO tab_debater (id, name, novice_status, tiebreaker, apda_id, qualified, needs_crash_housing, requests_wgm_housing)
             VALUES (?, ?, ?, ?, -1, FALSE, FALSE, FALSE)",
        ).bind(row.pk).bind(row.text("name")).bind(row.number("novice_status")).bind(row.number("tiebreaker"))
            .execute(&mut *db).await?;
    }
    for row in rows.iter().filter(|row| row.model == "tab.team") {
        sqlx::query(
            "INSERT INTO tab_team (id, name, school_id, hybrid_school_id, seed, checked_in, tiebreaker, team_code, break_preference, ranking_public)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, TRUE)",
        ).bind(row.pk).bind(row.text("name")).bind(row.number("school")).bind(row.number("hybrid_school"))
            .bind(row.number("seed")).bind(row.flag("checked_in")).bind(row.number("tiebreaker")).bind(row.text("team_code"))
            .execute(&mut *db).await?;
        for member in row.ids("debaters") {
            sqlx::query("INSERT INTO tab_team_debaters (team_id, debater_id) VALUES (?, ?)")
                .bind(row.pk)
                .bind(member)
                .execute(&mut *db)
                .await?;
        }
    }
    for row in rows.iter().filter(|row| row.model == "tab.judge") {
        sqlx::query(
            "INSERT INTO tab_judge (id, name, `rank`, ballot_code, is_dino, wing_only, request_wing, no_wings, needs_crash_housing, requests_wgm_housing)
             VALUES (?, ?, ?, ?, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE)",
        ).bind(row.pk).bind(row.text("name")).bind(rank(row)).bind(row.text("ballot_code")).execute(&mut *db).await?;
        for school in row.ids("schools") {
            sqlx::query("INSERT INTO tab_judge_schools (judge_id, school_id) VALUES (?, ?)")
                .bind(row.pk)
                .bind(school)
                .execute(&mut *db)
                .await?;
        }
    }
    for row in rows.iter().filter(|row| row.model == "tab.room") {
        sqlx::query("INSERT INTO tab_room (id, name, `rank`) VALUES (?, ?, ?)")
            .bind(row.pk)
            .bind(row.text("name"))
            .bind(rank(row))
            .execute(&mut *db)
            .await?;
    }
    for row in rows {
        match row.model.as_str() {
            "tab.scratch" => {
                sqlx::query("INSERT INTO tab_scratch (id, judge_id, team_id, scratch_type) VALUES (?, ?, ?, ?)")
                    .bind(row.pk).bind(row.number("judge")).bind(row.number("team")).bind(row.number("scratch_type"))
                    .execute(&mut *db).await?;
            }
            "tab.checkin" => {
                sqlx::query(
                    "INSERT INTO tab_checkin (id, judge_id, round_number) VALUES (?, ?, ?)",
                )
                .bind(row.pk)
                .bind(row.number("judge"))
                .bind(row.number("round_number"))
                .execute(&mut *db)
                .await?;
            }
            "tab.roomcheckin" => {
                sqlx::query(
                    "INSERT INTO tab_roomcheckin (id, room_id, round_number) VALUES (?, ?, ?)",
                )
                .bind(row.pk)
                .bind(row.number("room"))
                .bind(row.number("round_number"))
                .execute(&mut *db)
                .await?;
            }
            _ => {}
        }
    }
    Ok(())
}

fn rank(row: &Record) -> String {
    row.text("rank")
        .map(str::to_owned)
        .unwrap_or_else(|| row.fields["rank"].to_string())
}
