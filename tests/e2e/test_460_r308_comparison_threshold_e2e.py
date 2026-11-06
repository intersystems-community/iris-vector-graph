"""4.6.0 R308, live: TCK Comparison2 [3]'s statement, and a longer one, prepare and answer on this container in both
graph modes, every IRIS cached query purged before each statement (a cached plan of a near twin decided the outcome,
which made earlier measurements order-dependent).

`WITH [<list>] AS types UNWIND range(0, size(types) - 1) AS i UNWIND range(0, size(types) - 1) AS j WITH types[i] AS
lhs, types[j] AS rhs WHERE i <> j WITH lhs, rhs, lhs <op> rhs AS result WHERE result RETURN lhs, rhs`. On IRIS
2026.3 it failed at Prepare (-400 <LIST>LoadTableFunction) for some operators with one graph in the namespace and for
all with a second graph holding an edge; a longer list failed at Fetch (-400 <SUBSCRIPT> in ^||%t). The answer,
worked by hand from openCypher (a node, a relationship, a path, a map and null compare with nothing, '' with no other
string, true with no other boolean): only the two numbers compare, one row per relationship the MATCH finds.

Shapes: the TCK's (`MATCH p = (n)-[r]->()`, every relationship in the namespace) and a labelled one
(`(n:ZZC308)`, this file's relationship only); lists of 10 (the TCK's) and 20 elements, which must answer, and 40, which
must answer or fail at Prepare with the KNOWN_ISSUES error (the others compare with nothing); operators < <= >= >; one graph, and two (a named graph with an edge of its own). The namespace is
`IVG_NS_IVG460FXOC`; it must hold no other relationship (the TCK shape reads them all).
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_NS_IVG460FXOC", "IVG460FXOC")
TEN = "n, r, p, '', 1, 3.14, true, null, [], {}"
TWENTY = TEN + ", n, r, p, null, {}, {k: 1}, n, r, p, null"
FORTY = TWENTY + ", n, r, p, null, {}, {k: 2}, n, r, p, null" * 2
EXPECTED = {"<": (1, 3.14), "<=": (1, 3.14), ">=": (3.14, 1), ">": (3.14, 1)}


@pytest.fixture(scope="module", params=["one graph", "two graphs"])
def namespace(request):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", ns262.PORT, NS, "_SYSTEM", "SYS")
    engine = IRISGraphEngine(conn, namespace=NS, native_cypher=False)
    existing = engine.execute_cypher("MATCH ()-[r]->() RETURN count(r)").rows[0][0]
    assert existing == 0, f"{NS} holds {existing} relationships; the TCK shape reads every one"
    engine.execute_cypher("CREATE (:ZZC308)-[:T]->(:ZZC308)", raise_on_error=True)
    two = request.param == "two graphs"
    if two:
        engine.create_node("zzc308g:1", labels=["ZZC308G"], graph="zzc308g")
        engine.create_node("zzc308g:2", labels=["ZZC308G"], graph="zzc308g")
        engine.create_edge("zzc308g:1", "T", "zzc308g:2", graph="zzc308g")
    yield {"engine": engine, "conn": conn, "native": iris.createIRIS(conn), "relationships": 2 if two else 1}
    engine.execute_cypher("MATCH (n:ZZC308) DETACH DELETE n")
    if two:
        engine.delete_nodes_by_prefix("zzc308g:", graph="zzc308g")
    conn.close()


@pytest.mark.parametrize("items", [TEN, TWENTY, FORTY], ids=["10 elements", "20 elements", "40 elements"])
@pytest.mark.parametrize("start", ["(n)", "(n:ZZC308)"], ids=["TCK shape", "labelled"])
@pytest.mark.parametrize("op", ["<", "<=", ">=", ">"])
def test_comparing_across_types_answers(namespace, items, start, op):
    q = (f"MATCH p = {start}-[r]->() WITH [{items}] AS types "
         "UNWIND range(0, size(types) - 1) AS i UNWIND range(0, size(types) - 1) AS j "
         f"WITH types[i] AS lhs, types[j] AS rhs WHERE i <> j WITH lhs, rhs, lhs {op} rhs AS result "
         "WHERE result RETURN lhs, rhs")
    namespace["native"].classMethodValue("%SYSTEM.SQL", "Purge")
    r = namespace["engine"].execute_cypher(q)
    if items is FORTY and r.error and "<LIST>LoadTableFunction" in r.error:
        # docs/KNOWN_ISSUES.md, "On IRIS 2026.3 a list of many whole nodes unwound by position can fail at Prepare": past 20 elements it may
        # refuse, never answer wrong (measured: 40 elements, labelled `<=` / `>=`, one graph)
        return
    assert not r.error, r.error
    rows = 1 if start != "(n)" else namespace["relationships"]
    assert [tuple(float(v) for v in row) for row in r.rows] == [EXPECTED[op]] * rows, r.rows
