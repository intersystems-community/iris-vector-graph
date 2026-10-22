"""Spec 255 US2: any window size, without a cap, and a failure that raises.

QueryWindow builds its answer as one string, so a window whose JSON passes IRIS's 3,641,144-character limit raises
<MAXSTRING>, and get_edges_in_window used to turn that into []. The window here is built to pass the limit with few
rows (long ids, 12,500 edges, about 4.1 M characters as QueryWindow spells them), so the test stays small on a shared
container. The paged reader must return every edge once, in order, holding only a cursor between pages.

Run against a scratch namespace: `IVG_NAMESPACE=IVGTEST`.
"""

from __future__ import annotations

import gc
import json
import os
import time

import pytest

from iris_vector_graph.exceptions import TemporalWindowError

from .window_reader_fixture import WINDOW, bulk_insert, edge_attrs, graph_env, key, query_window  # noqa: F401

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T0 = 1_800_100_000
N_TS = 250
N_SRC = 50
END = T0 + N_TS - 1
PRED = "OBSERVED_EVENT_PREDICATE"
IRIS_MAX_STRING = 3_641_144


def _src(i):
    return f"tenant-{i:02d}|" + "s" * 90


def _dst(i):
    return f"target-{i:02d}|" + "o" * 140


@pytest.fixture(scope="module")
def big(graph_env):
    engine, native, graph = graph_env
    edges = [{"s": _src(i), "p": PRED, "o": _dst(i), "ts": T0 + j, "w": 1, "attrs": {"j": j}}
             for j in range(N_TS) for i in range(N_SRC)]
    started = time.perf_counter()
    assert bulk_insert(native, graph, edges) == len(edges)
    print(f"\nloaded {len(edges)} edges in {time.perf_counter() - started:.1f}s")
    one = len(json.dumps({"s": _src(0), "p": PRED, "o": _dst(0), "ts": T0, "w": 1}, separators=(",", ":")))
    assert one * len(edges) > IRIS_MAX_STRING, "the window must pass the string limit for this test to mean anything"
    return engine, native, graph, len(edges)


def _walk(native, graph):
    """The index itself, read in windows that each fit QueryWindow's string."""
    rows = []
    for start in range(T0, END + 1, 25):
        rows.extend(query_window(native, graph, "", "", start, min(start + 24, END)))
    return rows


def test_get_edges_in_window_raises_past_the_string_limit(big):
    """FR-006: the full window cannot be one string; the old reader must say so, not answer []."""
    engine, _, graph, _ = big
    assert len(engine.get_edges_in_window("", "", T0, T0 + 9, graph=graph)) == 10 * N_SRC
    with pytest.raises(TemporalWindowError, match="MAXSTRING"):
        engine.get_edges_in_window("", "", T0, END, graph=graph)


def test_every_edge_comes_back_once_in_order_in_pages(big):
    engine, native, graph, n = big
    want = _walk(native, graph)
    assert len(want) == n
    started = time.perf_counter()
    cur = engine.iter_edges_in_window(T0, END, graph=graph, page_size=1000)
    got = list(cur)
    elapsed = time.perf_counter() - started
    print(f"\nread {len(got)} edges with attrs in {cur.round_trips} pages, {elapsed * 1000:.0f} ms")
    assert [key(e) for e in got] == [key(e) for e in want]
    assert [key(e) for e in got] == sorted(key(e) for e in got)
    assert cur.round_trips >= n // 1000
    for e in got[:: n // 40]:
        assert e["attrs"] == edge_attrs(native, graph, e) == {"j": e["ts"] - T0}


def test_the_server_holds_a_cursor_not_the_rows(big):
    engine, _, graph, _ = big
    cur = engine.iter_edges_in_window(T0, END, graph=graph, page_size=100)
    first = json.loads(str(cur.native.classMethodValue(WINDOW, "State", cur.handle)))
    for _ in range(50 * 100):
        next(cur)
    later = json.loads(str(cur.native.classMethodValue(WINDOW, "State", cur.handle)))
    assert first["open"] == later["open"] == 1
    assert later["nodes"] == first["nodes"] <= 8, later
    assert later["key"] != first["key"]
    cur.close()


def test_close_frees_the_handle_at_once_and_a_closed_handle_raises(big):
    engine, _, graph, _ = big
    cur = engine.iter_edges_in_window(T0, END, graph=graph, page_size=100)
    native, handle = cur.native, cur.handle
    next(cur)
    cur.close()
    assert json.loads(str(native.classMethodValue(WINDOW, "State", handle)))["open"] == 0
    with pytest.raises(Exception, match="not open"):
        native.classMethodValue(WINDOW, "Next", handle, 100)


def test_an_abandoned_reader_is_freed(big):
    engine, _, graph, _ = big
    cur = engine.iter_edges_in_window(T0, END, graph=graph, page_size=100)
    native, handle = cur.native, cur.handle
    next(cur)
    del cur
    gc.collect()
    assert json.loads(str(native.classMethodValue(WINDOW, "State", handle)))["open"] == 0


def test_a_page_never_nears_the_string_limit(big):
    """A page stops at its row count or about 2 MB, whichever comes first, so a big page size cannot fail."""
    engine, _, graph, n = big
    cur = engine.iter_edges_in_window(T0, END, graph=graph, page_size=100_000)
    assert sum(1 for _ in cur) == n
    assert cur.round_trips >= 2
