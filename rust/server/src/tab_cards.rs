use std::collections::{BTreeMap, HashMap};

use axum::{
    Extension, Json,
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
};
use chrono::Utc;
use minijinja::context;
use mittab_tabulation::{
    ballots::ScoringPolicy,
    rankings::{Score, Stat},
};
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access, board_snapshot,
    html::{self, Navigation},
    legacy_session::Actor,
    private,
    settings::Settings,
    speakers,
    tournament_data::TournamentData,
    unavailable,
};

const SETTINGS: &[&str] = &[
    "cur_round",
    "tot_rounds",
    "lenient_late",
    "var_teams_visible",
    "nov_teams_visible",
    "speaker_rankings_use_single_adjusted",
    "publish_tab_cards_with_first_outround",
    "motions_enabled",
];

#[derive(Serialize)]
struct Person {
    name: String,
    status: &'static str,
    speaks: String,
    ranks: String,
}

#[derive(Serialize)]
struct Round {
    number: i32,
    side: &'static str,
    result: &'static str,
    opponent: Option<String>,
    judges: Vec<PanelMember>,
    room: Option<String>,
    speakers: Vec<Points>,
    cumulative: Option<Points>,
    history: Option<Vec<board_snapshot::History>>,
}

#[derive(Clone, Serialize)]
struct PanelMember {
    name: String,
    chair: bool,
}

#[derive(Clone, Copy, Default, Serialize)]
struct Points {
    speaks: f64,
    ranks: f64,
}

impl Points {
    fn add(self, other: Self) -> Self {
        Self {
            speaks: self.speaks + other.speaks,
            ranks: self.ranks + other.ranks,
        }
    }

    fn from_score(score: Score) -> Self {
        Self {
            speaks: score.get(Stat::Speaks),
            ranks: score.get(Stat::Ranks),
        }
    }
}

#[derive(Serialize)]
struct Card {
    id: i32,
    name: String,
    members: Vec<Person>,
    rounds: Vec<Round>,
    byes: Vec<i32>,
    no_shows: Vec<i32>,
    total: Points,
}

struct Team {
    id: i32,
    name: String,
    speakers: Vec<i32>,
}

type RoundRow = (
    i32,
    i32,
    i32,
    String,
    i32,
    String,
    i32,
    Option<i32>,
    Option<String>,
);

async fn cards(
    db: &mut MySqlConnection,
    settings: &Settings,
    chosen: Option<i32>,
    board: bool,
) -> Result<Vec<Card>, sqlx::Error> {
    let teams = sqlx::query_as::<_, (i32, String)>(
        "SELECT id, name FROM tab_team WHERE (? IS NULL OR id = ?) ORDER BY id",
    )
    .bind(chosen)
    .bind(chosen)
    .fetch_all(&mut *db)
    .await?;
    let mut members = HashMap::<_, Vec<_>>::new();
    let mut names = HashMap::new();
    for (team, id, name, status) in sqlx::query_as::<_, (i32, i32, String, i32)>(
        "SELECT t.team_id, d.id, d.name, d.novice_status FROM tab_team_debaters t
         JOIN tab_debater d ON d.id = t.debater_id WHERE (? IS NULL OR t.team_id = ?)
         ORDER BY d.name, d.id",
    )
    .bind(chosen)
    .bind(chosen)
    .fetch_all(&mut *db)
    .await?
    {
        members.entry(team).or_default().push(id);
        names.insert(id, (name, status));
    }
    let teams: Vec<_> = teams
        .into_iter()
        .map(|(id, name)| Team {
            id,
            name,
            speakers: members.remove(&id).unwrap_or_default(),
        })
        .collect();
    let scores: HashMap<_, _> = speakers::ordered_speaker_rows(db, settings)
        .await?
        .into_iter()
        .map(|row| (row.debater.id, Points::from_score(row.score)))
        .collect();
    let tournament = TournamentData::load(db).await?;
    let team_scores = tournament
        .scores_by_team(ScoringPolicy {
            current_round: settings.integer("cur_round", 1),
            lenient_late_through: settings.integer("lenient_late", 0),
        })
        .map_err(|error| sqlx::Error::Protocol(error.into()))?;
    let byes = numbers(
        db,
        "SELECT bye_team_id, round_number FROM tab_bye ORDER BY round_number, id",
    )
    .await?;
    let absences = numbers(
        db,
        "SELECT no_show_team_id, round_number FROM tab_noshow ORDER BY round_number, id",
    )
    .await?;
    let mut history = if board {
        board_snapshot::histories(db, "round").await?.retained
    } else {
        HashMap::new()
    };
    let mut panels = HashMap::<i32, Vec<_>>::new();
    for (round, id, name) in sqlx::query_as::<_, (i32, i32, String)>(
        "SELECT r.round_id, j.id, j.name FROM tab_round_judges r JOIN tab_judge j ON j.id = r.judge_id
         ORDER BY j.name, j.id",
    ).fetch_all(&mut *db).await? {
        panels.entry(round).or_default().push((id, name));
    }
    let raw = raw_points(db).await?;
    let mut rounds = HashMap::<i32, BTreeMap<i32, Round>>::new();
    for (id, number, gov, gname, opp, oname, victor, chair, room) in sqlx::query_as::<_, RoundRow>(
        "SELECT r.id, r.round_number, g.id, g.name, o.id, o.name, r.victor, r.chair_id, m.name
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         LEFT JOIN tab_room m ON m.id = r.room_id
         WHERE r.round_number > 0 AND (? IS NULL OR ? IN (g.id, o.id)) ORDER BY r.id",
    ).bind(chosen).bind(chosen).fetch_all(db).await? {
        let judges: Vec<_> = panels.remove(&id).unwrap_or_default().into_iter().map(|(id, name)| {
            PanelMember { name, chair: Some(id) == chair }
        }).collect();
        for (id_team, opponent, government) in [(gov, &oname, true), (opp, &gname, false)] {
            if chosen.is_some_and(|chosen| id_team != chosen) {
                continue;
            }
            rounds.entry(id_team).or_default().insert(number, Round {
                number, side: if government { "Gov" } else { "Opp" },
                result: result(victor, government),
                opponent: Some(opponent.clone()), judges: judges.clone(), room: room.clone(),
                speakers: Vec::new(), cumulative: None,
                history: history.remove(&id),
            });
        }
    }
    let total_rounds = settings.integer("tot_rounds", 5).max(0);
    let current = settings.integer("cur_round", 1).saturating_sub(1);
    Ok(teams
        .into_iter()
        .filter(|team| board || !team.speakers.is_empty())
        .map(|team| {
            let members = members_for(team.speakers.as_slice(), &names, &scores, board);
            let mut round_rows = rounds.remove(&team.id).unwrap_or_default();
            let mut cumulative = Points::default();
            let round_rows: Vec<_> = (1..=total_rounds)
                .map(|number| {
                    let mut entry = round_rows.remove(&number).unwrap_or_else(|| Round {
                        number,
                        side: "",
                        result: "",
                        opponent: None,
                        judges: Vec::new(),
                        room: None,
                        speakers: Vec::new(),
                        cumulative: None,
                        history: None,
                    });
                    if entry.opponent.is_some() {
                        entry.speakers = ballot_points(&raw, &team.speakers, number, board);
                        cumulative = entry.speakers.iter().copied().fold(cumulative, Points::add);
                    }
                    entry.cumulative = (number <= current).then_some(cumulative);
                    entry
                })
                .collect();
            Card {
                id: team.id,
                name: team.name,
                members,
                rounds: round_rows,
                byes: byes.get(&team.id).cloned().unwrap_or_default(),
                no_shows: absences.get(&team.id).cloned().unwrap_or_default(),
                total: Points::from_score(team_scores[&i64::from(team.id)]),
            }
        })
        .collect())
}

fn members_for(
    members: &[i32],
    names: &HashMap<i32, (String, i32)>,
    scores: &HashMap<i32, Points>,
    board: bool,
) -> Vec<Person> {
    let selection: Vec<_> = if board {
        members.to_vec()
    } else {
        members
            .first()
            .map(|&id| vec![id, members.get(1).copied().unwrap_or(id)])
            .unwrap_or_default()
    };
    selection
        .into_iter()
        .map(|id| {
            let (name, novice) = &names[&id];
            let points = scores.get(&id).copied().unwrap_or_default();
            Person {
                name: name.clone(),
                status: if *novice == 1 { "Novice" } else { "Varsity" },
                speaks: format!("{:.2}", points.speaks),
                ranks: format!("{:.2}", points.ranks),
            }
        })
        .collect()
}

fn ballot_points(
    raw: &HashMap<(i32, i32), Vec<Points>>,
    members: &[i32],
    round: i32,
    board: bool,
) -> Vec<Points> {
    let scores = |id| raw.get(&(round, id)).map_or(&[][..], Vec::as_slice);
    if board {
        return members
            .iter()
            .map(|&id| scores(id).first().copied().unwrap_or_default())
            .collect();
    }
    let Some(&first) = members.first() else {
        return Vec::new();
    };
    let first_points = scores(first);
    let second_points = scores(members.get(1).copied().unwrap_or(first));
    match (first_points, second_points) {
        ([first, ..], [second, ..]) => vec![*first, *second],
        ([first, second, ..], []) | ([], [first, second, ..]) => vec![*first, *second],
        ([first], []) => vec![*first, Points::default()],
        ([], [second]) => vec![Points::default(), *second],
        ([], []) => vec![Points::default(); 2],
    }
}

fn result(victor: i32, government: bool) -> &'static str {
    match victor {
        1 => {
            if government {
                "Win"
            } else {
                "Loss"
            }
        }
        2 => {
            if government {
                "Loss"
            } else {
                "Win"
            }
        }
        3 => {
            if government {
                "Win via forfeit"
            } else {
                "Loss via forfeit"
            }
        }
        4 => {
            if government {
                "Loss via forfeit"
            } else {
                "Win via forfeit"
            }
        }
        5 => "Both lose",
        6 => "Both win",
        _ => "Pending",
    }
}

async fn raw_points(
    db: &mut MySqlConnection,
) -> Result<HashMap<(i32, i32), Vec<Points>>, sqlx::Error> {
    let mut result: HashMap<_, Vec<_>> = HashMap::new();
    for (round, id, speaks, ranks) in sqlx::query_as::<_, (i32, i32, String, String)>(
        "SELECT r.round_number, s.debater_id, CAST(s.speaks AS CHAR), CAST(s.ranks AS CHAR)
         FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id ORDER BY s.id",
    )
    .fetch_all(db)
    .await?
    {
        let parse = |value: String| {
            value
                .parse()
                .map_err(|_| sqlx::Error::Protocol("Invalid ballot score".into()))
        };
        result.entry((round, id)).or_default().push(Points {
            speaks: parse(speaks)?,
            ranks: parse(ranks)?,
        });
    }
    Ok(result)
}

async fn numbers(
    db: &mut MySqlConnection,
    query: &'static str,
) -> Result<HashMap<i32, Vec<i32>>, sqlx::Error> {
    let mut numbers: HashMap<_, Vec<_>> = HashMap::new();
    for (team, number) in sqlx::query_as::<_, (i32, i32)>(query).fetch_all(db).await? {
        numbers.entry(team).or_default().push(number);
    }
    Ok(numbers)
}

pub async fn public(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    if matches!(
        actor,
        Actor::User {
            superuser: true,
            ..
        }
    ) {
        return access::found("/all_tab_cards/");
    }
    page(&app, actor, None, true).await
}

pub async fn all(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    page(&app, actor, None, false).await
}

pub async fn single(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
) -> Response {
    page(&app, actor, Some(id), false).await
}

async fn page(app: &App, actor: Actor, chosen: Option<i32>, public: bool) -> Response {
    let board = matches!(actor, Actor::User { board: true, .. });
    let result = async {
        let mut db = app.db.begin().await?;
        let settings = Settings::load(&mut db, SETTINGS).await?;
        if public {
            let released = sqlx::query_scalar::<_, bool>(
                "SELECT EXISTS(SELECT 1 FROM tab_outround
                 WHERE (type_of_round = 0 AND num_teams >= ?) OR (type_of_round = 1 AND num_teams >= ?))",
            ).bind(settings.integer("var_teams_visible", 256)).bind(settings.integer("nov_teams_visible", 256))
                .fetch_one(&mut *db).await?;
            if !released || !settings.flag("publish_tab_cards_with_first_outround", true) {
                return Ok(access::found("/public/access-error/"));
            }
        }
        let index: Vec<_> = if board { sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_team ORDER BY name, id")
            .fetch_all(&mut *db).await? } else { Vec::new() };
        let cards = if board && chosen.is_none() { Vec::new() } else { cards(&mut db, &settings, chosen, board).await? };
        db.commit().await?;
        if chosen.is_some() && cards.is_empty() {
            return Ok(private((StatusCode::NOT_FOUND, "Tab card not found.")));
        }
        Ok::<_, sqlx::Error>(html::render("tab_cards.html", context! {
            title => "Tab Cards", chosen, cards, index, board, public,
            nav => Navigation::new(actor, settings.flag("motions_enabled", true)),
        }, StatusCode::OK))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

pub async fn bundle(State(app): State<App>, headers: HeaderMap) -> Response {
    let label = headers
        .get(header::HOST)
        .and_then(|host| host.to_str().ok())
        .and_then(|host| host.split(['.', ':']).next())
        .filter(|label| {
            !label.is_empty()
                && label
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        })
        .unwrap_or("tournament");
    let exported_at = Utc::now().to_rfc3339();
    let result = async {
        let mut db = app.db.begin().await?;
        let payload = crate::bundle::export(&mut db, label, &exported_at).await?;
        db.commit().await?;
        let attachment = HeaderValue::from_str(&format!(
            "attachment; filename={label}-black-rod-bundle.json"
        ))
        .expect("ASCII filename");
        Ok::<_, sqlx::Error>(private((
            [(header::CONTENT_DISPOSITION, attachment)],
            Json(payload),
        )))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}
