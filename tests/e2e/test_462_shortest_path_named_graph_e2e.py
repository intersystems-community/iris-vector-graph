"""4.6.2, live: shortestPath walks the graph it reads.

By hand. Default graph: ta -[:L462]-> tb -[:L462]-> tc. Graph 'g462': ta, tc and a direct ta -[:L462]-> tc (no tb).

- `USE GRAPH 'g462'`: the path is ta -> tc, length 1, nodes [ta, tc].
- `USE GRAPH ''` (the default graph): ta -> tb -> tc, length 2.
- No USE GRAPH (each graph's own answer, spec 253): lengths 1 and 2, one row each; count(p) is 2.
- A path only a named graph holds: tx -[:L462]-> ty in 'g462' alone: `USE GRAPH 'g462'` answers length 1; no USE GRAPH
  answers it once.

4.6.1's routines read the default graph only: `USE GRAPH 'g462'` answered the default graph's length-2 path through a
node g462 does not hold, and a path only g462 holds answered no row (walkthrough round 6).
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

G = "g462"
SP = "MATCH p = shortestPath((a {node_id: $a})-[:L462*]->(b {node_id: $b}))"


@pytest.fixture(scope="module", params=["default", "sql"])
def engine(request):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    kw = {"native_cypher": False} if request.param == "sql" else {}
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, **kw)

    def clear():
        eng.execute_cypher("MATCH (n:T462) DETACH DELETE n", raise_on_error=True)
        eng.execute_cypher(f"USE GRAPH '{G}' MATCH (n:T462) DETACH DELETE n", raise_on_error=True)

    clear()
    eng.execute_cypher(
        "CREATE (:T462 {node_id: 'ta'})-[:L462]->(:T462 {node_id: 'tb'})-[:L462]->(:T462 {node_id: 'tc'})",
        raise_on_error=True,
    )
    eng.execute_cypher(
        f"USE GRAPH '{G}' CREATE (:T462 {{node_id: 'ta'}})-[:L462]->(:T462 {{node_id: 'tc'}}), "
        "(:T462 {node_id: 'tx'})-[:L462]->(:T462 {node_id: 'ty'})",
        raise_on_error=True,
    )
    eng.sync()
    yield eng
    clear()
    conn.close()


def _rows(engine, q, params):
    return sorted(engine.execute_cypher(q, params, raise_on_error=True).rows, key=repr)


def test_use_graph_walks_that_graph(engine):
    q = f"USE GRAPH '{G}' {SP} RETURN length(p), [n IN nodes(p) | n.node_id]"
    assert _rows(engine, q, {"a": "ta", "b": "tc"}) == [[1, ["ta", "tc"]]]


def test_the_default_graph_walks_its_own_edges(engine):
    q = f"USE GRAPH '' {SP} RETURN length(p)"
    assert _rows(engine, q, {"a": "ta", "b": "tc"}) == [[2]]


def test_no_use_graph_answers_each_graphs_own_path(engine):
    assert _rows(engine, f"{SP} RETURN length(p)", {"a": "ta", "b": "tc"}) == [[1], [2]]
    assert _rows(engine, f"{SP} RETURN count(p)", {"a": "ta", "b": "tc"}) == [[2]]


def test_a_path_only_a_named_graph_holds(engine):
    assert _rows(engine, f"USE GRAPH '{G}' {SP} RETURN length(p)", {"a": "tx", "b": "ty"}) == [[1]]
    assert _rows(engine, f"{SP} RETURN length(p)", {"a": "tx", "b": "ty"}) == [[1]]
