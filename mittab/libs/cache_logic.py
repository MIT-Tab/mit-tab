# Caching decorator taken from http://djangosnippets.org/snippets/564/
from hashlib import sha1
import random

from django.core.cache import cache as _djcache

CACHE_TIMEOUT = 20


def cache_fxn_key(fxn, key, *args, **kwargs):
    """
    Cache the result of a function call under the specified key

    For example, the calling this with cache_fxn_key(lambda a: a ** 2, 'squared', 1)

    would square `1` and store the result under the key 'squared'
    all subsequent function calls would read from the cache until it's cleared
    """
    result = _djcache.get(key)

    if not result:
        result = fxn(*args, **kwargs)
        _djcache.set(key, result)
    return result


def invalidate_cache(key):
    _djcache.delete(key)


def cache(seconds=CACHE_TIMEOUT, stampede=CACHE_TIMEOUT, key_base=None):
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
        def wrapper(*args, **kwargs):
            local_key_base = key_base
            if local_key_base is None:
                local_key_base = "%s%s" % (f.__module__, f.__name__)
            key = sha1(("%s%s%s" % (local_key_base, args, kwargs)).encode("utf-8")).hexdigest()
            result = _djcache.get(key)
            if result is None:
                result = f(*args, **kwargs)
                _djcache.set(key, result,
                             random.randint(seconds, seconds + stampede))
            return result

        return wrapper

    return do_cache


def clear_cache():
    _djcache.clear()
