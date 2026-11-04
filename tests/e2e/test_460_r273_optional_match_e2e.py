"""4.6.0 R273 (the OPTIONAL MATCH and collected-list WHERE cases), live.

Found by fixes35 on the release head:
* `OPTIONAL MATCH (a:L)-[r]->()` answered a row for every `a`, with a null `r` where `a` has no relationship: the
  relationship was left-joined to the anchor, as for an OPTIONAL MATCH after a MATCH that bound `a`;
* `WITH collect(v) AS x WHERE x[2] = 3` answered `size(x) = 0`: the WHERE was translated before the aggregate
  existed (x[2] read a node property of nothing).

Found with them: an OPTIONAL MATCH with ORDER BY or LIMIT failed -25 ("Input (UNION) encountered after end of
query": the null-row fallback followed the ORDER BY); `WITH collect(v) AS x WHERE size(x) = 3` failed "Undefined
variable: x".

Graph r273o, written by Cypher: (A:OL)-[:R {w: 1}]->(B:OL), (A)-[:R {w: 2}]->(C:OL). Worked by hand: an OPTIONAL MATCH
with nothing bound before it answers its whole pattern's matches, or one row of nulls when there is none; after a
MATCH that bound `a`, a row for each `a` with nulls where the rest does not match.

Namespace: an IVG460F* scratch namespace. Native on and off.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

G = "r273o"
U = f"USE GRAPH '{G}' "
N = [None, None]

#: (name, query, rows by hand, ordered)
CASES = [
    ("leading OPTIONAL MATCH, a relationship", "OPTIONAL MATCH (a:OL)-[r]->() RETURN a.name, r.w",
     [["A", 1], ["A", 2]], False),
    ("ordered", "OPTIONAL MATCH (a:OL)-[r]->() RETURN a.name, r.w ORDER BY r.w", [["A", 1], ["A", 2]], True),
    ("ordered, LIMIT", "OPTIONAL MATCH (a:OL)-[r]->() RETURN a.name, r.w ORDER BY r.w DESC LIMIT 1", [["A", 2]], True),
    ("incoming", "OPTIONAL MATCH (a:OL)<-[r]-(b) RETURN a.name, b.name ORDER BY a.name", [["B", "A"], ["C", "A"]],
     True),
    ("a WHERE that matches", "OPTIONAL MATCH (a:OL)-[r]->(b) WHERE b.name = 'C' RETURN a.name, b.name",
     [["A", "C"]], False),
    ("a WHERE that does not", "OPTIONAL MATCH (a:OL)-[r]->(b) WHERE b.name = 'Z' RETURN a.name, b.name", [N], False),
    ("an anchor with no relationship", "OPTIONAL MATCH (a:OL {name: 'B'})-[r]->(b) RETURN a.name, b.name", [N],
     False),
    ("a count, ordered", "OPTIONAL MATCH (a:OL)-[r]->() RETURN a.name, count(r) AS c ORDER BY a.name", [["A", 2]],
     True),
    ("no match, ordered", "OPTIONAL MATCH (a:Nope)-[r]->() RETURN a.name, r.w ORDER BY a.name", [N], True),
    ("a label alone, ordered", "OPTIONAL MATCH (a:OL) RETURN a.name ORDER BY a.name DESC", [["C"], ["B"], ["A"]],
     True),
    ("a missing label, ordered", "OPTIONAL MATCH (a:Nope) RETURN a.name ORDER BY a.name", [[None]], True),
    ("after a MATCH, control",
     "MATCH (a:OL) OPTIONAL MATCH (a)-[r]->() RETURN a.name, r.w ORDER BY a.name, r.w",
     [["A", 1], ["A", 2], ["B", None], ["C", None]], True),
]

LIST_WHERE = [
    ("x[2] = 3", "UNWIND [1, 2, 3] AS v WITH collect(v) AS x WHERE x[2] = 3 RETURN size(x), x", [[3, [1, 2, 3]]]),
    ("x[2] = 4", "UNWIND [1, 2, 3] AS v WITH collect(v) AS x WHERE x[2] = 4 RETURN size(x)", []),
    ("size(x) = 3", "UNWIND [1, 2, 3] AS v WITH collect(v) AS x WHERE size(x) = 3 RETURN size(x)", [[3]]),
    ("grouped", "UNWIND [1, 2, 3] AS v WITH v % 2 AS k, collect(v) AS x WHERE size(x) > 1 RETURN k, x",
     [[1, [1, 3]]]),
    ("a count, control", "UNWIND [1, 2, 3] AS v WITH count(v) AS c WHERE c = 3 RETURN c", [[3]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat.execute_cypher(U + "CREATE (a:OL {name: 'A'}), (b:OL {name: 'B'}), (c:OL {name: 'C'}), "
                       "(a)-[:R {w: 1}]->(b), (a)-[:R {w: 2}]->(c)", {}, raise_on_error=True)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _rows(engine, query):
    result = engine.execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    return [[json.loads(v) if isinstance(v, str) and v[:1] == "[" else v for v in r] for r in result.rows]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want,ordered", CASES, ids=[c[0] for c in CASES])
def test_optional_match(engines, path, name, query, want, ordered):
    got = _rows(engines[path], U + query)
    if not ordered:
        got, want = sorted(got, key=repr), sorted(want, key=repr)
    assert got == want


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", LIST_WHERE, ids=[c[0] for c in LIST_WHERE])
def test_a_with_where_reads_the_collected_list(engines, path, name, query, want):
    assert _rows(engines[path], query) == want
