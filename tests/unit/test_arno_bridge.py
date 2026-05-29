"""Unit tests for arno_bridge (Spec 163 T022e / FR-024).

Mocks `conn.cursor()` to simulate the SQL-function-based ZF call dispatch
that arno_bridge installs and invokes. Each call returns one row whose first
column is the result string (or fn_id integer for the probe path).
"""

import os
from unittest.mock import MagicMock

import pytest


def _make_conn(rows):
    """Build a MagicMock connection where `conn.cursor().fetchone()` returns
    successive `rows` (one per call).

    Each entry in `rows` is the value to be wrapped in a `(value,)` tuple so
    `fetchone()[0]` yields it. None entries pass through unwrapped.
    """
    conn = MagicMock()
    cursor = MagicMock()
    fetchone_results = [None if r is None else (r,) for r in rows]
    cursor.fetchone.side_effect = fetchone_results
    conn.cursor.return_value = cursor
    return conn, cursor


@pytest.fixture(autouse=True)
def clear_probe_cache_between_tests():
    from iris_vector_graph.stores.arno_bridge import clear_probe_cache
    clear_probe_cache()
    yield
    clear_probe_cache()


@pytest.fixture
def restore_env():
    saved = os.environ.get("IVG_DISABLE_ARNO")
    yield
    if saved is None:
        os.environ.pop("IVG_DISABLE_ARNO", None)
    else:
        os.environ["IVG_DISABLE_ARNO"] = saved


class TestArnoAvailable:
    def test_returns_false_when_disable_env_set(self, restore_env):
        from iris_vector_graph.stores.arno_bridge import arno_available
        os.environ["IVG_DISABLE_ARNO"] = "1"
        conn = MagicMock()
        assert arno_available(conn) is False

    def test_returns_false_when_zf4_returns_zero(self):
        from iris_vector_graph.stores.arno_bridge import arno_available
        conn, _ = _make_conn([0])
        assert arno_available(conn) is False

    def test_returns_true_when_lib_loads_and_version_found(self):
        from iris_vector_graph.stores.arno_bridge import arno_available
        conn, _ = _make_conn([5])
        assert arno_available(conn) is True

    def test_returns_false_when_version_function_not_found(self):
        from iris_vector_graph.stores.arno_bridge import arno_available
        conn, _ = _make_conn([0])
        assert arno_available(conn) is False

    def test_caches_first_probe(self):
        from iris_vector_graph.stores.arno_bridge import arno_available
        conn, cursor = _make_conn([5])
        arno_available(conn)
        first_count = cursor.execute.call_count
        arno_available(conn)
        assert cursor.execute.call_count == first_count, \
            "Second arno_available() call must hit cache, not re-probe"


class TestArnoCall:
    def test_raises_when_unavailable(self, restore_env):
        from iris_vector_graph.stores.arno_bridge import arno_call, ArnoError
        os.environ["IVG_DISABLE_ARNO"] = "1"
        conn = MagicMock()
        with pytest.raises(ArnoError, match="not available"):
            arno_call(conn, "kg_leiden_global", "^KG", 10)

    def test_raises_when_function_name_not_in_dispatch_table(self):
        from iris_vector_graph.stores.arno_bridge import arno_call, ArnoError
        conn, _ = _make_conn([5, '{"ok":true}'])
        with pytest.raises(ArnoError, match="No SQL wrapper registered"):
            arno_call(conn, "kg_nonexistent_global", "^KG")

    def test_invokes_kernel_with_correct_sql(self):
        from iris_vector_graph.stores.arno_bridge import arno_call
        conn, cursor = _make_conn([5, '[{"id":"a","community":0,"size":3}]'])
        result = arno_call(conn, "kg_leiden_global", "^KG", 10, 1.0, 1e-4, 50, 256, 42)
        assert result == '[{"id":"a","community":0,"size":3}]'
        last_sql = cursor.execute.call_args.args[0]
        assert "ivg_arno_zf_call_kg_leiden" in last_sql

    def test_raises_on_error_string_return(self):
        from iris_vector_graph.stores.arno_bridge import arno_call, ArnoError
        conn, _ = _make_conn([5, "ERROR: graph too large"])
        with pytest.raises(ArnoError, match="graph too large"):
            arno_call(conn, "kg_triangle_count_global", "^KG", 10)


class TestQuoteZfArg:
    def test_string_quoted(self):
        from iris_vector_graph.stores.arno_bridge import _quote_zf_arg
        assert _quote_zf_arg("hello") == '"hello"'

    def test_string_with_embedded_quote_escaped(self):
        from iris_vector_graph.stores.arno_bridge import _quote_zf_arg
        assert _quote_zf_arg('say "hi"') == '"say ""hi"""'

    def test_int_unquoted(self):
        from iris_vector_graph.stores.arno_bridge import _quote_zf_arg
        assert _quote_zf_arg(42) == "42"

    def test_float_unquoted(self):
        from iris_vector_graph.stores.arno_bridge import _quote_zf_arg
        assert _quote_zf_arg(1.5) == "1.5"

    def test_none_becomes_empty_string(self):
        from iris_vector_graph.stores.arno_bridge import _quote_zf_arg
        assert _quote_zf_arg(None) == '""'

    def test_bool_becomes_int(self):
        from iris_vector_graph.stores.arno_bridge import _quote_zf_arg
        assert _quote_zf_arg(True) == "1"
        assert _quote_zf_arg(False) == "0"
