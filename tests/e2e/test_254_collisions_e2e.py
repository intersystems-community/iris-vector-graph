"""Spec 254 US2 / FR-003: an "insert" at coordinates that already hold an edge is a reported collision.

Before: the second write overwrote ``tout``/``tin``, merged its attribute keys into ``edgeprop`` and was counted again
in ``bucket``, the ``tagg`` count and sum, and the sketch, so the aggregates described two edges where the raw store
held one. Now the second write changes no store and the batch says so: 1 written, 1 collision. Every assertion reads
the database afterwards; the strongest one is that the graph's digest equals a graph that only ever saw the first
write, aggregates included.

Run: ``IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972``.
"""

from __future__ import annotations

import json
import os
import uuid

import iris
import pytest

from iris_vector_graph._engine.temporal import TemporalWriteCount
from iris_vector_graph.engine import IRISGraphEngine

from .graph_snapshot import CACHES, compare, digest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.requires_database,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

TS = 1_760_000_000
FIRST = {"s": "inst:1", "p": "ACTION_AT", "o": "action:1", "ts": TS, "w": 2.0, "attrs": {"kind": "cfg", "a": 1}}
SECOND = {"s": "inst:1", "p": "ACTION_AT", "o": "action:1", "ts": TS, "w": 9.0, "attrs": {"kind": "deploy", "b": 2}}


@pytest.fixture
def env(iris_connection, iris_master_cleanup):
    tag = uuid.uuid4().hex[:6]
    engine = IRISGraphEngine(iris_connection)
    native = iris.createIRIS(iris_connection)
    return engine, native, f"g254c_{tag}", f"g254c_{tag}_ref"


def _window(engine, graph):
    return [(e["o"], float(e["w"])) for e in engine.get_edges_in_window("inst:1", "ACTION_AT", TS - 10, TS + 10, graph=graph)]


def _assert_first_write_only(engine, native, graph, ref):
    assert _window(engine, graph) == [("action:1", 2.0)]
    assert engine.get_edge_attrs(TS, "inst:1", "ACTION_AT", "action:1", graph=graph) == {"kind": "cfg", "a": 1}
    assert engine.get_temporal_aggregate("inst:1", "ACTION_AT", "count", TS - 10, TS + 10, graph=graph) == 1
    assert engine.get_temporal_aggregate("inst:1", "ACTION_AT", "sum", TS - 10, TS + 10, graph=graph) == 2.0
    assert engine.get_temporal_aggregate("inst:1", "ACTION_AT", "max", TS - 10, TS + 10, graph=graph) == 2.0
    # every store, aggregates and sketch included, equals a graph that saw the first write only
    engine.bulk_create_edges_temporal([FIRST], graph=ref)
    assert compare(digest(native, graph), digest(native, ref), exclude=CACHES) == []


def test_a_second_insert_in_another_call_is_a_collision(env):
    engine, native, g, ref = env
    first = engine.bulk_create_edges_temporal([FIRST], graph=g)
    second = engine.bulk_create_edges_temporal([SECOND], graph=g)
    assert isinstance(second, TemporalWriteCount)
    assert (first.written, first.collisions) == (1, 0)
    assert (second.written, second.collisions, second.skipped) == (0, 1, 0)
    assert second == 0
    _assert_first_write_only(engine, native, g, ref)


def test_a_second_insert_in_the_same_batch_is_a_collision(env):
    engine, native, g, ref = env
    n = engine.bulk_create_edges_temporal([FIRST, SECOND], graph=g)
    assert (int(n), n.collisions, n.skipped) == (1, 1, 0)
    _assert_first_write_only(engine, native, g, ref)


def test_the_default_mode_spelled_insert_is_the_same(env):
    engine, native, g, ref = env
    n = engine.bulk_create_edges_temporal([FIRST, SECOND], graph=g, mode="insert")
    assert (int(n), n.collisions) == (1, 1)
    _assert_first_write_only(engine, native, g, ref)


def test_the_single_edge_call_changes_nothing_either(env):
    engine, native, g, ref = env
    assert engine.create_edge_temporal("inst:1", "ACTION_AT", "action:1", timestamp=TS, weight=2.0,
                                       attrs={"kind": "cfg", "a": 1}, graph=g)
    # True: the call did not fail. The counts are the bulk path's (FR-003).
    assert engine.create_edge_temporal("inst:1", "ACTION_AT", "action:1", timestamp=TS, weight=9.0,
                                       attrs={"kind": "deploy", "b": 2}, graph=g)
    _assert_first_write_only(engine, native, g, ref)


def test_the_default_graph_too(env):
    engine, native, _, _ = env
    n = engine.bulk_create_edges_temporal([FIRST, SECOND])
    assert (int(n), n.collisions) == (1, 1)
    assert _window(engine, None) == [("action:1", 2.0)]
    assert engine.get_temporal_aggregate("inst:1", "ACTION_AT", "count", TS - 10, TS + 10) == 1


def test_update_is_unchanged(env):
    engine, _, g, _ = env
    engine.bulk_create_edges_temporal([FIRST], graph=g)
    n = engine.bulk_create_edges_temporal([SECOND], graph=g, mode="update")
    assert (int(n), n.collisions) == (1, 0)
    assert _window(engine, g) == [("action:1", 9.0)]
    assert engine.get_edge_attrs(TS, "inst:1", "ACTION_AT", "action:1", graph=g) == {"kind": "deploy", "b": 2}


def test_skip_is_counted_as_skipped_not_as_a_collision(env):
    engine, native, g, ref = env
    engine.bulk_create_edges_temporal([FIRST], graph=g)
    n = engine.bulk_create_edges_temporal([SECOND], graph=g, mode="skip")
    assert (int(n), n.collisions, n.skipped) == (0, 0, 1)
    _assert_first_write_only(engine, native, g, ref)


def test_a_collision_is_not_a_new_timestamp(env):
    """Same triple, another timestamp: written, not a collision."""
    engine, _, g, _ = env
    n = engine.bulk_create_edges_temporal([FIRST, dict(SECOND, ts=TS + 1)], graph=g)
    assert (int(n), n.collisions) == (2, 0)
    assert engine.get_temporal_aggregate("inst:1", "ACTION_AT", "count", TS - 10, TS + 10, graph=g) == 2


def test_millisecond_precision(env):
    _, native, graph, ref_graph = env
    ms = TS * 1000 + 7
    batch = [dict(FIRST, ts=ms), dict(SECOND, ts=ms)]
    res = str(native.classMethodValue("Graph.KG.TemporalIndexMS", "BulkInsertCounts", graph, json.dumps(batch), 0, ""))
    assert res == "1:1:0"
    native.classMethodValue("Graph.KG.TemporalIndexMS", "BulkInsertCounts", ref_graph, json.dumps(batch[:1]), 0, "")
    assert compare(digest(native, graph), digest(native, ref_graph), exclude=CACHES) == []
    assert int(native.classMethodValue("Graph.KG.TemporalIndexMS", "GetBucketCount", graph, ms // 300000, "inst:1")) == 1


def test_bulk_insert_still_returns_the_written_integer(env):
    _, native, graph, _ = env
    batch = json.dumps([FIRST, SECOND])
    assert int(native.classMethodValue("Graph.KG.TemporalIndex", "BulkInsert", graph, batch, 0, "")) == 1
