"""4.6.1, live: an all-graphs shortestPath answers its path once, whatever named graphs the namespace holds.

By hand. Default graph: sa -[:L461]-> sb -[:L461]-> sc. Graph 'g461': sx -[:U461]-> sy, nothing on the path. With no USE
GRAPH, `MATCH p = shortestPath((a {node_id: 'sa'})-[:L461*]->(b {node_id: 'sc'})) RETURN length(p)` is [[2]].
4.6.0 answered one copy per graph holding edges ([[2], [2]] here): the all-graphs route ran the shortest-path routine,
which reads no graph, once per graph (walkthrough round 5c). Default and SQL routes alike.

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG set up).
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

G = "g461"
Q = "MATCH p = shortestPath((a {node_id: 'sa'})-[:L461*]->(b {node_id: 'sc'})) RETURN length(p)"


@pytest.fixture(scope="module", params=["default", "sql"])
def engine(request):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    kw = {"native_cypher": False} if request.param == "sql" else {}
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, **kw)

    def clear():
        eng.execute_cypher("MATCH (n:S461) DETACH DELETE n", raise_on_error=True)
        eng.execute_cypher(f"USE GRAPH '{G}' MATCH (n:S461) DETACH DELETE n", raise_on_error=True)

    clear()
    eng.execute_cypher(
        "CREATE (:S461 {node_id: 'sa'})-[:L461]->(:S461 {node_id: 'sb'})-[:L461]->(:S461 {node_id: 'sc'})",
        raise_on_error=True,
    )
    eng.execute_cypher(
        f"USE GRAPH '{G}' CREATE (:S461 {{node_id: 'sx'}})-[:U461]->(:S461 {{node_id: 'sy'}})", raise_on_error=True
    )
    eng.sync()
    yield eng
    clear()
    conn.close()


def test_the_length_is_answered_once(engine):
    assert engine.execute_cypher(Q, raise_on_error=True).rows == [[2]]


def test_a_count_of_shortest_paths_is_one(engine):
    q = "MATCH p = shortestPath((a {node_id: 'sa'})-[:L461*]->(b {node_id: 'sc'})) RETURN count(p)"
    assert engine.execute_cypher(q, raise_on_error=True).rows == [[1]]
