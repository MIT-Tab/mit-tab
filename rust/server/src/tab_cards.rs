use std::collections::{BTreeMap, HashMap};

use axum::{
    Extension, Json,
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, Uri, header},
    response::Response,
};
use bigdecimal::{BigDecimal, ToPrimitive};
use chrono::Utc;
use minijinja::{Value, context};
use mittab_tabulation::{
    ballots::ScoringPolicy,
    rankings::{Score, Stat},
};
use sqlx::MySqlConnection;

use crate::{
    App,
    access::{self, PortalAccessError},
    board_outrounds, board_snapshot, legacy_html,
    legacy_session::Actor,
    private,
    public_cache::{self, Page},
    settings::Settings,
    speakers,
    tournament_data::TournamentData,
    unavailable,
};

const PUBLIC: &str = "/public/tab-cards/";
const PRINT: &str = "/all_tab_cards/";

const SETTINGS: &[&str] = &[
    "cur_round",
    "tot_rounds",
    "lenient_late",
    "var_teams_visible",
    "nov_teams_visible",
    "speaker_rankings_use_single_adjusted",
    "publish_tab_cards_with_first_outround",
];

#[derive(Clone, Copy, Default)]
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

    fn display_tuple(self) -> String {
        format!("({:?}, {:?})", self.speaks, self.ranks)
    }

    fn from_score(score: Score) -> Self {
        Self {
            speaks: score.get(Stat::Speaks),
            ranks: score.get(Stat::Ranks),
        }
    }

    fn board_context(self, has_scores: bool) -> Value {
        let number = |value| {
            if has_scores || value != 0.0 {
                format!("{value:?}")
            } else {
                "0".into()
            }
        };
        context! { speaks => number(self.speaks), ranks => number(self.ranks) }
    }
}

#[derive(Default)]
struct BallotScore {
    speaks: BigDecimal,
    ranks: BigDecimal,
}

impl BallotScore {
    fn points(&self) -> Points {
        Points {
            speaks: self.speaks.to_f64().unwrap_or_default(),
            ranks: self.ranks.to_f64().unwrap_or_default(),
        }
    }

    fn sum(&self, other: &Self) -> Points {
        Self {
            speaks: &self.speaks + &other.speaks,
            ranks: &self.ranks + &other.ranks,
        }
        .points()
    }
}

struct Totals {
    members: HashMap<i32, Points>,
    teams: HashMap<i64, Score>,
}

async fn totals(db: &mut MySqlConnection, settings: &Settings) -> Result<Totals, sqlx::Error> {
    let members = speakers::ordered_speaker_rows(db, settings)
        .await?
        .into_iter()
        .map(|row| (row.debater.id, Points::from_score(row.score)))
        .collect();
    let teams = TournamentData::load(db)
        .await?
        .scores_by_team(ScoringPolicy {
            current_round: settings.integer("cur_round", 1),
            lenient_late_through: settings.integer("lenient_late", 0),
        })
        .map_err(|error| sqlx::Error::Protocol(error.into()))?;
    Ok(Totals { members, teams })
}

impl Totals {
    fn member(&self, id: i32) -> Points {
        self.members.get(&id).copied().unwrap_or_default()
    }

    fn team(&self, id: i32) -> Points {
        self.teams
            .get(&i64::from(id))
            .copied()
            .map(Points::from_score)
            .unwrap_or_default()
    }
}

struct Member {
    id: i32,
    name: String,
    status: i32,
}

async fn members(
    db: &mut MySqlConnection,
    chosen: Option<i32>,
) -> Result<HashMap<i32, Vec<Member>>, sqlx::Error> {
    let mut members = HashMap::<_, Vec<_>>::new();
    for (team, id, name, status) in sqlx::query_as::<_, (i32, i32, String, i32)>(
        "SELECT m.team_id, d.id, d.name, d.novice_status FROM tab_team_debaters m
         JOIN tab_debater d ON d.id = m.debater_id WHERE (? IS NULL OR m.team_id = ?)
         ORDER BY d.name, d.id",
    )
    .bind(chosen)
    .bind(chosen)
    .fetch_all(db)
    .await?
    {
        members
            .entry(team)
            .or_default()
            .push(Member { id, name, status });
    }
    Ok(members)
}

type ScoresBySpeaker = HashMap<(i32, i32), Vec<BallotScore>>;

async fn raw_scores(
    db: &mut MySqlConnection,
    chosen: Option<i32>,
) -> Result<ScoresBySpeaker, sqlx::Error> {
    let mut scores = HashMap::<_, Vec<_>>::new();
    for (round, id, speaks, ranks) in sqlx::query_as::<_, (i32, i32, String, String)>(
        "SELECT r.round_number, s.debater_id, CAST(s.speaks AS CHAR), CAST(s.ranks AS CHAR)
         FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id
         WHERE ? IS NULL OR EXISTS(SELECT 1 FROM tab_team_debaters m
                                  WHERE m.team_id = ? AND m.debater_id = s.debater_id)
         ORDER BY s.id",
    )
    .bind(chosen)
    .bind(chosen)
    .fetch_all(db)
    .await?
    {
        let decimal = |value: &str| {
            value
                .parse::<BigDecimal>()
                .map_err(|_| sqlx::Error::Protocol("Invalid ballot score".into()))
        };
        scores.entry((round, id)).or_default().push(BallotScore {
            speaks: decimal(&speaks)?,
            ranks: decimal(&ranks)?,
        });
    }
    Ok(scores)
}

struct Game {
    opponent: String,
    judges: String,
    government: bool,
    victor: i32,
}

async fn games(
    db: &mut MySqlConnection,
    chosen: Option<i32>,
) -> Result<HashMap<i32, BTreeMap<i32, Game>>, sqlx::Error> {
    let mut panels = HashMap::<i32, Vec<String>>::new();
    for (round, name) in sqlx::query_as::<_, (i32, String)>(
        "SELECT x.round_id, j.name FROM tab_round_judges x JOIN tab_round r ON r.id = x.round_id
         JOIN tab_judge j ON j.id = x.judge_id WHERE (? IS NULL OR ? IN (r.gov_team_id, r.opp_team_id))
         ORDER BY j.name, j.id",
    ).bind(chosen).bind(chosen).fetch_all(&mut *db).await? {
        panels.entry(round).or_default().push(name);
    }
    let mut result = HashMap::<i32, BTreeMap<i32, Game>>::new();
    for (id, number, gov, gname, opp, oname, victor) in sqlx::query_as::<_, (i32, i32, i32, String, i32, String, i32)>(
        "SELECT r.id, r.round_number, g.id, g.name, o.id, o.name, r.victor
         FROM tab_round r JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
         WHERE r.round_number > 0 AND (? IS NULL OR ? IN (g.id, o.id)) ORDER BY r.id",
    ).bind(chosen).bind(chosen).fetch_all(db).await? {
        let judges = panels.remove(&id).unwrap_or_default().join(" - ");
        for (team, opponent, government) in [(gov, &oname, true), (opp, &gname, false)] {
            if chosen.is_some_and(|id| id != team) || !government && opp == gov {
                continue;
            }
            let games = result.entry(team).or_default();
            // The reference processes the government relation before the opposition relation.
            if government && games.get(&number).is_some_and(|game| !game.government) {
                continue;
            }
            games.insert(number, Game { opponent: opponent.clone(), judges: judges.clone(), government, victor });
        }
    }
    Ok(result)
}

fn outcome(victor: i32, government: bool) -> &'static str {
    match (victor, government) {
        (1, true) | (2, false) => "W",
        (2, true) | (1, false) => "L",
        (3, true) | (4, false) => "WF",
        (4, true) | (3, false) => "LF",
        (5, _) => "AD",
        (6, _) => "AW",
        _ => " ",
    }
}

fn ballot_pair<'a>(
    scores: &'a ScoresBySpeaker,
    round: i32,
    first: i32,
    second: i32,
    zero: &'a BallotScore,
) -> [&'a BallotScore; 2] {
    let scores = |id| scores.get(&(round, id)).map_or(&[][..], Vec::as_slice);
    match (scores(first), scores(second)) {
        ([first, ..], [second, ..]) | ([first, second, ..], []) | ([], [first, second, ..]) => {
            [first, second]
        }
        ([first], []) => [first, zero],
        ([], [second]) => [zero, second],
        ([], []) => [zero, zero],
    }
}

async fn numbers(
    db: &mut MySqlConnection,
    query: &'static str,
    team: i32,
) -> Result<Vec<i32>, sqlx::Error> {
    sqlx::query_scalar(query).bind(team).fetch_all(db).await
}

async fn cards(
    db: &mut MySqlConnection,
    settings: &Settings,
    chosen: Option<i32>,
) -> Result<Vec<Value>, sqlx::Error> {
    let teams = sqlx::query_as::<_, (i32, String)>(
        "SELECT id, name FROM tab_team WHERE (? IS NULL OR id = ?) ORDER BY id",
    )
    .bind(chosen)
    .bind(chosen)
    .fetch_all(&mut *db)
    .await?;
    let mut members = members(db, chosen).await?;
    let mut games = games(db, chosen).await?;
    let totals = totals(db, settings).await?;
    let raw = raw_scores(db, chosen).await?;
    let mut byes = HashMap::<i32, Vec<i32>>::new();
    for (team, number) in sqlx::query_as::<_, (i32, i32)>(
        "SELECT bye_team_id, round_number FROM tab_bye WHERE (? IS NULL OR bye_team_id = ?) ORDER BY id",
    ).bind(chosen).bind(chosen).fetch_all(db).await? {
        byes.entry(team).or_default().push(number);
    }
    let rounds = settings.integer("tot_rounds", 5).max(0);
    let completed = settings.integer("cur_round", 1).saturating_sub(1);
    let zero = BallotScore::default();
    let status = |member: &Member| if member.status == 0 { "V" } else { "N" };
    Ok(teams.into_iter().filter_map(|(id, name)| {
        let roster = members.remove(&id)?;
        let first = roster.first()?;
        let second = roster.get(1).unwrap_or(first);
        let mut running: Option<Points> = None;
        let mut games = games.remove(&id).unwrap_or_default();
        let round_stats: Vec<_> = (1..=rounds).map(|number| {
            let mut cells = std::array::from_fn::<_, 7, _>(|_| " ".to_owned());
            let round_points = games.remove(&number).map(|game| {
                let [a, b] = ballot_pair(&raw, number, first.id, second.id, &zero);
                cells[0] = if game.government { "G" } else { "O" }.into();
                cells[1] = outcome(game.victor, game.government).into();
                cells[2] = game.opponent;
                cells[3] = game.judges;
                cells[4] = a.points().display_tuple();
                cells[5] = b.points().display_tuple();
                a.sum(b)
            });
            if number <= completed {
                if let Some(round) = round_points {
                    running = Some(running.map_or(round, |before| round.add(before)));
                }
                cells[6] = running.map_or_else(|| "(0, 0)".into(), Points::display_tuple);
            } else if let Some(round) = round_points {
                cells[6] = round.display_tuple();
            }
            cells
        }).collect();
        let bye_round = match byes.get(&id).map(Vec::as_slice) {
            Some([round]) => Some(*round),
            _ => None,
        };
        let first_totals = totals.member(first.id);
        let second_totals = totals.member(second.id);
        let team_totals = totals.team(id);
        Some(context! {
            team => context! { pk => id }, team_name => name,
            debater_1 => &first.name, debater_2 => &second.name,
            debater_1_status => status(first), debater_2_status => status(second), round_stats, bye_round,
            d1st => first_totals.speaks, d1rt => first_totals.ranks, d2st => second_totals.speaks,
            d2rt => second_totals.ranks, ts => team_totals.speaks, tr => team_totals.ranks,
        })
    }).collect())
}

pub async fn public(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    match access::allowed_permission(&app, actor, "tab_settings.can_change").await {
        Ok(true) => return access::found(PRINT),
        Err(_) => return unavailable(),
        Ok(false) => {}
    }
    public_cache::render(&app, actor, Page::TabCards, async |db| {
        let settings = Settings::load(db, SETTINGS).await?;
        let released = sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM tab_outround
             WHERE (type_of_round = 0 AND num_teams >= ?) OR (type_of_round = 1 AND num_teams >= ?))",
        ).bind(settings.integer("var_teams_visible", 256)).bind(settings.integer("nov_teams_visible", 256))
            .fetch_one(&mut *db).await?;
        if !released || !settings.flag("publish_tab_cards_with_first_outround", true) {
            return Ok(access::found("/public/access-error/"));
        }
        let cards = cards(db, &settings, None).await?;
        let shell = legacy_html::shell(db, actor, PUBLIC).await?;
        Ok(legacy_html::render("public/tab_cards.html", shell, context! { cards }, StatusCode::OK))
    }).await
}

pub async fn all(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    private_page(&app, actor, &headers, uri.path(), None).await
}

pub async fn single(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    uri: Uri,
    Path(id): Path<i32>,
) -> Response {
    private_page(&app, actor, &headers, uri.path(), Some(id)).await
}

async fn current_actor(
    db: &mut MySqlConnection,
    app: &App,
    original: Actor,
    headers: &HeaderMap,
    path: &str,
) -> Result<Actor, PortalAccessError> {
    let current = access::refreshed_user(db, app, original, headers).await?;
    let Actor::User {
        id,
        board,
        staff,
        superuser,
    } = current
    else {
        return Err(PortalAccessError::Denied);
    };
    if board {
        access::require_open_board(db, current).await?;
        return Ok(current);
    }
    if path.starts_with("/apda-board/") {
        return Err(PortalAccessError::Denied);
    }
    if superuser
        || !staff
            && access::user_permission(db, id, "tab_settings.can_change")
                .await
                .map_err(|_| PortalAccessError::Database)?
    {
        return Ok(current);
    }
    Err(PortalAccessError::Denied)
}

async fn private_page(
    app: &App,
    actor: Actor,
    headers: &HeaderMap,
    path: &str,
    chosen: Option<i32>,
) -> Response {
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    let actor = match current_actor(&mut db, app, actor, headers, path).await {
        Ok(actor) => actor,
        Err(error) => {
            drop(db);
            return error.response(app, headers).await;
        }
    };
    let result = async {
        let response = if matches!(actor, Actor::User { board: true, .. }) {
            board_page(&mut db, actor, path, chosen).await?
        } else {
            staff_page(&mut db, actor, path, chosen).await?
        };
        db.commit().await?;
        Ok::<_, sqlx::Error>(response)
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn team_identity(db: &mut MySqlConnection, id: i32) -> Result<Option<String>, sqlx::Error> {
    sqlx::query_scalar("SELECT name FROM tab_team WHERE id = ?")
        .bind(id)
        .fetch_optional(db)
        .await
}

async fn not_found(
    db: &mut MySqlConnection,
    actor: Actor,
    path: &str,
) -> Result<Response, sqlx::Error> {
    let shell = legacy_html::shell(db, actor, path).await?;
    Ok(legacy_html::render(
        "common/404.html",
        shell,
        context! {},
        StatusCode::NOT_FOUND,
    ))
}

async fn staff_page(
    db: &mut MySqlConnection,
    actor: Actor,
    path: &str,
    chosen: Option<i32>,
) -> Result<Response, sqlx::Error> {
    let team = if let Some(id) = chosen {
        let Some(name) = team_identity(db, id).await? else {
            return not_found(db, actor, path).await;
        };
        Some(context! { id, display_backend => name })
    } else {
        None
    };
    let settings = Settings::load(db, SETTINGS).await?;
    let cards = cards(db, &settings, chosen).await?;
    let (template, shell) = if path.ends_with("/pretty/") {
        (
            "tab/pretty_tab_card.html",
            legacy_html::shell(db, actor, path).await?,
        )
    } else if chosen.is_some() {
        ("tab/tab_card.html", context! {})
    } else {
        ("tab/all_tab_cards.html", context! {})
    };
    let card = cards.first();
    Ok(legacy_html::render(
        template,
        shell,
        context! { cards => &cards, card, team },
        StatusCode::OK,
    ))
}

async fn board_page(
    db: &mut MySqlConnection,
    actor: Actor,
    path: &str,
    chosen: Option<i32>,
) -> Result<Response, sqlx::Error> {
    let page = if let Some(id) = chosen {
        let Some(name) = team_identity(db, id).await? else {
            return not_found(db, actor, path).await;
        };
        let settings = Settings::load(db, SETTINGS).await?;
        let members = members(db, Some(id)).await?.remove(&id).unwrap_or_default();
        let totals = totals(db, &settings).await?;
        let completed = settings.integer("cur_round", 1).saturating_sub(1);
        let byes = numbers(
            db,
            "SELECT round_number FROM tab_bye WHERE bye_team_id = ? ORDER BY round_number",
            id,
        )
        .await?;
        let no_shows = numbers(
            db,
            "SELECT round_number FROM tab_noshow WHERE no_show_team_id = ? ORDER BY round_number",
            id,
        )
        .await?;
        // Django displays an empty sum as an integer and a sum of scores as a float.
        // Attendance follows each debater's first team, including shared roster members.
        let scored = sqlx::query_scalar::<_, i32>(
            "WITH members AS (
                 SELECT m.debater_id, MIN(m.team_id) AS first_team FROM tab_team_debaters m
                 WHERE m.debater_id IN (SELECT debater_id FROM tab_team_debaters WHERE team_id = ?)
                 GROUP BY m.debater_id
             )
             SELECT m.debater_id FROM members m
             WHERE EXISTS(SELECT 1 FROM tab_roundstats s JOIN tab_round r ON r.id = s.round_id
                          WHERE s.debater_id = m.debater_id AND r.round_number BETWEEN 1 AND ?)
                OR EXISTS(SELECT 1 FROM tab_bye b WHERE b.bye_team_id = m.first_team AND b.round_number BETWEEN 1 AND ?)
                OR EXISTS(SELECT 1 FROM tab_noshow n WHERE n.no_show_team_id = m.first_team AND n.round_number BETWEEN 1 AND ?)",
        ).bind(id).bind(completed).bind(completed).bind(completed).fetch_all(&mut *db).await?;
        let debaters: Vec<_> = members
            .iter()
            .map(|member| {
                context! {
                    name => &member.name, novice_status => member.status,
                    ..totals.member(member.id).board_context(scored.contains(&member.id))
                }
            })
            .collect();
        let mut pairings: Vec<_> = board_snapshot::capture_team(db, id)
            .await?
            .into_iter()
            .collect();
        pairings.sort_by_key(|(id, pairing)| (pairing.round_number, *id));
        let history = board_snapshot::histories(db, "round").await?;
        let pairings: Vec<_> = pairings.iter().map(|(id, snapshot)| {
            let scores: Vec<_> = snapshot.scores.iter().flatten()
                .filter(|score| members.iter().any(|member| member.id == score.debater))
                .map(|score| context! { name => &score.name, role => score.role.as_deref().unwrap_or("None"),
                    speaks => &score.speaks, ranks => &score.ranks }).collect();
            context! { round_number => snapshot.round_number, scores, ..board_outrounds::pairing(*id, snapshot, &history) }
        }).collect();
        context! { team => context! { id, name }, debaters, pairings, byes, no_shows,
        totals => totals.team(id).board_context(!scored.is_empty()) }
    } else {
        let teams: Vec<_> =
            sqlx::query_as::<_, (i32, String)>("SELECT id, name FROM tab_team ORDER BY name, id")
                .fetch_all(&mut *db)
                .await?
                .into_iter()
                .map(|(id, name)| context! { id, name })
                .collect();
        context! { teams }
    };
    let shell = legacy_html::shell(db, actor, path).await?;
    Ok(legacy_html::render(
        "apda_board/tab_cards.html",
        shell,
        page,
        StatusCode::OK,
    ))
}

pub async fn bundle(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let label = crate::legacy_archive::tournament_slug(&headers);
    let exported_at = Utc::now().to_rfc3339();
    let Ok(mut db) = app.db.begin().await else {
        return unavailable();
    };
    if let Err(error) =
        current_actor(&mut db, &app, actor, &headers, "/archive/black_rod_bundle/").await
    {
        drop(db);
        return error.response(&app, &headers).await;
    }
    let result = async {
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
