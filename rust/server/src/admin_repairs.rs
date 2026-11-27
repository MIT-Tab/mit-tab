use axum::{
    Extension, Form, Router,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::MySqlConnection;

use crate::{
    App, access, board_snapshot,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private, publication, round_audit,
    settings::Settings,
    staff_pairing::lock_round,
    unavailable,
};

const STATISTICS: &str = "/admin/tab/roundstats/";

pub fn routes() -> Router<App> {
    Router::new()
        .route(
            "/admin/tab/roundstats/{id}/change/",
            get(score).post(edit_score),
        )
        .route(
            "/admin/tab/{model}/{id}/delete/",
            get(confirm_delete).post(delete),
        )
}

#[derive(Serialize)]
struct Score {
    round_id: i32,
    debater_id: i32,
    role: Option<String>,
    speaks: String,
    ranks: String,
}

async fn score_row(db: &mut MySqlConnection, id: i32) -> Result<Option<Score>, sqlx::Error> {
    type Row = (i32, i32, Option<String>, String, String);
    Ok(sqlx::query_as::<_, Row>(
        "SELECT round_id, debater_id, debater_role, CAST(speaks AS CHAR), CAST(ranks AS CHAR)
         FROM tab_roundstats WHERE id = ?",
    )
    .bind(id)
    .fetch_optional(db)
    .await?
    .map(|(round_id, debater_id, role, speaks, ranks)| Score {
        round_id,
        debater_id,
        role,
        speaks,
        ranks,
    }))
}

async fn score(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
) -> Response {
    let result = async {
        let mut db = app.db.acquire().await?;
        let Some(score) = score_row(&mut db, id).await? else {
            return Ok(missing());
        };
        let Ok(csrf) = html::page_csrf(&headers) else {
            return Ok(unavailable());
        };
        let response = html::render(
            "admin_repair.html",
            context! {
                title => "Edit Round Statistic", nav => Navigation::new(actor, true),
                score, id, csrf,
                cancel => STATISTICS, delete_url => format!("{STATISTICS}{id}/delete/"),
            },
            StatusCode::OK,
        );
        Ok::<_, sqlx::Error>(html::form_response(response, &headers, &csrf))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Deserialize)]
struct ScoreForm {
    csrfmiddlewaretoken: String,
    round_id: i32,
    debater_id: i32,
    role: String,
    speaks: String,
    ranks: String,
}

fn decimal(value: &str) -> bool {
    let value = value.trim();
    let digits = value.strip_prefix(['-', '+']).unwrap_or(value);
    let (integer, fraction) = digits.split_once('.').unwrap_or((digits, ""));
    !digits.is_empty()
        && (!integer.is_empty() || !fraction.is_empty())
        && integer.trim_start_matches('0').len() <= 2
        && fraction.len() <= 4
        && integer
            .bytes()
            .chain(fraction.bytes())
            .all(|byte| byte.is_ascii_digit())
}

async fn edit_score(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<i32>,
    headers: HeaderMap,
    Form(form): Form<ScoreForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    let Actor::User { id: user, .. } = actor else {
        return access::access_denied();
    };
    let role = form.role.trim();
    if !decimal(&form.speaks) || !decimal(&form.ranks) || role.chars().count() > 4 {
        return private((
            StatusCode::UNPROCESSABLE_ENTITY,
            "Enter valid scores and a debater role of at most four characters.",
        ));
    }
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let Some(score) = score_row(&mut db, id).await? else { return Ok(missing()); };
        let mut before = score_audit(&mut db, score.round_id).await?;
        if form.round_id != score.round_id {
            before.extend(score_audit(&mut db, form.round_id).await?);
        }
        let valid_debater: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM tab_debater WHERE id = ?)")
            .bind(form.debater_id).fetch_one(&mut *db).await?;
        if !before.contains_key(&form.round_id) || !valid_debater {
            return Ok(private((StatusCode::UNPROCESSABLE_ENTITY, "Choose an existing round and debater.")));
        }
        sqlx::query("UPDATE tab_roundstats SET round_id = ?, debater_id = ?, debater_role = ?,
                       speaks = CAST(? AS DECIMAL(6,4)), ranks = CAST(? AS DECIMAL(6,4)) WHERE id = ?")
            .bind(form.round_id).bind(form.debater_id)
            .bind((!role.is_empty()).then_some(role))
            .bind(form.speaks.trim()).bind(form.ranks.trim()).bind(id).execute(&mut *db).await?;
        let mut after = score_audit(&mut db, score.round_id).await?;
        if form.round_id != score.round_id {
            after.extend(score_audit(&mut db, form.round_id).await?);
        }
        round_audit::record(&mut db, &before, &after, user, "admin scores").await?;
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(STATISTICS))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}

async fn score_audit(
    db: &mut MySqlConnection,
    id: i32,
) -> Result<std::collections::BTreeMap<i32, round_audit::Snapshot>, sqlx::Error> {
    let round: Option<i32> = sqlx::query_scalar("SELECT round_number FROM tab_round WHERE id = ?")
        .bind(id)
        .fetch_optional(&mut *db)
        .await?;
    match round {
        Some(round) => round_audit::capture_one(db, round, id).await,
        None => Ok(Default::default()),
    }
}

#[derive(Clone, Copy)]
enum Record {
    Score,
    Round,
    Outround,
}

impl Record {
    fn parse(model: &str) -> Option<Self> {
        match model {
            "roundstats" => Some(Self::Score),
            "round" => Some(Self::Round),
            "outround" => Some(Self::Outround),
            _ => None,
        }
    }

    fn cancel(self, id: i32) -> String {
        match self {
            Self::Score => format!("{STATISTICS}{id}/change/"),
            Self::Round => format!("/round/{id}/result/"),
            Self::Outround => format!("/outround/{id}/result/"),
        }
    }

    async fn describe(
        self,
        db: &mut MySqlConnection,
        id: i32,
    ) -> Result<Option<String>, sqlx::Error> {
        let sql = match self {
            Self::Score => "SELECT CONCAT(d.name, ' · Round ', r.round_number) FROM tab_roundstats s
                            JOIN tab_debater d ON d.id = s.debater_id JOIN tab_round r ON r.id = s.round_id
                            WHERE s.id = ?",
            Self::Round => "SELECT CONCAT(g.name, ' vs. ', o.name) FROM tab_round r
                            JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
                            WHERE r.id = ?",
            Self::Outround => "SELECT CONCAT(g.name, ' vs. ', o.name) FROM tab_outround r
                            JOIN tab_team g ON g.id = r.gov_team_id JOIN tab_team o ON o.id = r.opp_team_id
                            WHERE r.id = ?",
        };
        sqlx::query_scalar(sql).bind(id).fetch_optional(db).await
    }
}

async fn confirm_delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((model, id)): Path<(String, i32)>,
    headers: HeaderMap,
) -> Response {
    let Some(record) = Record::parse(&model) else {
        return missing();
    };
    let result = async {
        let mut db = app.db.acquire().await?;
        let Some(description) = record.describe(&mut db, id).await? else {
            return Ok(missing());
        };
        let Ok(csrf) = html::page_csrf(&headers) else {
            return Ok(unavailable());
        };
        let response = html::render(
            "admin_repair.html",
            context! {
                title => "Confirm Deletion", nav => Navigation::new(actor, true), csrf, description,
                cancel => record.cancel(id),
            },
            StatusCode::OK,
        );
        Ok::<_, sqlx::Error>(html::form_response(response, &headers, &csrf))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

#[derive(Deserialize)]
struct DeleteForm {
    csrfmiddlewaretoken: String,
    confirm: Option<String>,
}

async fn delete(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    Path((model, id)): Path<(String, i32)>,
    headers: HeaderMap,
    Form(form): Form<DeleteForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    if form.confirm.as_deref() != Some("on") {
        return private((
            StatusCode::UNPROCESSABLE_ENTITY,
            "Confirm the deletion first.",
        ));
    }
    let Actor::User { id: user, .. } = actor else {
        return access::access_denied();
    };
    let Some(record) = Record::parse(&model) else {
        return missing();
    };
    let result = async {
        let mut db = app.db.begin().await?;
        lock_round(&mut db).await?;
        let destination = match record {
            Record::Score => {
                let Some(score) = score_row(&mut db, id).await? else {
                    return Ok(missing());
                };
                let before = score_audit(&mut db, score.round_id).await?;
                sqlx::query("DELETE FROM tab_roundstats WHERE id = ?")
                    .bind(id)
                    .execute(&mut *db)
                    .await?;
                let after = score_audit(&mut db, score.round_id).await?;
                round_audit::record(&mut db, &before, &after, user, "admin scores").await?;
                STATISTICS
            }
            Record::Round => {
                let before = score_audit(&mut db, id).await?;
                if before.is_empty() {
                    return Ok(missing());
                }
                crate::entity_store::delete_rounds(&mut db, &[id]).await?;
                round_audit::record(
                    &mut db,
                    &before,
                    &Default::default(),
                    user,
                    "admin deletion",
                )
                .await?;
                "/pairings/view_rounds/"
            }
            Record::Outround => {
                let mut snapshots =
                    board_snapshot::capture(&mut db, board_snapshot::Pairings::Outround).await?;
                let Some(before) = snapshots.remove(&id) else {
                    return Ok(missing());
                };
                let (Some(division), Some(size)) = (before.division, before.num_teams) else {
                    return Ok(missing());
                };
                if snapshots.values().any(|round| {
                    round.division == Some(division)
                        && round.num_teams.is_some_and(|teams| teams < size)
                }) {
                    return Ok(private((
                        StatusCode::CONFLICT,
                        "Resolve the later stage before deleting this pairing.",
                    )));
                }
                crate::entity_store::delete_outrounds(&mut db, &[id]).await?;
                prune_published_stage(&mut db, division, size, &before, &snapshots).await?;
                board_snapshot::audit_pairings(
                    &mut db,
                    &std::collections::BTreeMap::from([(id, before)]),
                    &Default::default(),
                    user,
                    "admin deletion",
                )
                .await?;
                crate::board_outrounds::recompute_staff_seeds(&mut db, user, &snapshots, division)
                    .await?;
                let settings = Settings::load(&mut db, publication::VISIBILITY_KEYS).await?;
                publication::refresh_placements(&mut db, &settings).await?;
                "/outround_pairing"
            }
        };
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(destination))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

async fn prune_published_stage(
    db: &mut MySqlConnection,
    division: i32,
    size: i32,
    removed: &board_snapshot::Snapshot,
    remaining: &std::collections::BTreeMap<i32, board_snapshot::Snapshot>,
) -> Result<(), sqlx::Error> {
    let stage: Vec<_> = remaining
        .values()
        .filter(|round| round.division == Some(division) && round.num_teams == Some(size))
        .collect();
    if !stage.is_empty() {
        let remaining_teams: std::collections::BTreeSet<_> = stage
            .iter()
            .flat_map(|round| [&round.gov_team, &round.opp_team])
            .flatten()
            .map(|team| team.id)
            .collect();
        for team in [&removed.gov_team, &removed.opp_team].into_iter().flatten() {
            if !remaining_teams.contains(&team.id) {
                sqlx::query(
                    "DELETE p FROM tab_publishedteamplacement p
                            JOIN tab_publishedoutroundstage s ON s.id = p.stage_id
                            WHERE s.type_of_round = ? AND s.num_teams = ? AND p.team_id = ?",
                )
                .bind(division)
                .bind(size)
                .bind(team.id)
                .execute(&mut *db)
                .await?;
            }
        }
        return Ok(());
    }
    sqlx::query(
        "DELETE p FROM tab_publishedteamplacement p
                JOIN tab_publishedoutroundstage s ON s.id = p.stage_id
                WHERE s.type_of_round = ? AND s.num_teams = ?",
    )
    .bind(division)
    .bind(size)
    .execute(&mut *db)
    .await?;
    sqlx::query("DELETE FROM tab_publishedoutroundstage WHERE type_of_round = ? AND num_teams = ?")
        .bind(division)
        .bind(size)
        .execute(db)
        .await?;
    Ok(())
}

fn missing() -> Response {
    private((StatusCode::NOT_FOUND, "Record not found."))
}
