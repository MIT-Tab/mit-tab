import pytest

from mittab.libs.cacheing import cache_logic


@pytest.fixture(autouse=True)
def clear_django_caches_between_tests():
    """Prevent stale cached stats (TabSettings-dependent) from leaking across tests."""
    cache_logic.clear_cache()
    yield
    cache_logic.clear_cache()
