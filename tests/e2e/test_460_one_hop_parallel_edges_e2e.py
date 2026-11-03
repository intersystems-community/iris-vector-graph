"""4.6.0 R074: the 1-hop ids shortcut answers one row per relationship in a multigraph default graph.

`MATCH (n {node_id: $x})-[:R]->(m) RETURN m.node_id` is answered by `_try_khop_fast_path` from
`Graph.KG.Traversal.KHopNeighborIds`, which listed each target of ^KG("out", 0, x, R) once. In a multigraph two
relationships can join the same pair; ^KG holds the pair once with its count at ^KG("out", g, s, p, o, "#"). openCypher
answers a row per matched relationship, so a pair joined twice is two rows.

The graph, worked by hand: the default graph is a multigraph; a-R->b twice, a-R->c once, a-S->d (not R). One R hop from
a: b, b, c (three relationships); count(m) is 3.

The shortcut answers only when the default graph is the only one holding edges, so the test checks that first.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    assert not engine.is_multigraph(""), "the default graph is already a multigraph: another test left it so"
    run = uuid.uuid4().hex[:8]
    r, s = f"IVG460PE_R_{run}", f"IVG460PE_S_{run}"
    ids = {k: f"ivg460pe_{run}_{k}" for k in "abcd"}
    engine.set_multigraph("", True)
    try:
        for nid in ids.values():
            engine.create_node(nid, labels=["Ivg460PE"])
        result = engine.execute_cypher(
            f"MATCH (a {{node_id: '{ids['a']}'}}), (b {{node_id: '{ids['b']}'}}), (c {{node_id: '{ids['c']}'}}), "
            f"(d {{node_id: '{ids['d']}'}}) CREATE (a)-[:{r}]->(b), (a)-[:{r}]->(b), (a)-[:{r}]->(c), (a)-[:{s}]->(d)"
        )
        assert not getattr(result, "error", None), result.error
        with contextlib.suppress(Exception):
            iris_connection.commit()
        yield engine, ids, r
    finally:
        for nid in ids.values():
            with contextlib.suppress(Exception):
                engine.delete_node(nid, graph=None)
        engine.set_multigraph("", False)


def _ask(g, cypher: str):
    engine, ids, r = g
    assert engine._edge_graphs() in ([], [""]), "another graph holds edges: the shortcut would not be asked"
    result = engine.execute_cypher(cypher.replace("[:R]", f"[:{r}]", 1), {"x": ids["a"]})
    assert not getattr(result, "error", None), result.error
    inverse = {v: k for k, v in ids.items()}
    return list(result.columns), [[inverse.get(v, v) for v in row] for row in result.rows]


def test_the_parallel_pair_is_stored_twice(g):
    engine, ids, r = g
    cur = engine.conn.cursor()
    try:
        cur.execute("SELECT o_id FROM Graph_KG.rdf_edges WHERE s = ? AND p = ?", [ids["a"], r])
        targets = sorted(row[0] for row in cur.fetchall())
    finally:
        cur.close()
    assert targets == sorted([ids["b"], ids["b"], ids["c"]])


def test_one_hop_ids_one_row_per_relationship(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R]->(m) RETURN m.node_id")
    assert cols == ["m.node_id"]
    assert sorted(row[0] for row in rows) == ["b", "b", "c"]


def test_one_hop_ids_aliased(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R]->(m) RETURN m.node_id AS id")
    assert cols == ["id"]
    assert sorted(row[0] for row in rows) == ["b", "b", "c"]


def test_one_hop_count_counts_each_relationship(g):
    assert _ask(g, "MATCH (n {node_id: $x})-[:R]->(m) RETURN count(m) AS total") == (["total"], [[3]])
