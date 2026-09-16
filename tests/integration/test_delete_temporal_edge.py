"""A temporal edge can be removed, one edge at a time.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_delete_temporal_edge.py

`Graph.KG.TemporalIndex` had no inverse for `InsertEdge`. Everything it offered
was time-based and coarse: `Purge` (all of it), `PurgeBefore`, `PurgeRawBefore`
(a timestamp range, raw stores only) and `PurgeBucketRange` (aggregates only,
and a *separate* call). So deleting one structural edge left its temporal
entries behind forever, and no caller could remove them without also removing
every other edge in the same window.

`InsertEdge` writes nine stores. Three of them cannot be decremented:

  `^KG("tagg", …, "min"/"max")` are running extremes. Removing the edge that set
  the current minimum cannot be undone arithmetically — the new minimum is a
  property of the survivors.

  the HLL sketch in `^KG("tagg", …, "hll")` only ever moves registers up. A
  HyperLogLog has no delete.

So the delete recomputes those from the bucket's surviving edges rather than
trying to invert them. That is the whole reason this is one method on
`TemporalIndex` and not a set of decrements at the call site: the caller cannot
know which stores are invertible and which have to be rebuilt.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "deltemporal_acme"
# A bucket is 300s wide (TemporalIndex.BUCKET) and starts on a multiple of 300, so
# T1 is picked to be one of those multiples: all three timestamps land in bucket
# 5666667, which spans [1700000100, 1700000399].
T1 = 1_700_000_100
T2 = T1 + 60
T3 = T1 + 120


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


@pytest.fixture()
def kg(engine):
    import iris

    return iris.createIRIS(engine.conn)


def _get(kg, *subs):
    return kg.get("^KG", *subs)


def _agg(engine, source, predicate, metric, graph=GRAPH):
    return engine.get_temporal_aggregate(
        source, predicate, metric, T1 - 10, T3 + 10, graph=graph
    )


def _window(engine, source, predicate, graph=GRAPH):
    return engine.get_edges_in_window(source, predicate, T1 - 10, T3 + 10, graph=graph)


# --- the raw stores ----------------------------------------------------------


def test_deleting_a_temporal_edge_removes_it_from_the_window(engine):
    """The whole point: one edge out, the others still there."""
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "c", timestamp=T2, graph=GRAPH)
    assert len(_window(engine, "a", "SAW")) == 2

    assert engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH) is True

    remaining = _window(engine, "a", "SAW")
    assert len(remaining) == 1
    assert remaining[0]["o"] == "c"


def test_deleting_a_temporal_edge_removes_the_reverse_index(engine, kg):
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)
    assert _get(kg, "tin", GRAPH, T1, "b", "SAW", "a") is not None

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _get(kg, "tout", GRAPH, T1, "a", "SAW", "b") is None
    assert _get(kg, "tin", GRAPH, T1, "b", "SAW", "a") is None, (
        "the reverse index outlived the edge, so inbound window queries still "
        "answer with a deleted edge"
    )


def test_deleting_a_temporal_edge_removes_its_attributes(engine, kg):
    engine.create_edge_temporal(
        "a", "SAW", "b", timestamp=T1, attrs={"site": "clinic"}, graph=GRAPH
    )
    assert _get(kg, "edgeprop", GRAPH, T1, "a", "SAW", "b", "site") is not None

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _get(kg, "edgeprop", GRAPH, T1, "a", "SAW", "b", "site") is None, (
        "^KG('edgeprop') survived the edge it described"
    )


def test_deleting_an_absent_edge_reports_it_and_changes_nothing(engine):
    """A no-op must be distinguishable from a delete, or retries hide bugs."""
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)

    assert engine.delete_edge_temporal("a", "SAW", "zzz", T1, graph=GRAPH) is False
    assert len(_window(engine, "a", "SAW")) == 1


def test_deleting_twice_is_idempotent_and_does_not_drive_counters_negative(engine, kg):
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "c", timestamp=T2, graph=GRAPH)

    assert engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH) is True
    assert engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH) is False

    bucket = T1 // 300
    assert int(_get(kg, "bucket", GRAPH, bucket, "a")) == 1, (
        "the repeated delete decremented the bucket counter a second time, which "
        "reads downstream as fresh drift rather than as a no-op"
    )


# --- graph isolation ---------------------------------------------------------


def test_deleting_in_one_graph_leaves_the_same_edge_in_another(engine):
    """The decisive fixture from spec-223, applied to deletion."""
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph="deltemporal_other")

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _window(engine, "a", "SAW", graph=GRAPH) == []
    assert len(_window(engine, "a", "SAW", graph="deltemporal_other")) == 1, (
        "deleting one graph's temporal edge removed another graph's edge at the "
        "same coordinates"
    )


def test_the_default_graph_is_reachable_by_the_delete(engine):
    engine.create_edge_temporal("d1", "SAW", "d2", timestamp=T1)

    assert engine.delete_edge_temporal("d1", "SAW", "d2", T1) is True
    assert engine.get_edges_in_window("d1", "SAW", T1 - 10, T3 + 10) == []


# --- the counters and the aggregates ----------------------------------------


def test_the_bucket_counter_is_decremented(engine, kg):
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "c", timestamp=T2, graph=GRAPH)
    bucket = T1 // 300
    assert int(_get(kg, "bucket", GRAPH, bucket, "a")) == 2

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert int(_get(kg, "bucket", GRAPH, bucket, "a")) == 1


def test_the_last_edge_in_a_bucket_removes_the_counter_rather_than_zeroing_it(
    engine, kg
):
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)
    bucket = T1 // 300

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _get(kg, "bucket", GRAPH, bucket, "a") is None, (
        "a zero counter still claims the node appears in that bucket"
    )
    assert _get(kg, "tagg", GRAPH, bucket, "a", "SAW", "count") is None, (
        "the aggregate outlived every edge it aggregated"
    )


def test_count_and_sum_track_the_surviving_edges(engine):
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, weight=2.0, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "c", timestamp=T2, weight=5.0, graph=GRAPH)
    assert _agg(engine, "a", "SAW", "count") == 2
    assert _agg(engine, "a", "SAW", "sum") == 7.0

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _agg(engine, "a", "SAW", "count") == 1
    assert _agg(engine, "a", "SAW", "sum") == 5.0


def test_min_is_recomputed_when_the_edge_that_set_it_is_removed(engine):
    """A running extreme cannot be decremented; it has to be rebuilt.

    This is the case a caller could not get right on its own, and the reason the
    delete belongs inside TemporalIndex rather than at the call site.
    """
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, weight=1.0, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "c", timestamp=T2, weight=4.0, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "d", timestamp=T3, weight=9.0, graph=GRAPH)
    assert _agg(engine, "a", "SAW", "min") == 1.0

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _agg(engine, "a", "SAW", "min") == 4.0, (
        "min still reports the weight of the deleted edge — a running extreme was "
        "treated as if it could be decremented"
    )


def test_max_is_recomputed_when_the_edge_that_set_it_is_removed(engine):
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, weight=1.0, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "c", timestamp=T2, weight=4.0, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "d", timestamp=T3, weight=9.0, graph=GRAPH)
    assert _agg(engine, "a", "SAW", "max") == 9.0

    engine.delete_edge_temporal("a", "SAW", "d", T3, graph=GRAPH)

    assert _agg(engine, "a", "SAW", "max") == 4.0, (
        "max still reports the weight of the deleted edge"
    )


def test_the_distinct_count_sketch_is_rebuilt_not_decremented(engine):
    """HyperLogLog registers only move up, so the sketch has to be rebuilt.

    Stated as rebuild-equivalence against a second graph that only ever held the
    survivors, rather than as a cardinality. `GetDistinctCount` merges 16 registers
    with the harmonic-mean estimator and has no small-range correction, so three
    distinct targets estimate to roughly twelve — no exact count is assertable here.
    Equivalence is the actual contract ("after the delete, the bucket's sketch is
    the sketch of the edges that remain") and it survives any change to the
    estimator.
    """
    survivors = "deltemporal_sketch_survivors"
    for i, target in enumerate(("b", "c", "d")):
        engine.create_edge_temporal("a", "SAW", target, timestamp=T1 + i, graph=GRAPH)
    for i, target in enumerate(("b", "c")):
        engine.create_edge_temporal(
            "a", "SAW", target, timestamp=T1 + i, graph=survivors
        )

    def estimate(graph):
        return engine.get_distinct_count("a", "SAW", T1 - 10, T3 + 10, graph=graph)

    assert estimate(GRAPH) != estimate(survivors), (
        "the fixture is not decisive: target 'd' hashes into a register that 'b' or "
        "'c' had already raised, so a sketch that was never rebuilt would pass. "
        "Pick a different target."
    )

    engine.delete_edge_temporal("a", "SAW", "d", T1 + 2, graph=GRAPH)

    assert estimate(GRAPH) == estimate(survivors), (
        "the distinct-target sketch still counts the deleted edge's target; an HLL "
        "cannot be decremented, so it must be rebuilt from the survivors"
    )


# --- the structural shadow --------------------------------------------------


def test_the_structural_shadow_survives_while_another_temporal_edge_does(engine, kg):
    """The shadow says "this edge exists at some time", so it outlives one time."""
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T2, graph=GRAPH)

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _get(kg, "out", GRAPH, "a", "SAW", "b") is not None, (
        "removing one timestamp removed the structural shadow, so traversal can "
        "no longer see an edge that still exists at T2"
    )


def test_the_structural_shadow_goes_with_the_last_temporal_edge(engine, kg):
    engine.create_edge_temporal("a", "SAW", "b", timestamp=T1, graph=GRAPH)

    engine.delete_edge_temporal("a", "SAW", "b", T1, graph=GRAPH)

    assert _get(kg, "out", GRAPH, "a", "SAW", "b") is None, (
        "the structural shadow outlived every temporal edge behind it, so "
        "traversal answers with an edge that no longer exists at any time"
    )
    assert _get(kg, "in", GRAPH, "b", "SAW", "a") is None
    assert _get(kg, "deg", GRAPH, "a") is None, (
        "the out-degree counter outlived the only edge it counted"
    )
