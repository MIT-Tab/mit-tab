# Caching decorator taken from http://djangosnippets.org/snippets/564/
from hashlib import sha1
import random

from django.core.cache import caches

CACHE_TIMEOUT = 20

DEFAULT = "default"
PERSISTENT = "filesystem"


def cache_fxn_key(fxn, key, cache_name, *args, **kwargs):
    """
    Cache the result of a function call under the specified key

    For example, the calling this like:
    cache_fxn_key(lambda a: a ** 2, 'squared', DEFAULT, 1)

    would square `1` and store the result under the key 'squared'
    all subsequent function calls would read from the cache until it's cleared
    """
    if key not in caches[cache_name]:
        result = fxn(*args, **kwargs)
        caches[cache_name].set(key, result)
        return result
    else:
        return caches[cache_name].get(key)


def invalidate_cache(key, cache_name=DEFAULT):
    caches[cache_name].delete(key)


def cache(seconds=CACHE_TIMEOUT, stampede=CACHE_TIMEOUT):
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
            key = sha1(("%s%s%s%s" % (f.__module__, f.__name__, args, kwargs)) \
                    .encode("utf-8")).hexdigest()
            result = caches[DEFAULT].get(key)
            if result is None:
                result = f(*args, **kwargs)
                caches[DEFAULT].set(key, result,
                                    random.randint(seconds, seconds + stampede))
            return result

        return wrapper

    return do_cache


def clear_cache():
    caches[DEFAULT].clear()
    caches[PERSISTENT].clear()
