use std::{collections::HashMap, io};

use axum::{
    extract::State,
    http::{HeaderMap, HeaderValue, header},
    response::Response,
};
use quick_xml::{
    Writer,
    events::{BytesEnd, BytesStart, BytesText, Event},
};
use sqlx::MySqlConnection;

use crate::{App, private, unavailable};

pub async fn download(State(app): State<App>, headers: HeaderMap) -> Response {
    let label = tournament_slug(&headers);
    let result = async {
        let mut db = app.db.begin().await?;
        let payload = export(&mut db, label).await?;
        db.commit().await?;
        let attachment = HeaderValue::from_str(&format!("attachment; filename={label}.xml"))
            .expect("valid ASCII archive filename");
        Ok::<_, sqlx::Error>(private((
            [
                (header::CONTENT_DISPOSITION, attachment),
                (
                    header::CONTENT_TYPE,
                    HeaderValue::from_static("text/xml; charset=utf-8"),
                ),
                (
                    header::X_CONTENT_TYPE_OPTIONS,
                    HeaderValue::from_static("nosniff"),
                ),
            ],
            payload,
        )))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

pub fn tournament_slug(headers: &HeaderMap) -> &str {
    headers
        .get(header::HOST)
        .and_then(|host| host.to_str().ok())
        .and_then(|host| host.split(['.', ':']).next())
        .filter(|name| {
            !name.is_empty()
                && name
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        })
        .unwrap_or("tournament")
}

struct Archive(Writer<Vec<u8>>);

impl Archive {
    fn start(&mut self, tag: &str, attrs: &[(&str, &str)]) -> io::Result<()> {
        let mut event = BytesStart::new(tag);
        event.extend_attributes(attrs.iter().copied());
        self.0.write_event(Event::Start(event))
    }

    fn end(&mut self, tag: &str) -> io::Result<()> {
        self.0.write_event(Event::End(BytesEnd::new(tag)))
    }

    fn element(&mut self, tag: &str, attrs: &[(&str, &str)], text: &str) -> io::Result<()> {
        self.start(tag, attrs)?;
        self.0.write_event(Event::Text(BytesText::new(text)))?;
        self.end(tag)
    }
}

async fn export(db: &mut MySqlConnection, name: &str) -> Result<Vec<u8>, sqlx::Error> {
    let mut xml = Archive(Writer::new(Vec::new()));
    xml.start("tournament", &[("name", name), ("style", "apda")])?;
    debates(db, &mut xml).await?;
    participants(db, &mut xml).await?;
    for (id, name) in
        sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_school ORDER BY name, id")
            .fetch_all(&mut *db)
            .await?
    {
        xml.element(
            "institution",
            &[("id", &format!("I{id}")), ("reference", &name)],
            &name,
        )?;
    }
    for (id, name, rank) in sqlx::query_as::<_, (i32, String, String)>(
        "SELECT id, name, CAST(`rank` AS CHAR) FROM tab_room ORDER BY name, id",
    )
    .fetch_all(&mut *db)
    .await?
    {
        xml.element(
            "venue",
            &[("id", &format!("V{id}")), ("score", &rank)],
            &name,
        )?;
    }
    for (category, name) in ["Varsity", "Novice"].into_iter().enumerate() {
        xml.element(
            "speaker-category",
            &[("id", &format!("SC{category}"))],
            name,
        )?;
    }
    xml.end("tournament")?;
    Ok(xml.0.into_inner())
}

type Debate = (i32, i32, Option<i32>, Option<i32>, i32, i32, i32);

struct Speech {
    debater: i32,
    speaks: i64,
    rank: i64,
}

async fn debates(db: &mut MySqlConnection, xml: &mut Archive) -> Result<(), sqlx::Error> {
    let mut assignments: HashMap<i32, Vec<String>> = HashMap::new();
    for (round, judge) in sqlx::query_as::<_, (i32, i32)>(
        "SELECT a.round_id, a.judge_id FROM tab_round_judges a JOIN tab_judge j ON j.id = a.judge_id
         ORDER BY j.name, j.id",
    ).fetch_all(&mut *db).await? {
        assignments.entry(round).or_default().push(format!("A{judge}"));
    }
    let mut speeches: HashMap<i32, Vec<Speech>> = HashMap::new();
    for (round, debater, speaks, rank) in sqlx::query_as::<_, (i32, i32, i64, i64)>(
        "SELECT round_id, debater_id, CAST(speaks * 10000 AS SIGNED), CAST(TRUNCATE(ranks, 0) AS SIGNED)
         FROM tab_roundstats ORDER BY FIELD(debater_role, 'pm', 'lo', 'mg', 'mo'), id",
    ).fetch_all(&mut *db).await? {
        speeches.entry(round).or_default().push(Speech { debater, speaks, rank });
    }
    let debates = sqlx::query_as::<_, Debate>(
        "SELECT id, round_number, chair_id, room_id, gov_team_id, opp_team_id, victor FROM tab_round ORDER BY round_number, id",
    ).fetch_all(&mut *db).await?;
    let mut current = None;
    for (id, number, chair, room, gov, opp, victor) in debates {
        if current != Some(number) {
            if current.is_some() {
                xml.end("round")?;
            }
            xml.start("round", &[("name", &format!("Round {number}"))])?;
            current = Some(number);
        }
        let judges = assignments
            .get(&id)
            .map_or_else(String::new, |ids| ids.join(" "));
        let chair = chair.map_or_else(|| "ANone".into(), |id| format!("A{id}"));
        let venue = room.map_or_else(|| "VNone".into(), |id| format!("V{id}"));
        xml.start(
            "debate",
            &[
                ("id", &format!("D{id}")),
                ("chair", &chair),
                ("adjudicators", &judges),
                ("venue", &venue),
            ],
        )?;
        // The legacy format lists government and opposition in order. On a
        // double drop it records the public forfeit flag on opposition.
        let results = [
            (gov, matches!(victor, 1 | 3 | 6), victor == 4),
            (opp, matches!(victor, 2 | 4 | 6), matches!(victor, 3 | 5)),
        ];
        let scored = if matches!(victor, 1 | 2) {
            speeches.get(&id).map_or(&[][..], Vec::as_slice)
        } else {
            &[]
        };
        for (side, (team, winner, forfeited)) in results.into_iter().enumerate() {
            let team = format!("T{team}");
            let mut attrs = vec![("team", team.as_str())];
            if forfeited {
                attrs.push(("forfeit", "true"));
            }
            xml.start("side", &attrs)?;
            let points: Vec<_> = scored.iter().skip(side).step_by(2).collect();
            let total = if points.is_empty() {
                "0".into()
            } else {
                format_speaks(points.iter().map(|row| row.speaks).sum())
            };
            xml.element(
                "ballot",
                &[
                    ("adjudicators", &judges),
                    ("rank", if winner { "1" } else { "2" }),
                ],
                &total,
            )?;
            for speech in points {
                xml.start(
                    "speech",
                    &[
                        ("speaker", &format!("S{}", speech.debater)),
                        ("reply", "false"),
                    ],
                )?;
                xml.element(
                    "ballot",
                    &[
                        ("adjudicators", &judges),
                        ("rank", &speech.rank.to_string()),
                        ("ignored", "false"),
                    ],
                    &format_speaks(speech.speaks),
                )?;
                xml.end("speech")?;
            }
            xml.end("side")?;
        }
        xml.end("debate")?;
    }
    if current.is_some() {
        xml.end("round")?;
    }
    Ok(())
}

fn format_speaks(value: i64) -> String {
    let sign = if value < 0 { "-" } else { "" };
    let unsigned = value.unsigned_abs();
    format!("{sign}{}.{:04}", unsigned / 10000, unsigned % 10000)
}

type Team = (i32, String, i32, Option<i32>, Option<String>);

async fn participants(db: &mut MySqlConnection, xml: &mut Archive) -> Result<(), sqlx::Error> {
    let mut members: HashMap<i32, Vec<(i32, String, i32)>> = HashMap::new();
    for (team, speaker, name, category) in sqlx::query_as::<_, (i32, i32, String, i32)>(
        "SELECT m.team_id, d.id, d.name, d.novice_status FROM tab_team_debaters m
         JOIN tab_debater d ON d.id = m.debater_id ORDER BY d.name, d.id",
    )
    .fetch_all(&mut *db)
    .await?
    {
        members
            .entry(team)
            .or_default()
            .push((speaker, name, category));
    }
    let mut schools: HashMap<i32, Vec<String>> = HashMap::new();
    for (judge, school) in sqlx::query_as::<_, (i32, i32)>(
        "SELECT js.judge_id, s.id FROM tab_judge_schools js JOIN tab_school s ON s.id = js.school_id ORDER BY s.name, s.id",
    ).fetch_all(&mut *db).await? {
        schools.entry(judge).or_default().push(format!("I{school}"));
    }
    xml.start("participants", &[])?;
    for (id, name, school, hybrid, code) in sqlx::query_as::<_, Team>(
        "SELECT id, name, school_id, hybrid_school_id, team_code FROM tab_team ORDER BY id",
    )
    .fetch_all(&mut *db)
    .await?
    {
        let key = format!("T{id}");
        let mut attrs = vec![("id", key.as_str()), ("name", name.as_str())];
        if let Some(code) = code.as_deref() {
            attrs.push(("code", code));
        }
        xml.start("team", &attrs)?;
        let institutions = hybrid.map_or_else(
            || format!("I{school}"),
            |other| format!("I{school} I{other}"),
        );
        for (speaker, name, category) in members.get(&id).into_iter().flatten() {
            xml.element(
                "speaker",
                &[
                    ("id", &format!("S{speaker}")),
                    ("categories", &format!("SC{category}")),
                    ("institutions", &institutions),
                ],
                name,
            )?;
        }
        xml.end("team")?;
    }
    for (id, name, rank) in sqlx::query_as::<_, (i32, String, String)>(
        "SELECT id, name, CAST(`rank` AS CHAR) FROM tab_judge ORDER BY name, id",
    )
    .fetch_all(db)
    .await?
    {
        let affiliations = schools
            .get(&id)
            .map_or_else(String::new, |ids| ids.join(" "));
        xml.start(
            "adjudicator",
            &[
                ("id", &format!("A{id}")),
                ("name", &name),
                ("score", &rank),
                ("institutions", &affiliations),
            ],
        )?;
        xml.end("adjudicator")?;
    }
    xml.end("participants")?;
    Ok(())
}
