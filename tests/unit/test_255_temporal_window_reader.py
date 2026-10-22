"""Spec 255: the paged temporal window reader, latest per source, and FR-006, without a container.

The server half (Graph.KG.TemporalWindow) is exercised live in tests/e2e/test_255_*; these pin the Python half: the
spec sent to Open, the paging loop, when the server handle is freed (exhausted, close(), abandoned, error), and that a
failed window read raises instead of returning [] (FR-006).
"""

from __future__ import annotations

import gc
import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import TemporalWindowError
from iris_vector_graph.result import IVGResult

WINDOW = "Graph.KG.TemporalWindow"


def _row(i, attrs=None):
    r = {"s": f"t|e{i % 3}", "p": "EMITS", "o": f"evt{i}", "ts": 1000 + i, "w": 1}
    if attrs is not None:
        r["a"] = attrs
    return r


class FakeNative:
    """Answers Graph.KG.TemporalWindow's Open / Next / Close from a list of pages and records each call."""

    def __init__(self, pages, latest=None, fail_on=None):
        self.pages = list(pages)
        self.latest = list(latest or [])
        self.calls = []
        self.fail_on = fail_on
        self.closed = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method) + args)
        if method == self.fail_on:
            raise RuntimeError("<MAXSTRING>Next+3^Graph.KG.TemporalWindow.1")
        if cls == WINDOW and method in ("Open", "Next"):
            rows = self.pages.pop(0)
            done = 0 if self.pages else 1
            return json.dumps({"h": 7, "rows": rows, "done": done})
        if cls == WINDOW and method == "Latest":
            return json.dumps(self.latest.pop(0))
        if method == "RebuildLatest":
            return 42
        raise AssertionError(f"unexpected call {cls}.{method}")

    def classMethodVoid(self, cls, method, *args):
        self.calls.append((cls, method) + args)
        if method == "Close":
            self.closed.append(args[0])

    def methods(self):
        return [c[1] for c in self.calls]


def _engine(native):
    conn = MagicMock()
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    eng._iris_obj = lambda: native
    return eng


# ---------------------------------------------------------------------------
# The spec sent to Open
# ---------------------------------------------------------------------------


class TestOpenSpec:
    def _spec(self, **kw):
        native = FakeNative([[]])
        list(_engine(native).iter_edges_in_window(100, 200, **kw))
        cls, method, graph, spec = native.calls[0]
        assert (cls, method) == (WINDOW, "Open")
        return graph, json.loads(spec)

    def test_window_bounds_direction_attrs_and_page(self):
        graph, spec = self._spec()
        assert graph == ""
        assert spec == {"start": 100, "end": 200, "dir": "out", "attrs": 1, "page": 1000}

    def test_filters_are_sent_only_when_given(self):
        _, spec = self._spec(source="t|e1", predicates=["EMITS", "SEES"], direction="in", attrs=False, page_size=50)
        assert spec == {
            "start": 100, "end": 200, "dir": "in", "attrs": 0, "page": 50,
            "source": "t|e1", "predicates": ["EMITS", "SEES"],
        }

    def test_a_list_and_a_prefix(self):
        assert self._spec(sources=["b", "a"])[1]["sources"] == ["b", "a"]
        assert self._spec(source_prefix="t|")[1]["prefix"] == "t|"

    def test_an_empty_source_list_is_a_list_not_every_source(self):
        """sources=[] names no source: it must not reach the server as "no filter"."""
        assert self._spec(sources=[])[1]["sources"] == []

    def test_the_graph_name_is_validated_and_passed(self):
        assert self._spec(graph="tenant-a")[0] == "tenant-a"
        with pytest.raises(ValueError):
            self._spec(graph="0")

    @pytest.mark.parametrize(
        "kw",
        [
            {"source": "a", "sources": ["b"]},
            {"source": "a", "source_prefix": "t|"},
            {"sources": ["a"], "source_prefix": "t|"},
            {"direction": "both"},
            {"page_size": 0},
            {"sources": "abc"},
        ],
    )
    def test_bad_arguments_raise_before_the_round_trip(self, kw):
        native = FakeNative([[]])
        with pytest.raises(ValueError):
            _engine(native).iter_edges_in_window(100, 200, **kw)
        assert native.calls == []


# ---------------------------------------------------------------------------
# Paging and freeing the server handle
# ---------------------------------------------------------------------------


class TestCursor:
    def test_one_page_is_one_round_trip_and_needs_no_close(self):
        native = FakeNative([[_row(0, {"code": 5}), _row(1)]])
        events = list(_engine(native).iter_edges_in_window(0, 10))
        assert native.methods() == ["Open"]
        assert [e["o"] for e in events] == ["evt0", "evt1"]
        e = events[0]
        assert (e["s"], e["p"], e["o"], e["ts"], e["w"]) == ("t|e0", "EMITS", "evt0", 1000, 1)
        assert (e["source"], e["predicate"], e["target"], e["timestamp"], e["weight"]) == (
            "t|e0", "EMITS", "evt0", 1000, 1,
        )
        assert e["attrs"] == {"code": 5}
        assert events[1]["attrs"] == {}

    def test_attrs_false_leaves_the_key_out(self):
        native = FakeNative([[_row(0)]])
        events = list(_engine(native).iter_edges_in_window(0, 10, attrs=False))
        assert "attrs" not in events[0]

    def test_pages_are_read_in_order_until_done(self):
        native = FakeNative([[_row(0), _row(1)], [_row(2)], [_row(3), _row(4)]])
        cur = _engine(native).iter_edges_in_window(0, 10, page_size=2)
        assert [e["o"] for e in cur] == [f"evt{i}" for i in range(5)]
        assert native.methods() == ["Open", "Next", "Next"]
        assert native.calls[1][2:] == (7, 2)
        # The last page came back done: the server freed the handle itself.
        assert native.closed == []
        assert cur.closed

    def test_an_empty_middle_page_does_not_end_the_read(self):
        native = FakeNative([[_row(0)], [], [_row(1)]])
        assert len(list(_engine(native).iter_edges_in_window(0, 10))) == 2

    def test_close_mid_read_frees_the_handle_once(self):
        native = FakeNative([[_row(0)], [_row(1)]])
        cur = _engine(native).iter_edges_in_window(0, 10)
        assert next(cur)["o"] == "evt0"
        cur.close()
        cur.close()
        assert native.closed == [7]
        with pytest.raises(StopIteration):
            next(cur)

    def test_the_context_manager_closes(self):
        native = FakeNative([[_row(0)], [_row(1)]])
        with _engine(native).iter_edges_in_window(0, 10) as cur:
            next(cur)
        assert native.closed == [7]

    def test_an_abandoned_cursor_is_closed_when_collected(self):
        native = FakeNative([[_row(0)], [_row(1)]])
        cur = _engine(native).iter_edges_in_window(0, 10)
        next(cur)
        del cur
        gc.collect()
        assert native.closed == [7]

    def test_a_failed_page_raises_and_frees_the_handle(self):
        native = FakeNative([[_row(0)], [_row(1)]], fail_on="Next")
        cur = _engine(native).iter_edges_in_window(0, 10)
        next(cur)
        with pytest.raises(TemporalWindowError, match="MAXSTRING"):
            next(cur)
        assert native.closed == [7]

    def test_a_failed_open_raises_at_the_call(self):
        native = FakeNative([[]], fail_on="Open")
        with pytest.raises(TemporalWindowError):
            _engine(native).iter_edges_in_window(0, 10)

    def test_the_round_trip_count_is_visible(self):
        native = FakeNative([[_row(0)], [_row(1)], [_row(2)]])
        cur = _engine(native).iter_edges_in_window(0, 10)
        list(cur)
        assert cur.round_trips == 3


# ---------------------------------------------------------------------------
# FR-006: a failed window read is an error, never an empty window
# ---------------------------------------------------------------------------


class TestFR006:
    def _store(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store.conn = MagicMock()
        return store

    def test_the_store_reports_the_error(self):
        store = self._store()
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("<MAXSTRING>QueryWindow+30")):
            result = store.execute_temporal_window_query("", "", 0, 9999999999)
        assert result.rows == []
        assert result.error and "MAXSTRING" in result.error

    def test_the_store_answers_without_error_when_the_call_succeeds(self):
        store = self._store()
        payload = json.dumps([{"s": "a", "p": "R", "o": "b", "ts": 5, "w": 1}])
        with patch.object(store, "_call_classmethod", return_value=payload):
            result = store.execute_temporal_window_query("a", "R", 0, 10)
        assert result.error is None
        assert result.rows == [["a", "R", "b", 5, 1]]

    def test_get_edges_in_window_raises_on_a_store_error(self):
        eng = _engine(FakeNative([]))
        eng._store = MagicMock()
        eng._store.execute_temporal_window_query.return_value = IVGResult(
            columns=[], rows=[], error="<MAXSTRING>QueryWindow+30"
        )
        with pytest.raises(TemporalWindowError, match="MAXSTRING"):
            eng.get_edges_in_window("", "", 0, 9999999999)

    def test_the_error_names_the_paged_reader(self):
        eng = _engine(FakeNative([]))
        eng._store = MagicMock()
        eng._store.execute_temporal_window_query.return_value = IVGResult(columns=[], rows=[], error="x")
        with pytest.raises(TemporalWindowError, match="iter_edges_in_window"):
            eng.get_edges_in_window("", "", 0, 10)

    def test_the_error_type_is_a_runtime_error(self):
        assert issubclass(TemporalWindowError, RuntimeError)


# ---------------------------------------------------------------------------
# Latest per source
# ---------------------------------------------------------------------------


class TestLatest:
    def test_a_source_list_is_sent_in_chunks(self):
        sources = [f"t|e{i}" for i in range(1200)]
        answers = [{"rows": [_row(i, {})], "next": None} for i in range(3)]
        native = FakeNative([], latest=answers)
        out = _engine(native).get_latest_edges(sources=sources, predicate="EMITS", before=5000, lookback=60)
        calls = [c for c in native.calls if c[1] == "Latest"]
        assert len(calls) == 3
        specs = [json.loads(c[3]) for c in calls]
        assert [len(s["sources"]) for s in specs] == [500, 500, 200]
        assert specs[0]["predicate"] == "EMITS" and specs[0]["before"] == 5000 and specs[0]["lookback"] == 60
        assert sum(len(s["sources"]) for s in specs) == 1200
        assert len(out) == 3 and out[0]["attrs"] == {} and out[0]["source"] == "t|e0"

    def test_a_prefix_pages_by_source(self):
        answers = [{"rows": [_row(0)], "next": "t|e0"}, {"rows": [_row(1)], "next": None}]
        native = FakeNative([], latest=answers)
        out = _engine(native).get_latest_edges(source_prefix="t|", graph="g1")
        calls = [c for c in native.calls if c[1] == "Latest"]
        assert [c[2] for c in calls] == ["g1", "g1"]
        specs = [json.loads(c[3]) for c in calls]
        assert specs[0]["prefix"] == "t|" and "after" not in specs[0]
        assert specs[1]["after"] == "t|e0"
        assert [e["o"] for e in out] == ["evt0", "evt1"]

    def test_no_filter_means_every_source(self):
        native = FakeNative([], latest=[{"rows": [], "next": None}])
        _engine(native).get_latest_edges()
        spec = json.loads(native.calls[0][3])
        assert "sources" not in spec and "prefix" not in spec

    def test_an_empty_source_list_makes_no_call(self):
        native = FakeNative([])
        assert _engine(native).get_latest_edges(sources=[]) == []
        assert native.calls == []

    def test_a_failure_raises(self):
        native = FakeNative([], fail_on="Latest")
        with pytest.raises(TemporalWindowError):
            _engine(native).get_latest_edges(sources=["a"])

    def test_both_a_list_and_a_prefix_is_refused(self):
        with pytest.raises(ValueError):
            _engine(FakeNative([])).get_latest_edges(sources=["a"], source_prefix="t|")

    def test_rebuild_names_the_graph(self):
        native, graph = FakeNative([]), "g1"
        assert _engine(native).rebuild_temporal_latest(graph=graph) == 42
        assert native.calls == [("Graph.KG.TemporalIndex", "RebuildLatest", graph)]


# ---------------------------------------------------------------------------
# Facade and ObjectScript surface
# ---------------------------------------------------------------------------


def test_the_temporal_sub_engine_forwards_the_new_methods():
    from iris_vector_graph.engine import _TemporalSubEngine

    for name in ("iter_edges_in_window", "get_latest_edges", "rebuild_temporal_latest"):
        assert hasattr(_TemporalSubEngine, name), name


def test_the_reader_class_has_its_entry_points():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/TemporalWindow.cls").read_text()
    for sig in (
        "ClassMethod Open(graphId As %String = \"\", specJSON As %String)",
        "ClassMethod Next(pHandle As %Integer, pCount As %Integer",
        "ClassMethod Close(pHandle As %Integer)",
        "ClassMethod Latest(graphId As %String = \"\", specJSON As %String)",
    ):
        assert sig in src, sig


def test_the_string_capped_readers_name_the_cap_and_the_reader():
    """FR-008: QueryWindow, QueryWindowInbound and GetEdgeAttrs keep their output and say where to go instead."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/TemporalIndex.cls").read_text()
    for method in ("QueryWindow", "QueryWindowInbound", "GetEdgeAttrs"):
        m = re.search(r"((?:///[^\n]*\n)+)ClassMethod " + method + r"\(", src)
        assert m, f"{method} has no doc comment"
        assert "TemporalWindow" in m.group(1), f"{method}'s comment does not name the paged reader"
    for method in ("QueryWindow", "QueryWindowInbound"):
        m = re.search(r"((?:///[^\n]*\n)+)ClassMethod " + method + r"\(", src)
        assert "MAXSTRING" in m.group(1), f"{method}'s comment does not name the string cap"
