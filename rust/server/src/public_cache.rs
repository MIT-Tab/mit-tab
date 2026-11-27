use std::{
    collections::HashMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

use axum::{
    body::{Body, Bytes, HttpBody, to_bytes},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
};
use sqlx::{MySql, MySqlConnection, QueryBuilder};

use crate::{App, legacy_session::Actor, publication, settings::Settings, unavailable};

#[derive(Clone, Copy, Hash, PartialEq, Eq)]
pub enum Page {
    Home,
    AccessError,
    Teams,
    Judges,
    Pairings(Option<i32>),
    MissingBallots,
    TeamRankings,
    SpeakerRankings,
    Ballots,
    Outrounds(i32),
    Motions,
    TabCards,
    LiveResults,
    StaffTeamRankings,
    StaffSpeakerRankings,
}

type Dependency = (&'static str, &'static str);

const SETTINGS: Dependency = ("tab_tabsettings", "id, `key`, value, value_string");
const DISPLAY: Dependency = (
    "tab_publicdisplaysetting",
    "id, slug, display_type, is_enabled, include_speaks, include_ranks, max_visible, round_number",
);
const TEAMS: Dependency = (
    "tab_team",
    "id, name, seed, checked_in, school_id, hybrid_school_id, tiebreaker, ranking_public",
);
const MEMBERS: Dependency = ("tab_team_debaters", "id, team_id, debater_id");
const DEBATERS: Dependency = ("tab_debater", "id, name, novice_status, tiebreaker");
const ROUNDS: Dependency = (
    "tab_round",
    "id, round_number, victor, pullup, gov_team_id, opp_team_id, room_id, chair_id",
);
const OUTROUNDS: Dependency = (
    "tab_outround",
    "id, num_teams, type_of_round, victor, chair_id, gov_team_id, opp_team_id, room_id, sidelock, choice",
);
const BYES: Dependency = ("tab_bye", "id, bye_team_id, round_number");
const NO_SHOWS: Dependency = ("tab_noshow", "id, no_show_team_id, round_number");
const SCORES: Dependency = (
    "tab_roundstats",
    "id, debater_id, round_id, speaks, ranks, debater_role",
);
// Ballot scores are the largest input to the rankings cache. Fixed-width
// SQL types and a hex-encoded role make the separators unambiguous.
const SCORE_VERSION: &str = "(SELECT IF(COUNT(*) > 16384, NULL,
    UNHEX(SHA2(COALESCE(JSON_ARRAYAGG(score_row), ''), 256)))
    FROM (
        SELECT CONCAT_WS(':', id, debater_id, round_id, speaks, ranks,
                         COALESCE(HEX(debater_role), '~')) AS score_row
        FROM tab_roundstats ORDER BY id LIMIT 16385
    ) AS ballot_scores)";
const SCHOOLS: Dependency = ("tab_school", "id, name");
const JUDGES: Dependency = ("tab_judge", "id, name");
const ROOMS: Dependency = ("tab_room", "id, name");
const ROSTER_LOG: Dependency = (
    "tab_publicrosterchange",
    "id, list_kind, message, involves_debaters, created_at",
);
const RANKING_GROUPS: Dependency = ("tab_rankinggroup", "id, name");
const ROUND_JUDGES: Dependency = ("tab_round_judges", "id, round_id, judge_id");

impl Page {
    fn dependencies(self) -> &'static [Dependency] {
        match self {
            Self::Home => &[
                SETTINGS,
                DISPLAY,
                ROUNDS,
                OUTROUNDS,
                (
                    "registration_infolink",
                    "id, title, url, description, is_active, display_order",
                ),
                (
                    "registration_infotext",
                    "id, title, text, is_active, display_order",
                ),
                ("tab_tournamentstaffrole", "id, title, names, sort_order"),
                ("mittab_public_logo", "id, updated_at"),
            ],
            Self::AccessError => &[SETTINGS, DISPLAY, ROUNDS, OUTROUNDS],
            Self::Teams => &[
                SETTINGS, DISPLAY, TEAMS, MEMBERS, DEBATERS, SCHOOLS, ROSTER_LOG,
            ],
            Self::Judges => &[
                SETTINGS,
                DISPLAY,
                JUDGES,
                SCHOOLS,
                ROSTER_LOG,
                ("tab_judge_schools", "id, judge_id, school_id"),
                ("tab_judgeexpectedcheckin", "id, judge_id, round_number"),
            ],
            Self::Pairings(_) => &[
                SETTINGS,
                DISPLAY,
                TEAMS,
                MEMBERS,
                DEBATERS,
                ROUNDS,
                OUTROUNDS,
                BYES,
                JUDGES,
                ROOMS,
                ROUND_JUDGES,
            ],
            Self::MissingBallots => &[SETTINGS, DISPLAY, TEAMS, ROUNDS, OUTROUNDS, JUDGES, ROOMS],
            Self::TeamRankings | Self::SpeakerRankings => &[
                SETTINGS, DISPLAY, TEAMS, MEMBERS, DEBATERS, ROUNDS, OUTROUNDS, BYES, NO_SHOWS,
                SCORES,
            ],
            Self::Ballots => &[
                SETTINGS, DISPLAY, TEAMS, MEMBERS, DEBATERS, ROUNDS, OUTROUNDS, SCORES,
            ],
            Self::Outrounds(_) => &[
                SETTINGS,
                DISPLAY,
                TEAMS,
                MEMBERS,
                DEBATERS,
                ROUNDS,
                OUTROUNDS,
                ROOMS,
                JUDGES,
                ("tab_outround_judges", "id, outround_id, judge_id"),
                ("tab_breakingteam", "id, team_id, seed, type_of_team"),
            ],
            Self::Motions => &[
                SETTINGS,
                DISPLAY,
                ROUNDS,
                OUTROUNDS,
                (
                    "tab_motion",
                    "id, round_number, outround_type, num_teams, info_slide, motion_text, is_published",
                ),
            ],
            Self::TabCards => &[
                SETTINGS,
                TEAMS,
                MEMBERS,
                DEBATERS,
                ROUNDS,
                OUTROUNDS,
                BYES,
                NO_SHOWS,
                SCORES,
                ROOMS,
                JUDGES,
                ROUND_JUDGES,
            ],
            Self::LiveResults => &[
                SETTINGS,
                MEMBERS,
                BYES,
                NO_SHOWS,
                SCORES,
                OUTROUNDS,
                (
                    "tab_team",
                    "id, name, hybrid_school_id, school_id, break_preference",
                ),
                (
                    "tab_debater",
                    "id, name, novice_status, tiebreaker, apda_id, school_id",
                ),
                ("tab_school", "id, apda_id, name"),
                (
                    "tab_round",
                    "id, round_number, victor, gov_team_id, opp_team_id, result_entered_at",
                ),
                ("tab_breakingteam", "id, team_id, type_of_team"),
                ("tab_publishedteamplacement", "id, stage_id, team_id, place"),
                ("tab_publishedoutroundstage", "id, type_of_round, num_teams"),
            ],
            Self::StaffTeamRankings => &[
                SETTINGS,
                TEAMS,
                MEMBERS,
                DEBATERS,
                ROUNDS,
                OUTROUNDS,
                BYES,
                NO_SHOWS,
                SCORES,
                RANKING_GROUPS,
                ("tab_rankinggroup_teams", "id, rankinggroup_id, team_id"),
            ],
            Self::StaffSpeakerRankings => &[
                SETTINGS,
                TEAMS,
                MEMBERS,
                DEBATERS,
                ROUNDS,
                BYES,
                NO_SHOWS,
                SCORES,
                RANKING_GROUPS,
                (
                    "tab_rankinggroup_debaters",
                    "id, rankinggroup_id, debater_id",
                ),
            ],
        }
    }

    fn has_role_navigation(self) -> bool {
        matches!(
            self,
            Self::Home
                | Self::AccessError
                | Self::Motions
                | Self::Teams
                | Self::Judges
                | Self::TeamRankings
                | Self::SpeakerRankings
                | Self::Ballots
                | Self::Pairings(_)
                | Self::MissingBallots
                | Self::Outrounds(_)
        )
    }

    fn is_public_html(self) -> bool {
        !matches!(
            self,
            Self::LiveResults | Self::StaffTeamRankings | Self::StaffSpeakerRankings
        )
    }

    fn ttl(self) -> Duration {
        Duration::from_secs(match self {
            Self::AccessError => 300,
            Self::MissingBallots => 30,
            Self::StaffTeamRankings | Self::StaffSpeakerRankings => 300,
            Self::LiveResults => {
                use ring::rand::SecureRandom;
                let mut jitter = [0u8];
                let _ = ring::rand::SystemRandom::new().fill(&mut jitter);
                60 + u64::from(jitter[0] % 31)
            }
            _ => 60,
        })
    }

    async fn version(self, db: &mut MySqlConnection) -> Result<Vec<u8>, sqlx::Error> {
        // Each input contributes a fixed-width digest. Including primary keys
        // keeps distinct rows distinct even when all their display values match.
        let mut query = QueryBuilder::<MySql>::new("SELECT CONCAT(");
        let mut inputs = query.separated(", ");
        for &(table, columns) in self.dependencies() {
            if table == SCORES.0 {
                inputs.push(SCORE_VERSION);
            } else {
                inputs.push(format!(
                    "(SELECT BIT_XOR(UNHEX(SHA2(JSON_ARRAY({columns}), 256))) FROM {table})"
                ));
            }
        }
        if matches!(self, Self::Home) {
            inputs.push(
                "COALESCE((SELECT allow_new_registrations AND
                    (new_registrations_open_at IS NULL OR new_registrations_open_at <= UTC_TIMESTAMP(6)) AND
                    (new_registrations_close_at IS NULL OR new_registrations_close_at > UTC_TIMESTAMP(6))
                  FROM registration_registrationconfig WHERE id = 1), 1)",
            );
        }
        if matches!(self, Self::Teams | Self::Judges) {
            // The roster navigation depends on whether pairings exist and on the
            // largest outround in each division, independent of pairing details.
            inputs.push("EXISTS(SELECT 1 FROM tab_round)");
            inputs.push(
                "(SELECT JSON_ARRAY(MAX(CASE WHEN type_of_round = 0 THEN num_teams END),
                                             MAX(CASE WHEN type_of_round = 1 THEN num_teams END))
                          FROM tab_outround)",
            );
        }
        if matches!(self, Self::LiveResults) {
            inputs.push("UTC_DATE()");
        }
        query.push(")");
        let version: Option<Vec<u8>> = query.build_query_scalar().fetch_one(&mut *db).await?;
        let mut version = version.ok_or_else(|| {
            sqlx::Error::Protocol("Public cache inputs exceeded the aggregate limit".into())
        })?;
        if matches!(self, Self::LiveResults) {
            let settings = Settings::load(db, &["tot_rounds"]).await?;
            let completed_at = publication::completion_time(db, &settings).await?;
            version.push(u8::from(completed_at.is_some_and(|completed| {
                chrono::Utc::now().signed_duration_since(completed) >= chrono::TimeDelta::hours(3)
            })));
        }
        Ok(version)
    }
}

#[derive(Clone, Copy, Hash, PartialEq, Eq)]
enum Audience {
    Anonymous,
    SignedIn,
    Tab,
}

impl Audience {
    fn for_actor(actor: Actor) -> Option<Self> {
        match actor {
            Actor::Anonymous => Some(Self::Anonymous),
            Actor::User { board: true, .. } => None,
            Actor::User {
                superuser: true, ..
            } => Some(Self::Tab),
            Actor::User { .. } => Some(Self::SignedIn),
        }
    }
}

struct Entry {
    version: Vec<u8>,
    fresh_until: Instant,
    stale_until: Instant,
    refreshing: Arc<AtomicBool>,
    status: StatusCode,
    headers: HeaderMap,
    body: Bytes,
}

#[derive(Clone, Hash, PartialEq, Eq)]
struct Key {
    page: Page,
    audience: Audience,
    source: Option<String>,
}

struct Refresh(Arc<AtomicBool>);

impl Drop for Refresh {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Relaxed);
    }
}

enum Lookup {
    Hit(Response),
    Refresh(Refresh),
    Miss,
}

#[derive(Default)]
pub struct PublicCache(Mutex<HashMap<Key, Entry>>);

impl PublicCache {
    fn has_entry(&self, key: &Key) -> bool {
        self.0.lock().is_ok_and(|cache| cache.contains_key(key))
    }

    fn get(&self, key: &Key, version: &[u8]) -> Lookup {
        let Ok(cache) = self.0.lock() else {
            return Lookup::Miss;
        };
        let Some(entry) = cache.get(key) else {
            return Lookup::Miss;
        };
        let now = Instant::now();
        if entry.version != version || entry.stale_until <= now {
            return Lookup::Miss;
        }
        if entry.fresh_until <= now && !entry.refreshing.swap(true, Ordering::Relaxed) {
            return Lookup::Refresh(Refresh(entry.refreshing.clone()));
        }
        let mut response = Response::new(Body::from(entry.body.clone()));
        *response.status_mut() = entry.status;
        *response.headers_mut() = entry.headers.clone();
        Lookup::Hit(response)
    }

    fn insert(&self, key: Key, entry: Entry) {
        const MAX_ENTRIES: usize = 128;
        let Ok(mut cache) = self.0.lock() else {
            return;
        };
        if cache.len() >= MAX_ENTRIES
            && !cache.contains_key(&key)
            && let Some(expiring) = cache
                .iter()
                .min_by_key(|(_, value)| value.stale_until)
                .map(|(key, _)| key.clone())
        {
            cache.remove(&expiring);
        }
        cache.insert(key, entry);
    }
}

pub async fn render(
    app: &App,
    actor: Actor,
    page: Page,
    load: impl for<'db> AsyncFnOnce(&'db mut MySqlConnection) -> Result<Response, sqlx::Error>,
) -> Response {
    render_response(app, actor, page, None, load)
        .await
        .unwrap_or_else(|_| unavailable())
}

pub async fn home(
    app: &App,
    actor: Actor,
    legacy_logo_href: Option<String>,
    load: impl for<'db> AsyncFnOnce(&'db mut MySqlConnection) -> Result<Response, sqlx::Error>,
) -> Response {
    render_response(app, actor, Page::Home, legacy_logo_href, load)
        .await
        .unwrap_or_else(|_| unavailable())
}

pub async fn live_results(
    app: &App,
    source: &str,
    load: impl for<'db> AsyncFnOnce(&'db mut MySqlConnection) -> Result<Response, sqlx::Error>,
) -> Response {
    render_response(
        app,
        Actor::Anonymous,
        Page::LiveResults,
        Some(source.into()),
        load,
    )
    .await
    .unwrap_or_else(|_| unavailable())
}

async fn render_response(
    app: &App,
    actor: Actor,
    page: Page,
    source: Option<String>,
    load: impl for<'db> AsyncFnOnce(&'db mut MySqlConnection) -> Result<Response, sqlx::Error>,
) -> Result<Response, sqlx::Error> {
    // Board always renders through its own authorization and projection.
    let cache_key = app
        .cache_public_pages
        .then(|| Audience::for_actor(actor))
        .flatten()
        .filter(|audience| !page.has_role_navigation() || *audience != Audience::SignedIn)
        .map(|audience| Key {
            page,
            audience,
            source,
        });

    // A warm page needs one consistent SQL read. Only rendering a cache miss
    // needs a transaction spanning the revision query and the page queries.
    let mut refresh = None;
    if page != Page::LiveResults
        && let Some(key) = &cache_key
        && app.public_cache.has_entry(key)
    {
        let cached = async {
            let mut db = app.db.acquire().await?;
            let version = page.version(&mut db).await?;
            Ok::<_, sqlx::Error>(app.public_cache.get(key, &version))
        }
        .await
        .unwrap_or(Lookup::Miss);
        match cached {
            Lookup::Hit(response) => return Ok(response),
            Lookup::Refresh(guard) => refresh = Some(guard),
            Lookup::Miss => {}
        }
    }

    let mut db = app.db.begin().await?;
    if page == Page::LiveResults {
        crate::staff_pairing::lock_round_if_configured(&mut db).await?;
    }
    let version = if cache_key.is_some() {
        page.version(&mut db).await.ok()
    } else {
        None
    };
    if refresh.is_none()
        && let (Some(key), Some(version)) = (&cache_key, &version)
    {
        match app.public_cache.get(key, version) {
            Lookup::Hit(response) => {
                db.commit().await?;
                return Ok(response);
            }
            Lookup::Refresh(guard) => refresh = Some(guard),
            Lookup::Miss => {}
        }
    }

    let _refresh = refresh;
    let response = load(&mut db).await?;
    db.commit().await?;
    let (Some(key), Some(version)) = (cache_key, version) else {
        return Ok(response);
    };
    let public_redirect = response.status() == StatusCode::FOUND
        && response
            .headers()
            .get(header::LOCATION)
            .is_some_and(|location| location == "/public/access-error/");
    if !(response.status() == StatusCode::OK || public_redirect)
        || response.headers().contains_key(header::SET_COOKIE)
    {
        return Ok(response);
    }
    let (mut parts, body) = response.into_parts();
    const MAX_BODY: usize = 1024 * 1024;
    // These handlers return fixed bodies. Streamed or large responses stay live.
    if body
        .size_hint()
        .upper()
        .is_none_or(|size| size > MAX_BODY as u64)
    {
        return Ok(Response::from_parts(parts, body));
    }
    let body = to_bytes(body, MAX_BODY)
        .await
        .map_err(|_| sqlx::Error::Protocol("Could not buffer the public page".into()))?;
    parts
        .headers
        .append(header::VARY, HeaderValue::from_static("Cookie"));
    // Shared caching requires the proxy to bypass requests with session cookies.
    // Signed-in audiences always keep the private response headers.
    if app.cache_shared_public_pages && key.audience == Audience::Anonymous && page.is_public_html()
    {
        parts.headers.insert(
            header::CACHE_CONTROL,
            HeaderValue::from_static("public, max-age=10, s-maxage=10, stale-while-revalidate=10"),
        );
    }
    let fresh_until = Instant::now() + page.ttl();
    let stale_until = fresh_until
        + if page.is_public_html() {
            page.ttl().max(Duration::from_secs(30))
        } else {
            Duration::ZERO
        };
    app.public_cache.insert(
        key,
        Entry {
            version,
            fresh_until,
            stale_until,
            refreshing: Arc::default(),
            status: parts.status,
            headers: parts.headers.clone(),
            body: body.clone(),
        },
    );
    Ok(Response::from_parts(parts, Body::from(body)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stale_entries_require_the_same_version_and_allow_only_one_refresh() {
        let cache = PublicCache::default();
        let key = Key {
            page: Page::TeamRankings,
            audience: Audience::Anonymous,
            source: None,
        };
        let now = Instant::now();
        cache.insert(
            key.clone(),
            Entry {
                version: vec![1],
                fresh_until: now,
                stale_until: now + Duration::from_secs(60),
                refreshing: Arc::default(),
                status: StatusCode::OK,
                headers: HeaderMap::new(),
                body: Bytes::from_static(b"published"),
            },
        );
        assert!(matches!(cache.get(&key, &[2]), Lookup::Miss));
        let Lookup::Refresh(refresh) = cache.get(&key, &[1]) else {
            panic!("the first expired request should refresh the entry");
        };
        assert!(matches!(cache.get(&key, &[1]), Lookup::Hit(_)));
        assert!(matches!(
            cache.get(
                &Key {
                    audience: Audience::Tab,
                    ..key.clone()
                },
                &[1]
            ),
            Lookup::Miss,
        ));
        drop(refresh);
        assert!(matches!(cache.get(&key, &[1]), Lookup::Refresh(_)));
    }
}
