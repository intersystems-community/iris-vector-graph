"""4.6.0, live: a WITH stage read back after a SET keeps each row, in a namespace holding more than one graph.

Found by fixes65: `MATCH (n:L) WITH n.k AS k, n SET n.r = k RETURN n.name, n.r, k` answered no row once a named graph
held a node. The RETURN reads the stage as it was before the SET (a snapshot, read back through JSON_TABLE), lined up
with the live stage on its key columns, one of which is the node's graph `__g_n`; JSON_TABLE reads the default graph's
`""` as NULL, so no row lined up. A stage column holding an empty string came back NULL the same way.

Worked by hand: the default graph holds (:L68 {name: 'A', k: 3, e: ''}) and (:L68 {name: 'B', k: 5}); r68g holds
(:L68 {name: 'A', k: 30}).

* no USE GRAPH, `WITH n.k AS k, n SET n.r = k RETURN n.name, n.r, k`: ['A', 3, 3], ['A', 30, 30], ['B', 5, 5]; each
  node's r is its own k, in its own graph;
* no USE GRAPH, `WITH n.e AS e, n SET n.r2 = e RETURN n.name, e`: A's e is '' (the empty string, not null) in the
  default graph and null in r68g, B's null; only the default graph's A gets r2, ''.
* `USE GRAPH 'r68g'`, the first query: ['A', 30, 30], and nothing in the default graph changes.

Namespace: an IVG460FX* scratch namespace. Native on and off.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.prop_values import decode_typed_text
from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")
G = "r68g"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


@pytest.fixture(scope="module", params=["native", "sql"])
def engine(request):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=request.param == "native")
    eng.initialize_schema()
    try:
        yield eng
    finally:
        _clean(eng)
        conn.close()


def _clean(eng):
    eng.erase_graph(G)
    eng.execute_cypher("MATCH (n:L68) DETACH DELETE n", {}, raise_on_error=True)
    eng.conn.commit()


@pytest.fixture
def seeded(engine):
    _clean(engine)
    engine.execute_cypher("CREATE (:L68 {name: 'A', k: 3, e: ''}), (:L68 {name: 'B', k: 5})", {}, raise_on_error=True)
    engine.execute_cypher(f"USE GRAPH '{G}' CREATE (:L68 {{name: 'A', k: 30}})", {}, raise_on_error=True)
    engine.conn.commit()
    return engine


def _run(eng, q):
    r = eng.execute_cypher(q, {}, raise_on_error=True)
    assert getattr(r, "error", None) is None, r.error
    eng.conn.commit()
    return [list(row) for row in r.rows]


def _stored(eng, key) -> list:
    """(graph, name, key's value) of every L68 node holding `key`, read with plain SQL through CY_TYPED."""
    cur = eng.conn.cursor()
    cur.execute(
        "SELECT n.graph_id, nm.val, Graph_KG.CY_TYPED(p.val) FROM Graph_KG.nodes n "
        "JOIN Graph_KG.rdf_labels l ON l.s = n.node_id AND l.graph_id = n.graph_id AND l.label = 'L68' "
        "JOIN Graph_KG.rdf_props nm ON nm.s = n.node_id AND nm.graph_id = n.graph_id AND nm.\"key\" = 'name' "
        "JOIN Graph_KG.rdf_props p ON p.s = n.node_id AND p.graph_id = n.graph_id AND p.\"key\" = ?",
        [key],
    )
    return sorted((g or "", str(name), decode_typed_text(v)) for g, name, v in cur.fetchall())


def test_the_return_after_a_set_reads_every_graphs_rows(seeded):
    rows = _run(seeded, "MATCH (n:L68) WITH n.k AS k, n SET n.r = k RETURN n.name, n.r, k ORDER BY n.name, k")
    assert rows == [["A", 3, 3], ["A", 30, 30], ["B", 5, 5]]
    assert _stored(seeded, "r") == [("", "A", 3), ("", "B", 5), (G, "A", 30)]


def test_an_empty_string_through_the_stage_stays_an_empty_string(seeded):
    rows = _run(seeded, "MATCH (n:L68) WITH n.e AS e, n SET n.r2 = e RETURN n.name, e ORDER BY n.name, e")
    assert sorted(rows, key=repr) == sorted([["A", ""], ["A", None], ["B", None]], key=repr)
    assert _stored(seeded, "r2") == [("", "A", "")]


def test_under_use_graph_only_that_graphs_rows(seeded):
    rows = _run(seeded, f"USE GRAPH '{G}' MATCH (n:L68) WITH n.k AS k, n SET n.r = k RETURN n.name, n.r, k")
    assert rows == [["A", 30, 30]]
    assert _stored(seeded, "r") == [(G, "A", 30)]
