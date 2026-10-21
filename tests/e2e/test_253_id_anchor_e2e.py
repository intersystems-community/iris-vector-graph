"""Spec 253: a labelled node named by its id answers natively as the SQL path does.

DRKG's queries name their start by id (`(s:Entity {id: $x})`); so do many application reads. In shadow mode, against
the SQL path: the id as the anchor, in two graphs at once and in one (USE GRAPH); an id that names no node, or a node
without the label; an id with a property beside it; an id on the far node; a count of distinct nodes two hops out; a node with no `id` property row (as a bulk load
leaves it), which `{id: ..}` does not match on the SQL path and `{node_id: ..}` does.
And a variable-length hop from an id (DRKG's D_Q2, D_Q3), which the translator hands to the SQL path's BFS routes:
the native path answers it where it plans it.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
G1, G2 = f"ia{T}_g1", f"ia{T}_g2"
E = f"IaEntity{T}"


def n(k):
    return f"ia{T}_{k}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    for g in (G1, G2):
        for k, kind in (("a", "gene"), ("b", "drug"), ("c", "gene"), ("d", "disease")):
            e.create_node(n(k), labels=[E], properties={"kind": kind, "name": f"{k}_{g}"}, graph=g)
        e.create_edge(n("a"), "IA_R", n("b"), graph=g)
        e.create_edge(n("b"), "IA_S", n("c"), graph=g)
    e.create_edge(n("a"), "IA_R", n("c"), graph=G1)  # a's out-edges differ by graph
    e.create_edge(n("c"), "IA_S", n("d"), graph=G2)
    e.create_node(n("x"), labels=[f"IaOther{T}"], graph=G1)  # a node of another label
    e.create_edge(n("x"), "IA_R", n("a"), graph=G1)
    # a node as a bulk load leaves it: no `id` property row beside its identity, which {id: ..} requires on the SQL
    # path and {node_id: ..} does not
    e.create_node(n("bulk"), labels=[E], properties={"kind": "gene"}, graph=G1)
    e.create_edge(n("bulk"), "IA_R", n("b"), graph=G1)
    cur = iris_connection.cursor()
    cur.execute("DELETE FROM Graph_KG.rdf_props WHERE s = ? AND \"key\" = 'id'", [n("bulk")])
    iris_connection.commit()
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    for g in (G1, G2):
        e.erase_graph(g)


CASES = [
    ("out-degree of an id", f"MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(r) AS cnt", {"x": n("a")}),
    ("in one graph", f"USE GRAPH '{G2}' MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(r) AS cnt", {"x": n("a")}),
    ("the reach of two hops", f"USE GRAPH '{G1}' MATCH (s:{E} {{id: $x}})-[:IA_R]->()-[:IA_S]->(m) RETURN count(DISTINCT m) AS cnt", {"x": n("a")}),
    ("an id naming no node", f"MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(r) AS cnt", {"x": n("nobody")}),
    ("a node without the label", f"MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(r) AS cnt", {"x": n("x")}),
    ("an id with a property", f"MATCH (s:{E} {{id: $x, kind: 'gene'}})-[r]->(m) RETURN m.name AS n ORDER BY m.name", {"x": n("a")}),
    ("an id with a property that fails", f"MATCH (s:{E} {{id: $x, kind: 'drug'}})-[r]->(m) RETURN m.name AS n ORDER BY m.name", {"x": n("a")}),
    ("an id without its property row", f"MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(r) AS cnt", {"x": n("bulk")}),
    ("a node_id without it", f"MATCH (s:{E} {{node_id: $x}})-[r]->(m) RETURN count(r) AS cnt", {"x": n("bulk")}),
    ("an id on the far node", f"MATCH (s:{E})-[:IA_R]->(t:{E} {{node_id: $x}}) RETURN s.name AS n ORDER BY s.name", {"x": n("b")}),
    ("the reach of an id, untyped (D_Q2)", f"USE GRAPH '{G1}' MATCH (s:{E} {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt", {"x": n("a")}),
    ("the reach of an id, typed", f"USE GRAPH '{G2}' MATCH (s:{E} {{id: $x}})-[:IA_R|IA_S*1..3]->(m) RETURN count(DISTINCT m) AS cnt", {"x": n("a")}),
    ("the reach of an id without its property row", f"USE GRAPH '{G1}' MATCH (s:{E} {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt", {"x": n("bulk")}),
    ("names within the reach of an id", f"USE GRAPH '{G1}' MATCH (s:{E} {{id: $x}})-[*1..2]->(m) RETURN DISTINCT m.name ORDER BY m.name", {"x": n("a")}),
    # over both graphs: the SQL path answers each graph and adds the counts, (graph, node) pairs, as native counts
    ("the reach of an id over every graph", f"MATCH (s:{E} {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt", {"x": n("a")}),
    ("projection from an id", f"MATCH (s:{E} {{id: $x}})-[:IA_R]->(m)-[:IA_S]->(k) RETURN DISTINCT k.name AS n ORDER BY k.name", {"x": n("a")}),
]


@pytest.mark.parametrize("name,cypher,params", CASES, ids=[c[0] for c in CASES])
def test_an_id_anchor_answers_as_sql(engine, name, cypher, params):
    # shadow mode (the fixture) compares the native answer with the SQL path's and raises where they differ
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN_ISSUES: count(DISTINCT m) over graphs holding one node id: native counts (graph, node) pairs, SQL ids",
)
def test_a_distinct_count_over_graphs_sharing_a_node_id(engine):
    # c is two hops from a in both graphs: the SQL path counts it once, the native path once per graph
    r = engine.execute_cypher(f"MATCH (s:{E} {{id: $x}})-[:IA_R]->()-[:IA_S]->(m) RETURN count(DISTINCT m) AS cnt", {"x": n("a")})
    assert r.rows == [[1]]


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN_ISSUES: the ID-bound variable-length route answers (id, hops, pred) for an aliased target property",
)
def test_an_aliased_name_within_the_reach_of_an_id(engine):
    # the native answer is the right one (one column, n); the SQL path's raw BFS rows make shadow mode raise
    r = engine.execute_cypher(
        f"USE GRAPH '{G1}' MATCH (s:{E} {{id: $x}})-[*1..2]->(m) RETURN DISTINCT m.name AS n ORDER BY m.name", {"x": n("a")}
    )
    assert r.columns == ["n"]


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN_ISSUES: the ID-bound variable-length route answers (id, hops, pred) under a WITH stage",
)
def test_the_reach_of_an_id_grouped_twice(engine):
    r = engine.execute_cypher(
        f"USE GRAPH '{G1}' MATCH (s:{E} {{id: $x}})-[*1..2]->(m) WITH m.kind AS kind, count(DISTINCT m) AS c "
        "RETURN kind, c ORDER BY kind",
        {"x": n("a")},
    )
    assert r.columns == ["kind", "c"]


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN_ISSUES: the labeled variable-length route miscounts the starts reaching a named end",
)
def test_an_id_at_the_far_end_of_a_variable_length_hop(engine):
    # a, b and bulk reach c within two hops in G1 (x does too, without the label): the SQL path counts 2
    r = engine.execute_cypher(
        f"USE GRAPH '{G1}' MATCH (s:{E})-[:IA_R|IA_S*1..2]->(t:{E} {{id: $x}}) RETURN count(DISTINCT s) AS cnt", {"x": n("c")}
    )
    assert r.rows == [[3]]
