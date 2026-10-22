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


def _generation_key(view_name):
    return f"pv:{view_name}:generation"


def _get_generation(cache, view_name):
    return cache.get(_generation_key(view_name), 0)


def _advance_generation(cache, view_name):
    generation_key = _generation_key(view_name)
    if cache.add(generation_key, 1, timeout=None):
        return
    try:
        cache.incr(generation_key)
    except ValueError:
        # The key can disappear between add() and incr() when another process
        # clears the cache. Recreate it so invalidation still takes effect.
        cache.set(generation_key, 1, timeout=None)


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
            # Models import this module; defer the role import to avoid a cycle.
            from mittab.apps.tab.auth_roles import is_apda_board_user  # pylint: disable=import-outside-toplevel
            if is_apda_board_user(request.user):
                response = view_func(request, *args, **kwargs)
                response["Cache-Control"] = "private, no-store"
                return response
            cache = caches[PUBLIC_CACHE_ALIAS]
            cache_kwargs = {
                **kwargs,
                "_cache_generation": _get_generation(cache, view_func.__name__),
            }
            cache_key = build_cache_key(view_func.__name__, cache_kwargs,
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
    generation = _get_generation(cache, view_name)
    for is_authenticated in AUTH_STATES:
        cache_kwargs = {
            **(kwargs or {}),
            "_cache_generation": generation,
        }
        cache.delete(build_cache_key(view_name, cache_kwargs, is_authenticated))


def _delete_all_keys_for_view(view_name):
    """Invalidate every cached kwargs variant of a public view."""
    _advance_generation(caches[PUBLIC_CACHE_ALIAS], view_name)


def invalidate_inround_public_pairings_cache(*_args, **_kwargs):
    """
    Invalidate cached in-round public pages.

    Clears origin cache immediately. CDN will refresh within 10 seconds
    based on the short TTL configured in cache_public_view.
    """
    _delete_all_keys_for_view("pretty_pair")
    _delete_key_for_all_auth_states("missing_ballots")
    _delete_key_for_all_auth_states("public_home")


def invalidate_outround_public_pairings_cache(type_of_round, *_args, **_kwargs):
    """
    Invalidate cached outround public pages for the provided division.

    Clears origin cache immediately. CDN will refresh within 10 seconds.
    """
    kwargs = {"type_of_round": type_of_round}
    _delete_key_for_all_auth_states("outround_pretty_pair", kwargs)
    _delete_key_for_all_auth_states("public_home")


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
    _delete_key_for_all_auth_states("public_speaker_rankings")
    _delete_key_for_all_auth_states("public_ballots")
    _delete_key_for_all_auth_states("public_home")


def invalidate_public_motions_cache(*_args, **_kwargs):
    """
    Invalidate cached public motions view.

    Clears origin cache immediately. CDN will refresh within 10 seconds.
    """
    _delete_key_for_all_auth_states("public_motions")


def invalidate_all_public_caches(*_args, **_kwargs):
    """
    Invalidate all public view caches.

    Use this when settings change that could affect multiple public views.
    Clears origin cache immediately. CDN will refresh within 10 seconds.
    """
    # Invalidate all view-specific caches
    _delete_all_keys_for_view("pretty_pair")
    _delete_key_for_all_auth_states("missing_ballots")
    _delete_key_for_all_auth_states("public_home")
    _delete_key_for_all_auth_states("public_view_judges")
    _delete_key_for_all_auth_states("public_view_teams")
    _delete_key_for_all_auth_states("rank_teams_public")
    _delete_key_for_all_auth_states("public_speaker_rankings")
    _delete_key_for_all_auth_states("public_ballots")
    _delete_key_for_all_auth_states("e_ballot_search_page")
    _delete_key_for_all_auth_states("public_motions")

    # Invalidate outrounds for both divisions
    for type_of_round in [0, 1]:  # VARSITY=0, NOVICE=1
        kwargs = {"type_of_round": type_of_round}
        _delete_key_for_all_auth_states("outround_pretty_pair", kwargs)
