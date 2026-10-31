"""set_edge_weight on a multigraph triple: every row or none (Tom, Q5 = b, 2026-10-31).

The default graph is put in multigraph mode for the test, a triple gets two rows (ekey 0 and
1), and a second connection holds a row lock on the second row inside an open transaction, so
set_edge_weight's second UPDATE fails with a lock timeout (SQLCODE -110) after its first has
run. Before the fix the first row kept the new weight (its UPDATE had committed on its own);
now the call answers False, as documented, and both rows, ^KG("out"/"in") and the degree are
what they were. ^KG("gen") is not compared: a generation bump survives a rollback by design
(Graph.KG.Adjacency: an over-bump only makes a cache entry look stale).

`SKIP_IRIS_TESTS=true` fails rather than skips.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "SEWTXN"


@pytest.fixture
def triple(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail("this test asserts what a failed write leaves in IRIS; a skip is not a pass")
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    tag = "sew" + uuid.uuid4().hex[:6]
    s, o = tag + "s", tag + "o"
    was_multi = eng.is_multigraph(None)
    eng.set_multigraph(None, True)
    cur = iris_connection.cursor()
    try:
        for n in (s, o):
            cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, '')", [n])
        for ekey, w in ((0, 1.0), (1, 2.0)):
            cur.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey, qualifiers) VALUES (?, ?, ?, '', ?, ?)",
                [s, PRED, o, ekey, json.dumps({"weight": w})],
            )
        iris_connection.commit()
        yield eng, iris_connection, s, o
    finally:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND p = ?", [s, PRED])
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id IN (?, ?)", [s, o])
        iris_connection.commit()
        cur.close()
        eng.set_multigraph(None, was_multi)


def _state(eng, conn, s, o) -> dict:
    import iris

    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT ekey, qualifiers FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? ORDER BY ekey",
            [s, PRED, o],
        )
        rows = {int(k): json.loads(q)["weight"] for k, q in cur.fetchall()}
    finally:
        cur.close()
    obj = iris.createIRIS(conn)

    def walk(*subs):
        out, cur_sub = {}, ""
        while True:
            cur_sub = obj.nextSubscript(False, "^KG", *subs, cur_sub)
            if cur_sub in (None, ""):
                return out
            out[cur_sub] = obj.getString("^KG", *subs, cur_sub) if obj.isDefined("^KG", *subs, cur_sub) in (1, 11) else None

    return {
        "rows": rows,
        "out_leaf": obj.getString("^KG", "out", 0, s, PRED, o),
        "out_keys": walk("out", 0, s, PRED, o),
        "in_leaf": obj.getString("^KG", "in", 0, o, PRED, s),
        "degree": int(obj.classMethodValue("Graph.KG.Adj", "Degree", 0, s)),
    }


def test_a_failed_second_row_update_leaves_every_row_and_the_adjacency_as_they_were(triple):
    import iris

    eng, conn, s, o = triple
    before = _state(eng, conn, s, o)
    assert before["rows"] == {0: 1.0, 1: 2.0}
    other = iris.dbapi.connect(hostname=conn.hostname, port=conn.port, namespace=conn.namespace,
                               username="_SYSTEM", password="SYS")
    hold = other.cursor()
    try:
        hold.execute("START TRANSACTION")
        hold.execute(
            "UPDATE Graph_KG.rdf_edges SET qualifiers = qualifiers WHERE s = ? AND p = ? AND o_id = ? AND ekey = 1",
            [s, PRED, o],
        )
        t0 = time.monotonic()
        assert eng.set_edge_weight(s, PRED, o, 9.0) is False
        waited = time.monotonic() - t0
    finally:
        hold.execute("ROLLBACK")
        hold.close()
        other.close()
    print(f"\nset_edge_weight answered False after {waited:.1f} s (lock timeout on the second row)")
    assert _state(eng, conn, s, o) == before


def test_a_multigraph_update_sets_every_row(triple):
    eng, conn, s, o = triple
    assert eng.set_edge_weight(s, PRED, o, 9.0) is True
    after = _state(eng, conn, s, o)
    assert after["rows"] == {0: 9.0, 1: 9.0}
    assert float(after["out_leaf"]) == 9.0
