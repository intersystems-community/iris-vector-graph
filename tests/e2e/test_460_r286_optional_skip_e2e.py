"""4.6.0 R286 (the OPTIONAL MATCH items), live: an OPTIONAL MATCH's null row is a row SKIP and LIMIT count, and a
leading OPTIONAL MATCH's property map filters its matches.

Found by fixes40 on the release head, under intersystems-irispython 5.4.0 and 5.4.1: R273 tested the match itself for
the null row (`NOT EXISTS` over it) except with SKIP, which kept the old form (the null row's `UNION ALL` after an
`OFFSET` failed -25), and a LIMIT without ORDER BY kept the label test (`OPTIONAL MATCH (n:L {k: 999}) RETURN n LIMIT 1`
answered no row where L has nodes). Found with them: the anchor of a leading OPTIONAL MATCH matched a node without the
property its map names (`(p.s IS NULL OR p.val = ...)`), so `OPTIONAL MATCH (n:L {k: 999})` answered every L node that
has no k, and `LIMIT 0` answered the null row.

Worked by hand. The graph: (:O286 {k: 7, name: 'a'}) -[:R286]-> (:O286 {name: 'nok'}).
* `OPTIONAL MATCH (n:O286 {k: 999}) RETURN n.name` matches nothing (no node has k = 999; 'nok' has no k at all), so
  it is one null row: [[null]]. With SKIP 0, LIMIT 1, or ORDER BY n.name SKIP 0 LIMIT 5 still [[null]]; SKIP 1 skips
  that row, and LIMIT 0 keeps none: [].
* `{k: 7}` matches 'a' only: [['a']]; SKIP 0 [['a']]; SKIP 1 [].
* `OPTIONAL MATCH (n:O286) RETURN n.name ORDER BY n.name SKIP 1`: with no USE GRAPH a read answers every graph's
  rows (R192), so the rows are 'a', 'nok' and g286's node, whose name is null (last in ascending order); the first
  skipped: [['nok'], [null]].
* `OPTIONAL MATCH (n:O286)-[:R286]->(m) RETURN m.name`: 'a' -> 'nok' is the one match: SKIP 0 [['nok']]; SKIP 1 [].
  With `-[:NOPE286]->` nothing matches: SKIP 0 [[null]], SKIP 1 [].
* `OPTIONAL MATCH (n:NoSuch286) RETURN n`: no node has the label: SKIP 0 [[null]], SKIP 1 [], LIMIT 0 [].
* SKIP $s with s = 1 is SKIP 1: [].
* Under USE GRAPH 'g286', which holds (:O286 {k: 1}) only: `{k: 999}` SKIP 0 [[null]], SKIP 1 []; `{k: 1}` SKIP 0 one
  row.

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

M = "OPTIONAL MATCH (n:O286 {k: 999}) RETURN n.name"
H = "OPTIONAL MATCH (n:O286 {k: 7}) RETURN n.name"
CASES = [
    ("no match", M, [[None]]),
    ("no match, SKIP 0", M + " SKIP 0", [[None]]),
    ("no match, SKIP 1", M + " SKIP 1", []),
    ("no match, LIMIT 1", M + " LIMIT 1", [[None]]),
    ("no match, LIMIT 0", M + " LIMIT 0", []),
    ("no match, ORDER BY, SKIP 0 LIMIT 5", M + " ORDER BY n.name SKIP 0 LIMIT 5", [[None]]),
    ("no match, SKIP $s", M + " SKIP $s", []),
    ("a match", H, [["a"]]),
    ("a match, SKIP 0", H + " SKIP 0", [["a"]]),
    ("a match, SKIP 1", H + " SKIP 1", []),
    ("three rows in order, SKIP 1", "OPTIONAL MATCH (n:O286) RETURN n.name ORDER BY n.name SKIP 1", [["nok"], [None]]),
    ("a relationship's match, SKIP 0", "OPTIONAL MATCH (n:O286)-[:R286]->(m) RETURN m.name SKIP 0", [["nok"]]),
    ("a relationship's match, SKIP 1", "OPTIONAL MATCH (n:O286)-[:R286]->(m) RETURN m.name SKIP 1", []),
    ("no relationship, SKIP 0", "OPTIONAL MATCH (n:O286)-[:NOPE286]->(m) RETURN m.name SKIP 0", [[None]]),
    ("no relationship, SKIP 1", "OPTIONAL MATCH (n:O286)-[:NOPE286]->(m) RETURN m.name SKIP 1", []),
    ("no such label, SKIP 0", "OPTIONAL MATCH (n:NoSuch286) RETURN n SKIP 0", [[None]]),
    ("no such label, SKIP 1", "OPTIONAL MATCH (n:NoSuch286) RETURN n SKIP 1", []),
    ("no such label, LIMIT 0", "OPTIONAL MATCH (n:NoSuch286) RETURN n LIMIT 0", []),
    ("USE GRAPH, no match, SKIP 0", "USE GRAPH 'g286' " + M + " SKIP 0", [[None]]),
    ("USE GRAPH, no match, SKIP 1", "USE GRAPH 'g286' " + M + " SKIP 1", []),
    ("USE GRAPH, a match, SKIP 0", "USE GRAPH 'g286' OPTIONAL MATCH (n:O286 {k: 1}) RETURN n.k SKIP 0", [[1]]),
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
    sql.execute_cypher("MATCH (n:O286) DETACH DELETE n", {}, raise_on_error=True)
    sql.erase_graph("g286")
    sql.execute_cypher("CREATE (:O286 {k: 7, name: 'a'})-[:R286]->(:O286 {name: 'nok'})", {}, raise_on_error=True)
    sql.execute_cypher("USE GRAPH 'g286' CREATE (:O286 {k: 1})", {}, raise_on_error=True)
    conn.commit()
    try:
        yield {"native": nat, "sql": sql}
    finally:
        sql.execute_cypher("MATCH (n:O286) DETACH DELETE n", {}, raise_on_error=True)
        sql.erase_graph("g286")
        conn.commit()
        conn.close()


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_the_null_row_is_a_row_skip_and_limit_count(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, {"s": 1}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    rows = [list(r[:1]) for r in result.rows]
    assert rows == want
