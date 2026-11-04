"""4.6.0 R181: a node `store_node` wrote is found by its `id` property, as one `create_node` wrote is.

`store_node('x', properties={...})` wrote no `id` row, so `WHERE n.id = 'x'` answered nothing and `RETURN n.id` null
(a pattern's `{id: 'x'}` compares the node id, and did find it). The graph, worked by hand (label L181, ids unique to the run):

- s_plain: `store_node(id, properties={name: 'p'}, labels=['L181'])` -> `id` is the node id (`{id: x}` in a
  pattern compares the node id, so it found the node; `n.id`, and `WHERE n.id = x`, read the missing property)
- s_bare:  `store_node(id, labels=['L181'])` (no properties) -> `id` is the node id
- s_own:   `store_node(id, properties={id: 'own-<run>'}, labels=['L181'])` -> `id` is 'own-<run>', one row
- s_kept:  `create_node(id, properties={id: 'kept-<run>'})`, then `store_node(id, properties={name: 'k'})` -> the id
  the node held stays 'kept-<run>'
- s_none:  `store_node(id, properties={gone: 'g'})`, then `store_node(id, properties={gone: None})` -> `gone` absent
  (the text 'None' was stored)
- s_graph: `store_node(id, properties={name: 'g'}, labels=['L181'], graph=<g>)` -> `id` in graph <g> only
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r181_{run}_{k}" for k in ("s_plain", "s_bare", "s_own", "s_kept", "s_none", "s_graph")}
    graph = f"g181_{run}"
    assert e.store_node(ids["s_plain"], properties={"name": "p"}, labels=["L181"])
    assert e.store_node(ids["s_bare"], labels=["L181"])
    assert e.store_node(ids["s_own"], properties={"id": f"own-{run}"}, labels=["L181"])
    assert e.create_node(ids["s_kept"], labels=["L181"], properties={"id": f"kept-{run}"})
    assert e.store_node(ids["s_kept"], properties={"name": "k"})
    assert e.store_node(ids["s_none"], properties={"gone": "g"}, labels=["L181"])
    assert e.store_node(ids["s_none"], properties={"gone": None})
    assert e.store_node(ids["s_graph"], properties={"name": "g"}, labels=["L181"], graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids, run, graph
    for nid in ids.values():
        e.delete_node(nid)
    with contextlib.suppress(Exception):
        e.erase_graph(graph)


def _rows(e, q, params=None):
    r = e.execute_cypher(q, dict(params or {}))
    assert r.error is None, (q, r.error)
    return r.rows


def test_match_by_id_finds_a_stored_node(g):
    """`{id: x}` in a pattern compares the node id; `n.id` reads the property, which is what was missing."""
    e, ids, _, _ = g
    for k in ("s_plain", "s_bare"):
        assert _rows(e, "MATCH (n:L181 {id: $id}) RETURN n.id", {"id": ids[k]}) == [[ids[k]]], k
        assert _rows(e, "MATCH (n:L181) WHERE n.id = $id RETURN n.id", {"id": ids[k]}) == [[ids[k]]], k


def test_return_id_answers_the_node_id(g):
    e, ids, _, _ = g
    assert _rows(e, "MATCH (n:L181) WHERE n.name = 'p' RETURN n.id") == [[ids["s_plain"]]]


def test_a_given_id_is_written_as_given_and_once(g, iris_connection):
    e, ids, run, _ = g
    assert _rows(e, "MATCH (n:L181) WHERE n.id = $id RETURN n.name", {"id": f"own-{run}"}) == [[None]]
    cur = iris_connection.cursor()
    cur.execute(f"SELECT val FROM {e._t('rdf_props')} WHERE s = ? AND \"key\" = 'id'", [ids["s_own"]])
    assert [r[0] for r in cur.fetchall()] == [f"own-{run}"]


def test_an_id_the_node_holds_is_kept(g):
    e, ids, run, _ = g
    assert _rows(e, "MATCH (n:L181) WHERE n.id = $id RETURN n.name", {"id": f"kept-{run}"}) == [["k"]]


def test_a_none_value_removes_the_property(g, iris_connection):
    e, ids, _, _ = g
    cur = iris_connection.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {e._t('rdf_props')} WHERE s = ? AND \"key\" = 'gone'", [ids["s_none"]])
    assert cur.fetchone()[0] == 0
    assert e.get_node(ids["s_none"]).get("gone") is None


def test_a_named_graph_node_holds_its_id_in_that_graph(g, iris_connection):
    e, ids, _, graph = g
    q = f"USE GRAPH '{graph}' MATCH (n:L181) WHERE n.id = $id RETURN n.name"
    assert _rows(e, q, {"id": ids["s_graph"]}) == [["g"]]
    cur = iris_connection.cursor()
    cur.execute(f"SELECT graph_id FROM {e._t('rdf_props')} WHERE s = ? AND \"key\" = 'id'", [ids["s_graph"]])
    assert [r[0] for r in cur.fetchall()] == [graph]
