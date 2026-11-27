use std::collections::{BTreeMap, HashMap};

use axum::{
    Json,
    extract::State,
    http::{HeaderMap, header},
    response::Response,
};
use chrono::{DateTime, Datelike, NaiveDate, NaiveDateTime, SecondsFormat, Utc};
use serde::Serialize;
use serde_json::json;
use sqlx::{MySql, MySqlConnection, QueryBuilder};

use crate::{
    App, bundle,
    placements::Placements,
    private,
    public_api::{self, ELIGIBLE_TEAMS},
    public_cache, publication,
    settings::Settings,
    speakers::{self, RankedSpeaker},
    unavailable,
};

const METADATA_KEYS: &[&str] = &[
    "host_school",
    "host_school_name",
    "tournament_date",
    "tournament_name",
    "tournament_type",
    "sanctioned_apda_tournament",
];

#[derive(Serialize)]
struct School {
    apda_id: Option<i32>,
    name: String,
}

#[derive(Serialize)]
struct Debater {
    apda_id: Option<i32>,
    tournament_id: i32,
    name: String,
    novice: bool,
    school: Option<School>,
}

#[derive(Serialize)]
struct Team {
    tournament_id: i32,
    name: String,
    hybrid: bool,
    debaters: Vec<Debater>,
}

#[derive(Serialize)]
struct TeamResult {
    place: i32,
    division: &'static str,
    team: Team,
}

#[derive(Serialize)]
struct SpeakerResult<'a> {
    place: usize,
    division: &'static str,
    tie: bool,
    debater: &'a Debater,
}

pub async fn get(State(app): State<App>, headers: HeaderMap) -> Response {
    respond(&app, headers, false).await
}

pub async fn import(State(app): State<App>, headers: HeaderMap) -> Response {
    respond(&app, headers, true).await
}

async fn respond(app: &App, headers: HeaderMap, include_import: bool) -> Response {
    let scheme = headers
        .get("x-forwarded-proto")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(',').next())
        .map(str::trim);
    let scheme = if scheme == Some("https") {
        "https"
    } else {
        "http"
    };
    let host = headers
        .get(header::HOST)
        .and_then(|value| value.to_str().ok())
        .and_then(|host| host.parse::<axum::http::uri::Authority>().ok())
        .filter(|host| !host.as_str().contains('@'));
    let Some(host) = host else {
        return private(axum::http::StatusCode::BAD_REQUEST);
    };
    let source = format!("{scheme}://{host}");
    if !include_import {
        return public_cache::live_results(app, &source, async |db| {
            Ok(private(Json(payload(db, &source, false).await?)))
        })
        .await;
    }
    let result = async {
        let mut db = app.db.begin().await?;
        let value = payload(&mut db, &source, true).await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(private(Json(value)))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn payload(
    db: &mut MySqlConnection,
    source: &str,
    include_import: bool,
) -> Result<serde_json::Value, sqlx::Error> {
    let keys: Vec<_> = speakers::SETTINGS
        .iter()
        .chain(METADATA_KEYS)
        .copied()
        .collect();
    let settings = Settings::load(db, &keys).await?;
    let placements = publication::refresh_placements(db, &settings).await?;
    let completed_at = publication::completion_time(db, &settings).await?;
    let now = Utc::now();
    let speakers_available = publication::speakers_released(&placements, completed_at, now);
    let (ranked_speakers, debaters) = if speakers_available {
        (
            speakers::ordered_speaker_rows(db, &settings).await?,
            speaker_profiles(db).await?,
        )
    } else {
        (Vec::new(), HashMap::new())
    };

    let mut team_results = Vec::new();
    for (division, id_map) in placements.iter().enumerate() {
        if id_map.is_empty() {
            continue;
        }
        let names = team_rosters(db, id_map).await?;
        let mut teams: Vec<_> = names.into_values().collect();
        teams.sort_by_key(|team| (id_map[&team.tournament_id], team.tournament_id));
        team_results.extend(teams.into_iter().map(|team| TeamResult {
            place: id_map[&team.tournament_id],
            division: ["varsity", "novice"][division],
            team,
        }));
    }

    let (team_count, novice_count) = QueryBuilder::<MySql>::new(ELIGIBLE_TEAMS)
        .push(
            "SELECT (SELECT COUNT(*) FROM eligible_teams),
                      COUNT(DISTINCT CASE WHEN d.novice_status = 1 THEN d.id END)
               FROM eligible_teams e
               JOIN tab_team_debaters m ON m.team_id = e.id
               JOIN tab_debater d ON d.id = m.debater_id",
        )
        .build_query_as::<(i64, i64)>()
        .fetch_one(&mut *db)
        .await?;
    let first_result = sqlx::query_scalar::<_, Option<NaiveDateTime>>(
        "SELECT MIN(result_entered_at) FROM tab_round",
    )
    .fetch_one(&mut *db)
    .await?;

    let held_on = first_result
        .map(|date| date.date())
        .or_else(|| {
            NaiveDate::parse_from_str(&settings.string("tournament_date", ""), "%Y-%m-%d").ok()
        })
        .unwrap_or_else(|| now.date_naive());
    let host = settings.string("host_school", "");
    let host_id = host
        .strip_prefix("apda:")
        .and_then(|value| value.trim().parse::<i64>().ok());
    let season = held_on.year() - i32::from(held_on.month() < 7);
    let tournament_name = settings.string("tournament_name", "Tournament");
    let mut payload = json!({
        "schema_version": 1,
        "source_url": source,
        "tournament": {
            "name": tournament_name,
            "date": held_on.to_string(),
            "season": season.to_string(),
            "host_school": {
                "apda_id": host_id,
                "name": settings.string("host_school_name", ""),
            },
            "tournament_type": settings.integer("tournament_type", 0),
            "sanctioned": settings.flag("sanctioned_apda_tournament", true),
            "num_teams": team_count,
            "num_novice_debaters": novice_count,
        },
        "availability": {
            "speakers": speakers_available,
            "final_inround_completed_at": completed_at.map(iso_timestamp),
        },
        "team_results": team_results,
        "speaker_results": speaker_results(&ranked_speakers, &debaters),
        "generated_at": iso_timestamp(now),
    });
    if include_import {
        let object = payload
            .as_object_mut()
            .expect("live-results payload is an object");
        append_import(
            db,
            object,
            &ranked_speakers,
            speakers_available,
            &tournament_name,
        )
        .await?;
    }
    Ok(payload)
}

async fn append_import(
    db: &mut MySqlConnection,
    payload: &mut serde_json::Map<String, serde_json::Value>,
    ranked: &[RankedSpeaker],
    ready: bool,
    tournament_name: &str,
) -> Result<(), sqlx::Error> {
    payload.insert("import_ready".into(), json!(ready));
    for (key, novice) in [
        ("varsity_speaker_awards", false),
        ("novice_speaker_awards", true),
    ] {
        let awards: Vec<_> = ranked
            .iter()
            .filter(|row| !novice || row.debater.novice_status == 1)
            .take(10)
            .map(|row| json!({"apda_id": row.debater.apda_id, "tournament_id": row.debater.id}))
            .collect();
        payload.insert(key.into(), json!(awards));
    }
    for value in [
        Placements::load_from(db).await?.legacy_fields(),
        public_api::debaters_payload(&mut *db).await?,
        public_api::schools_payload(&mut *db).await?,
        public_api::counts_payload(&mut *db).await?,
    ] {
        if let serde_json::Value::Object(fields) = value {
            payload.extend(fields);
        }
    }
    let bundle = if ready {
        bundle::export(db, tournament_name, &iso_timestamp(Utc::now())).await?
    } else {
        serde_json::Value::Null
    };
    payload.insert("bundle".into(), bundle);
    Ok(())
}

fn iso_timestamp(timestamp: DateTime<Utc>) -> String {
    let format = if timestamp.timestamp_subsec_micros() == 0 {
        SecondsFormat::Secs
    } else {
        SecondsFormat::Micros
    };
    timestamp.to_rfc3339_opts(format, false)
}

async fn speaker_profiles(db: &mut MySqlConnection) -> Result<HashMap<i32, Debater>, sqlx::Error> {
    const QUERY: &str = "
        SELECT d.id, d.apda_id, d.name, d.novice_status, school.apda_id, school.name
        FROM tab_debater d
        LEFT JOIN (
            SELECT debater_id, MIN(team_id) AS team_id FROM tab_team_debaters GROUP BY debater_id
        ) m ON m.debater_id = d.id
        LEFT JOIN tab_team t ON t.id = m.team_id
        LEFT JOIN tab_school school ON school.id = COALESCE(d.school_id, t.school_id)
    ";
    let rows =
        sqlx::query_as::<_, (i32, Option<i32>, String, i32, Option<i32>, Option<String>)>(QUERY)
            .fetch_all(db)
            .await?;
    Ok(rows
        .into_iter()
        .map(|(id, apda_id, name, status, school_id, school)| {
            (
                id,
                Debater {
                    apda_id,
                    tournament_id: id,
                    name,
                    novice: status == 1,
                    school: school.map(|name| School {
                        apda_id: school_id,
                        name,
                    }),
                },
            )
        })
        .collect())
}

async fn team_rosters(
    db: &mut MySqlConnection,
    ids: &BTreeMap<i32, i32>,
) -> Result<BTreeMap<i32, Team>, sqlx::Error> {
    let mut query = QueryBuilder::<MySql>::new(
        "
        SELECT t.id, t.name, t.hybrid_school_id, d.id, d.apda_id, d.name, d.novice_status,
               school.apda_id, school.name
        FROM tab_team t
        LEFT JOIN tab_team_debaters m ON m.team_id = t.id
        LEFT JOIN tab_debater d ON d.id = m.debater_id
        LEFT JOIN tab_school school ON school.id = COALESCE(d.school_id, t.school_id)
        WHERE t.id IN (",
    );
    let mut params = query.separated(", ");
    for id in ids.keys() {
        params.push_bind(id);
    }
    query.push(") ORDER BY t.id, d.name");
    type TeamMember = (
        i32,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        Option<String>,
        Option<i32>,
        Option<i32>,
        Option<String>,
    );
    let mut teams = BTreeMap::new();
    for (id, name, hybrid, debater, apda_id, debater_name, status, school_id, school) in
        query.build_query_as::<TeamMember>().fetch_all(db).await?
    {
        let team = teams.entry(id).or_insert_with(|| Team {
            tournament_id: id,
            name,
            hybrid: hybrid.is_some(),
            debaters: Vec::new(),
        });
        if let Some((tournament_id, name)) = debater.zip(debater_name) {
            team.debaters.push(Debater {
                tournament_id,
                apda_id,
                name,
                novice: status == Some(1),
                school: school.map(|name| School {
                    apda_id: school_id,
                    name,
                }),
            });
        }
    }
    Ok(teams)
}

fn speaker_results<'a>(
    ranked: &[RankedSpeaker],
    debaters: &'a HashMap<i32, Debater>,
) -> Vec<SpeakerResult<'a>> {
    let mut results = Vec::new();
    for (division, status) in [("varsity", 0), ("novice", 1)] {
        let mut previous_place = 0;
        let mut previous_tied_forward = false;
        for (index, row) in ranked
            .iter()
            .filter(|row| row.debater.novice_status == status)
            .take(10)
            .enumerate()
        {
            let place = if previous_tied_forward {
                previous_place
            } else {
                index + 1
            };
            results.push(SpeakerResult {
                place,
                division,
                tie: previous_tied_forward || row.tied_forward,
                debater: &debaters[&row.debater.id],
            });
            previous_place = place;
            previous_tied_forward = row.tied_forward;
        }
    }
    results
}
