"""Spec 231 FR-016 / SC-004 — personalized PageRank walks only the graph it is given.

`Graph.KG.PageRank.RunJson` began with `Set pGraph = 0`, so a PPR seeded in a named
graph walked the default graph's `^KG("out", 0, ...)`. The fixture puts the same seed
ID in two graphs with different successors: each walk must reach only its own
successor. Only IRIS can show it, because the defect was a hardwired subscript in the
server-side walk.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "IVG231_PPR_LINKS"


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 231 SC-004 asserts which ^KG subtree the server-side PPR walks. "
            "SKIP_IRIS_TESTS=true is not an acceptable outcome — start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection")


@pytest.fixture
def two_graphs(iris_connection):
    _require_iris(iris_connection)
    from iris_vector_graph import IRISGraphEngine

    # No embedding_dimension, no initialize_schema: see test_230_bfs_direction_and_limit_e2e.
    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    g = f"ivg231ppr:{run}"
    seed, near_g, near_default = (f"ivg231ppr_{run}_{s}" for s in ("seed", "g", "d"))

    for graph, succ in ((g, near_g), (None, near_default)):
        engine.create_node(seed, labels=["Ivg231Ppr"], graph=graph)
        engine.create_node(succ, labels=["Ivg231Ppr"], graph=graph)
        engine.create_edge(seed, PRED, succ, graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()

    yield engine, g, seed, near_g, near_default

    cursor = iris_connection.cursor()
    try:
        for graph in (g, ""):
            with contextlib.suppress(Exception):
                engine.delete_edge(seed, PRED, near_g if graph else near_default, graph=graph or None)
        for nid in (seed, near_g, near_default):
            for sql in (
                "DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?",
                "DELETE FROM Graph_KG.rdf_labels WHERE s=?",
                "DELETE FROM Graph_KG.nodes WHERE node_id=?",
            ):
                with contextlib.suppress(Exception):
                    cursor.execute(sql, (nid, nid) if "o_id" in sql else (nid,))
        with contextlib.suppress(Exception):
            iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _reached(scores) -> set:
    if isinstance(scores, dict):
        return {k for k, v in scores.items() if v and v > 0}
    return {row[0] for row in scores if row[1] and row[1] > 0}


def test_named_graph_walk_stays_in_its_graph(two_graphs):
    engine, g, seed, near_g, near_default = two_graphs
    reached = _reached(engine.kg_PERSONALIZED_PAGERANK([seed], graph=g))
    assert near_g in reached, f"named-graph successor missing: {reached}"
    assert near_default not in reached, (
        f"PPR over {g!r} reached the default graph's successor: {reached}"
    )


def test_default_graph_walk_stays_in_the_default_graph(two_graphs):
    engine, g, seed, near_g, near_default = two_graphs
    reached = _reached(engine.kg_PERSONALIZED_PAGERANK([seed]))
    assert near_default in reached, f"default-graph successor missing: {reached}"
    assert near_g not in reached, f"default-graph PPR reached {g!r}: {reached}"


def test_runjson_takes_the_graph_directly(two_graphs, iris_connection):
    """The ObjectScript entry point itself, bypassing every Python adapter."""
    import json

    import iris

    engine, g, seed, near_g, near_default = two_graphs
    native = iris.createIRIS(iris_connection)
    out = native.classMethodValue(
        "Graph.KG.PageRank", "RunJson", json.dumps([seed]), 0.85, 20, 0, 1.0, g
    )
    ids = {item["id"] for item in json.loads(out)}
    assert near_g in ids and near_default not in ids, ids
