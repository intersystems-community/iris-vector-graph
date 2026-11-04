"""4.6.0 R213: ``bulk_load_session().add_edges`` writes the ``source_id``/``predicate``/``target_id`` dicts every other
edge loader takes, and an edge shape it cannot write raises before anything is written.

Walkthrough 2 (B1) ran the documented dict shape through the session and got back 0, with no edge and no error.
By hand: the session below writes the node ``co`` and two edges, ``ann -WORKS_AT-> co`` (dict shape) and
``ben -KNOWS-> co`` (dict shape with the session's default predicate), so the read answers ``[[ann, WORKS_AT],
[ben, KNOWS]]`` and the session's stats count 2 edges. An edge keyed ``from``/``to`` raises ``ValueError`` and leaves
no edge from ``cat``.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r213_{run}_{k}" for k in ("ann", "ben", "cat", "co")}
    for k in ("ann", "ben", "cat"):
        assert e.create_node(ids[k], labels=["P213"])
    yield e, ids
    for nid in ids.values():
        try:
            e.delete_node(nid, graph=None)
        except Exception:
            pass


def test_session_add_edges_writes_the_source_id_shape(g):
    e, ids = g
    with e.bulk_load_session() as s:
        s.add_nodes([{"id": ids["co"], "labels": ["Co213"]}])
        n = s.add_edges(
            [
                {"source_id": ids["ann"], "predicate": "WORKS_AT", "target_id": ids["co"]},
                {"source_id": ids["ben"], "target_id": ids["co"]},
            ]
        )
        stats = dict(s.stats)
    assert n == 2
    assert stats["edges"] == 2
    r = e.execute_cypher(
        "MATCH (a)-[r]->(c {node_id: $co}) RETURN a.node_id, type(r) ORDER BY a.node_id", {"co": ids["co"]}
    )
    assert r.error is None
    assert r.rows == [[ids["ann"], "WORKS_AT"], [ids["ben"], "KNOWS"]]


def test_session_add_edges_refuses_an_unknown_shape_and_writes_nothing(g):
    e, ids = g
    with pytest.raises(ValueError, match="source_id"):
        with e.bulk_load_session() as s:
            s.add_edges([{"from": ids["cat"], "to": ids["co"]}])
    r = e.execute_cypher("MATCH (a {node_id: $c})-[r]->() RETURN count(r)", {"c": ids["cat"]})
    assert r.rows == [[0]]


def test_bulk_create_edges_refuses_a_weight_it_would_drop(g):
    e, ids = g
    with pytest.raises(ValueError, match="'weight'"):
        e.bulk_create_edges(
            [{"source_id": ids["cat"], "predicate": "R213", "target_id": ids["co"], "weight": 5.0}], auto_sync=False
        )
    r = e.execute_cypher("MATCH (a {node_id: $c})-[r:R213]->() RETURN count(r)", {"c": ids["cat"]})
    assert r.rows == [[0]]
