# Caching decorator taken from http://djangosnippets.org/snippets/564/
from hashlib import sha1
from django.core.cache import cache as _djcache
import random

CACHE_TIMEOUT = 20


def cache(seconds=CACHE_TIMEOUT, stampede=CACHE_TIMEOUT):
    """
        Cache the result of a function call for the specified number of seconds, 
        using Django's caching mechanism.
        Assumes that the function never returns None (as the cache returns None to indicate a miss), and that the function's result only depends on its parameters.
        Note that the ordering of parameters is important. e.g. myFunction(x = 1, y = 2), myFunction(y = 2, x = 1), and myFunction(1,2) will each be cached separately. 

        Usage:

        @cache(600)
        def myExpensiveMethod(parm1, parm2, parm3):
            ....
            return expensiveResult
    """

    def do_cache(f):
        def x(*args, **kwargs):
            key = sha1(("%s%s%s%s" % (f.__module__, f.__name__, args,
                                      kwargs)).encode("utf-8")).hexdigest()
            result = _djcache.get(key)
            if result is None:
                #print "busting cache"
                result = f(*args, **kwargs)
                _djcache.set(key, result,
                             random.randint(seconds, seconds + stampede))
            return result

        return x

    return do_cache


def invalidate(f):
    key = sha1((
        "%s%s%s%s" %
        (f.__module__, f.__name__, args, kwargs)).encode("utf-8")).hexdigest()
    _djcache.delete(key)


def clear_cache():
    _djcache.clear()
