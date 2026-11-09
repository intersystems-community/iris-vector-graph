import pytest


@pytest.fixture(autouse=True)
def _no_algorithm_cache(monkeypatch):
    """Unit tests pin which path answers a call, with mocked classmethods.

    The result cache (spec 236) adds its own calls to those mocks, so it is off here;
    tests/unit/test_236_cache_keys.py and test_236_front_layer.py switch it back on, and
    tests/integration/test_236_algo_cache.py covers it against a live server.
    """
    monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")


@pytest.fixture(autouse=True)
def _write_txn_without_a_server(request, monkeypatch):
    """4.6.4: a write's own transaction (iris_vector_graph._write_txn) asks the server for its level, which the fake
    connections here cannot answer (most answer every fetchone None). With no server, a write runs as no caller
    transaction is open: the helper's own plain mode, 4.6.3's START TRANSACTION / COMMIT / ROLLBACK on the writing
    cursor, which these tests' fakes see and pin. tests/unit/test_464_write_txn.py tests the helper itself against a
    fake server's $TLEVEL, and tests/e2e/test_464_caller_txn_e2e.py against IRIS."""
    if request.module.__name__.endswith("test_464_write_txn"):
        return
    from iris_vector_graph._write_txn import WriteTxn

    def begin(self):
        self._start_plain()
        return self

    monkeypatch.setattr(WriteTxn, "begin", begin)
