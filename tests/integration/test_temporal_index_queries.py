"""Line coverage for the Graph.KG.TemporalIndex readers and purges the suite never called.

Each case calls the ObjectScript method directly on a small fixed graph and asserts the
result a reader of the method would predict, so an unexecuted branch shows up as a
wrong answer and not only as a missing line. Runs against the enterprise container
(``IVG_TEST_CONTAINER=ivg-iris-enterprise``); the fixture graph lives under its own
graph id and is removed with ``Purge`` at the end.
"""

from __future__ import annotations

import json
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

TI = "Graph.KG.TemporalIndex"
CALLS = "CALLS"

# (source, predicate, target, timestamp, weight); all in one 300 s bucket (1000..1030).
EDGES = [
    ("a", CALLS, "b", 1000, 2.0),
    ("a", CALLS, "c", 1010, 4.0),
    ("d", CALLS, "b", 1020, 6.0),
    ("a", "OTHER", "e", 1030, 1.0),
]


@pytest.fixture
def ti(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    store = eng._store
    graph = f"t_ti_{uuid.uuid4().hex[:8]}"

    class Handle:
        def call(self, method, *args):
            return store._call_classmethod(TI, method, graph, *args)

        def raw(self, method, *args):
            return store._call_classmethod(TI, method, *args)

    h = Handle()
    h.graph = graph
    for s, p, o, ts, w in EDGES:
        store._call_classmethod(TI, "InsertEdge", graph, s, p, o, str(ts), str(w))
    yield h
    store._iris_obj().classMethodVoid(TI, "Purge")


def _json(value):
    return json.loads(value)


def test_query_window_sources_dedupes_and_filters_by_predicate(ti):
    assert _json(ti.call("QueryWindowSources", CALLS, 990, 1025)) == ["a", "d"]
    # no predicate: every source with any edge in the window
    assert _json(ti.call("QueryWindowSources", "", 1025, 1040)) == ["a"]
    # the window end is inclusive of ts <= tsEnd and nothing later
    assert _json(ti.call("QueryWindowSources", CALLS, 1011, 1019)) == []


def test_query_window_inbound_filters_target_and_predicate(ti):
    rows = _json(ti.call("QueryWindowInbound", "b", "", 0, 2000))
    assert sorted((r["s"], r["o"], r["ts"], r["w"]) for r in rows) == [
        ("a", "b", 1000, 2.0),
        ("d", "b", 1020, 6.0),
    ]
    assert _json(ti.call("QueryWindowInbound", "b", "OTHER", 0, 2000)) == []
    everything = _json(ti.call("QueryWindowInbound", "", "", 0, 2000))
    assert len(everything) == len(EDGES)
    # window end cuts later edges
    assert len(_json(ti.call("QueryWindowInbound", "", "", 0, 1010))) == 2


def test_bucket_group_targets_dedupe_and_predicate_filter(ti):
    assert _json(ti.call("GetBucketGroupTargets", "a", CALLS, 0, 2000)) == ["b", "c"]
    assert _json(ti.call("GetBucketGroupTargets", "a", "", 0, 2000)) == ["b", "c", "e"]
    assert _json(ti.call("GetBucketGroupTargets", "nobody", "", 0, 2000)) == []
    assert _json(ti.call("GetBucketGroupTargets", "a", CALLS, 1005, 2000)) == ["c"]


def test_bucket_groups_aggregate_per_source_and_predicate(ti):
    rows = {(r["source"], r["predicate"]): r for r in _json(ti.call("GetBucketGroups", "", 0, 2000))}
    assert set(rows) == {("a", CALLS), ("a", "OTHER"), ("d", CALLS)}
    a = rows[("a", CALLS)]
    assert (a["count"], a["sum"], a["min"], a["max"]) == (2, 6, 2, 4)
    assert a["avg"] == pytest.approx(3.0)
    only_calls = _json(ti.call("GetBucketGroups", CALLS, 0, 2000))
    assert {(r["source"], r["predicate"]) for r in only_calls} == {("a", CALLS), ("d", CALLS)}
    by_prefix = _json(ti.call("GetBucketGroups", "", 0, 2000, "d"))
    assert [(r["source"], r["count"]) for r in by_prefix] == [("d", 1)]


@pytest.mark.parametrize(
    "metric,expected",
    [("count", 2), ("sum", 6), ("min", 2), ("max", 4), ("avg", 3), ("median", "")],
)
def test_get_aggregate_metrics(ti, metric, expected):
    got = ti.call("GetAggregate", "a", CALLS, metric, 0, 2000)
    if expected == "":
        assert got in ("", None)  # an unknown metric answers empty
    else:
        assert float(got) == expected


def test_get_aggregate_empty_window_has_no_min_max_avg(ti):
    for metric in ("min", "max", "avg"):
        assert ti.call("GetAggregate", "a", CALLS, metric, 10_000, 20_000) in ("", None)
    assert float(ti.call("GetAggregate", "a", CALLS, "count", 10_000, 20_000)) == 0


def test_find_bursts_reports_nodes_at_or_over_the_threshold(ti):
    velocity = int(ti.call("GetVelocity", "a", 100, 1040))
    assert velocity >= 2
    bursts = {b["id"]: b["velocity"] for b in _json(ti.call("FindBursts", CALLS, 100, 2, 1040))}
    assert bursts.get("a") == velocity
    assert _json(ti.call("FindBursts", CALLS, 100, 10**6, 1040)) == []


def test_purge_raw_before_deletes_below_the_end_and_keeps_the_rest(ti):
    assert ti.call("PurgeRawBefore", 1015) == "2:0"
    assert _json(ti.call("QueryWindowSources", "", 0, 1015)) == []
    assert _json(ti.call("QueryWindowSources", CALLS, 0, 2000)) == ["d"]
    # the inbound mirror went with it
    assert [r["s"] for r in _json(ti.call("QueryWindowInbound", "b", "", 0, 2000))] == ["d"]


def test_purge_raw_before_honours_a_start_bound(ti):
    # ts < tsStart is counted as skipped and left in place
    assert ti.call("PurgeRawBefore", 1025, 1010) == "2:1"
    assert _json(ti.call("QueryWindowSources", CALLS, 0, 1005)) == ["a"]
    assert _json(ti.call("QueryWindowSources", "", 1030, 1030)) == ["a"]


def test_get_distinct_count_over_a_window(ti):
    n = int(ti.call("GetDistinctCount", "a", CALLS, 0, 2000))
    assert n >= 1
    assert int(ti.call("GetDistinctCount", "nobody", CALLS, 0, 2000)) == 0


def test_intern_label_set_is_canonical_and_order_independent(ti):
    one = ti.raw("InternLabelSet", '{"b":"2","a":"1"}')
    two = ti.raw("InternLabelSet", '{"a":"1","b":"2"}')
    assert one == two and one != ""
    assert ti.raw("InternLabelSet", '{"a":"1"}') != one
