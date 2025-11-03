import hashlib
import json
import time
from functools import wraps

from django.conf import settings
from django.core.cache import caches

from mittab.apps.tab.models import TabSettings, BreakingTeam

PUBLIC_CACHE_ALIAS = getattr(settings, "PUBLIC_VIEW_CACHE_ALIAS", "public")


def _build_cache_key(view_name, kwargs, settings_vals, is_authenticated):
    normalized_kwargs = tuple(sorted((kwargs or {}).items()))
    payload = {
        "view": view_name,
        "kwargs": normalized_kwargs,
        "settings": list(settings_vals),
        "auth": bool(is_authenticated),
    }
    raw = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"pv:{view_name}:{digest}"


def cache_public_view(timeout=60, settings_keys=None):
    """
    Cache a public view, invalidating when specified TabSettings change.
    Serves stale content while one request asynchronously refreshes it to
    avoid thundering herds when the TTL expires.
    """
    if settings_keys is None:
        settings_keys = []

    stale_extension = max(timeout, 30)
    lock_timeout = max(30, timeout // 2 or 1)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            settings_vals = (
                tuple(TabSettings.get(k, "") for k in settings_keys) if settings_keys else ()
            )
            cache_key = _build_cache_key(
                view_func.__name__, kwargs, settings_vals, request.user.is_authenticated
            )

            cache = caches[PUBLIC_CACHE_ALIAS]
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
            cache.set(
                cache_key,
                {"response": fresh_response, "expires_at": now + timeout},
                timeout + stale_extension,
            )
            return fresh_response

        return wrapper

    return decorator


def _delete_key_for_all_auth_states(view_name, kwargs, settings_vals):
    cache = caches[PUBLIC_CACHE_ALIAS]
    for is_authenticated in (False, True):
        cache.delete(_build_cache_key(view_name, kwargs, settings_vals, is_authenticated))


def invalidate_inround_public_pairings_cache(old_value=None, new_value=None):
    """
    Remove cached entries for in-round public pages without clearing the entire cache.
    """
    cur_round = TabSettings.get("cur_round", 0)
    debaters_public = TabSettings.get("debaters_public", 1)
    tournament_name = TabSettings.get("tournament_name", "MIT-TAB Tournament")
    results_published = TabSettings.get("results_published", False)

    states = {value for value in (old_value, new_value) if value is not None}
    if not states:
        states = {TabSettings.get("pairing_released", 0)}

    for pairing_released in states:
        _delete_key_for_all_auth_states(
            "pretty_pair",
            {},
            (cur_round, pairing_released, debaters_public),
        )
        _delete_key_for_all_auth_states(
            "missing_ballots",
            {},
            (cur_round, pairing_released),
        )
        _delete_key_for_all_auth_states(
            "public_home",
            {},
            (tournament_name, pairing_released, results_published),
        )


def invalidate_outround_public_pairings_cache(type_of_round, old_value=None, new_value=None):
    """
    Remove cached entries for outround public pages without clearing the entire cache.
    """
    gov_opp_display = TabSettings.get("gov_opp_display", 0)
    sidelock = TabSettings.get("sidelock", 0)
    choice = TabSettings.get("choice", 0)
    debaters_public = TabSettings.get("debaters_public", 1)
    show_outs_bracket = TabSettings.get("show_outs_bracket", False)
    current_var = TabSettings.get("var_teams_visible", 256)
    current_nov = TabSettings.get("nov_teams_visible", 256)

    var_candidates = {current_var}
    nov_candidates = {current_nov}

    if type_of_round == BreakingTeam.VARSITY:
        var_candidates.update(value for value in (old_value, new_value) if value is not None)
    else:
        nov_candidates.update(value for value in (old_value, new_value) if value is not None)

    for var_visible in var_candidates:
        for nov_visible in nov_candidates:
            settings_vals = (
                gov_opp_display,
                var_visible,
                nov_visible,
                sidelock,
                choice,
                debaters_public,
                show_outs_bracket,
            )
            _delete_key_for_all_auth_states(
                "outround_pretty_pair",
                {"type_of_round": type_of_round},
                settings_vals,
            )
