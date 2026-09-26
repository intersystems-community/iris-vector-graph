"""Spec 234 US1/US2 gate — Cypher CREATE and MERGE over parallel edges, on IRIS.

The SQL shapes are pinned in ``tests/unit/test_234_multigraph_cypher_sql.py``; this
file proves they do the right thing on the server:

- US1: with the mode on, two CREATEs of the same ``(s, p, o)`` make two edges with
  their own ids and ekeys, and a MATCH or a fixed-length path sees both
  (Match6 [14]); with the mode off, a triple never gets a second edge;
- US2: MERGE binds every parallel edge that fits the pattern (Merge5 [3]), filters
  on inline properties (Merge5 [5]), creates when none fits, and does not fit
  against edges the same statement deletes (Merge5 [21]).

Runs against ``ivg-iris-enterprise`` only. ``iris_master_cleanup`` erases the whole
namespace before and after each test, and the fixture brings ``rdf_edges`` to 234.
"""

from __future__ import annotations

import contextlib
import os

import pytest

pytestmark = [pytest.mark.e2e]


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 234 asserts rows written on the server; SKIP_IRIS_TESTS=true is not an "
            "acceptable outcome — start ivg-iris-enterprise."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection")


@pytest.fixture
def engine(iris_connection, iris_master_cleanup):
    _require_iris(iris_connection)
    import iris as _iris_mod

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    cur = iris_connection.cursor()
    try:
        result = GraphSchema.ensure_ekey(cur)
    finally:
        cur.close()
        with contextlib.suppress(Exception):
            iris_connection.commit()
    assert result["status"] in ("migrated", "already at 234"), result
    iris_obj = _iris_mod.createIRIS(iris_connection)
    iris_obj.classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
    eng = IRISGraphEngine(iris_connection)
    yield eng
    with contextlib.suppress(Exception):
        iris_obj.kill("^IVG.GraphMode")


@pytest.fixture
def multi(engine):
    engine.set_multigraph(None, True)
    return engine


def _rows(engine, query):
    return [list(r) for r in engine.execute_cypher(query, {}).rows]


def _count(engine, query):
    return _rows(engine, query)[0][0]


def _edges(engine, conn_pred):
    cur = engine.conn.cursor()
    try:
        cur.execute(
            "SELECT edge_id, ekey FROM Graph_KG.rdf_edges WHERE p = ? ORDER BY ekey",
            [conn_pred],
        )
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


# --- US1: CREATE -----------------------------------------------------------------------


def test_two_creates_make_two_edges(multi):
    multi.execute_cypher(
        "CREATE (a:MgA), (b:MgB) CREATE (a)-[:MG_T]->(b) CREATE (a)-[:MG_T]->(b)", {}
    )
    edges = _edges(multi, "MG_T")
    assert [k for _, k in edges] == [0, 1]
    assert len({e for e, _ in edges}) == 2
    assert _count(multi, "MATCH (:MgA)-[r:MG_T]->(:MgB) RETURN count(r)") == 2


def test_create_after_match_makes_one_edge_per_row(multi):
    multi.execute_cypher("CREATE (:MgA), (:MgB)", {})
    multi.execute_cypher("MATCH (a:MgA), (b:MgB) CREATE (a)-[:MG_T]->(b)", {})
    multi.execute_cypher("MATCH (a:MgA), (b:MgB) CREATE (a)-[:MG_T]->(b)", {})
    assert [k for _, k in _edges(multi, "MG_T")] == [0, 1]


def test_create_after_match_keeps_the_properties(multi):
    multi.execute_cypher("CREATE (:MgA), (:MgB)", {})
    multi.execute_cypher("MATCH (a:MgA), (b:MgB) CREATE (a)-[:MG_T {name: 'x'}]->(b)", {})
    multi.execute_cypher("MATCH (a:MgA), (b:MgB) CREATE (a)-[:MG_T {name: 'y'}]->(b)", {})
    rows = _rows(multi, "MATCH (:MgA)-[r:MG_T]->(:MgB) RETURN r.name")
    assert sorted(rows) == [["x"], ["y"]]


def test_a_path_walks_each_parallel_edge(multi):
    # Match6 [14]
    multi.execute_cypher(
        "CREATE (db1:MgStart), (db2:MgEnd), (mid:MgN), (other:MgN) "
        "CREATE (mid)-[:MG_C]->(db1), (mid)-[:MG_C]->(db2), (mid)-[:MG_C]->(db2), "
        "(mid)-[:MG_C]->(other), (mid)-[:MG_C]->(other)",
        {},
    )
    rows = _rows(
        multi,
        "MATCH p = (:MgStart)<-[:MG_C]-()-[:MG_C*3..3]-(:MgEnd) RETURN length(p)",
    )
    assert rows == [[4]] * 4


def test_mode_off_never_makes_a_second_edge(engine):
    # Today's behaviour: the triple's unique key refuses the second INSERT (-119)
    # and the statement rolls back; after MATCH, the NOT EXISTS guard skips it.
    with contextlib.suppress(Exception):
        engine.execute_cypher(
            "CREATE (a:MgA), (b:MgB) CREATE (a)-[:MG_T]->(b) CREATE (a)-[:MG_T]->(b)", {}
        )
    assert len(_edges(engine, "MG_T")) == 0
    engine.execute_cypher("CREATE (:MgA), (:MgB)", {})
    engine.execute_cypher("MATCH (a:MgA), (b:MgB) CREATE (a)-[:MG_T]->(b)", {})
    engine.execute_cypher("MATCH (a:MgA), (b:MgB) CREATE (a)-[:MG_T]->(b)", {})
    assert [k for _, k in _edges(engine, "MG_T")] == [0]


# --- US2: MERGE ------------------------------------------------------------------------


def test_merge_binds_every_parallel_edge(multi):
    # Merge5 [3]
    multi.execute_cypher(
        "CREATE (a:MgA), (b:MgB) CREATE (a)-[:MG_T]->(b) CREATE (a)-[:MG_T]->(b)", {}
    )
    assert _count(multi, "MATCH (a:MgA), (b:MgB) MERGE (a)-[r:MG_T]->(b) RETURN count(r)") == 2
    assert len(_edges(multi, "MG_T")) == 2


def test_merge_filters_on_inline_properties(multi):
    # Merge5 [5]
    multi.execute_cypher(
        "CREATE (a:MgA), (b:MgB) CREATE (a)-[:MG_T {name: 'r1'}]->(b) "
        "CREATE (a)-[:MG_T {name: 'r2'}]->(b)",
        {},
    )
    q = "MATCH (a:MgA), (b:MgB) MERGE (a)-[r:MG_T {name: 'r2'}]->(b) RETURN count(r)"
    assert _count(multi, q) == 1
    assert len(_edges(multi, "MG_T")) == 2


def test_merge_creates_when_no_parallel_edge_fits(multi):
    multi.execute_cypher(
        "CREATE (a:MgA), (b:MgB) CREATE (a)-[:MG_T {name: 'r1'}]->(b)", {}
    )
    q = "MATCH (a:MgA), (b:MgB) MERGE (a)-[r:MG_T {name: 'r3'}]->(b) RETURN r.name"
    assert _rows(multi, q) == [["r3"]]
    assert [k for _, k in _edges(multi, "MG_T")] == [0, 1]
    assert _rows(multi, q) == [["r3"]], "a second MERGE fits the edge the first made"
    assert len(_edges(multi, "MG_T")) == 2


def test_merge_does_not_fit_edges_the_statement_deletes(multi):
    # Merge5 [21]
    multi.execute_cypher(
        "CREATE (a:MgA), (b:MgB) "
        "CREATE (a)-[:MG_T {name: 'rel1'}]->(b), (a)-[:MG_T {name: 'rel2'}]->(b)",
        {},
    )
    rows = _rows(
        multi,
        "MATCH (a)-[t:MG_T]->(b) DELETE t MERGE (a)-[t2:MG_T {name: 'rel3'}]->(b) "
        "RETURN t2.name",
    )
    assert rows == [["rel3"], ["rel3"]]
    assert _rows(multi, "MATCH (:MgA)-[r:MG_T]->(:MgB) RETURN r.name") == [["rel3"]]
