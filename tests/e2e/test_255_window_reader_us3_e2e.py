"""Spec 255 US3: the latest event per source, from ^KG("tlast", g, s, p) = ts (Decision 1 (b)).

The reference is brute force over every edge the graph holds (QueryWindow over all time, small data): for each
source, the maximum (ts, p, o) among its edges of the predicate asked for, before the cutoff (exclusive). The index
must stay exact through every path that removes temporal edges: one-edge delete, both purges, node erase. A graph
written before the index existed has entries for nothing and no completeness flag: Latest walks it, and a read that
needs the index (a prefix, every source) raises until rebuild_temporal_latest.

Run against a scratch namespace: `IVG_NAMESPACE=IVGTEST`.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.exceptions import TemporalWindowError

from .window_reader_fixture import (  # noqa: F401  (fixtures)
    INDEX,
    bulk_insert,
    edge_attrs,
    fresh_graph,
    graph_env,
    query_window,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T0 = 1_800_200_000
ALL_TIME = (0, 9_999_999_999)
SOURCES = [f"{t}|e{i}" for t in ("A", "B") for i in range(5)]


def _edges():
    edges = []
    for k in range(400):
        s = SOURCES[k % len(SOURCES)]
        # k and k + 300 share a source, a predicate and a timestamp, and differ in the target: a tie on o
        edges.append({"s": s, "p": "SEES" if k % 3 == 0 else "EMITS", "o": f"o{k % 7}", "ts": T0 + (k * 37) % 300,
                      "w": 1, "attrs": {"k": k}})
    # a source whose only event is late: absent under an early cutoff
    edges.append({"s": "A|late", "p": "EMITS", "o": "o1", "ts": T0 + 900, "w": 1})
    return edges


def brute(native, graph, sources=None, predicate=None, before=None, lookback=None):
    best = {}
    for e in query_window(native, graph, "", "", *ALL_TIME):
        if sources is not None and e["s"] not in sources:
            continue
        if predicate and e["p"] != predicate:
            continue
        if before is not None and e["ts"] >= before:
            continue
        if lookback is not None and e["ts"] < before - lookback:
            continue
        k = (e["ts"], e["p"], e["o"])
        if e["s"] not in best or k > best[e["s"]][0]:
            best[e["s"]] = (k, e)
    out = []
    for s in sorted(best):
        e = dict(best[s][1])
        e["attrs"] = edge_attrs(native, graph, e)
        out.append(e)
    return out


def latest(engine, graph, **kw):
    rows = engine.get_latest_edges(graph=graph, **kw)
    return [{"s": e["s"], "p": e["p"], "o": e["o"], "ts": e["ts"], "w": e["w"], "attrs": e["attrs"]} for e in rows]


@pytest.fixture(scope="module")
def loaded(graph_env):
    engine, native, graph = graph_env
    bulk_insert(native, graph, _edges())
    return engine, native, graph


@pytest.mark.parametrize(
    "kw",
    [{}, {"predicate": "SEES"}, {"before": T0 + 150}, {"predicate": "EMITS", "before": T0 + 120},
     {"before": T0 + 150, "lookback": 60}],
    ids=["plain", "predicate", "before", "predicate-before", "lookback"],
)
def test_latest_per_source_equals_brute_force(loaded, kw):
    engine, native, graph = loaded
    want = brute(native, graph, sources=SOURCES + ["A|late"], **kw)
    assert want
    assert latest(engine, graph, sources=SOURCES + ["A|late"], **kw) == want


def test_a_source_with_nothing_before_the_cutoff_is_absent(loaded):
    engine, _, graph = loaded
    got = latest(engine, graph, sources=["A|late", "nobody", "A|e0"], before=T0 + 300)
    assert [e["s"] for e in got] == ["A|e0"]


def test_a_prefix_and_every_source(loaded):
    engine, native, graph = loaded
    a = [s for s in SOURCES if s.startswith("A|")] + ["A|late"]
    assert latest(engine, graph, source_prefix="A|") == brute(native, graph, sources=a)
    assert latest(engine, graph) == brute(native, graph)
    assert latest(engine, graph, source_prefix="A|", before=T0 + 150) == brute(native, graph, sources=a, before=T0 + 150)


def test_the_newest_event_wins_ties_by_predicate_then_target(fresh_graph):
    engine, native, graph = fresh_graph
    bulk_insert(native, graph, [{"s": "x", "p": p, "o": o, "ts": T0, "w": 1} for p in ("A", "B") for o in ("o1", "o2")])
    got = latest(engine, graph, sources=["x"])
    assert [(e["p"], e["o"]) for e in got] == [("B", "o2")]


def _check(engine, native, graph):
    assert latest(engine, graph, sources=SOURCES + ["A|late"]) == brute(native, graph, sources=SOURCES + ["A|late"])
    assert latest(engine, graph) == brute(native, graph)
    for p in ("SEES", "EMITS"):
        assert latest(engine, graph, predicate=p) == brute(native, graph, predicate=p)


def test_the_index_stays_exact_through_deletes(fresh_graph):
    engine, native, graph = fresh_graph
    bulk_insert(native, graph, _edges())
    for e in brute(native, graph)[:4]:
        assert engine.delete_edge_temporal(e["s"], e["p"], e["o"], e["ts"], graph=graph)
        _check(engine, native, graph)


def test_the_index_stays_exact_through_purges(fresh_graph):
    engine, native, graph = fresh_graph
    bulk_insert(native, graph, _edges())
    engine.purge_raw_before(T0 + 290, ts_start=T0 + 200, graph=graph)
    _check(engine, native, graph)
    engine.purge_raw_before(T0 + 100, graph=graph)
    _check(engine, native, graph)


def test_the_index_stays_exact_through_a_node_erase(fresh_graph):
    engine, native, graph = fresh_graph
    bulk_insert(native, graph, _edges())
    engine.delete_nodes_by_prefix("o3", graph=graph)
    engine.delete_nodes_by_prefix("B|e1", graph=graph)
    assert not any(e["o"] == "o3" or e["s"] == "B|e1" for e in query_window(native, graph, "", "", *ALL_TIME))
    _check(engine, native, graph)


def test_a_graph_written_before_the_index_reads_and_rebuilds(fresh_graph):
    engine, native, graph = fresh_graph
    bulk_insert(native, graph, _edges())
    native.kill("^KG", "tlast", graph)  # what a graph written by 4.2 looks like
    bulk_insert(native, graph, [{"s": "A|e0", "p": "EMITS", "o": "o9", "ts": T0 + 50, "w": 1}])
    want = brute(native, graph, sources=SOURCES)
    assert latest(engine, graph, sources=SOURCES) == want
    assert latest(engine, graph, sources=SOURCES, before=T0 + 150) == brute(native, graph, sources=SOURCES, before=T0 + 150)
    with pytest.raises(TemporalWindowError, match="rebuild_temporal_latest"):
        engine.get_latest_edges(source_prefix="A|", graph=graph)
    assert engine.rebuild_temporal_latest(graph=graph) == len({(e["s"], e["p"]) for e in _edges()})
    _check(engine, native, graph)


def test_an_erased_graph_starts_a_complete_index_again(fresh_graph):
    engine, native, graph = fresh_graph
    bulk_insert(native, graph, _edges())
    native.kill("^KG", "tlast", graph)
    engine.erase_graph(graph)
    bulk_insert(native, graph, _edges())
    _check(engine, native, graph)


def test_the_server_call(loaded):
    import json

    _, native, graph = loaded
    out = json.loads(str(native.classMethodValue(
        "Graph.KG.TemporalWindow", "Latest", graph, json.dumps({"sources": ["A|e0", "zz"], "attrs": 1}))))
    assert [r["s"] for r in out["rows"]] == ["A|e0"] and out["next"] is None
    assert int(native.classMethodValue(INDEX, "RebuildLatest", graph)) > 0
