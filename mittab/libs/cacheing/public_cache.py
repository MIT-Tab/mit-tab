import hashlib
import json
from functools import wraps

from django.conf import settings
from django.core.cache import caches

from mittab.apps.tab.models import TabSettings

PUBLIC_CACHE_ALIAS = getattr(settings, "PUBLIC_VIEW_CACHE_ALIAS", "public")

def cache_public_view(timeout=60, settings_keys=None):
    """
    Cache a public view, invalidating when specified TabSettings change.
    """
    if settings_keys is None:
        settings_keys = []

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Include settings values in cache key so it invalidates when they change
            settings_vals = (
                tuple(TabSettings.get(k, "") for k in settings_keys) if settings_keys else ()
            )
            normalized_kwargs = sorted((kwargs or {}).items())
            payload = {
                "view": view_func.__name__,
                "kwargs": normalized_kwargs,
                "settings": list(settings_vals),
                "auth": bool(request.user.is_authenticated),
            }
            raw = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            cache_key = f"pv:{view_func.__name__}:{digest}"

            cached = caches[PUBLIC_CACHE_ALIAS].get(cache_key)
            if cached is not None:
                print("Public cache hit for key:", cache_key)
                return cached
            print("Public cache miss for key:", cache_key)

            response = view_func(request, *args, **kwargs)
            caches[PUBLIC_CACHE_ALIAS].set(cache_key, response, timeout)
            return response

        return wrapper

    return decorator


def invalidate_inround_public_pairings_cache():
    """
    Drop cached responses for the in-round public pairing display when the
    released pairing data becomes outdated.
    """
    caches[PUBLIC_CACHE_ALIAS].clear()


def invalidate_outround_public_pairings_cache(type_of_round, released_visible_value):
    """
    Drop cached responses for the outround public pairing display when the
    released pairing data becomes outdated. The parameters are retained for
    compatibility with existing call sites.
    """
    caches[PUBLIC_CACHE_ALIAS].clear()
