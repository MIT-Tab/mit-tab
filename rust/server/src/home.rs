use std::{ffi::OsString, path::PathBuf, time::UNIX_EPOCH};

use axum::{
    Extension,
    extract::State,
    http::{HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};
use minijinja::context;
use serde::Serialize;
use sqlx::MySqlConnection;

use crate::{
    App, access, legacy_html, legacy_session::Actor, public_cache, settings::Settings, unavailable,
};

const SETTINGS: &[&str] = &[
    "tournament_name",
    "cur_round",
    "tot_rounds",
    "var_teams_visible",
    "nov_teams_visible",
    "pairing_released",
    "judges_public",
    "teams_public",
    "motions_enabled",
    "publish_tab_cards_with_first_outround",
    "tournament_director",
    "tab_director",
    "equity_officers",
    "tournament_observer",
];

#[derive(Serialize)]
struct Shortcut {
    title: &'static str,
    url: &'static str,
    subtitle: String,
}

#[derive(Serialize)]
struct InfoLink {
    title: String,
    url: String,
    description: String,
}

#[derive(Serialize)]
struct InfoText {
    title: String,
    text: String,
}

#[derive(Serialize)]
struct Staff {
    title: String,
    names: Vec<String>,
}

pub async fn public_home(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    let logo = legacy_logo_href().await;
    public_cache::home(&app, actor, logo.clone(), async |db| {
        page(db, actor, logo).await
    })
    .await
}

async fn page(
    db: &mut MySqlConnection,
    actor: Actor,
    legacy_logo: Option<String>,
) -> Result<Response, sqlx::Error> {
    let settings = Settings::load(db, SETTINGS).await?;
    let started = settings.integer("cur_round", 1) > 1;
    let prelim = settings.integer("cur_round", 1).saturating_sub(1);
    let (has_pairing, elims) = round_status(db, &settings).await?;
    let has_outrounds = elims.iter().any(Option::is_some);
    let published_inround = settings.integer("pairing_released", 0) == 1;
    let (team_results, speaker_results, ballots) = display_state(db).await?;
    let open = registration_open(db).await?;
    let show_registration_lead = !started && open;

    let mut quick_links = Vec::new();
    for (division, current) in elims.iter().enumerate() {
        if let Some(round) = current.filter(|round| round.has_visible) {
            let (title, url, prefix) = [
                ("Varsity Outrounds", "/public/outrounds/0/", "V"),
                ("Novice Outrounds", "/public/outrounds/1/", "N"),
            ][division];
            let status = if round.latest_public {
                "paired"
            } else {
                "pairing in progress"
            };
            let subtitle = if started {
                format!("[{prefix}] Ro{} {status}", round.latest_size)
            } else if division == 0 {
                "Varsity elimination brackets and pairings.".into()
            } else {
                "Novice elimination brackets and pairings.".into()
            };
            quick_links.push(Shortcut {
                title,
                url,
                subtitle,
            });
        }
    }
    if started && !has_outrounds && has_pairing {
        let title = if published_inround {
            "Released Pairings"
        } else {
            "Pairings in Progress"
        };
        let state = if published_inround {
            "Pairing released"
        } else {
            "Pairing in progress"
        };
        let subtitle = if prelim <= settings.integer("tot_rounds", 5) {
            format!("Round {prelim} · {state}")
        } else {
            state.into()
        };
        quick_links.push(Shortcut {
            title,
            url: "/public/pairings/",
            subtitle,
        });
    }
    let judges_public = settings.flag("judges_public", false);
    let teams_public = settings.flag("teams_public", false);
    let motions_public = settings.flag("motions_enabled", false);
    let tab_cards = settings.flag("publish_tab_cards_with_first_outround", true)
        && elims.iter().flatten().any(|round| round.has_visible);
    for (enabled, title, subtitle, url) in [
        (
            started && !has_outrounds && has_pairing && published_inround,
            "Missing Ballots",
            "Outstanding ballots that need attention.",
            "/public/missing-ballots/",
        ),
        (
            true,
            "Judge Portal and Ballots",
            "Judges enter codes to update availability and file results.",
            "/public/e-ballots/",
        ),
        (
            true,
            "Team Portal",
            "Teams enter codes to manage names and scratches.",
            "/team_portal/",
        ),
        (
            judges_public,
            "Judge List",
            "Roster and check-in status.",
            "/public/judges/",
        ),
        (
            teams_public,
            "Team List",
            "Registered teams and schools.",
            "/public/teams/",
        ),
        (
            started && !has_outrounds && team_results,
            "Public Team Results",
            "Standings and published team records.",
            "/public/team-rankings/",
        ),
        (
            started && !has_outrounds && speaker_results,
            "Public Speaker Results",
            "Published speaker rankings and records.",
            "/public/speaker-rankings/",
        ),
        (
            started && !has_outrounds && ballots,
            "Public Ballots",
            "Published ballots and detailed round results.",
            "/public/ballots/",
        ),
        (
            started && motions_public,
            "Motions",
            "View debate motions and info slides.",
            "/public/motions/",
        ),
        (
            tab_cards,
            "Tab Cards",
            "Team results, speaker scores, and round records.",
            "/public/tab-cards/",
        ),
    ] {
        if enabled {
            quick_links.push(Shortcut {
                title,
                url,
                subtitle: subtitle.into(),
            });
        }
    }
    let (info_links, info_texts, tournament_staff) = resources(db, &settings).await?;
    let tournament = settings.string("tournament_name", "New Tournament");
    let logo = database_logo_href(&mut *db).await.or(legacy_logo);
    let public_tiles_odd = (quick_links.len() + usize::from(show_registration_lead)) % 2 == 1;
    let shell = legacy_html::shell(db, actor, "/public/").await?;
    Ok(legacy_html::render(
        "public/home.html",
        shell,
        context! {
            tournament, tournament_logo => logo, quick_links, show_registration_lead, public_tiles_odd,
            registration_url => "/registration/",
            info_links, info_texts, tournament_staff,
        },
        StatusCode::OK,
    ))
}

#[derive(Clone, Copy)]
struct ElimState {
    latest_size: i32,
    latest_public: bool,
    has_visible: bool,
}

async fn round_status(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<(bool, [Option<ElimState>; 2]), sqlx::Error> {
    let has_pairing = sqlx::query_scalar::<_, i64>("SELECT EXISTS(SELECT 1 FROM tab_round)")
        .fetch_one(&mut *db)
        .await?
        != 0;
    let mut outrounds = [None; 2];
    for (division, latest, opening) in sqlx::query_as::<_, (i32, i32, i32)>(
        "SELECT type_of_round, MIN(num_teams), MAX(num_teams)
         FROM tab_outround WHERE type_of_round IN (0, 1) GROUP BY type_of_round",
    )
    .fetch_all(db)
    .await?
    {
        let visibility_key = ["var_teams_visible", "nov_teams_visible"][division as usize];
        let visible = settings.integer(visibility_key, 256);
        outrounds[division as usize] = Some(ElimState {
            latest_size: latest,
            latest_public: latest >= visible,
            has_visible: opening >= visible,
        });
    }
    Ok((has_pairing, outrounds))
}

async fn display_state(db: &mut MySqlConnection) -> Result<(bool, bool, bool), sqlx::Error> {
    let (team, speaker, ballot) = sqlx::query_as::<_, (i64, i64, i64)>(
        "SELECT
          EXISTS(SELECT 1 FROM tab_publicdisplaysetting WHERE slug = 'team' AND is_enabled = TRUE),
          EXISTS(SELECT 1 FROM tab_publicdisplaysetting WHERE slug IN ('varsity','novice') AND is_enabled = TRUE),
          EXISTS(SELECT 1 FROM tab_publicdisplaysetting WHERE display_type = 'ballot' AND is_enabled = TRUE)",
    ).fetch_one(db).await?;
    Ok((team != 0, speaker != 0, ballot != 0))
}

async fn registration_open(db: &mut MySqlConnection) -> Result<bool, sqlx::Error> {
    let open = sqlx::query_scalar::<_, i64>(
        "SELECT allow_new_registrations AND
           (new_registrations_open_at IS NULL OR new_registrations_open_at <= UTC_TIMESTAMP(6)) AND
           (new_registrations_close_at IS NULL OR new_registrations_close_at > UTC_TIMESTAMP(6))
         FROM registration_registrationconfig WHERE id = 1",
    )
    .fetch_optional(db)
    .await?
    .unwrap_or(1);
    Ok(open != 0)
}

async fn resources(
    db: &mut MySqlConnection,
    settings: &Settings,
) -> Result<(Vec<InfoLink>, Vec<InfoText>, Vec<Staff>), sqlx::Error> {
    let info_links = sqlx::query_as::<_, (String, String, String)>(
        "SELECT title, url, description FROM registration_infolink WHERE is_active = TRUE
         ORDER BY display_order, created_at, id",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .filter(|(_, url, _)| {
        url.parse::<axum::http::Uri>().is_ok_and(|uri| {
            uri.authority()
                .is_some_and(|authority| !authority.host().is_empty())
                && matches!(uri.scheme_str(), Some("http" | "https" | "ftp" | "ftps"))
        })
    })
    .map(|(title, url, description)| InfoLink {
        title,
        url,
        description,
    })
    .collect();
    let info_texts = sqlx::query_as::<_, (String, String)>(
        "SELECT title, text FROM registration_infotext WHERE is_active = TRUE
         ORDER BY display_order, created_at, id",
    )
    .fetch_all(&mut *db)
    .await?
    .into_iter()
    .map(|(title, text)| InfoText { title, text })
    .collect();

    let mut staff = Vec::new();
    for (key, title) in [
        ("tournament_director", "Tournament Director"),
        ("tab_director", "Tab Director"),
        ("equity_officers", "Equity Officer(s)"),
        ("tournament_observer", "Tournament Observer"),
    ] {
        add_staff(&mut staff, title, &settings.string(key, ""));
    }
    for (title, names) in sqlx::query_as::<_, (String, String)>(
        "SELECT title, names FROM tab_tournamentstaffrole ORDER BY sort_order, id",
    )
    .fetch_all(db)
    .await?
    {
        add_staff(&mut staff, &title, &names);
    }
    Ok((info_links, info_texts, staff))
}

fn add_staff(staff: &mut Vec<Staff>, title: &str, names: &str) {
    let title = title.trim();
    let names: Vec<_> = names
        .lines()
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(str::to_owned)
        .collect();
    if !title.is_empty() && !names.is_empty() {
        staff.push(Staff {
            title: title.into(),
            names,
        });
    }
}

fn uploads_dir() -> PathBuf {
    PathBuf::from(
        std::env::var_os("MITTAB_UPLOADS_ROOT").unwrap_or_else(|| OsString::from("mittab/uploads")),
    )
}

pub async fn logo_url(app: &App) -> Option<String> {
    match database_logo_href(&app.db).await {
        Some(logo) => Some(logo),
        None => legacy_logo_href().await,
    }
}

async fn database_logo_href<'e>(db: impl sqlx::MySqlExecutor<'e>) -> Option<String> {
    sqlx::query_scalar::<_, i64>(
        "SELECT TIMESTAMPDIFF(MICROSECOND, '1970-01-01', updated_at) FROM mittab_public_logo WHERE id = 1",
    ).fetch_optional(db).await.ok().flatten().map(|version| format!("/tournament-logo/?v={version}"))
}

async fn legacy_logo_href() -> Option<String> {
    let path = uploads_dir().join("tournament_logo.png");
    let metadata = tokio::fs::symlink_metadata(path).await.ok()?;
    if !metadata.is_file() {
        return None;
    }
    let seconds = metadata
        .modified()
        .ok()
        .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok());
    Some(seconds.map_or_else(
        || "/tournament-logo/".into(),
        |modified| format!("/tournament-logo/?v={}", modified.as_secs()),
    ))
}

async fn read_logo(app: &App) -> Option<Vec<u8>> {
    if let Ok(Some(bytes)) =
        sqlx::query_scalar::<_, Vec<u8>>("SELECT bytes FROM mittab_public_logo WHERE id = 1")
            .fetch_optional(&app.db)
            .await
    {
        return Some(bytes);
    }
    let path = uploads_dir().join("tournament_logo.png");
    let metadata = tokio::fs::symlink_metadata(&path).await.ok()?;
    if !metadata.is_file() {
        return None;
    }
    let bytes = tokio::fs::read(path).await.ok()?;
    bytes.starts_with(b"\x89PNG\r\n\x1a\n").then_some(bytes)
}

pub async fn logo(State(app): State<App>) -> Response {
    match read_logo(&app).await {
        Some(bytes) => asset("image/png", bytes),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

pub async fn favicon(State(app): State<App>) -> Response {
    match read_logo(&app).await {
        Some(bytes) => asset("image/png", bytes),
        None => asset(
            "image/x-icon",
            include_bytes!("../../../assets/img/favicon.ico").as_slice(),
        ),
    }
}

fn asset(content_type: &'static str, body: impl IntoResponse) -> Response {
    let mut response = body.into_response();
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    response
}

pub async fn index(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    match actor {
        Actor::Anonymous => access::found("/public/"),
        Actor::User { board: true, .. } => access::found("/apda-board/"),
        Actor::User {
            superuser: true, ..
        } => match access::wizard_complete(&app).await {
            Ok(true) => access::found("/pairings/status/"),
            Ok(false) => access::found("/setup/start-tournament/"),
            Err(_) => unavailable(),
        },
        Actor::User {
            id, staff: true, ..
        } => match crate::staff_roles::preset(&app.db, id).await {
            Ok(Some(preset)) if preset.data_entry() => access::found("/view_teams/"),
            Ok(Some(preset)) => access::found(preset.landing()),
            Ok(None) => access::access_denied(),
            Err(_) => unavailable(),
        },
        Actor::User { id, .. } => match access::legacy_entry_user(&app, id).await {
            Ok(true) => access::found("/pairings/status/"),
            Ok(false) => access::found("/public/"),
            Err(_) => unavailable(),
        },
    }
}
