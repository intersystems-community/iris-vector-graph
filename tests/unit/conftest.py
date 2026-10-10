import pytest


@pytest.fixture(autouse=True)
def _no_algorithm_cache(monkeypatch):
    """Unit tests pin which path answers a call, with mocked classmethods.

    The result cache (spec 236) adds its own calls to those mocks, so it is off here;
    tests/unit/test_236_cache_keys.py and test_236_front_layer.py switch it back on, and
    tests/integration/test_236_algo_cache.py covers it against a live server.
    """
    monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
