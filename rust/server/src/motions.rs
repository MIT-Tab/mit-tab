use axum::{
    Extension, Form,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::context;
use serde::{Deserialize, Serialize};
use sqlx::{MySql, MySqlExecutor, QueryBuilder};

use crate::{
    App, access,
    html::{self, Navigation},
    legacy_html,
    legacy_session::{Actor, cookie},
    login,
    public_cache::{self, Page},
    settings::Settings,
    unavailable,
};

const MANAGE: &str = "/motions/";
const PUBLIC_ERROR: &str = "/public/access-error/";
const SETTINGS: &[&str] = &["motions_enabled", "tot_rounds", "tournament_name"];
const FLASH: &str = "mittab_motion_message";

#[derive(Deserialize, Default)]
pub struct MotionForm {
    #[serde(default)]
    csrfmiddlewaretoken: String,
    #[serde(default)]
    round_selection: String,
    #[serde(default)]
    info_slide: String,
    #[serde(default)]
    motion_text: String,
}

#[derive(Deserialize)]
pub struct ActionForm {
    #[serde(default)]
    csrfmiddlewaretoken: String,
}

#[derive(Clone, Copy)]
struct RoundKey {
    preliminary: Option<i32>,
    division: Option<i32>,
    size: Option<i32>,
}

impl RoundKey {
    fn parse(value: &str) -> Option<Self> {
        if let Some(round) = value.strip_prefix("inround_") {
            return Some(Self {
                preliminary: Some(round.trim().parse().ok()?),
                division: None,
                size: None,
            });
        }
        let (division, size) = value.strip_prefix("outround_")?.split_once('_')?;
        let division = division.trim().parse().ok()?;
        if !matches!(division, 0 | 1) {
            return None;
        }
        Some(Self {
            preliminary: None,
            division: Some(division),
            size: Some(size.trim().parse().ok()?),
        })
    }

    fn display(self) -> String {
        match (self.preliminary, self.size) {
            (Some(number), _) => format!("Round {number}"),
            (None, Some(size)) => {
                let division = if self.division == Some(0) {
                    "Varsity"
                } else {
                    "Novice"
                };
                format!("{division} {}", elimination_name(size))
            }
            _ => "Unknown Round".into(),
        }
    }

    fn selection(self) -> String {
        match (self.preliminary, self.division, self.size) {
            (Some(number), _, _) => format!("inround_{number}"),
            (None, Some(division), Some(size)) => format!("outround_{division}_{size}"),
            _ => String::new(),
        }
    }

    fn sort_key(self) -> (i32, i32, i64) {
        match self.preliminary {
            Some(number) => (0, number, 0),
            None => (
                1,
                self.division.unwrap_or_default(),
                -i64::from(self.size.unwrap_or_default()),
            ),
        }
    }

    fn is_outround(self) -> bool {
        self.preliminary.is_none() && self.size.is_some()
    }
}

type Row = (
    i32,
    Option<i32>,
    Option<i32>,
    Option<i32>,
    String,
    String,
    bool,
    NaiveDateTime,
    NaiveDateTime,
);

#[derive(Serialize)]
struct Motion {
    id: i32,
    round_display: String,
    info_slide: String,
    motion_text: String,
    info_preview: String,
    motion_preview: String,
    published: bool,
    created: String,
    updated: String,
    #[serde(skip)]
    round: RoundKey,
}

impl From<Row> for Motion {
    fn from(
        (id, preliminary, division, size, info_slide, motion_text, published, created, updated): Row,
    ) -> Self {
        let round = RoundKey {
            preliminary,
            division,
            size,
        };
        Self {
            id,
            round_display: round.display(),
            info_preview: truncate(&info_slide, 100),
            motion_preview: truncate(&motion_text, 150),
            info_slide,
            motion_text,
            published,
            created: created.format("%b %d, %Y %H:%M").to_string(),
            updated: updated.format("%b %d, %Y %H:%M").to_string(),
            round,
        }
    }
}

const SELECT: &str = "
    SELECT id, round_number, outround_type, num_teams, info_slide, motion_text,
           is_published, created_at, updated_at
    FROM tab_motion
";

async fn list<'e>(
    db: impl MySqlExecutor<'e>,
    public_only: bool,
) -> Result<Vec<Motion>, sqlx::Error> {
    // The public projection is constrained in SQL before the rows are loaded.
    let mut query = QueryBuilder::<MySql>::new(SELECT);
    if public_only {
        query.push(" WHERE is_published = TRUE");
    }
    query.push(" ORDER BY round_number, outround_type, num_teams DESC, id");
    let rows = query.build_query_as::<Row>().fetch_all(db).await?;
    Ok(rows.into_iter().map(Motion::from).collect())
}

async fn one(app: &App, id: i32) -> Result<Option<Motion>, sqlx::Error> {
    let row = QueryBuilder::<MySql>::new(SELECT)
        .push(" WHERE id = ")
        .push_bind(id)
        .build_query_as::<Row>()
        .fetch_optional(&app.db)
        .await?;
    Ok(row.map(Motion::from))
}

async fn page_settings(app: &App) -> Result<Settings, sqlx::Error> {
    let mut db = app.db.acquire().await?;
    Settings::load(&mut db, SETTINGS).await
}

pub async fn public(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::Motions, async |db| {
        let settings = Settings::load(db, SETTINGS).await?;
        if !settings.flag("motions_enabled", false) {
            return Ok(access::found(PUBLIC_ERROR));
        }
        let mut rows = list(&mut *db, true).await?;
        rows.sort_by_key(|motion| std::cmp::Reverse(motion.round.sort_key()));
        let has_motions = !rows.is_empty();
        let [mut varsity, mut novice, mut preliminary] = [Vec::new(), Vec::new(), Vec::new()];
        for row in rows {
            if !row.round.is_outround() {
                preliminary.push(row);
            } else if row.round.division == Some(0) {
                varsity.push(row);
            } else if row.round.division == Some(1) {
                novice.push(row);
            }
        }
        let tournament = settings.string("tournament_name", "New Tournament");
        let shell = legacy_html::shell(db, actor, "/public/motions/").await?;
        Ok(legacy_html::render(
            "public/motions.html",
            shell,
            context! {
                tournament, has_motions,
                varsity_outrounds => varsity,
                novice_outrounds => novice,
                inround_motions => preliminary,
            },
            StatusCode::OK,
        ))
    })
    .await
}

pub async fn manage(
    State(app): State<App>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    let Ok(settings) = page_settings(&app).await else {
        return unavailable();
    };
    if !settings.flag("motions_enabled", false) {
        return flash(&headers, "/settings_form", Notice::Disabled);
    }
    let Ok(mut motions) = list(&app.db, false).await else {
        return unavailable();
    };
    motions.sort_by_key(|motion| motion.round.sort_key());
    let Ok(csrf) = html::page_csrf(&headers) else {
        return unavailable();
    };
    let notice = cookie(&headers, FLASH).and_then(Notice::from_code);
    let response = html::render(
        "manage_motions.html",
        context! {
            title => "Manage Motions", nav => Navigation::new(actor, true),
            rounds => available_rounds(settings.integer("tot_rounds", 5)),
            csrf => &csrf, motions,
            message => notice.map(Notice::message),
            has_error => notice.is_some_and(Notice::error),
        },
        StatusCode::OK,
    );
    let mut response = html::form_response(response, &headers, &csrf);
    if notice.is_some() {
        login::set_cookie(
            &mut response,
            FLASH,
            "",
            0,
            login::secure_request(&headers),
            true,
        );
    }
    response
}

pub async fn edit(
    State(app): State<App>,
    Path(id): Path<i32>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> Response {
    render_edit(&app, id, actor, &headers, None).await
}

async fn render_edit(
    app: &App,
    id: i32,
    actor: Actor,
    headers: &HeaderMap,
    invalid: Option<(MotionForm, Notice)>,
) -> Response {
    let motion = match one(app, id).await {
        Ok(Some(motion)) => motion,
        Ok(None) => return not_found(actor),
        Err(_) => return unavailable(),
    };
    let Ok(settings) = page_settings(app).await else {
        return unavailable();
    };
    let Ok(csrf) = html::page_csrf(headers) else {
        return unavailable();
    };
    let (form, error) = match invalid {
        Some((form, notice)) => (form, Some(notice.message())),
        None => (
            MotionForm {
                round_selection: motion.round.selection(),
                info_slide: motion.info_slide.clone(),
                motion_text: motion.motion_text.clone(),
                ..Default::default()
            },
            None,
        ),
    };
    let response = html::render(
        "edit_motion.html",
        context! {
            title => format!("Edit Motion - {}", motion.round_display),
            nav => Navigation::new(actor, settings.flag("motions_enabled", false)),
            rounds => available_rounds(settings.integer("tot_rounds", 5)),
            csrf => &csrf, motion, selected => form.round_selection,
            info_slide => form.info_slide, motion_text => form.motion_text,
            message => error, has_error => error.is_some(),
        },
        StatusCode::OK,
    );
    html::form_response(response, headers, &csrf)
}

pub async fn add(
    State(app): State<App>,
    headers: HeaderMap,
    Form(form): Form<MotionForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    let round = match validate(&form) {
        Ok(round) => round,
        Err(notice) => return flash(&headers, MANAGE, notice),
    };
    let result = sqlx::query(
        "
        INSERT INTO tab_motion (round_number, outround_type, num_teams, info_slide, motion_text,
                                is_published, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, FALSE, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
    ",
    )
    .bind(round.preliminary)
    .bind(round.division)
    .bind(round.size)
    .bind(form.info_slide.trim())
    .bind(form.motion_text.trim())
    .execute(&app.db)
    .await;
    match result {
        Ok(_) => flash(&headers, MANAGE, Notice::Added),
        Err(_) => unavailable(),
    }
}

pub async fn save(
    State(app): State<App>,
    Path(id): Path<i32>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<MotionForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    let round = match validate(&form) {
        Ok(round) => round,
        Err(notice) => return render_edit(&app, id, actor, &headers, Some((form, notice))).await,
    };
    let result = sqlx::query(
        "
        UPDATE tab_motion SET round_number = ?, outround_type = ?, num_teams = ?,
            info_slide = ?, motion_text = ?, updated_at = UTC_TIMESTAMP(6) WHERE id = ?
    ",
    )
    .bind(round.preliminary)
    .bind(round.division)
    .bind(round.size)
    .bind(form.info_slide.trim())
    .bind(form.motion_text.trim())
    .bind(id)
    .execute(&app.db)
    .await;
    match result {
        Ok(row) if row.rows_affected() == 0 => not_found(actor),
        Ok(_) => flash(&headers, MANAGE, Notice::Updated),
        Err(_) => unavailable(),
    }
}

pub async fn delete(
    State(app): State<App>,
    Path(id): Path<i32>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<ActionForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    match sqlx::query("DELETE FROM tab_motion WHERE id = ?")
        .bind(id)
        .execute(&app.db)
        .await
    {
        Ok(row) if row.rows_affected() == 0 => not_found(actor),
        Ok(_) => flash(&headers, MANAGE, Notice::Deleted),
        Err(_) => unavailable(),
    }
}

pub async fn toggle(
    State(app): State<App>,
    Path(id): Path<i32>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
    Form(form): Form<ActionForm>,
) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    let Ok(mut tx) = app.db.begin().await else {
        return unavailable();
    };
    let changed = sqlx::query("
        UPDATE tab_motion SET is_published = NOT is_published, updated_at = UTC_TIMESTAMP(6) WHERE id = ?
    ").bind(id).execute(&mut *tx).await;
    match changed {
        Ok(row) if row.rows_affected() == 0 => return not_found(actor),
        Ok(_) => {}
        Err(_) => return unavailable(),
    }
    let published =
        sqlx::query_scalar::<_, bool>("SELECT is_published FROM tab_motion WHERE id = ?")
            .bind(id)
            .fetch_one(&mut *tx)
            .await;
    let Ok(published) = published else {
        return unavailable();
    };
    if tx.commit().await.is_err() {
        return unavailable();
    }
    flash(
        &headers,
        MANAGE,
        if published {
            Notice::Published
        } else {
            Notice::Unpublished
        },
    )
}

pub async fn publish_all(
    State(app): State<App>,
    headers: HeaderMap,
    Form(form): Form<ActionForm>,
) -> Response {
    bulk(&app, headers, form, true).await
}

pub async fn unpublish_all(
    State(app): State<App>,
    headers: HeaderMap,
    Form(form): Form<ActionForm>,
) -> Response {
    bulk(&app, headers, form, false).await
}

async fn bulk(app: &App, headers: HeaderMap, form: ActionForm, publish: bool) -> Response {
    if !login::valid_csrf(&headers, &form.csrfmiddlewaretoken) {
        return login::csrf_error();
    }
    match sqlx::query("UPDATE tab_motion SET is_published = ?")
        .bind(publish)
        .execute(&app.db)
        .await
    {
        Ok(_) => flash(
            &headers,
            MANAGE,
            if publish {
                Notice::AllPublished
            } else {
                Notice::AllUnpublished
            },
        ),
        Err(_) => unavailable(),
    }
}

pub async fn back_to_manage() -> Response {
    access::found(MANAGE)
}

pub async fn public_error(State(app): State<App>, Extension(actor): Extension<Actor>) -> Response {
    public_cache::render(&app, actor, Page::AccessError, async |db| {
        let shell = legacy_html::shell(db, actor, PUBLIC_ERROR).await?;
        Ok(legacy_html::render(
            "public/access_error.html",
            shell,
            context! {},
            StatusCode::OK,
        ))
    })
    .await
}

pub async fn forbidden(Extension(actor): Extension<Actor>) -> Response {
    html::render(
        "error.html",
        context! {
            nav => Navigation::new(actor, false), title => "Access Denied", heading => "Access Denied",
        },
        StatusCode::FORBIDDEN,
    )
}

fn not_found(actor: Actor) -> Response {
    html::render(
        "error.html",
        context! {
            nav => Navigation::new(actor, false), title => "Page Not Found", heading => "Page Not Found",
        },
        StatusCode::NOT_FOUND,
    )
}

fn validate(form: &MotionForm) -> Result<RoundKey, Notice> {
    if form.motion_text.trim().is_empty() {
        return Err(Notice::MissingText);
    }
    if form.round_selection.is_empty() {
        return Err(Notice::MissingRound);
    }
    RoundKey::parse(&form.round_selection).ok_or(Notice::InvalidRound)
}

fn available_rounds(total: i32) -> [(&'static str, Vec<(String, String)>); 3] {
    let preliminary = (1..=total)
        .map(|number| (format!("inround_{number}"), format!("Round {number}")))
        .collect();
    let final_rounds = |division: i32| {
        [64, 32, 16, 8, 4, 2]
            .into_iter()
            .map(|size| {
                let key = RoundKey {
                    preliminary: None,
                    division: Some(division),
                    size: Some(size),
                };
                (key.selection(), key.display())
            })
            .collect()
    };
    [
        ("Preliminary Rounds", preliminary),
        ("Varsity Outrounds", final_rounds(0)),
        ("Novice Outrounds", final_rounds(1)),
    ]
}

fn elimination_name(size: i32) -> String {
    match size {
        2 => "Finals",
        4 => "Semifinals",
        8 => "Quarterfinals",
        16 => "Octofinals",
        32 => "Double Octofinals",
        64 => "Triple Octofinals",
        _ => return format!("Round of {size}"),
    }
    .into()
}

fn truncate(value: &str, limit: usize) -> String {
    if let Some((cutoff, _)) = value.char_indices().nth(limit) {
        let mut preview: String = value[..cutoff].chars().take(limit - 1).collect();
        preview.push('…');
        preview
    } else {
        value.into()
    }
}

#[derive(Clone, Copy)]
enum Notice {
    Added,
    Updated,
    Deleted,
    Published,
    Unpublished,
    AllPublished,
    AllUnpublished,
    MissingText,
    MissingRound,
    InvalidRound,
    Disabled,
}

impl Notice {
    fn values(self) -> (&'static str, &'static str, bool) {
        match self {
            Self::Added => ("added", "Motion added successfully.", false),
            Self::Updated => ("updated", "Motion updated successfully.", false),
            Self::Deleted => ("deleted", "Motion deleted successfully.", false),
            Self::Published => ("published", "Motion published successfully.", false),
            Self::Unpublished => ("unpublished", "Motion unpublished successfully.", false),
            Self::AllPublished => ("all-published", "All motions published.", false),
            Self::AllUnpublished => ("all-unpublished", "All motions unpublished.", false),
            Self::MissingText => ("missing-text", "Motion text is required.", true),
            Self::MissingRound => ("missing-round", "Round selection is required.", true),
            Self::InvalidRound => ("invalid-round", "Invalid round selection.", true),
            Self::Disabled => (
                "disabled",
                "Motions feature is disabled. Enable it in Settings to use this feature.",
                true,
            ),
        }
    }

    fn from_code(code: &str) -> Option<Self> {
        [
            Self::Added,
            Self::Updated,
            Self::Deleted,
            Self::Published,
            Self::Unpublished,
            Self::AllPublished,
            Self::AllUnpublished,
            Self::MissingText,
            Self::MissingRound,
            Self::InvalidRound,
            Self::Disabled,
        ]
        .into_iter()
        .find(|notice| notice.values().0 == code)
    }

    fn message(self) -> &'static str {
        self.values().1
    }

    fn error(self) -> bool {
        self.values().2
    }
}

fn flash(headers: &HeaderMap, target: &str, notice: Notice) -> Response {
    let mut response = access::found(target);
    login::set_cookie(
        &mut response,
        FLASH,
        notice.values().0,
        60,
        login::secure_request(headers),
        true,
    );
    response
}
