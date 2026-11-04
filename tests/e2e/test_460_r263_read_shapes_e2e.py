"""4.6.0 R263, R267 (the CALL subquery case), live: read shapes that failed or answered the wrong rows or types.

Found by fixes30 and fixes33, all on the 4.5.x baseline too:
- `CALL { MATCH (n) RETURN n } RETURN n` failed -29, and `CALL { WITH y MATCH (y)-[:K]->(z) RETURN z } RETURN
  z.name` -25 (an imported variable);
- `OPTIONAL MATCH (n:L {id:'nope'}) RETURN n` answered no row where openCypher answers one row holding null, when the
  label has nodes but none match;
- `CALL db.labels() YIELD label RETURN label LIMIT 1` ignored the LIMIT;
- native `RETURN n.g` of a stored 5.0 answered the integer 5 (the SQL path 5.0);
- `WHERE x IN [1, null]` failed -14, and `RETURN n.g IN [5.0, null]` "Incorrect number of parameters";
- a sum carried through WITH turned a whole float into an integer (`UNWIND [1.5, 2.5] AS v WITH sum(v) AS s RETURN s`
  answered 4).

Graph r263g, written by Cypher: (:L {id: 'a', name: 'A', g: 5.0}), (:L {id: 'b', name: 'B', g: 2.0}),
(:M {id: 'c', name: 'C'}), and (a)-[:K]->(b). Worked by hand:
- the CALL over (n:L) answers A and B; from y = a the subquery's z is B;
- no L has id 'nope': one row, n null. A node in a result row is three columns (id, labels, properties), so the
  null node is three nulls, as `OPTIONAL MATCH (n:NoSuchLabel) RETURN n` already answers;
- LIMIT 1 answers one label of the several there are;
- a.g is the float 5.0;
- 1 IN [1, null] is true and 2 IN [1, null] null, so the WHERE keeps x = 1; 5.0 IN [5.0, null] is true, 2.0 IN
  [5.0, null] null;
- 1.5 + 2.5 = 4.0 and 5.0 + 2.0 = 7.0, floats; 1 + 2 = 3 stays an integer.

Namespace: `IVG_NAMESPACE`, an IVG460F* scratch namespace with this checkout's routines and classes. Run it under
intersystems-irispython 5.4.0 (the repo .venv) and 5.4.1:

    IVG_NAMESPACE=IVG460FX35 IVG_TEST_CONTAINER=fixes35ce-iris-1 IVG_PORT=42972 \
        pytest tests/e2e/test_460_r263_read_shapes_e2e.py
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

G = "r263g"
U = f"USE GRAPH '{G}' "

#: (name, query, rows by hand)
CASES = [
    ("R263: CALL { MATCH (n) RETURN n } RETURN n",
     U + "CALL { MATCH (n:L) RETURN n } RETURN n.name ORDER BY n.name", [["A"], ["B"]]),
    ("R263: CALL { MATCH (n) RETURN n } RETURN n, whole nodes",
     U + "CALL { MATCH (n:L) RETURN n } RETURN count(n)", [[2]]),
    ("R267: CALL { WITH y MATCH (y)-[:K]->(z) RETURN z } RETURN z.name",
     U + "MATCH (y:L {id: 'a'}) CALL { WITH y MATCH (y)-[:K]->(z) RETURN z } RETURN z.name", [["B"]]),
    ("R263: OPTIONAL MATCH of a label with nodes, none matching",
     U + "OPTIONAL MATCH (n:L {id: 'nope'}) RETURN n", [[None, None, None]]),
    ("OPTIONAL MATCH of a label with nodes, none matching, a property",
     U + "OPTIONAL MATCH (n:L {id: 'nope'}) RETURN n.name", [[None]]),
    ("R263: native RETURN n.g of a stored 5.0",
     U + "MATCH (n:L {id: 'a'}) RETURN n.g", [[5.0]]),
    ("R263: x IN [1, null] in a WHERE",
     "UNWIND [1, 2] AS x WITH x WHERE x IN [1, null] RETURN x", [[1]]),
    ("a stored value IN [5.0, null] in a WHERE",
     U + "MATCH (n:L) WHERE n.g IN [5.0, null] RETURN n.name", [["A"]]),
    ("R263: a stored value IN [5.0, null] in a RETURN",
     U + "MATCH (n:L) RETURN n.name, n.g IN [5.0, null] ORDER BY n.name", [["A", True], ["B", None]]),
    ("x IN [1, null] in a RETURN",
     "UNWIND [1, 2] AS x RETURN x, x IN [1, null] ORDER BY x", [[1, True], [2, None]]),
    ("R263: a sum of floats carried through WITH",
     "UNWIND [1.5, 2.5] AS v WITH sum(v) AS s RETURN s", [[4.0]]),
    ("a sum of stored floats carried through WITH",
     U + "MATCH (n:L) WITH sum(n.g) AS s RETURN s", [[7.0]]),
    ("a sum of integers carried through WITH",
     "UNWIND [1, 2] AS v WITH sum(v) AS s RETURN s", [[3]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat.execute_cypher(
        U + "CREATE (a:L {id: 'a', name: 'A', g: 5.0}), (b:L {id: 'b', name: 'B', g: 2.0}), "
        "(:M {id: 'c', name: 'C'}), (a)-[:K]->(b)", {}, raise_on_error=True)
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
    return [list(r) for r in result.rows]


def _typed(rows):
    """Each value with its type: 5 is not 5.0, 1 is not True."""
    return [[(type(v).__name__, v) for v in row] for row in rows]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_the_read_answers_its_rows_with_their_types(engines, path, name, query, want):
    assert _typed(_rows(engines[path], query)) == _typed(want)


@pytest.mark.parametrize("path", ["native", "sql"])
def test_db_labels_keeps_its_limit(engines, path):
    every = [r[0] for r in _rows(engines[path], "CALL db.labels() YIELD label RETURN label")]
    assert {"L", "M"} <= set(every), every  # the namespace has several labels, so a LIMIT has rows to cut
    got = _rows(engines[path], "CALL db.labels() YIELD label RETURN label LIMIT 1")
    assert len(got) == 1 and got[0][0] in every, got


@pytest.mark.parametrize("path", ["native", "sql"])
def test_db_relationship_types_keeps_its_limit(engines, path):
    engines[path].execute_cypher(U + "MATCH (a:L {id: 'a'}), (c:M {id: 'c'}) MERGE (a)-[:J]->(c)", {},
                                 raise_on_error=True)
    every = [r[0] for r in _rows(engines[path], "CALL db.relationshipTypes() YIELD relationshipType "
                                                "RETURN relationshipType")]
    assert {"K", "J"} <= set(every), every
    got = _rows(engines[path], "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType LIMIT 1")
    assert len(got) == 1 and got[0][0] in every, got
