"""4.6.0 R292 (the OPTIONAL MATCH item), live: a leading OPTIONAL MATCH whose first node is anonymous answers one null
row when its pattern has no match.

Found by fixes43 on the release head, under intersystems-irispython 5.4.0 and 5.4.1: `OPTIONAL MATCH (:L {name: 'b'})
-[r:R]->() RETURN r` answered no rows. An anonymous first node of a relationship is not joined as a node: the edge
heads the FROM and the node's label and property filter its source column. translate_node_pattern, which registers
the null-row fallback for an anchor, never ran, so the query had no fallback. With a variable, `(x:L {name: 'b'})`,
the answer was right.

Worked by hand. The graph: (:O292 {name: 'a292'}) -[:R292]-> (:O292 {name: 'c292'}), and (:O292 {name: 'b292'}) with
no relationship; under USE GRAPH 'g292': (:O292 {name: 'g292'}) -[:R292]-> (:O292 {name: 'h292'}). With no USE GRAPH a
read answers every graph's rows (R192).
* 'b292' has no outgoing R292, and no node is named 'zz292': each is one null row, [[null]]; with two columns (r,
  m.name) [[null, null]]; incoming into 'b292' too.
* 'a292' -[:R292]-> 'c292' is the one match from 'a292': [['R292', 'c292']]; into 'c292' from 'a292': [['a292']].
* Every O292 with an outgoing R292: 'a292' and 'g292', so the far ends 'c292' and 'h292' in order.
* No NOPE292 relationship and no NoSuch292 node: one null row each.
* SKIP 1 or LIMIT 0 drops the null row (R286): []. count(r) over no match is 0, one row.
* Under USE GRAPH 'g292' 'a292' is not there (one null row) and 'g292' -> 'h292' is.

Namespace: an IVG460F* scratch namespace; the labels are this file's own. Native on and off.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

B = "OPTIONAL MATCH (:O292 {name: 'b292'})-[r:R292]->() RETURN r"
CASES = [
    ("no relationship from the node", B, [[None]]),
    ("no such node", "OPTIONAL MATCH (:O292 {name: 'zz292'})-[r:R292]->() RETURN r", [[None]]),
    ("two columns", "OPTIONAL MATCH (:O292 {name: 'b292'})-[r:R292]->(m) RETURN r, m.name", [[None, None]]),
    ("incoming, no match", "OPTIONAL MATCH (:O292 {name: 'b292'})<-[r:R292]-(m) RETURN m.name", [[None]]),
    ("a match", "OPTIONAL MATCH (:O292 {name: 'a292'})-[r:R292]->(m) RETURN type(r), m.name", [["R292", "c292"]]),
    ("incoming, a match", "OPTIONAL MATCH (:O292 {name: 'c292'})<-[r:R292]-(m) RETURN m.name", [["a292"]]),
    ("every graph's matches", "OPTIONAL MATCH (:O292)-[r:R292]->(m) RETURN m.name ORDER BY m.name",
     [["c292"], ["h292"]]),
    ("no such relationship type", "OPTIONAL MATCH (:O292)-[r:NOPE292]->(m) RETURN m.name", [[None]]),
    ("no such label", "OPTIONAL MATCH (:NoSuch292)-[r]->() RETURN r", [[None]]),
    ("SKIP 1 drops the null row", B + " SKIP 1", []),
    ("LIMIT 0 drops the null row", B + " LIMIT 0", []),
    ("SKIP 0 keeps it", B + " SKIP 0", [[None]]),
    ("count over no match", "OPTIONAL MATCH (:O292 {name: 'b292'})-[r:R292]->() RETURN count(r)", [[0]]),
    ("USE GRAPH, no match", "USE GRAPH 'g292' OPTIONAL MATCH (:O292 {name: 'a292'})-[r:R292]->(m) RETURN m.name",
     [[None]]),
    ("USE GRAPH, a match", "USE GRAPH 'g292' OPTIONAL MATCH (:O292 {name: 'g292'})-[r:R292]->(m) RETURN m.name",
     [["h292"]]),
    ("a named start (control)", "OPTIONAL MATCH (x:O292 {name: 'b292'})-[r:R292]->() RETURN r", [[None]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    sql.execute_cypher("MATCH (n:O292) DETACH DELETE n", {}, raise_on_error=True)
    sql.erase_graph("g292")
    sql.execute_cypher(
        "CREATE (:O292 {name: 'a292'})-[:R292]->(:O292 {name: 'c292'}), (:O292 {name: 'b292'})", {},
        raise_on_error=True,
    )
    sql.execute_cypher(
        "USE GRAPH 'g292' CREATE (:O292 {name: 'g292'})-[:R292]->(:O292 {name: 'h292'})", {}, raise_on_error=True
    )
    conn.commit()
    try:
        yield {"native": nat, "sql": sql}
    finally:
        sql.execute_cypher("MATCH (n:O292) DETACH DELETE n", {}, raise_on_error=True)
        sql.erase_graph("g292")
        conn.commit()
        conn.close()


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_an_anonymous_start_has_its_null_row(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    assert [list(r) for r in result.rows] == want
