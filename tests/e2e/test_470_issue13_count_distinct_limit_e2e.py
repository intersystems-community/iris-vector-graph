"""4.7.0 row 1, GitHub #13 (ArnolfJp), live: count(DISTINCT ...) + ORDER BY that count + LIMIT on a from_connect() engine.

Reported on 4.5.1 against IRIS Community 2026.1 (Build 234U) and reproduced on 4.6.5 (release-4.7.0 at 9995d0d7) in a
throwaway `containers.intersystems.com/intersystems/iris-community:2026.1` container: the from_connect() engine
(whose subprocess probe found the JOIN shape safe, so it emitted FETCH FIRST) failed at Prepare, SQLCODE -400
`<UNDEFINED>main+129^%qaqcasl *mt("top")`; the engine made from a connection (no stored parameters, so the $ZVERSION
fallback, TOP) answered. On that build any ORDER BY of an aggregate with FETCH FIRST, LIMIT or OFFSET fails at
Prepare, on one table too; 2026.3 takes them all.

Known answer, from the issue: both engines answer [['InterSystems', 2]] with LIMIT 5 and without; InterSystems'
two products are the only DEVELOPED_BY edges, so SKIP 1 LIMIT 5 answers no row. The two products' names in order are
'InterSystems Caché', 'InterSystems IRIS' ('C' before 'I'): SKIP 1 answers the second, LIMIT 1 the first.

Also measured on 9995d0d7: on 2026.3 enterprise, whose probe finds FETCH FIRST safe, a from_connect() engine wrote
SKIP with LIMIT as `FETCH FIRST n ROWS ONLY OFFSET k`, which IRIS refuses (SQLCODE -25, "Input encountered after end
of query"): every SKIP + LIMIT query failed on such an engine. OFFSET goes before FETCH.

Where: the IRIS of `IVG_ISSUE13_PORT` (default 31972), namespace `IVG_ISSUE13_NS`, else `IVG_NAMESPACE` (an IVG470*
scratch namespace, the E2E gate's primary one, or USER on a throwaway container: tests/e2e/gate.py's
SESSION_NAMESPACES says how to make the 2026.1 one), user `IVG_ISSUE13_USER` / password `IVG_ISSUE13_PASSWORD`.
Run it on 2026.1 Community and on 2026.3 enterprise.
"""

from __future__ import annotations

import os

import pytest
from tests.e2e import namespace_width

PORT = int(os.environ.get("IVG_ISSUE13_PORT", os.environ.get("IVG_PORT", "31972")))
NS = os.environ.get("IVG_ISSUE13_NS", os.environ.get("IVG_NAMESPACE", ""))
USER = os.environ.get("IVG_ISSUE13_USER", "_SYSTEM")
PASSWORD = os.environ.get("IVG_ISSUE13_PASSWORD", "SYS")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not (NS.startswith(("IVG470", "IVG460FXG"))
                            or (NS == "USER" and "IVG_ISSUE13_PORT" in os.environ)),
                       reason="IVG_ISSUE13_NS must name an IVG470* scratch namespace (the gate's IVG460FXG* one), or "
                              "USER on a throwaway container given by IVG_ISSUE13_PORT"),
]

NODES = [("Q948399", "Organization", "InterSystems"), ("Q7185789", "Person", "Phillip Ragon"),
         ("Q1882071", "Language", "MUMPS"), ("Q1150391", "Product", "InterSystems Caché"),
         ("L0645", "Product", "InterSystems IRIS")]
EDGES = [("Q948399", "FOUNDED_BY", "Q7185789"), ("Q1150391", "DEVELOPED_BY", "Q948399"),
         ("L0645", "DEVELOPED_BY", "Q948399"), ("Q1150391", "IMPLEMENTS", "Q1882071")]
QUERY = ("MATCH (x)-[:DEVELOPED_BY]->(c) RETURN c.name AS company, count(DISTINCT x.name) AS products "
         "ORDER BY products DESC LIMIT 5")


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect("127.0.0.1", PORT, NS, USER, PASSWORD)
    dim = namespace_width.of(conn)  # the width the namespace is set up at (4.7.0 row 25)
    e1 = IRISGraphEngine.from_connect("127.0.0.1", PORT, NS, USER, PASSWORD, embedding_dimension=dim)
    e1.initialize_schema()
    for nid, _, _ in NODES:
        e1.delete_node(nid)
    for nid, label, name in NODES:
        assert e1.create_node(nid, labels=[label], properties={"name": name})
    for s, p, o in EDGES:
        assert e1.create_edge(s, p, o)
    e2 = IRISGraphEngine(conn, embedding_dimension=dim)
    yield {"from_connect": e1, "connection": e2}
    for nid, _, _ in NODES:
        e1.delete_node(nid)
    conn.close()


@pytest.mark.parametrize("engine", ["from_connect", "connection"])
@pytest.mark.parametrize("query, want", [
    (QUERY, [["InterSystems", 2]]),
    (QUERY.replace(" LIMIT 5", ""), [["InterSystems", 2]]),
    (QUERY.replace(" LIMIT 5", " SKIP 1 LIMIT 5"), []),
    (QUERY.replace(" LIMIT 5", " LIMIT 1"), [["InterSystems", 2]]),
    ("MATCH (x)-[:DEVELOPED_BY]->(c) RETURN x.name AS p ORDER BY p SKIP 1 LIMIT 5", [["InterSystems IRIS"]]),
    ("MATCH (x)-[:DEVELOPED_BY]->(c) RETURN x.name AS p ORDER BY p SKIP 1", [["InterSystems IRIS"]]),
    ("MATCH (x)-[:DEVELOPED_BY]->(c) RETURN x.name AS p ORDER BY p LIMIT 1", [["InterSystems Caché"]]),
], ids=["limit5", "nolimit", "skip1limit5", "limit1", "plain-skip-limit", "plain-skip", "plain-limit"])
def test_the_issue_query_answers_on_both_engines(engines, engine, query, want):
    r = engines[engine].execute_cypher(query, {}, raise_on_error=True)
    assert [list(row) for row in r.rows] == want
