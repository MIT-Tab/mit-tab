import hashlib
import json
import logging
import time
from functools import wraps

from django.conf import settings
from django.core.cache import caches

from mittab.libs.cdn import purge_cdn_paths

logger = logging.getLogger(__name__)

PUBLIC_CACHE_ALIAS = getattr(settings, "PUBLIC_VIEW_CACHE_ALIAS", "public")
AUTH_STATES = (False, True)


def build_cache_key(view_name, kwargs, is_authenticated):
    normalized_kwargs = tuple(sorted((kwargs or {}).items()))
    payload = {
        "view": view_name,
        "kwargs": normalized_kwargs,
        "auth": bool(is_authenticated),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"pv:{view_name}:{digest}"


def cache_public_view(timeout=60):
    """Cache a public view and serve stale content while one request refreshes it."""

    stale_extension = max(timeout, 30)
    lock_timeout = max(30, timeout // 2 or 1)
    
    # If CDN credentials aren't configured, use much shorter cache times
    # to ensure content refreshes quickly even without purging
    from mittab.libs.cdn import _CDN_ENDPOINT_ID, _CDN_API_TOKEN
    if not (_CDN_ENDPOINT_ID and _CDN_API_TOKEN):
        # Without CDN purge capability, use 30s max-age for permission-sensitive pages
        # This ensures CDN refreshes within 30 seconds of changes
        timeout = min(timeout, 30)
        stale_extension = 15
        logger.warning(
            "CDN credentials not configured - using short cache timeout (%ds) "
            "for cache invalidation. Set DIGITALOCEAN_CDN_ENDPOINT_ID and "
            "DIGITALOCEAN_API_TOKEN for instant cache purging.",
            timeout
        )
    
    cache_control_header = (
        f"public, max-age={timeout}, stale-while-revalidate={stale_extension}"
    )

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            cache = caches[PUBLIC_CACHE_ALIAS]
            cache_key = build_cache_key(view_func.__name__, kwargs,
                                         request.user.is_authenticated)
            cached_entry = cache.get(cache_key)
            now = time.time()

            if isinstance(cached_entry, dict):
                response = cached_entry.get("response")
                expires_at = cached_entry.get("expires_at", 0)
                if response is not None:
                    if hasattr(response, "__setitem__"
                               ) and "Cache-Control" not in response:
                        response["Cache-Control"] = cache_control_header
                    if expires_at > now:
                        return response

                    lock_key = f"{cache_key}:lock"
                    if cache.add(lock_key, True, lock_timeout):
                        try:
                            fresh_response = view_func(request, *args, **kwargs)
                            cache.set(
                                cache_key,
                                {"response": fresh_response,
                                 "expires_at": now + timeout},
                                timeout + stale_extension,
                            )
                            return fresh_response
                        finally:
                            cache.delete(lock_key)
                    return response

            fresh_response = view_func(request, *args, **kwargs)
            if hasattr(fresh_response, "__setitem__"):
                fresh_response["Cache-Control"] = cache_control_header
            cache.set(
                cache_key,
                {"response": fresh_response, "expires_at": now + timeout},
                timeout + stale_extension,
            )
            return fresh_response

        return wrapper

    return decorator


def _delete_key_for_all_auth_states(view_name, kwargs=None):
    cache = caches[PUBLIC_CACHE_ALIAS]
    for is_authenticated in AUTH_STATES:
        cache.delete(build_cache_key(view_name, kwargs or {}, is_authenticated))


def invalidate_inround_public_pairings_cache(*_args, **_kwargs):
    """Invalidate cached in-round public pages."""

    logger.info("[CACHE INVALIDATION] Starting in-round public pairings cache invalidation")
    
    cache_keys = ["pretty_pair", "missing_ballots", "public_home"]
    for view_name in cache_keys:
        _delete_key_for_all_auth_states(view_name)
        logger.info(f"[CACHE INVALIDATION] Deleted cache keys for view: {view_name}")

    cdn_paths = [
        "/public/",
        "/public/pairings/",
        "/public/missing-ballots/",
        "/public/judges/",
        "/public/teams/",
        "/public/team-rankings/",
    ]
    
    # Check if CDN credentials are available
    from mittab.libs.cdn import _CDN_ENDPOINT_ID, _CDN_API_TOKEN
    has_cdn_creds = bool(_CDN_ENDPOINT_ID and _CDN_API_TOKEN)
    
    if has_cdn_creds:
        logger.info(f"[CACHE INVALIDATION] Starting CDN purge (blocking=True) for {len(cdn_paths)} paths")
        cdn_start = time.time()
        
        # Use blocking=True for permission changes to ensure CDN purges immediately
        cdn_result = purge_cdn_paths(cdn_paths, blocking=True)
        
        cdn_duration = (time.time() - cdn_start) * 1000
        logger.info(f"[CACHE INVALIDATION] CDN purge completed in {cdn_duration:.0f}ms")
    else:
        logger.warning(
            "[CACHE INVALIDATION] CDN credentials not configured - skipping CDN purge. "
            "Content will refresh within 30s based on cache timeout. "
            "For instant updates, set DIGITALOCEAN_CDN_ENDPOINT_ID and DIGITALOCEAN_API_TOKEN."
        )
        cdn_result = {"status": "skipped", "reason": "no_credentials"}
        cdn_duration = 0
    
    return {
        "cache_keys_deleted": cache_keys,
        "cdn_paths_purged": cdn_paths if has_cdn_creds else [],
        "cdn_purge_ms": round(cdn_duration, 2),
        "cdn_result": cdn_result,
    }


def invalidate_outround_public_pairings_cache(type_of_round, *_args, **_kwargs):
    """Invalidate cached outround public pages for the provided division."""

    kwargs = {"type_of_round": type_of_round}
    _delete_key_for_all_auth_states("outround_pretty_pair", kwargs)

    # Use blocking=True for permission changes to ensure CDN purges immediately
    purge_cdn_paths([
        f"/public/outrounds/{type_of_round}/",
    ], blocking=True)


def invalidate_public_judges_cache(*_args, **_kwargs):
    """Invalidate cached public judges view."""

    _delete_key_for_all_auth_states("public_view_judges")

    # Use blocking=True for permission changes to ensure CDN purges immediately
    purge_cdn_paths(["/public/judges/"], blocking=True)


def invalidate_public_teams_cache(*_args, **_kwargs):
    """Invalidate cached public teams view."""

    _delete_key_for_all_auth_states("public_view_teams")

    # Use blocking=True for permission changes to ensure CDN purges immediately
    purge_cdn_paths(["/public/teams/"], blocking=True)


def invalidate_public_rankings_cache(*_args, **_kwargs):
    """Invalidate cached public rankings view."""

    _delete_key_for_all_auth_states("rank_teams_public")

    # Use blocking=True for permission changes to ensure CDN purges immediately
    purge_cdn_paths(["/public/team-rankings/"], blocking=True)


def invalidate_all_public_caches(*_args, **_kwargs):
    """
    Invalidate all public view caches.
    
    Use this when settings change that could affect multiple public views,
    ensuring the CDN immediately stops serving stale content.
    """

    # Invalidate all view-specific caches
    _delete_key_for_all_auth_states("pretty_pair")
    _delete_key_for_all_auth_states("missing_ballots")
    _delete_key_for_all_auth_states("public_home")
    _delete_key_for_all_auth_states("public_view_judges")
    _delete_key_for_all_auth_states("public_view_teams")
    _delete_key_for_all_auth_states("rank_teams_public")
    _delete_key_for_all_auth_states("e_ballot_search_page")
    
    # Invalidate outrounds for both divisions
    for type_of_round in [0, 1]:  # VARSITY=0, NOVICE=1
        kwargs = {"type_of_round": type_of_round}
        _delete_key_for_all_auth_states("outround_pretty_pair", kwargs)

    # Use blocking=True for permission changes to ensure CDN purges immediately
    # This prevents users from seeing stale content after settings changes
    purge_cdn_paths([
        "/public/",
        "/public/pairings/",
        "/public/missing-ballots/",
        "/public/judges/",
        "/public/teams/",
        "/public/team-rankings/",
        "/public/outrounds/0/",
        "/public/outrounds/1/",
        "/public/e-ballot/",
    ], blocking=True)
