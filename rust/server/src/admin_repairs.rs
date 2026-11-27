use std::collections::{BTreeMap, BTreeSet};

use axum::{
    Extension, Form, Router,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};
use minijinja::context;
use serde::Deserialize;
use sqlx::MySqlConnection;

use crate::{
    App, access,
    admin_scores::score_audit,
    board_snapshot,
    html::{self, Navigation},
    legacy_session::Actor,
    login, private, publication, round_audit,
    settings::Settings,
    staff_pairing::lock_round,
    unavailable,
};

pub fn routes() -> Router<App> {
    Router::new().route(
        "/admin/tab/{model}/{id}/delete/",
        get(confirm_delete).post(delete),
    )
}

#[derive(Clone, Copy)]
enum Record {
    Round,
    Outround,
}

impl Record {
    fn parse(model: &str) -> Option<Self> {
        match model {
            "round" => Some(Self::Round),
            "outround" => Some(Self::Outround),
            _ => None,
        }
    }

    fn cancel(self, id: i32) -> String {
        match self {
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
                let before =
                    board_snapshot::capture(&mut db, board_snapshot::Pairings::Outround).await?;
                if !before.contains_key(&id) {
                    return Ok(missing());
                }
                let removed = BTreeSet::from([id]);
                if later_stage_depends_on(&before, &removed) {
                    return Ok(private((StatusCode::CONFLICT, LATER_STAGE)));
                }
                crate::entity_store::delete_outrounds(&mut db, &[id]).await?;
                finish_outround_changes(&mut db, user, &before, &removed).await?;
                "/outround_pairing"
            }
        };
        db.commit().await?;
        Ok::<_, sqlx::Error>(access::found(destination))
    }
    .await;
    result.unwrap_or_else(|_| unavailable())
}

pub(super) const LATER_STAGE: &str = "Resolve the later stage before deleting this pairing.";

pub(super) fn later_stage_depends_on(
    before: &BTreeMap<i32, board_snapshot::Snapshot>,
    removed: &BTreeSet<i32>,
) -> bool {
    removed.iter().filter_map(|id| before.get(id)).any(|round| {
        before.iter().any(|(id, later)| {
            !removed.contains(id)
                && round.division == later.division
                && later
                    .num_teams
                    .zip(round.num_teams)
                    .is_some_and(|(next, current)| next < current)
        })
    })
}

pub(super) async fn finish_outround_changes(
    db: &mut MySqlConnection,
    user: i32,
    before: &BTreeMap<i32, board_snapshot::Snapshot>,
    removed: &BTreeSet<i32>,
) -> Result<(), sqlx::Error> {
    let after = board_snapshot::capture(db, board_snapshot::Pairings::Outround).await?;
    let mut divisions = BTreeSet::new();
    for round in removed.iter().filter_map(|id| before.get(id)) {
        if let (Some(division), Some(size)) = (round.division, round.num_teams) {
            prune_published_stage(db, division, size, round, &after).await?;
            divisions.insert(division);
        }
    }
    board_snapshot::audit_pairings(db, before, &after, user, "admin deletion").await?;
    for division in divisions {
        crate::board_outrounds::recompute_staff_seeds(db, user, &after, division).await?;
    }
    if !removed.is_empty() {
        let settings = Settings::load(db, publication::VISIBILITY_KEYS).await?;
        publication::refresh_placements(db, &settings).await?;
    }
    Ok(())
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
