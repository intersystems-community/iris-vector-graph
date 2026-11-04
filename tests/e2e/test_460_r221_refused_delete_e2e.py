"""4.6.0 R221: a Cypher DELETE IVG refuses raises ``GraphWriteError``, a ``RuntimeError``, and writes nothing.

Walkthrough 2 (W10): ``MATCH (p {node_id: 'hal'}) DELETE p`` on a node with relationships raised a bare
``Exception``. By hand: ``hal`` has one relationship to ``ida``, so the DELETE is refused, with or without
``raise_on_error``, and both nodes and the relationship are still there afterwards (1 relationship); ``DETACH DELETE``
then removes ``hal`` and its relationship (0 nodes named hal).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture()
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    hal, ida = f"r221_{run}_hal", f"r221_{run}_ida"
    assert e.create_node(hal, labels=["P221"])
    assert e.create_node(ida, labels=["P221"])
    assert e.create_edge(hal, "KNOWS", ida)
    yield e, hal, ida
    for nid in (hal, ida):
        try:
            e.delete_node(nid, graph=None)
        except Exception:
            pass


@pytest.mark.parametrize("raise_on_error", [False, True])
def test_a_refused_delete_raises_graph_write_error_and_writes_nothing(g, raise_on_error):
    from iris_vector_graph import GraphWriteError

    e, hal, ida = g
    with pytest.raises(GraphWriteError, match="Cannot delete node with existing relationships") as exc:
        e.execute_cypher("MATCH (p {node_id: $h}) DELETE p", {"h": hal}, raise_on_error=raise_on_error)
    assert isinstance(exc.value, RuntimeError)
    r = e.execute_cypher("MATCH (p {node_id: $h})-[r]->(q {node_id: $i}) RETURN count(r)", {"h": hal, "i": ida})
    assert r.rows == [[1]]


def test_detach_delete_still_deletes(g):
    e, hal, _ = g
    r = e.execute_cypher("MATCH (p {node_id: $h}) DETACH DELETE p", {"h": hal})
    assert r.error is None
    assert e.execute_cypher("MATCH (p {node_id: $h}) RETURN count(p)", {"h": hal}).rows == [[0]]
