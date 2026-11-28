# Caching decorator taken from http://djangosnippets.org/snippets/564/
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha1
from random import SystemRandom
from uuid import uuid4

from django.core.cache import caches
from django.db import transaction

from mittab.libs.cacheing.public_cache import (
    PUBLIC_CACHE_ALIAS, invalidate_public_rankings_cache,
)

CACHE_TIMEOUT = 20

DEFAULT = "default"
PERSISTENT = "filesystem"

# Cache hits must not affect the seeded pairing tie-breaks.
_cache_random = SystemRandom()
_TABULATION_VERSION_KEY = "tabulation:version"
_tabulation_version = ContextVar("tabulation_cache_version", default=None)
_tabulation_settings = ContextVar("tabulation_settings_cache", default=None)
_tabulation_isolated = ContextVar("tabulation_cache_isolated", default=False)
_RANKING_SETTINGS = {
    "tab_settings_cur_round",
    "tab_settings_lenient_late",
    "tab_settings_speaker_rankings_use_single_adjusted",
}


@contextmanager
def tabulation_cache_scope(*, isolated=False):
    """Share one version across the score lookups in a standings calculation."""
    version = _tabulation_version.get()
    if version is not None and (not isolated or _tabulation_isolated.get()):
        yield version
        return

    # Pairing transactions may read their own uncommitted changes.
    version = uuid4().hex if isolated else caches[PUBLIC_CACHE_ALIAS].get_or_set(
        _TABULATION_VERSION_KEY, lambda: uuid4().hex, timeout=None,
    )
    token = _tabulation_version.set(version)
    settings_token = _tabulation_settings.set({})
    isolation_token = _tabulation_isolated.set(isolated)
    try:
        yield version
    finally:
        _tabulation_isolated.reset(isolation_token)
        _tabulation_settings.reset(settings_token)
        _tabulation_version.reset(token)


def invalidate_tabulation_cache():
    # A calculation already in progress can only repopulate its older version.
    caches[PUBLIC_CACHE_ALIAS].set(_TABULATION_VERSION_KEY, uuid4().hex, timeout=None)


def refresh_rankings_after_commit(*, using=None):
    transaction.on_commit(invalidate_tabulation_cache, using=using)
    transaction.on_commit(invalidate_public_rankings_cache, using=using)


def cache_tabulation_result(fxn, key, *args, **kwargs):
    with tabulation_cache_scope() as version:
        return caches[DEFAULT].get_or_set(
            f"{key}:{version}", lambda: fxn(*args, **kwargs),
        )

def cache_fxn_key(fxn, key, cache_name, *args, **kwargs):
    """
    Cache the result of a function call under the specified key

    For example, the calling this like:
    cache_fxn_key(lambda a: a ** 2, 'squared', DEFAULT, 1)

    would square `1` and store the result under the key 'squared'
    all subsequent function calls would read from the cache until it's cleared
    """
    local = _tabulation_settings.get()
    if (cache_name == PERSISTENT and local is not None
            and (_tabulation_isolated.get() or key in _RANKING_SETTINGS)):
        if key not in local:
            local[key] = fxn(*args, **kwargs)
        return local[key]
    if key not in caches[cache_name]:
        result = fxn(*args, **kwargs)
        caches[cache_name].set(key, result)
        return result
    return caches[cache_name].get(key)


def invalidate_cache(key, cache_name=DEFAULT):
    local = _tabulation_settings.get()
    if cache_name == PERSISTENT and local is not None:
        local.pop(key, None)
    caches[cache_name].delete(key)


def cache(seconds=CACHE_TIMEOUT, stampede=CACHE_TIMEOUT, *, tabulation=False):
    """
    Cache the result of a function call for the specified number of seconds,
    using Django's caching mechanism.
    Assumes that the function never returns None (as the cache returns None to indicate
    a miss), and that the function's result only depends on its parameters.
    Note that the ordering of parameters is important. e.g. myFunction(x = 1, y = 2),
    myFunction(y = 2, x = 1), and myFunction(1,2) will each be cached separately.

    Usage:

    @cache(600)
    def myExpensiveMethod(parm1, parm2, parm3):
        ....
        return expensiveResult
    """

    def do_cache(f):
        def cached(version, args, kwargs):
            key = sha1(
                f"{version}{f.__module__}{f.__name__}{args}{kwargs}".encode("utf-8")
            ).hexdigest()
            result = caches[DEFAULT].get(key)
            if result is None:
                result = f(*args, **kwargs)
                caches[DEFAULT].set(key, result,
                                    _cache_random.randint(seconds, seconds + stampede))
            return result

        def wrapper(*args, **kwargs):
            version = _tabulation_version.get() if tabulation else None
            return cached(version or "", args, kwargs)
        return wrapper

    return do_cache


def clear_cache():
    local = _tabulation_settings.get()
    if local is not None:
        local.clear()
    caches[DEFAULT].clear()
    caches[PERSISTENT].clear()
