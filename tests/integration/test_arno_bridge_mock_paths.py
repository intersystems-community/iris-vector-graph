"""
Mock-based tests for arno_bridge.py paths that require libarno_callout.so
(not available in test containers). Patches the SQL cursor layer to simulate
ZF probe success and chunk-fetch responses.

Covers:
  L283-288  — arno_available: ZF probe SQL exception → cache False
  L297      — arno_available: cache miss, probe fn_id == 0 → False
  L304      — arno_available: cache write available=True
  L351-352  — arno_call: SQL exception → ArnoError
  L355      — arno_call: NULL row → ArnoError
  L362-386  — arno_call: _run suffix OK: response with chunk fetch
  L503      — build_kg_adjacency_json: in-nodes not already in nodes_seen
  L541-546  — build_kg_adjacency_chunked: arno_available → serverside path
  L585      — build_kg_adjacency_chunked: arno unavailable → ArnoError
  L606-630  — build_kg_adjacency_chunked: adj_append chunk loop
  L759      — _build_kg_adjacency_serverside: cache DDL flag set
  L763-789  — _build_kg_adjacency_serverside: SQL call + node chunk fetch
"""
import os
import pytest
from unittest.mock import MagicMock, patch, call
from iris_vector_graph.stores.arno_bridge import (
    arno_available,
    arno_call,
    build_kg_adjacency_chunked,
    build_kg_adjacency_json,
    _build_kg_adjacency_serverside,
    _quote_zf_arg,
    _probe_cache,
    _conn_key,
    clear_probe_cache,
    ArnoError,
)


@pytest.fixture(autouse=True)
def clean_probe_cache():
    clear_probe_cache()
    yield
    clear_probe_cache()


def _make_conn(cursor_results=None):
    """Build a minimal mock connection whose cursor().execute() returns given results."""
    conn = MagicMock()
    conn.__class__ = MagicMock  # prevent _conn_key from crashing
    cursor_seq = iter(cursor_results or [])

    def _cursor():
        cur = MagicMock()
        try:
            spec = next(cursor_seq)
        except StopIteration:
            spec = {"fetchone": (None,)}

        if isinstance(spec, Exception):
            cur.execute.side_effect = spec
        else:
            cur.execute.return_value = None
            cur.fetchone.return_value = spec.get("fetchone")
            cur.fetchall.return_value = spec.get("fetchall", [])
        cur.close = MagicMock()
        return cur

    conn.cursor.side_effect = _cursor
    conn.commit = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# arno_available — probe paths
# ---------------------------------------------------------------------------

class TestArnoAvailableProbe:

    def test_ensure_zf_raises_caches_false(self, iris_connection):
        """_ensure_zf_call_function raising → cache False, return False."""
        clear_probe_cache()
        with patch("iris_vector_graph.stores.arno_bridge._ensure_zf_call_function",
                   side_effect=Exception("no zf")):
            result = arno_available(iris_connection)
        assert result is False
        key = _conn_key(iris_connection)
        assert _probe_cache[key]["available"] is False

    def test_probe_sql_raises_caches_false(self, iris_connection):
        """SQL probe raises → cache False."""
        clear_probe_cache()
        with patch("iris_vector_graph.stores.arno_bridge._ensure_zf_call_function"):
            mock_cur = MagicMock()
            mock_cur.execute.side_effect = Exception("SQL probe failed")
            mock_cur.close = MagicMock()
            with patch.object(iris_connection, "cursor", return_value=mock_cur):
                result = arno_available(iris_connection)
        assert result is False

    def test_probe_fn_id_zero_returns_false(self, iris_connection):
        """fn_id == 0 → available False."""
        clear_probe_cache()
        with patch("iris_vector_graph.stores.arno_bridge._ensure_zf_call_function"):
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (0,)
            mock_cur.close = MagicMock()
            with patch.object(iris_connection, "cursor", return_value=mock_cur):
                result = arno_available(iris_connection)
        assert result is False

    def test_probe_fn_id_positive_returns_true(self, iris_connection):
        """fn_id > 0 → available True, cached."""
        clear_probe_cache()
        with patch("iris_vector_graph.stores.arno_bridge._ensure_zf_call_function"):
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (42,)
            mock_cur.close = MagicMock()
            with patch.object(iris_connection, "cursor", return_value=mock_cur):
                result = arno_available(iris_connection)
        assert result is True
        key = _conn_key(iris_connection)
        assert _probe_cache[key]["available"] is True


# ---------------------------------------------------------------------------
# arno_call — dispatch and error paths
# ---------------------------------------------------------------------------

class TestArnoCallMocked:

    def _prime_cache(self, conn):
        key = _conn_key(conn)
        _probe_cache[key] = {"available": True, "lib_path": "/fake/libarno.so"}

    def test_sql_exception_raises_arno_error(self, iris_connection):
        self._prime_cache(iris_connection)
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = Exception("connection died")
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            with pytest.raises(ArnoError, match="failed"):
                arno_call(iris_connection, "kg_triangle_count_global")

    def test_null_row_raises_arno_error(self, iris_connection):
        self._prime_cache(iris_connection)
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            with pytest.raises(ArnoError, match="NULL"):
                arno_call(iris_connection, "kg_triangle_count_global")

    def test_error_result_raises_arno_error(self, iris_connection):
        self._prime_cache(iris_connection)
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("ERROR: kernel panic",)
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            with pytest.raises(ArnoError, match="returned"):
                arno_call(iris_connection, "kg_triangle_count_global")

    def test_run_suffix_ok_chunk_fetch(self, iris_connection):
        """OK:<n> response triggers chunk fetch loop (lines 362-386)."""
        self._prime_cache(iris_connection)

        call_count = [0]

        def make_cursor():
            cur = MagicMock()
            call_count[0] += 1
            n = call_count[0]
            if n == 1:
                # First cursor: the main SELECT call returns "OK:5"
                cur.fetchone.return_value = ("OK:5",)
            else:
                # Second cursor: chunk fetch
                cur.fetchone.return_value = ("hello",)
            cur.close = MagicMock()
            return cur

        with patch.object(iris_connection, "cursor", side_effect=make_cursor):
            # kg_leiden_run ends with _run
            result = arno_call(iris_connection, "kg_leiden_run", "[]", "1.0", "0.001", "10", "256", "42")
        assert isinstance(result, str)
        assert "hello" in result

    def test_run_suffix_ok_zero_length(self, iris_connection):
        """OK:0 → return empty string immediately."""
        self._prime_cache(iris_connection)
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("OK:0",)
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            result = arno_call(iris_connection, "kg_leiden_run", "[]", "1.0", "0.001", "10", "256", "42")
        assert result == ""

    def test_run_suffix_ok_bad_length(self, iris_connection):
        """OK:notanint → ValueError swallowed, returns result_str."""
        self._prime_cache(iris_connection)
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("OK:notanint",)
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            result = arno_call(iris_connection, "kg_leiden_run", "[]", "1.0", "0.001", "10", "256", "42")
        assert result == "OK:notanint"

    def test_chunk_fetch_null_raises(self, iris_connection):
        """Chunk fetch returns NULL → ArnoError."""
        self._prime_cache(iris_connection)
        call_count = [0]

        def make_cursor():
            cur = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                cur.fetchone.return_value = ("OK:5",)
            else:
                cur.fetchone.return_value = None
            cur.close = MagicMock()
            return cur

        with patch.object(iris_connection, "cursor", side_effect=make_cursor):
            with pytest.raises(ArnoError, match="NULL"):
                arno_call(iris_connection, "kg_leiden_run", "[]", "1.0", "0.001", "10", "256", "42")

    def test_chunk_fetch_error_raises(self, iris_connection):
        """Chunk fetch returns ERROR: → ArnoError."""
        self._prime_cache(iris_connection)
        call_count = [0]

        def make_cursor():
            cur = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                cur.fetchone.return_value = ("OK:5",)
            else:
                cur.fetchone.return_value = ("ERROR: chunk fail",)
            cur.close = MagicMock()
            return cur

        with patch.object(iris_connection, "cursor", side_effect=make_cursor):
            with pytest.raises(ArnoError, match="chunk"):
                arno_call(iris_connection, "kg_leiden_run", "[]", "1.0", "0.001", "10", "256", "42")


# ---------------------------------------------------------------------------
# build_kg_adjacency_chunked — serverside + chunked upload paths
# ---------------------------------------------------------------------------

class TestBuildKgAdjacencyChunkedMocked:

    def test_arno_available_routes_to_serverside(self, iris_connection):
        """When arno_available returns True, delegates to _build_kg_adjacency_serverside."""
        key = _conn_key(iris_connection)
        _probe_cache[key] = {"available": True, "lib_path": "/fake/lib.so"}

        with patch("iris_vector_graph.stores.arno_bridge._build_kg_adjacency_serverside",
                   return_value=(["node_a", "node_b"], 2)) as mock_ss:
            result = build_kg_adjacency_chunked(iris_connection)

        mock_ss.assert_called_once_with(iris_connection)
        assert result == (["node_a", "node_b"], 2)

    def test_serverside_arno_error_falls_through(self, iris_connection):
        """If _build_kg_adjacency_serverside raises ArnoError, falls through to Native API."""
        key = _conn_key(iris_connection)
        _probe_cache[key] = {"available": True, "lib_path": "/fake/lib.so"}

        with patch("iris_vector_graph.stores.arno_bridge._build_kg_adjacency_serverside",
                   side_effect=ArnoError("serverside failed")):
            with patch("iris_vector_graph.stores.arno_bridge.arno_available",
                       side_effect=[True, False]):
                with pytest.raises(ArnoError):
                    build_kg_adjacency_chunked(iris_connection)

    def test_chunked_upload_loop(self, iris_connection):
        """adj_append chunk loop (L606-630) runs when arno is available."""
        key = _conn_key(iris_connection)
        _probe_cache[key] = {"available": True, "lib_path": "/fake/lib.so"}

        # Override _build_kg_adjacency_serverside to raise → falls through to chunked path
        # but we also need arno_available to stay True for the adj_append check.
        # Patch the full code path: arno_available True, serverside raises generic Exception
        # so it falls into the native-API adjacency build, then adj_append loop.
        with patch("iris_vector_graph.stores.arno_bridge._build_kg_adjacency_serverside",
                   side_effect=Exception("generic not ArnoError")):
            with patch("iris_vector_graph.stores.arno_bridge.arno_available", return_value=True):
                mock_cur = MagicMock()
                mock_cur.fetchone.return_value = ("OK",)
                mock_cur.close = MagicMock()
                # Patch iris.createIRIS and nextSubscript for native walk
                mock_iris_inst = MagicMock()
                mock_iris_inst.nextSubscript.return_value = ""  # empty graph
                with patch("iris.createIRIS", return_value=mock_iris_inst):
                    with patch.object(iris_connection, "cursor", return_value=mock_cur):
                        with patch.object(iris_connection, "commit"):
                            result = build_kg_adjacency_chunked(iris_connection)
        # adj_str for empty graph is "NODEMAP:0\n---\n"
        # chunk loop runs once (small string)
        assert isinstance(result, tuple)
        assert isinstance(result[0], list)  # idx_to_node


# ---------------------------------------------------------------------------
# _build_kg_adjacency_serverside — DDL install + SQL exec paths
# ---------------------------------------------------------------------------

class TestBuildKgAdjacencyServersideMocked:

    def test_serverside_ok_with_nodes(self, iris_connection):
        """Simulate OK:2:3:1 response + node chunk fetch.

        _build_kg_adjacency_serverside creates ONE cursor (line 652) and reuses it
        for both the main SELECT call and the chunk SELECTs.
        """
        key = _conn_key(iris_connection)
        _probe_cache[key] = {
            "available": True,
            "lib_path": "/fake/lib.so",
            "serverside_ddl_installed": True,  # skip DDL install
        }

        # One cursor used throughout; fetchone called twice:
        #   call 1: SELECT ivg_arno_build_adj(...)  → "OK:2:3:1"
        #   call 2: SELECT ivg_arno_get_node_chunk(1) → "node_a\x1fnode_b"
        cur = MagicMock()
        cur.fetchone.side_effect = [("OK:2:3:1",), ("node_a\x1fnode_b",)]
        cur.close = MagicMock()

        with patch.object(iris_connection, "cursor", return_value=cur):
            idx_to_node, edge_count = _build_kg_adjacency_serverside(iris_connection)

        assert edge_count == 3
        assert "node_a" in idx_to_node
        assert "node_b" in idx_to_node

    def test_serverside_null_response_raises(self, iris_connection):
        key = _conn_key(iris_connection)
        _probe_cache[key] = {
            "available": True,
            "lib_path": "/fake/lib.so",
            "serverside_ddl_installed": True,
        }
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            with pytest.raises(ArnoError, match="NULL"):
                _build_kg_adjacency_serverside(iris_connection)

    def test_serverside_error_response_raises(self, iris_connection):
        key = _conn_key(iris_connection)
        _probe_cache[key] = {
            "available": True,
            "lib_path": "/fake/lib.so",
            "serverside_ddl_installed": True,
        }
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("ERROR: dlopen failed",)
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            with pytest.raises(ArnoError, match="ivg_arno_build_adj"):
                _build_kg_adjacency_serverside(iris_connection)

    def test_serverside_unexpected_status_raises(self, iris_connection):
        key = _conn_key(iris_connection)
        _probe_cache[key] = {
            "available": True,
            "lib_path": "/fake/lib.so",
            "serverside_ddl_installed": True,
        }
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("WHAT:2:3:1",)
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            with pytest.raises(ArnoError, match="unexpected"):
                _build_kg_adjacency_serverside(iris_connection)

    def test_serverside_malformed_parts_raises(self, iris_connection):
        """OK: response with wrong number of parts → ArnoError."""
        key = _conn_key(iris_connection)
        _probe_cache[key] = {
            "available": True,
            "lib_path": "/fake/lib.so",
            "serverside_ddl_installed": True,
        }
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("OK:2",)  # only 1 part, needs 3
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            with pytest.raises(ArnoError, match="malformed"):
                _build_kg_adjacency_serverside(iris_connection)

    def test_serverside_zero_nodes_returns_empty(self, iris_connection):
        """n==0 → return ([], 0) immediately."""
        key = _conn_key(iris_connection)
        _probe_cache[key] = {
            "available": True,
            "lib_path": "/fake/lib.so",
            "serverside_ddl_installed": True,
        }
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("OK:0:0:0",)
        mock_cur.close = MagicMock()
        with patch.object(iris_connection, "cursor", return_value=mock_cur):
            idx_to_node, edge_count = _build_kg_adjacency_serverside(iris_connection)
        assert idx_to_node == []
        assert edge_count == 0

    def test_serverside_installs_ddl_when_not_cached(self, iris_connection):
        """When serverside_ddl_installed is absent, DDL executes (L759).

        conn.cursor() called once; cur.execute() called 3 times (2 DDL + 1 main SELECT).
        fetchone() is only called for the main SELECT → ("OK:0:0:0",).
        """
        key = _conn_key(iris_connection)
        _probe_cache[key] = {
            "available": True,
            "lib_path": "/fake/lib.so",
            # No "serverside_ddl_installed" key
        }

        # Single cursor; fetchone only called for the main SELECT (after DDL installs)
        cur = MagicMock()
        cur.fetchone.return_value = ("OK:0:0:0",)
        cur.close = MagicMock()

        with patch.object(iris_connection, "cursor", return_value=cur):
            with patch.object(iris_connection, "commit"):
                idx_to_node, edge_count = _build_kg_adjacency_serverside(iris_connection)

        # DDL flag should now be set
        assert _probe_cache[key].get("serverside_ddl_installed") is True


# ---------------------------------------------------------------------------
# arno_available — IVG_DISABLE_ARNO env var path (L273)
# ---------------------------------------------------------------------------

class TestArnoDisableEnv:

    def test_disable_arno_env_returns_false(self, iris_connection):
        with patch.dict(os.environ, {"IVG_DISABLE_ARNO": "1"}):
            result = arno_available(iris_connection)
        assert result is False

    def test_disable_arno_env_does_not_probe(self, iris_connection):
        """IVG_DISABLE_ARNO=1 should not call conn.cursor() at all."""
        with patch.dict(os.environ, {"IVG_DISABLE_ARNO": "1"}):
            with patch.object(iris_connection, "cursor") as mock_cursor:
                arno_available(iris_connection)
        mock_cursor.assert_not_called()


# ---------------------------------------------------------------------------
# _quote_zf_arg (L413-420)
# ---------------------------------------------------------------------------

class TestQuoteZfArg:

    def test_none_becomes_empty_string(self):
        assert _quote_zf_arg(None) == '""'

    def test_bool_true(self):
        assert _quote_zf_arg(True) == "1"

    def test_bool_false(self):
        assert _quote_zf_arg(False) == "0"

    def test_int(self):
        assert _quote_zf_arg(42) == "42"

    def test_float(self):
        assert _quote_zf_arg(3.14) == "3.14"

    def test_string_plain(self):
        assert _quote_zf_arg("hello") == '"hello"'

    def test_string_with_quotes(self):
        # Embedded quotes get doubled per ObjectScript convention
        assert _quote_zf_arg('say "hi"') == '"say ""hi"""'


# ---------------------------------------------------------------------------
# build_kg_adjacency_json (L471-506) — in-node not already seen path
# ---------------------------------------------------------------------------

class TestBuildKgAdjacencyJson:

    def test_adjacency_json_with_in_only_node(self, iris_connection, iris_master_cleanup):
        """Exercise the in_src not-in-nodes_seen path (L503) by having a node
        that appears only in ^KG("in",...) and not in ^KG("out",...)."""
        from iris_vector_graph.engine import IRISGraphEngine
        eng = IRISGraphEngine(iris_connection, embedding_dimension=128)
        # node_sink is a destination-only node (no outgoing edges)
        eng.create_node("src_node", labels=["J"], properties={})
        eng.create_node("sink_node", labels=["J"], properties={})
        eng.create_edge("src_node", "J_REL", "sink_node")
        eng.sync()

        result = build_kg_adjacency_json(iris_connection)
        import json as _json
        parsed = _json.loads(result)
        assert "nodes" in parsed
        assert "edges" in parsed
        # sink_node has no outgoing edges — it should still appear in nodes list
        assert "sink_node" in parsed["nodes"]

    def test_adjacency_json_empty_graph(self, iris_connection, iris_master_cleanup):
        result = build_kg_adjacency_json(iris_connection)
        import json as _json
        parsed = _json.loads(result)
        assert parsed["nodes"] == []
        assert parsed["edges"] == []


# ---------------------------------------------------------------------------
# build_kg_adjacency_chunked — arno unavailable raises (L585)
# ---------------------------------------------------------------------------

class TestBuildKgAdjacencyChunkedRaises:

    def test_chunked_raises_when_arno_unavailable_after_walk(self, iris_connection, iris_master_cleanup):
        """After building the adj_str, if arno_available returns False, ArnoError raised (L585)."""
        # Keep arno_available returning False (no mock needed — real container has no libarno)
        with pytest.raises(ArnoError, match="libarno_callout not available"):
            build_kg_adjacency_chunked(iris_connection)

    def test_adj_append_error_row_raises(self, iris_connection, iris_master_cleanup):
        """In the chunk upload loop, if adj_append returns ERROR:... raise ArnoError (L627)."""
        # Prime cache so arno_available returns True
        key = _conn_key(iris_connection)
        _probe_cache[key] = {"available": True, "lib_path": "/fake/lib.so"}

        with patch("iris_vector_graph.stores.arno_bridge._build_kg_adjacency_serverside",
                   side_effect=ArnoError("serverside unavailable")):
            cur = MagicMock()
            cur.fetchone.return_value = ("ERROR: adj_append fail",)
            cur.close = MagicMock()
            mock_iris_inst = MagicMock()
            mock_iris_inst.nextSubscript.return_value = ""  # empty graph
            with patch("iris.createIRIS", return_value=mock_iris_inst):
                with patch.object(iris_connection, "cursor", return_value=cur):
                    with patch.object(iris_connection, "commit"):
                        with pytest.raises(ArnoError, match="kg_adj_append failed"):
                            build_kg_adjacency_chunked(iris_connection)
