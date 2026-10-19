"""Spec 252, T020: every writer path leaves the Adjacency Index equal to a recount from the tables.

One case per path in FR-005 that the engine exposes. Each writes through the path, then recounts
(tests/e2e/adjacency_recount.py): no entry missing, none without a row behind it. Weights are left at
their default here; the weight paths are Phase 3 (T031).

Run against a scratch namespace: `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .adjacency_recount import differences

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture(scope="module", autouse=True)
def declared(iris_connection):
    from iris_vector_graph.schema import GraphSchema

    status = GraphSchema.ensure_adjacency_index(iris_connection.cursor())
    iris_connection.commit()
    assert "skipped" not in status, status


@pytest.fixture
def ids(iris_master_cleanup):
    t = uuid.uuid4().hex[:8]
    return [f"wp_{t}_{i}" for i in range(4)]


@pytest.fixture
def engine(iris_connection):
    return IRISGraphEngine(iris_connection)


def _nodes(engine, ids, label="WP"):
    for n in ids:
        engine.create_node(n, labels=[label])


def test_create_edge_and_delete_edge(engine, iris_connection, ids):
    a, b = ids[:2]
    _nodes(engine, ids[:2])
    engine.create_edge(a, "R", b)
    assert differences(iris_connection, ids[:2]) == []
    engine.delete_edge(a, "R", b)
    assert differences(iris_connection, ids[:2]) == []


def test_create_edge_in_a_named_graph_then_erase_the_graph(engine, iris_connection, ids):
    a, b = ids[:2]
    g = f"g_{ids[0][-8:]}"
    engine.create_edge(a, "R", b, graph=g)
    assert differences(iris_connection, ids[:2], graphs=(0, g)) == []
    engine.erase_graph(g)
    assert differences(iris_connection, ids[:2], graphs=(0, g)) == []


def test_bulk_ingest_edges(engine, iris_connection, ids):
    _nodes(engine, ids)
    engine.bulk_ingest_edges([{"s": ids[i], "p": "R", "o": ids[i + 1]} for i in range(3)], predicate="R")
    assert differences(iris_connection, ids) == []


@pytest.mark.parametrize("auto_sync", [True, False])
def test_bulk_create_edges(engine, iris_connection, ids, auto_sync):
    _nodes(engine, ids)
    edges = [{"source_id": ids[i], "predicate": "R", "target_id": ids[i + 1]} for i in range(3)]
    engine.bulk_create_edges(edges, auto_sync=auto_sync)
    assert differences(iris_connection, ids) == []


def test_raw_sql_update_moves_the_entry(engine, iris_connection, ids):
    a, b, c = ids[:3]
    _nodes(engine, ids[:3])
    cur = iris_connection.cursor()
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'R', ?)", [a, b])
    cur.execute("UPDATE Graph_KG.rdf_edges SET o_id = ? WHERE s = ? AND o_id = ?", [c, a, b])
    cur.execute("UPDATE Graph_KG.rdf_edges SET p = 'S' WHERE s = ? AND o_id = ?", [a, c])
    iris_connection.commit()
    assert differences(iris_connection, ids[:3]) == []


def test_erase_all(engine, iris_connection, ids):
    _nodes(engine, ids)
    engine.bulk_ingest_edges([{"s": ids[i], "p": "R", "o": ids[i + 1]} for i in range(3)], predicate="R")
    engine.erase_all()
    assert differences(iris_connection, ids) == []


def test_delete_node_removes_its_edges(engine, iris_connection, ids):
    _nodes(engine, ids[:3])
    engine.create_edge(ids[0], "R", ids[1])
    engine.create_edge(ids[2], "R", ids[1])
    engine.delete_node(ids[1])
    assert differences(iris_connection, ids[:3]) == []


def test_materialize_and_retract_inference(engine, iris_connection, ids):
    a, c, d = ids[:3]
    rdf_type, sub = "rdf:type", "http://www.w3.org/2000/01/rdf-schema#subClassOf"
    engine.create_edge(a, rdf_type, c)
    engine.create_edge(c, sub, d)
    engine.materialize_inference()
    assert differences(iris_connection, ids[:3]) == []
    engine.retract_inference()
    assert differences(iris_connection, ids[:3]) == []


def test_snapshot_restore(engine, iris_connection, ids, tmp_path):
    _nodes(engine, ids)
    engine.bulk_ingest_edges([{"s": ids[i], "p": "R", "o": ids[i + 1]} for i in range(3)], predicate="R")
    path = str(tmp_path / "snap.ivg")
    engine.save_snapshot(path)
    engine.erase_all()
    engine.restore_snapshot(path)
    assert differences(iris_connection, ids) == []


def test_rebuild_from_the_rows_equals_the_recount(engine, iris_connection, ids):
    _nodes(engine, ids)
    engine.bulk_ingest_edges([{"s": ids[i], "p": "R", "o": ids[i + 1]} for i in range(3)], predicate="R")
    engine.sync()
    assert differences(iris_connection, ids) == []


WEIGHTED = "CALL ivg.shortestPath.weighted($a, $b, 'weight', 9999, 10, 'out', ['R']) YIELD totalCost RETURN totalCost"


def _cost(engine, a, b):
    r = engine.execute_cypher(WEIGHTED, {"a": a, "b": b})
    assert getattr(r, "error", None) is None, r.error
    return [row[-1] for row in r.rows]


def test_create_edge_weight_survives_a_rebuild(engine, iris_connection, ids):
    a, b = ids[:2]
    _nodes(engine, ids[:2])
    engine.create_edge(a, "R", b, weight=5.0)
    assert _cost(engine, a, b) == [5.0]
    engine.sync()
    assert _cost(engine, a, b) == [5.0]
    assert differences(iris_connection, ids[:2]) == []


def test_set_edge_weight_survives_a_rebuild(engine, iris_connection, ids):
    a, b = ids[:2]
    _nodes(engine, ids[:2])
    engine.create_edge(a, "R", b)
    assert engine.set_edge_weight(a, "R", b, 3.0) is True
    engine.sync()
    assert _cost(engine, a, b) == [3.0]
    assert differences(iris_connection, ids[:2]) == []


def test_set_edge_weight_on_no_edge_creates_nothing(engine, iris_connection, ids):
    a, b = ids[:2]
    _nodes(engine, ids[:2])
    assert engine.set_edge_weight(a, "R", b, 3.0) is False
    assert differences(iris_connection, ids[:2]) == []
