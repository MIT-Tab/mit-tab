import hashlib
import json
import time
from functools import wraps

from django.conf import settings
from django.core.cache import caches

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
    """
    Cache a public view with short CDN TTL and longer origin cache.
    
    The CDN is configured with a 10-second TTL, so changes propagate quickly.
    The origin cache uses the specified timeout for better performance.
    When invalidated, both origin cache and response are cleared immediately.
    """

    stale_extension = max(timeout, 30)
    lock_timeout = max(30, timeout // 2 or 1)
    
    # Set CDN cache to 10 seconds for quick propagation of permission changes
    # Origin cache uses the full timeout for performance
    cdn_max_age = 10
    cache_control_header = (
        f"public, max-age={cdn_max_age}, s-maxage={cdn_max_age}, "
        f"stale-while-revalidate={cdn_max_age}"
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
    """
    Invalidate cached in-round public pages.
    
    Clears origin cache immediately. CDN will refresh within 10 seconds
    based on the short TTL configured in cache_public_view.
    """
    _delete_key_for_all_auth_states("pretty_pair")
    _delete_key_for_all_auth_states("missing_ballots")
    _delete_key_for_all_auth_states("public_home")


def invalidate_outround_public_pairings_cache(type_of_round, *_args, **_kwargs):
    """
    Invalidate cached outround public pages for the provided division.
    
    Clears origin cache immediately. CDN will refresh within 10 seconds.
    """
    kwargs = {"type_of_round": type_of_round}
    _delete_key_for_all_auth_states("outround_pretty_pair", kwargs)


def invalidate_public_judges_cache(*_args, **_kwargs):
    """
    Invalidate cached public judges view.
    
    Clears origin cache immediately. CDN will refresh within 10 seconds.
    """
    _delete_key_for_all_auth_states("public_view_judges")


def invalidate_public_teams_cache(*_args, **_kwargs):
    """
    Invalidate cached public teams view.
    
    Clears origin cache immediately. CDN will refresh within 10 seconds.
    """
    _delete_key_for_all_auth_states("public_view_teams")


def invalidate_public_rankings_cache(*_args, **_kwargs):
    """
    Invalidate cached public rankings view.
    
    Clears origin cache immediately. CDN will refresh within 10 seconds.
    """
    _delete_key_for_all_auth_states("rank_teams_public")


def invalidate_all_public_caches(*_args, **_kwargs):
    """
    Invalidate all public view caches.
    
    Use this when settings change that could affect multiple public views.
    Clears origin cache immediately. CDN will refresh within 10 seconds.
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
