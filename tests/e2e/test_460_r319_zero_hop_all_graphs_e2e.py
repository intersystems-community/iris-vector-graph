"""4.6.0 R319, live: an all-graphs variable-length match from 0 hops answers the source only in graphs that hold it.

By hand. Default graph: zj (name 'jo'), zk (name 'k1'), and zd1 -[:U319]-> zd2 so the default graph has edges.
Graph 'g319': zk (name 'k2'), za (name 'A'), zb (name 'B'), zk -[:T319]-> za -[:T319]-> zb.
With no USE GRAPH a read is the union of each graph's own answer (spec 253):

- `MATCH (a {node_id: 'zj'})-[*0..3]-(b) RETURN DISTINCT b.node_id, b.name`: zj is only in the default graph and has no
  edge there, so the only b is zj itself: [['zj', 'jo']]. Before R319 the SQL route also answered ['zj', None], zj at
  depth 0 in g319, which does not hold it (r308 differential, seeds 2 and 3, "two" graphs).
- The same with `[:T319*0..3]`: [['zj', 'jo']].
- Both on the SQL route (`native_cypher=False`); the native executor answered right.
- From zk, in both graphs: the default graph's zk ('k1'); g319's zk ('k2'), za and zb.

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG set up).
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

G = "g319"


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    # the SQL route (native_cypher=False), where the extra row came from; the native executor answered right
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=False)

    def clear():
        eng.execute_cypher("MATCH (n:R319) DETACH DELETE n", raise_on_error=True)
        eng.execute_cypher(f"USE GRAPH '{G}' MATCH (n:R319) DETACH DELETE n", raise_on_error=True)

    clear()
    eng.execute_cypher(
        "CREATE (:R319 {node_id: 'zj', name: 'jo'}), (:R319 {node_id: 'zk', name: 'k1'}), "
        "(:R319 {node_id: 'zd1'})-[:U319]->(:R319 {node_id: 'zd2'})",
        raise_on_error=True,
    )
    eng.execute_cypher(
        f"USE GRAPH '{G}' CREATE (:R319 {{node_id: 'zk', name: 'k2'}})-[:T319]->(:R319 {{node_id: 'za', name: 'A'}})"
        "-[:T319]->(:R319 {node_id: 'zb', name: 'B'})",
        raise_on_error=True,
    )
    eng.sync()
    yield eng
    clear()
    conn.close()


def _rows(engine, q, params=None):
    r = engine.execute_cypher(q, params or {}, raise_on_error=True)
    return sorted(r.rows, key=repr)


@pytest.mark.parametrize("pattern", ["[*0..3]", "[:T319*0..3]"])
def test_a_source_in_one_graph_answers_once(engine, pattern):
    q = f"MATCH (a {{node_id: $p}})-{pattern}-(b) RETURN DISTINCT b.node_id, b.name"
    assert _rows(engine, q, {"p": "zj"}) == [["zj", "jo"]]


def test_a_source_in_both_graphs_answers_each_graphs_own_node(engine):
    q = "MATCH (a {node_id: $p})-[*0..3]-(b) RETURN DISTINCT b.node_id, b.name"
    assert _rows(engine, q, {"p": "zk"}) == sorted(
        [["zk", "k1"], ["zk", "k2"], ["za", "A"], ["zb", "B"]], key=repr
    )
