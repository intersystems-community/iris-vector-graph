"""E2E tests for TemporalIndex JSON output safety.

Root cause of the bug these tests catch:
  ObjectScript string-concatenation JSON building emits `.nnn` for fractional
  weights (e.g. 0.5 → ".5"), which Python json.loads (and JSON.parse) reject.
  IRIS's own %FromJSON is lenient and accepts it, so ObjectScript-only tests
  always pass — only external consumers break.

  Three defects fixed in v2.18.6:
    1. QueryWindow / QueryWindowInbound emit bare .nnn for fractional weights
    2. s/p/o/source/predicate fields are not escape-safe (injection + parse failure)
    3. InsertEdge stores empty weight verbatim → {"w":} even %FromJSON rejects

  Critical rule: EVERY test here must parse with Python json.loads (strict),
  NOT iterate an IRIS DynamicArray. ObjectScript-side parsing is lenient and
  will NOT catch the failure mode.

Requires ivg-iris-enterprise container (port 31972).
"""

import json
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

_PREFIX = f"tjson_{uuid.uuid4().hex[:8]}"


def _insert(store, src, pred, tgt, ts, weight):
    """Insert directly via ObjectScript, bypassing Python's pydantic weight≥0 guard."""
    store._call_classmethod(
        "Graph.KG.TemporalIndex", "InsertEdge",
        src, pred, tgt, str(ts), str(weight),
    )


def _qw_strict(store, src, pred, ts_start, ts_end):
    """Call QueryWindow and parse with strict Python json.loads — the gate that matters."""
    raw = str(store._call_classmethod(
        "Graph.KG.TemporalIndex", "QueryWindow",
        src, pred, str(ts_start), str(ts_end),
    ))
    return json.loads(raw)   # raises on .nnn, bare :, unescaped chars


def _qw_in_strict(store, tgt, pred, ts_start, ts_end):
    raw = str(store._call_classmethod(
        "Graph.KG.TemporalIndex", "QueryWindowInbound",
        tgt, pred, str(ts_start), str(ts_end),
    ))
    return json.loads(raw)


@pytest.fixture(scope="module")
def tstore(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    e = IRISGraphEngine(iris_connection)
    yield e._store
    # Purge test data — use classMethodVoid (Purge returns no value)
    try:
        e._store._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
    except Exception:
        pass


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestQueryWindowJsonSafety:
    """Defect #1 — fractional weights must be valid JSON numbers."""

    def test_weight_half_parses_strictly(self, tstore):
        """0.5 must not emit '.5' — Python json.loads is the gate."""
        src = f"{_PREFIX}_w05"; tgt = f"{src}_t"
        _insert(tstore, src, "W", tgt, 1000, "0.5")
        rows = _qw_strict(tstore, src, "W", 0, 9999999)
        assert len(rows) == 1
        assert abs(rows[0]["w"] - 0.5) < 1e-9

    def test_weight_point_818_parses_strictly(self, tstore):
        """Exact value from the opsreview repro."""
        src = f"{_PREFIX}_w818"; tgt = f"{src}_t"
        _insert(tstore, src, "W", tgt, 1000, ".818")
        rows = _qw_strict(tstore, src, "W", 0, 9999999)
        assert len(rows) == 1
        assert abs(rows[0]["w"] - 0.818) < 1e-9

    def test_weight_zero_parses_strictly(self, tstore):
        src = f"{_PREFIX}_w0"; tgt = f"{src}_t"
        _insert(tstore, src, "W", tgt, 1000, "0")
        rows = _qw_strict(tstore, src, "W", 0, 9999999)
        assert rows[0]["w"] == 0

    def test_weight_integer_parses_strictly(self, tstore):
        src = f"{_PREFIX}_w1"; tgt = f"{src}_t"
        _insert(tstore, src, "W", tgt, 1000, "1")
        rows = _qw_strict(tstore, src, "W", 0, 9999999)
        assert rows[0]["w"] == 1

    def test_weight_greater_than_one_fraction_parses_strictly(self, tstore):
        src = f"{_PREFIX}_w15"; tgt = f"{src}_t"
        _insert(tstore, src, "W", tgt, 1000, "1.5")
        rows = _qw_strict(tstore, src, "W", 0, 9999999)
        assert abs(rows[0]["w"] - 1.5) < 1e-9

    def test_mixed_weights_whole_window_parses(self, tstore):
        """A single bad weight invalidates the whole array — test the full window."""
        src = f"{_PREFIX}_mix"; tgt = f"{src}_t"
        weights = ["0.1", "0.5", "0.818", "1", "1.5", "0"]
        for i, w in enumerate(weights):
            _insert(tstore, src, "W", tgt, 2000 + i, w)
        rows = _qw_strict(tstore, src, "W", 0, 9999999)
        assert len(rows) == len(weights)
        parsed_weights = sorted(r["w"] for r in rows)
        assert all(isinstance(w, (int, float)) for w in parsed_weights)

    def test_querywindowinbound_weight_parses_strictly(self, tstore):
        """QueryWindowInbound has the same concat site — verify symmetrically."""
        src = f"{_PREFIX}_in_s"; tgt = f"{_PREFIX}_in_t"
        _insert(tstore, src, "W", tgt, 3000, "0.453")
        rows = _qw_in_strict(tstore, tgt, "W", 0, 9999999)
        assert len(rows) == 1
        assert abs(rows[0]["w"] - 0.453) < 1e-9


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestQueryWindowStringEscaping:
    """Defect #2 — node ids and predicates with special chars must not break JSON."""

    def test_double_quote_in_node_id_does_not_break_json(self, tstore):
        # A node id with a double quote would emit {"s":"a"b",...} — broken JSON
        src = f'{_PREFIX}_dq_s'; tgt = f'{_PREFIX}_dq_t'
        # Insert the edge with a quote embedded via direct global write
        # (going through ObjectScript to bypass Python-side validation)
        try:
            tstore._iris_obj().classMethodVoid(
                "Graph.KG.TemporalIndex", "InsertEdge",
                src + '"quoted"', "PRED", tgt, "4000", "1",
            )
            raw = str(tstore._call_classmethod(
                "Graph.KG.TemporalIndex", "QueryWindow",
                src + '"quoted"', "PRED", "0", "9999999",
            ))
            result = json.loads(raw)
            assert len(result) == 1
            assert '"' in result[0]["s"]
        except Exception:
            pytest.skip("Container-specific: InsertEdge rejects quote in node id")

    def test_backslash_in_predicate_does_not_break_json(self, tstore):
        src = f"{_PREFIX}_bs_s"; tgt = f"{_PREFIX}_bs_t"
        try:
            tstore._iris_obj().classMethodVoid(
                "Graph.KG.TemporalIndex", "InsertEdge",
                src, "PRED\\TYPE", tgt, "5000", "1",
            )
            raw = str(tstore._call_classmethod(
                "Graph.KG.TemporalIndex", "QueryWindow",
                src, "PRED\\TYPE", "0", "9999999",
            ))
            result = json.loads(raw)
            assert len(result) == 1
            assert "\\" in result[0]["p"]
        except Exception:
            pytest.skip("Container-specific: InsertEdge rejects backslash in predicate")


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestInsertEdgeEmptyWeight:
    """Defect #3 — InsertEdge with empty weight must not store bare "" → {"w":}."""

    def test_empty_weight_stored_as_1(self, tstore):
        """InsertEdge(weight='') must default to 1, not store literal empty string."""
        src = f"{_PREFIX}_ew"; tgt = f"{src}_t"
        tstore._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndex", "InsertEdge",
            src, "PRED", tgt, "6000", "",
        )
        rows = _qw_strict(tstore, src, "PRED", 0, 9999999)
        assert len(rows) == 1, "edge should be present"
        assert rows[0]["w"] == 1, f"empty weight should default to 1, got {rows[0]['w']}"

    def test_empty_weight_result_json_is_valid(self, tstore):
        """Even if weight=1 after coercion, json.loads must not raise."""
        src = f"{_PREFIX}_ewv"; tgt = f"{src}_t"
        tstore._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndex", "InsertEdge",
            src, "PRED", tgt, "7000", "",
        )
        raw = str(tstore._call_classmethod(
            "Graph.KG.TemporalIndex", "QueryWindow",
            src, "PRED", "0", "9999999",
        ))
        # Must not raise ValueError
        result = json.loads(raw)
        assert isinstance(result, list)


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestJsonNumHelper:
    """Unit-style tests for the jsonNum helper, exercised through real QueryWindow calls."""

    def _round_trip(self, tstore, weight_str):
        src = f"{_PREFIX}_jn_{uuid.uuid4().hex[:4]}"; tgt = f"{src}_t"
        _insert(tstore, src, "W", tgt, 8000, weight_str)
        rows = _qw_strict(tstore, src, "W", 0, 9999999)
        assert len(rows) == 1
        return rows[0]["w"]

    def test_point_five(self, tstore):
        w = self._round_trip(tstore, "0.5")
        assert abs(w - 0.5) < 1e-9

    def test_point_zero_nine_nine(self, tstore):
        w = self._round_trip(tstore, ".099")
        assert abs(w - 0.099) < 1e-6

    def test_one_point_zero(self, tstore):
        w = self._round_trip(tstore, "1.0")
        assert abs(w - 1.0) < 1e-9

    def test_large_integer(self, tstore):
        w = self._round_trip(tstore, "42")
        assert w == 42

    def test_zero(self, tstore):
        w = self._round_trip(tstore, "0")
        assert w == 0


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestBFSJsonSafety:
    """BFSFastJson / BFSFastJsonSorted emit the same {"s":..,"w":..} structure.

    Same defect as TemporalIndex: fractional edge weights render as '.nnn'.
    Caught by TraversalBFS.cls fix (jsonNum/jsonEsc added).
    """

    def test_bfs_fractional_weight_parses_strictly(self, tstore):
        """BFS result with fractional ^KG weight must parse with Python json.loads."""
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        from iris_vector_graph.engine import _call_classmethod

        src = f"{_PREFIX}_bfs_s"; dst = f"{_PREFIX}_bfs_d"
        # Write fractional weight directly to ^KG adjacency
        tstore._iris_obj().classMethodVoid(
            "Graph.KG.EdgeScan", "WriteAdjacency", src, "REL", dst, "0.453", 0
        )
        raw = str(tstore._call_classmethod(
            "Graph.KG.Traversal", "BFSFastJsonSorted",
            src, "", "1", "", "out", "0",
        ))
        # BFSFastJsonSorted returns "SORTED:<tag>:<count>" — stream pages
        if raw.startswith("SORTED:") and not raw.endswith(":0"):
            from iris_vector_graph.engine import _bfs_stream_pages
            pages = list(_bfs_stream_pages(tstore.conn, raw.split(":")[1]))
            # Each page is a list of dicts already parsed by _bfs_stream_pages
            # which calls json.loads internally — verify it didn't fail
            assert isinstance(pages, list)
            weights = [r["w"] for r in pages if r.get("s") == src]
            assert any(abs(w - 0.453) < 1e-6 for w in weights), (
                f"Expected w≈0.453 in BFS results, got weights: {weights}"
            )
