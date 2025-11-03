import hashlib
import json
import time
from functools import wraps

from django.conf import settings
from django.core.cache import caches

from mittab.libs.cdn import purge_cdn_paths

PUBLIC_CACHE_ALIAS = getattr(settings, "PUBLIC_VIEW_CACHE_ALIAS", "public")
AUTH_STATES = (False, True)


def _build_cache_key(view_name, kwargs, is_authenticated):
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
    cache_control_header = (
        f"public, max-age={timeout}, stale-while-revalidate={stale_extension}"
    )

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            cache = caches[PUBLIC_CACHE_ALIAS]
            cache_key = _build_cache_key(view_func.__name__, kwargs, request.user.is_authenticated)
            cached_entry = cache.get(cache_key)
            now = time.time()

            if isinstance(cached_entry, dict):
                response = cached_entry.get("response")
                expires_at = cached_entry.get("expires_at", 0)
                if response is not None:
                    if hasattr(response, "__setitem__") and "Cache-Control" not in response:
                        response["Cache-Control"] = cache_control_header
                    if expires_at > now:
                        return response

                    lock_key = f"{cache_key}:lock"
                    if cache.add(lock_key, True, lock_timeout):
                        try:
                            fresh_response = view_func(request, *args, **kwargs)
                            cache.set(
                                cache_key,
                                {"response": fresh_response, "expires_at": now + timeout},
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
        cache.delete(_build_cache_key(view_name, kwargs or {}, is_authenticated))


def invalidate_inround_public_pairings_cache(*_args, **_kwargs):
    """Invalidate cached in-round public pages."""

    _delete_key_for_all_auth_states("pretty_pair")
    _delete_key_for_all_auth_states("missing_ballots")
    _delete_key_for_all_auth_states("public_home")

    purge_cdn_paths([
        "/public/",
        "/public/pairings/",
        "/public/missing-ballots/",
        "/public/judges/",
        "/public/teams/",
        "/public/team-rankings/",
    ])


def invalidate_outround_public_pairings_cache(type_of_round, *_args, **_kwargs):
    """Invalidate cached outround public pages for the provided division."""

    kwargs = {"type_of_round": type_of_round}
    _delete_key_for_all_auth_states("outround_pretty_pair", kwargs)

    purge_cdn_paths([
        f"/public/outrounds/{type_of_round}/",
    ])


def invalidate_public_judges_cache(*_args, **_kwargs):
    """Invalidate cached public judges view."""

    _delete_key_for_all_auth_states("public_view_judges")

    purge_cdn_paths(["/public/judges/"])


def invalidate_public_teams_cache(*_args, **_kwargs):
    """Invalidate cached public teams view."""

    _delete_key_for_all_auth_states("public_view_teams")

    purge_cdn_paths(["/public/teams/"])
