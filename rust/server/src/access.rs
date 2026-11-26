use axum::{
    extract::{Request, State},
    http::{StatusCode, header},
    middleware::Next,
    response::Response,
};

use crate::{
    App,
    legacy_session::{Actor, actor},
    private,
    settings::Settings,
    unavailable,
};

pub async fn public_api(State(app): State<App>, request: Request, next: Next) -> Response {
    let Ok(actor) = actor(&app.db, request.headers(), app.django_secret.as_deref()).await else {
        return unavailable();
    };
    match actor {
        Actor::User { board: true, .. } => {
            let Ok(open) = board_access_open(&app).await else {
                return unavailable();
            };
            if open {
                access_denied()
            } else {
                private((
                    StatusCode::FORBIDDEN,
                    "Board access opens near the end of inrounds.",
                ))
            }
        }
        Actor::User {
            staff: true,
            superuser: false,
            ..
        } => access_denied(),
        Actor::User {
            superuser: true, ..
        } => {
            let Ok(mut db) = app.db.acquire().await else {
                return unavailable();
            };
            let Ok(settings) =
                Settings::load(&mut db, &["start_tournament_wizard_completed"]).await
            else {
                return unavailable();
            };
            let completed = settings.string("start_tournament_wizard_completed", "1");
            if matches!(
                completed.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            ) {
                drop(db);
                next.run(request).await
            } else {
                private((
                    StatusCode::FOUND,
                    [(header::LOCATION, "/setup/start-tournament/")],
                ))
            }
        }
        _ => next.run(request).await,
    }
}

fn access_denied() -> Response {
    private((StatusCode::FOUND, [(header::LOCATION, "/403/")]))
}

async fn board_access_open(app: &App) -> Result<bool, sqlx::Error> {
    let total_rounds = sqlx::query_as::<_, (Option<i32>, Option<String>)>(
        "SELECT value, value_string FROM tab_tabsettings WHERE `key` = 'tot_rounds' ORDER BY id LIMIT 1",
    ).fetch_optional(&app.db).await?
        .and_then(|(value, text)| match text {
            Some(text) => text.trim().parse().ok(),
            None => value,
        })
        .filter(|&rounds| rounds > 0);
    let Some(final_round) = total_rounds else {
        return Ok(false);
    };
    const MILESTONE: &str = "
        SELECT EXISTS(SELECT 1 FROM tab_outround)
           OR (COUNT(*) > 0 AND COUNT(CASE WHEN victor <> 0 THEN 1 END) * 5 >= COUNT(*) * 4)
        FROM tab_round WHERE round_number = ?
    ";
    let open = sqlx::query_scalar::<_, i64>(MILESTONE)
        .bind(final_round)
        .fetch_one(&app.db)
        .await?;
    Ok(open != 0)
}
