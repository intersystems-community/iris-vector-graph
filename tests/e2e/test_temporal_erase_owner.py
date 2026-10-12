"""Erasing content removes its temporal edges, through the temporal index's own entry points (ADR-0002, ADR-0004).

Before this, Graph.KG.Eraser killed ^KG("tout"/"tin"/"bucket"/"tagg"/"edgeprop") itself.
Now it calls Graph.KG.TemporalIndex.EraseGraph, EraseAll and EraseNodes. These tests check
what IRIS actually holds afterwards, through the engine's read paths, for each erase
entry point. The source-level guard is tests/unit/test_temporal_single_owner.py.

Not run by the package author: no IRIS in that worktree. The lead runs this file.
"""

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

START = 1600000000
END = 1800000000
PREDICATE = "CITED"


def _names():
    tag = uuid.uuid4().hex[:6]
    return f"TERA_{tag}_A", f"TERA_{tag}_B", f"TERA_{tag}"


def _targets(edges):
    """Every target id named in a window result, without caring about the row shape."""
    return {str(edge) for edge in (edges or [])}


def _window_out(engine, source, graph):
    return engine.get_edges_in_window(source, PREDICATE, START, END, graph=graph)


@pytest.fixture
def engine(iris_connection):
    return IRISGraphEngine(iris_connection)


@pytest.fixture
def graphs(engine):
    graph_a, graph_b, prefix = _names()
    yield graph_a, graph_b, prefix
    # Erase both even when a test failed part-way; erase is idempotent on an empty graph.
    for graph in (graph_a, graph_b):
        try:
            engine.erase_graph(graph)
        except Exception:
            pass


def test_erase_graph_removes_only_its_temporal_edges(engine, graphs):
    graph_a, graph_b, prefix = graphs
    x, y = f"{prefix}:x", f"{prefix}:y"
    engine.create_edge_temporal(x, PREDICATE, y, timestamp=1700000000, graph=graph_a)
    engine.create_edge_temporal(x, PREDICATE, y, timestamp=1700000000, graph=graph_b)

    assert _targets(_window_out(engine, x, graph_a)), "the edge was not written, so the erase proves nothing"
    engine.erase_graph(graph_a)

    assert not _targets(_window_out(engine, x, graph_a)), "A's temporal edge survived its erase"
    assert _targets(_window_out(engine, x, graph_b)), "erasing A removed B's temporal edge"


def test_erase_graph_leaves_no_temporal_aggregate_behind(engine, graphs):
    graph_a, _graph_b, prefix = graphs
    x, y = f"{prefix}:x", f"{prefix}:y"
    engine.create_edge_temporal(x, PREDICATE, y, timestamp=1700000000, graph=graph_a)
    before = engine.get_temporal_aggregate(x, PREDICATE, "count", START, END, graph=graph_a)
    assert before, "the aggregate was not written, so the erase proves nothing"

    engine.erase_graph(graph_a)

    after = engine.get_temporal_aggregate(x, PREDICATE, "count", START, END, graph=graph_a)
    assert not after, f"A's bucket aggregate survived its erase: {after!r}"


@pytest.mark.requires_clean_isolation
def test_erase_all_empties_the_temporal_index(engine, graphs):
    """erase_all clears every graph, so this test runs when nothing else holds temporal data."""
    graph_a, graph_b, prefix = graphs
    x, y = f"{prefix}:x", f"{prefix}:y"
    engine.create_edge_temporal(x, PREDICATE, y, timestamp=1700000000, graph=graph_a)
    engine.create_edge_temporal(x, PREDICATE, y, timestamp=1700000000, graph=graph_b)

    engine.erase_all()

    assert not _targets(_window_out(engine, x, graph_a))
    assert not _targets(_window_out(engine, x, graph_b))


def test_erase_node_removes_its_temporal_edges_both_directions(engine, graphs):
    graph_a, graph_b, prefix = graphs
    x, y, z = f"{prefix}:x", f"{prefix}:y", f"{prefix}:z"
    # X is a source (X->Y) and a target (Z->X) in graph A.
    engine.create_edge_temporal(x, PREDICATE, y, timestamp=1700000000, graph=graph_a)
    engine.create_edge_temporal(z, PREDICATE, x, timestamp=1700000100, graph=graph_a)
    # Graph B holds the same outgoing edge, which must survive.
    engine.create_edge_temporal(x, PREDICATE, y, timestamp=1700000000, graph=graph_b)

    assert _targets(_window_out(engine, x, graph_a))
    assert _targets(_window_out(engine, z, graph_a))

    engine.delete_node(x, graph=graph_a)

    assert not _targets(_window_out(engine, x, graph_a)), "X's outbound temporal edge survived"
    assert not _targets(_window_out(engine, z, graph_a)), "the temporal edge into X survived"
    assert _targets(_window_out(engine, x, graph_b)), "erasing X from A removed B's edge"
