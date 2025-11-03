import hashlib
import json
import time
from functools import wraps

from django.conf import settings
from django.core.cache import caches

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


def _request_has_messages(request):
    storage = getattr(request, "_messages", None)
    if storage is not None:
        queued = getattr(storage, "_queued_messages", None)
        if queued:
            return True
        loaded = getattr(storage, "_loaded_data", None)
        if loaded:
            return True
    session = getattr(request, "session", None)
    if session is not None:
        if session.get("_messages"):
            return True
    return False


def _should_skip_cache(request):
    if request.method and request.method.upper() != "GET":
        return True

    if _request_has_messages(request):
        return True

    if request.META.get("CSRF_COOKIE_USED"):
        return True

    return False


def cache_public_view(timeout=60):
    """Cache a public view and serve stale content while one request refreshes it."""

    stale_extension = max(timeout, 30)
    lock_timeout = max(30, timeout // 2 or 1)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            cache = caches[PUBLIC_CACHE_ALIAS]
            cache_key = _build_cache_key(view_func.__name__, kwargs, request.user.is_authenticated)
            skip_cache = _should_skip_cache(request)

            if not skip_cache:
                cached_entry = cache.get(cache_key)
                now = time.time()

                if isinstance(cached_entry, dict):
                    response = cached_entry.get("response")
                    expires_at = cached_entry.get("expires_at", 0)
                    if response is not None:
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
            if not skip_cache:
                now = time.time()
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


def invalidate_outround_public_pairings_cache(type_of_round, *_args, **_kwargs):
    """Invalidate cached outround public pages for the provided division."""

    kwargs = {"type_of_round": type_of_round}
    _delete_key_for_all_auth_states("outround_pretty_pair", kwargs)


def invalidate_public_judges_cache(*_args, **_kwargs):
    """Invalidate cached public judges view."""

    _delete_key_for_all_auth_states("public_view_judges")


def invalidate_public_teams_cache(*_args, **_kwargs):
    """Invalidate cached public teams view."""

    _delete_key_for_all_auth_states("public_view_teams")
