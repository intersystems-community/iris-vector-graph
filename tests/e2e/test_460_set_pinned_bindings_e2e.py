"""4.6.0 R186: a SET, REMOVE, DELETE or MERGE ON MATCH writes every node and relationship its MATCH bound.

Each statement of a write re-ran the MATCH as its own subquery, so once one statement had changed what the MATCH
filters on, the later ones found nothing: `MATCH (n:X {k: 1}) SET n.k = 2, n.q = 3` never wrote `q`. The bound ids
are now read once, before the first write of the query part, and every later statement writes those ids.

Every case builds its own nodes (ids and labels unique to the run) and is worked by hand:

- `MATCH (n {k: 1}) SET n.k = 2, n.q = 3`: `{k: 2, q: 3}`
- `... SET n.k = 2 REMOVE n.keep`: `{k: 2}`
- `... SET n.k = 2 SET n.q = 3`: `{k: 2, q: 3}`
- `MATCH (n:L {id}) REMOVE n:L SET n.q = 3`: no labels, `{q: 3}`
- A `{k: 1}`, B `{}`; `MATCH (a {k: 1}), (b) SET b.k = 2` binds (A, A) and (A, B): A and B both get `k: 2`
- `MATCH ()-[r:T {k: 1}]->() SET r.k = 2, r.q = 3`: the edge holds `{k: 2, q: 3}`
- one node `{k: 1}`; `MERGE (n {k: 1}) ON MATCH SET n.k = 2, n.q = 3`: `{k: 2, q: 3}`, still one node
- no node; `MERGE (n {k: 1}) ON MATCH SET n.q = 3`: the node MERGE creates is `{k: 1}`, ON MATCH does not fire
- `MATCH (n {k: 1}) SET n.k = 2 DELETE n` (and DETACH DELETE with an edge, and a relationship): gone
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    made: list = []

    def run_q(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    def node(case, **props):
        """A node labelled `L<case><run>` with `props` and a run-unique id; returns (id, label)."""
        nid, label = f"r186_{run}_{case}_{len(made)}", f"L{case}{run}"
        made.append(nid)
        assert e.create_node(nid, labels=[label], properties=props)
        return nid, label

    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, run, run_q, node, made
    for nid in made:
        e.delete_node(nid)


def _props(run_q, nid):
    rows = run_q("MATCH (n) WHERE n.id = $id RETURN properties(n)", {"id": nid})
    assert len(rows) == 1, rows
    props = dict(rows[0][0])  # 4.6.0 R311: properties() answers the dict
    props.pop("id")
    return props


def _count(run_q, nid):
    return run_q("MATCH (n) WHERE n.id = $id RETURN count(n)", {"id": nid})[0][0]


@pytest.mark.parametrize(
    "tail, want",
    [
        ("SET n.k = 2, n.q = 3", {"k": 2, "keep": "x", "q": 3}),
        ("SET n.k = 2 REMOVE n.keep", {"k": 2}),
        ("SET n.k = 2 SET n.q = 3", {"k": 2, "keep": "x", "q": 3}),
        ("SET n.k = 2, n += {q: 3}", {"k": 2, "keep": "x", "q": 3}),
        ("SET n.k = 2, n:Extra186", {"k": 2, "keep": "x"}),
    ],
    ids=["two_items", "set_then_remove", "two_set_clauses", "item_then_map", "item_then_label"],
)
def test_node_writes_after_the_filter_key_changes(g, tail, want):
    _, _, run_q, node, _ = g
    nid, label = node("n", k=1, keep="x")
    run_q(f"MATCH (n:{label} {{k: 1}}) WHERE n.id = $id {tail}", {"id": nid})
    assert _props(run_q, nid) == want


def test_label_added_after_the_filter_key_changes(g):
    _, _, run_q, node, _ = g
    nid, label = node("lb", k=1)
    run_q(f"MATCH (n:{label} {{k: 1}}) WHERE n.id = $id SET n.k = 2, n:Extra186", {"id": nid})
    rows = run_q("MATCH (n) WHERE n.id = $id RETURN labels(n)", {"id": nid})
    assert sorted(rows[0][0]) == sorted([label, "Extra186"])


def test_remove_label_then_set(g):
    _, _, run_q, node, _ = g
    nid, label = node("rl")
    run_q(f"MATCH (n:{label}) WHERE n.id = $id REMOVE n:{label} SET n.q = 3", {"id": nid})
    rows = run_q("MATCH (n) WHERE n.id = $id RETURN labels(n), n.q", {"id": nid})
    assert [[rows[0][0], rows[0][1]]] == [[[], 3]]


def test_cross_product_sets_every_bound_node(g):
    _, _, run_q, node, _ = g
    a, label = node("cp", k=1)
    b, _ = node("cp")
    run_q(f"MATCH (a:{label} {{k: 1}}), (b:{label}) SET b.k = 2")
    assert _props(run_q, a) == {"k": 2}
    assert _props(run_q, b) == {"k": 2}


def test_relationship_two_items(g):
    e, _, run_q, node, _ = g
    a, label = node("re")
    t, _ = node("re")
    assert e.create_edge(a, "T186", t, qualifiers={"k": 1})
    run_q(f"MATCH (:{label} {{id: $a}})-[r:T186 {{k: 1}}]->() SET r.k = 2, r.q = 3", {"a": a})
    rows = run_q(f"MATCH (:{label} {{id: $a}})-[r:T186]->() RETURN properties(r)", {"a": a})
    assert [r[0] for r in rows] == [{"k": 2, "q": 3}]


def test_merge_on_match_two_items(g):
    _, _, run_q, node, _ = g
    nid, label = node("mm", k=1)
    run_q(f"MERGE (n:{label} {{k: 1}}) ON MATCH SET n.k = 2, n.q = 3")
    assert _props(run_q, nid) == {"k": 2, "q": 3}
    assert run_q(f"MATCH (n:{label}) RETURN count(n)") == [[1]]


def test_merge_on_match_does_not_fire_on_the_node_merge_creates(g):
    _, run, run_q, _, _ = g
    label = f"Lmc{run}"
    try:
        run_q(f"MERGE (n:{label} {{k: 1}}) ON MATCH SET n.q = 3")
        assert run_q(f"MATCH (n:{label}) RETURN n.k, n.q") == [[1, None]]
    finally:
        run_q(f"MATCH (n:{label}) DETACH DELETE n")


def test_delete_after_set(g):
    _, _, run_q, node, _ = g
    nid, label = node("dl", k=1)
    run_q(f"MATCH (n:{label} {{k: 1}}) SET n.k = 2 DELETE n")
    assert _count(run_q, nid) == 0


def test_detach_delete_after_set(g):
    e, _, run_q, node, _ = g
    a, label = node("dd", k=1)
    t, _ = node("dd")
    assert e.create_edge(a, "T186D", t)
    run_q(f"MATCH (n:{label} {{k: 1}}) SET n.k = 2 DETACH DELETE n")
    assert _count(run_q, a) == 0
    assert run_q(f"MATCH (:{label})-[r:T186D]-() RETURN count(r)") == [[0]]
    assert _count(run_q, t) == 1


def test_relationship_delete_after_set(g):
    e, _, run_q, node, _ = g
    a, label = node("rd")
    t, _ = node("rd")
    assert e.create_edge(a, "T186R", t, qualifiers={"k": 1})
    run_q(f"MATCH (:{label})-[r:T186R {{k: 1}}]->() SET r.k = 2 DELETE r")
    assert run_q(f"MATCH (:{label})-[r:T186R]->() RETURN count(r)") == [[0]]


def test_set_after_a_relationship_delete(g):
    """`DELETE r` takes the MATCH's row with it; the SET after it still writes the node the row bound."""
    e, _, run_q, node, _ = g
    a, label = node("ds")
    t, _ = node("ds")
    assert e.create_edge(a, "T186S", t)
    run_q(f"MATCH (n:{label} {{id: $a}})-[r:T186S]->() DELETE r SET n.done = true", {"a": a})
    assert run_q(f"MATCH (:{label})-[r:T186S]->() RETURN count(r)") == [[0]]
    assert _props(run_q, a) == {"done": True}
