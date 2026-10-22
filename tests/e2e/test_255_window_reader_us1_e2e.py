"""Spec 255 US1: the hero query in one call. One tenant's events in a 5-minute window, with attributes.

The reference is the reader that exists: QueryWindow for the coordinates and GetEdgeAttrs once per edge, which is
what opsreview's explorer does today. The new reader must return the same rows, in the same order, in one round
trip, and its filters (a source, a list, a prefix, predicates, inbound) must each equal the reference filtered the
same way.

Run against a scratch namespace: `IVG_NAMESPACE=IVGTEST`.
"""

from __future__ import annotations

import os

import pytest

from .window_reader_fixture import (
    WINDOW,
    bulk_insert,
    coords,
    edge_attrs,
    fresh_graph,  # noqa: F401  (fixture)
    graph_env,  # noqa: F401  (fixture)
    key,
    query_window,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T0 = 1_800_000_000
END = T0 + 299
UNSAFE = 'quote " back \\ newline \n tab \t ctl \x01 accent é check ✓'
A_SOURCES = [f"A|e{i}" for i in range(5)]


def _attrs(k):
    if k % 4 == 0:
        return {"seq": k, "sev": "warn", "ratio": 0.5, "neg": -0.25, "msg": UNSAFE}
    if k % 4 == 1:
        # a numeric-looking string stays a string, and a boolean is what GetEdgeAttrs makes of it
        return {"seq": k, "code_str": "237", "neg_str": "-.5", "flag": True, "empty": "", "big": 12345678901234567}
    return None


def _edges():
    edges = []
    for k in range(150):
        e = {"s": f"A|e{k % 5}", "p": "SEES" if k % 3 == 0 else "EMITS", "o": f"A|evt{k:04d}", "ts": T0 + 2 * k,
             "w": 1 + (k % 3) * 0.5}
        if _attrs(k):
            e["attrs"] = _attrs(k)
        edges.append(e)
    # the last second of the window, and one second either side of it
    edges.append({"s": "A|e0", "p": "EMITS", "o": "A|edge-end", "ts": END, "w": 1})
    edges.append({"s": "A|e1", "p": "EMITS", "o": "A|before", "ts": T0 - 1, "w": 1})
    edges.append({"s": "A|e1", "p": "EMITS", "o": "A|after", "ts": END + 1, "w": 1})
    # other tenants at the same timestamps, some into a shared hub (the inbound reads)
    for t in ("B", "C"):
        for k in range(150):
            edges.append({"s": f"{t}|e{k % 5}", "p": "EMITS", "o": "hub|x" if k % 10 == 0 else f"{t}|evt{k:04d}",
                          "ts": T0 + 2 * k + (k % 2), "w": 1, "attrs": {"t": t}})
    # an edge into the hub with no reverse index: no inbound read can see it
    edges.append({"s": "A|e3", "p": "EMITS", "o": "hub|x", "ts": T0 + 7, "w": 1, "sri": 1})
    return edges


@pytest.fixture(scope="module")
def loaded(graph_env):
    engine, native, graph = graph_env
    edges = _edges()
    assert bulk_insert(native, graph, edges) == len(edges)
    return engine, native, graph


def _reference(native, graph, keep, start=T0, end=END, source="", predicate="", inbound=False):
    rows = [e for e in query_window(native, graph, source, predicate, start, end, inbound) if keep(e)]
    for e in rows:
        e["attrs"] = edge_attrs(native, graph, e)
    return rows


def test_the_hero_query_equals_querywindow_plus_getedgeattrs_in_one_round_trip(loaded):
    engine, native, graph = loaded
    want = _reference(native, graph, lambda e: e["s"].startswith("A|"))
    assert len(want) == 152  # 150 + the edge at END + the suppressed-reverse one; not the two outside
    cur = engine.iter_edges_in_window(T0, END, sources=A_SOURCES, graph=graph)
    got = list(cur)
    assert coords(got) == want
    assert cur.round_trips == 1


def test_attribute_values_come_back_as_written(loaded):
    engine, native, graph = loaded
    got = {e["o"]: e["attrs"] for e in engine.iter_edges_in_window(T0, END, source="A|e0", graph=graph)}
    assert got["A|evt0000"]["msg"] == UNSAFE
    assert got["A|evt0000"]["ratio"] == 0.5 and got["A|evt0000"]["neg"] == -0.25
    assert got["A|evt0000"]["seq"] == 0
    assert got["A|evt0010"] == {}
    assert got["A|evt0005"]["code_str"] == "237" and got["A|evt0005"]["neg_str"] == "-.5"
    assert got["A|evt0005"]["empty"] == "" and got["A|evt0005"]["big"] == 12345678901234567


def test_a_prefix_reads_one_tenant(loaded):
    engine, native, graph = loaded
    want = _reference(native, graph, lambda e: e["s"].startswith("A|"))
    assert coords(engine.iter_edges_in_window(T0, END, source_prefix="A|", graph=graph)) == want


def test_one_source(loaded):
    engine, native, graph = loaded
    want = _reference(native, graph, lambda e: True, source="A|e1")
    assert want
    assert coords(engine.iter_edges_in_window(T0, END, source="A|e1", graph=graph)) == want


def test_a_list_is_the_union_of_its_sources_each_event_once(loaded):
    engine, native, graph = loaded
    want = _reference(native, graph, lambda e: e["s"] in ("A|e1", "A|e2"))
    got = coords(engine.iter_edges_in_window(T0, END, sources=["A|e2", "A|e1", "A|e2", "nobody"], graph=graph))
    assert got == want
    assert len({key(e) for e in got}) == len(got)


def test_an_empty_list_reads_nothing(loaded):
    engine, _, graph = loaded
    assert list(engine.iter_edges_in_window(T0, END, sources=[], graph=graph)) == []


@pytest.mark.parametrize("preds", [["SEES"], ["EMITS", "SEES"], ["SEES", "NONE"]])
def test_predicates(loaded, preds):
    engine, native, graph = loaded
    want = _reference(native, graph, lambda e: e["s"].startswith("A|") and e["p"] in preds)
    got = coords(engine.iter_edges_in_window(T0, END, source_prefix="A|", predicates=preds, graph=graph))
    assert got == want


def test_every_source(loaded):
    engine, native, graph = loaded
    want = _reference(native, graph, lambda e: True)
    assert coords(engine.iter_edges_in_window(T0, END, graph=graph)) == want


def test_both_ends_are_inclusive_and_a_reversed_window_is_empty(loaded):
    engine, _, graph = loaded
    first = list(engine.iter_edges_in_window(T0, T0, source_prefix="A|", graph=graph))
    last = list(engine.iter_edges_in_window(END, END, source_prefix="A|", graph=graph))
    assert [e["o"] for e in first] == ["A|evt0000"]
    assert [e["o"] for e in last] == ["A|edge-end"]
    assert list(engine.iter_edges_in_window(END, T0, graph=graph)) == []


def test_small_pages_read_the_same_rows_in_the_same_order(loaded):
    engine, _, graph = loaded
    whole = coords(engine.iter_edges_in_window(T0, END, graph=graph))
    cur = engine.iter_edges_in_window(T0, END, graph=graph, page_size=7)
    assert coords(cur) == whole
    assert cur.round_trips >= len(whole) // 7


def test_inbound_equals_querywindowinbound_and_misses_suppressed_edges(loaded):
    engine, native, graph = loaded
    want = _reference(native, graph, lambda e: True, source="hub|x", inbound=True)
    got = coords(engine.iter_edges_in_window(T0, END, source="hub|x", direction="in", graph=graph))
    assert got == want
    assert want and all(e["o"] == "hub|x" for e in got)
    assert ("A|e3", T0 + 7) not in {(e["s"], e["ts"]) for e in got}


def test_without_attrs(loaded):
    engine, native, graph = loaded
    want = [e for e in query_window(native, graph, "", "", T0, END) if e["s"].startswith("A|")]
    got = list(engine.iter_edges_in_window(T0, END, source_prefix="A|", attrs=False, graph=graph))
    assert "attrs" not in got[0]
    assert coords(got, with_attrs=False) == want


def test_millisecond_timestamps_read_through_the_same_reader(fresh_graph):
    engine, native, graph = fresh_graph
    base = 1_800_000_000_000
    edges = [{"s": f"m|e{k % 3}", "p": "TICK", "o": f"m|t{k}", "ts": base + k * 250, "w": 1, "attrs": {"k": k}}
             for k in range(40)]
    bulk_insert(native, graph, edges, cls="Graph.KG.TemporalIndexMS")
    want = _reference(native, graph, lambda e: True, start=base, end=base + 5_000)
    assert len(want) == 21
    assert coords(engine.iter_edges_in_window(base, base + 5_000, graph=graph)) == want


def test_numeric_sources_collate_before_strings_and_a_prefix_still_finds_them(fresh_graph):
    """IRIS orders canonical numbers before strings, so "42" is not between "4" and "4a": a range scan from the
    prefix would miss it. The reader falls back to comparing every source for such a prefix."""
    engine, native, graph = fresh_graph
    ids = ["4", "42", "400", "4a", "4.5", "5", "-4", "40x"]
    bulk_insert(native, graph, [{"s": s, "p": "R", "o": f"o{i}", "ts": T0 + i, "w": 1} for i, s in enumerate(ids)])
    got = {e["s"] for e in engine.iter_edges_in_window(T0, END, source_prefix="4", graph=graph)}
    assert got == {"4", "42", "400", "4a", "4.5", "40x"}
    got = {e["s"] for e in engine.iter_edges_in_window(T0, END, sources=["400", "4a", "42", "-4"], graph=graph)}
    assert got == {"400", "4a", "42", "-4"}


def test_a_page_after_a_delete_is_a_live_read(fresh_graph):
    """Decision 2: each page walks on from the resume key, so an edge removed after the first page is not returned."""
    engine, native, graph = fresh_graph
    edges = [{"s": "A|e0", "p": "R", "o": f"o{k:03d}", "ts": T0 + k, "w": 1} for k in range(30)]
    bulk_insert(native, graph, edges)
    cur = engine.iter_edges_in_window(T0, END, graph=graph, page_size=10)
    seen = [next(cur)["o"] for _ in range(10)]
    assert engine.delete_edge_temporal("A|e0", "R", "o020", T0 + 20, graph=graph)
    seen += [e["o"] for e in cur]
    assert "o020" not in seen and len(seen) == 29


def test_the_server_reader_answers_open_with_the_first_page(loaded):
    """The ObjectScript entry point as the explorer would call it, without the Python cursor."""
    import json

    _, native, graph = loaded
    head = json.loads(str(native.classMethodValue(
        WINDOW, "Open", graph, json.dumps({"start": T0, "end": END, "prefix": "A|", "attrs": 1, "page": 1000}))))
    assert head["done"] == 1 and len(head["rows"]) == 152
    assert set(head["rows"][0]) >= {"s", "p", "o", "ts", "w", "a"}
