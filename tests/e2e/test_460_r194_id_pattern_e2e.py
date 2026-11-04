"""4.6.0 R194, live: `{id: 'x'}` in a pattern names the node x on every route.

`_is_node_id_key` (translator) is the one definition: a string (or parameter) `id` in a node pattern names the node,
as `CREATE ({id: 'x'})` makes the node x; a non-string literal (`{id: 1}`) is an ordinary property. The single-node
pattern, the cross product, the variable-length labelled route and shortestPath read it so. The fixed-hop SQL path,
the native executor and the variable-length count route read it as the node x *and* an `id` property equal to x, which
create_node writes and bulk_create_edges's endpoint nodes do not have, so on a bulk load they answered nothing:
`count(h)` 1, `RETURN x.node_id` 5 rows, but `count(x)`, `count(DISTINCT x)` and every hop read 0.

Namespace: `IVG_NAMESPACE`, an IVG460FX* scratch namespace set up for `Graph_KG` (IVG460FXL when this was written);
the fixture empties it first (`Graph.KG.Eraser.EraseAll`).

By hand, from bulk_create_edges alone (no node has an `id` property): h -R-> t0..t4, and every t_i -S-> u0..u3.

    (h {id:'h'})                      1 node
    (h {id:'h'})-[:R]->(x)            5 rows, 5 distinct x
    (h {id:'h'})-[:R]->(x)-[:S]->(y)  20 rows, 4 distinct y
    (h {id:'h'})-[*1..2]->(y)         9 distinct y (t0..t4, u0..u3)
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    edges = [{"source_id": "h", "predicate": "R", "target_id": f"t{i}"} for i in range(5)]
    edges += [{"source_id": f"t{i}", "predicate": "S", "target_id": f"u{j}"} for i in range(5) for j in range(4)]
    eng.bulk_create_edges(edges)
    conn.commit()
    yield eng
    conn.close()


CASES = [
    ("MATCH (h {id: 'h'}) RETURN count(h)", [[1]]),
    ("MATCH (h {id: 'h'}) RETURN count(DISTINCT h)", [[1]]),
    ("MATCH (h {id: 'h'})-[:R]->(x) RETURN count(x)", [[5]]),
    ("MATCH (h {id: 'h'})-[:R]->(x) RETURN count(DISTINCT x)", [[5]]),
    ("MATCH ({id: 'h'})-[:R]->(x) RETURN count(x)", [[5]]),
    ("MATCH (x)<-[:R]-(h {id: 'h'}) RETURN count(x)", [[5]]),
    ("MATCH (h {id: 'h'})-[:R]->(x {id: 't3'}) RETURN count(*)", [[1]]),
    ("MATCH (h {id: 'h'})-[:R]->(x)-[:S]->(y) RETURN count(*)", [[20]]),
    ("MATCH (h {id: 'h'})-[:R]->(x)-[:S]->(y) RETURN count(DISTINCT y)", [[4]]),
    ("MATCH p = (h {id: 'h'})-[:R]->()-[:S]->() RETURN count(p)", [[20]]),
    ("MATCH (h {id: 'h'})-[*1..2]->(y) RETURN count(DISTINCT y)", [[9]]),
]


@pytest.mark.parametrize("native", [True, False], ids=["native-on", "native-off"])
@pytest.mark.parametrize("query,expected", CASES, ids=[q for q, _ in CASES])
def test_id_names_the_node_on_every_route(engine, query, expected, native, monkeypatch):
    monkeypatch.setattr(engine, "native_cypher", native)
    assert engine.execute_cypher(query, raise_on_error=True).rows == expected


@pytest.mark.parametrize("native", [True, False], ids=["native-on", "native-off"])
def test_distinct_ids_read_after_the_hop(engine, native, monkeypatch):
    monkeypatch.setattr(engine, "native_cypher", native)
    rows = engine.execute_cypher(
        "MATCH (h {id: 'h'})-[:R]->(x) RETURN DISTINCT x.node_id AS id ORDER BY id", raise_on_error=True
    ).rows
    assert rows == [[f"t{i}"] for i in range(5)]


def test_the_path_read_and_the_count_agree(engine):
    rows = engine.execute_cypher("MATCH (h {id: 'h'})-[:R]->(x)-[:S]->(y) RETURN x.node_id, y.node_id").rows
    assert len(rows) == 20 and len({tuple(r) for r in rows}) == 20
