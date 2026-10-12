"""Traversal readers answer from the `out` entries, not from the stored counters.

Package adj-traversal-readers (spec 243). `^KG("deg")` and `^KG("degp")` are not read by
`KHopCount`, the BFS predicate pruning, or the rebuild's `^NKG(-3)` and `deg2p`. The
test proves it against IRIS: after an edge is removed, a stored counter is set to a
value that disagrees with the rows, and every reader must still answer what the SQL
rows say.

Uncompiled: the ObjectScript these tests call was written in this package without
the container. The lead runs this file in the serial e2e pass.

`SKIP_IRIS_TESTS=true` fails rather than skips, as in test_230_erase_residue.py.
"""

from __future__ import annotations

import contextlib
import json
import os

import pytest

pytestmark = [pytest.mark.e2e]

PREFIX = "ivg243:trav:"
A = PREFIX + "a"  # A -P-> B, A -P-> X
B = PREFIX + "b"  # B -Q-> D
X = PREFIX + "x"  # X -P-> Y (removed), X -Q-> Z: its only P edge goes away
Y = PREFIX + "y"
Z = PREFIX + "z"
D = PREFIX + "d"
S = PREFIX + "s"  # S -P-> X, S -P-> W: two-hop on P through a middle node
W = PREFIX + "w"  # W -P-> V1, W -P-> V2
V1 = PREFIX + "v1"
V2 = PREFIX + "v2"

P = "IVG243_TRAV_P"
Q = "IVG243_TRAV_Q"

NODES = (A, B, X, Y, Z, D, S, W, V1, V2)

#: (s, p, o) edges as they exist after the setup. X-P->Y is removed in the test.
EDGES = (
    (A, P, B),
    (A, P, X),
    (B, Q, D),
    (X, P, Y),
    (X, Q, Z),
    (S, P, X),
    (S, P, W),
    (W, P, V1),
    (W, P, V2),
)
REMOVED = (X, P, Y)


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 243 asserts reader values against IRIS. SKIP_IRIS_TESTS=true is not "
            "an acceptable outcome; start ivg-iris-enterprise first."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection")


def _wipe(conn):
    cursor = conn.cursor()
    try:
        for node_id in NODES:
            for sql, params in (
                ("DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?", (node_id, node_id)),
                ("DELETE FROM Graph_KG.rdf_props WHERE s = ?", (node_id,)),
                ("DELETE FROM Graph_KG.rdf_labels WHERE s = ?", (node_id,)),
                ("DELETE FROM Graph_KG.nodes WHERE node_id = ?", (node_id,)),
            ):
                with contextlib.suppress(Exception):
                    cursor.execute(sql, params)
        with contextlib.suppress(Exception):
            conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _sql_out_count(conn, source: str, pred: str | None = None) -> int:
    """Outgoing rows of `source` (optionally on `pred`), counted from the table."""
    sql = "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ?"
    params: tuple = (source,)
    if pred is not None:
        sql += " AND p = ?"
        params += (pred,)
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()


def _sql_edges(conn) -> set:
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE s LIKE ?", (PREFIX + "%",))
        return {(row[0], row[1], row[2]) for row in cursor.fetchall()}
    finally:
        cursor.close()


def _oracle_bfs(edges: set, src: str, preds: list, max_hops: int) -> set:
    """The fast BFS's answer with no cycles and no repeated node in this fixture.

    Hop h follows preds[h-1] (the last predicate repeats). A reached node enters the
    frontier only if it has an edge of the next predicate, except on the last hop.
    Every edge followed is a result, so pruning removes only the expansion.
    """
    out: dict = {}
    for s, p, o in edges:
        out.setdefault((s, p), set()).add(o)

    def pick(idx: int) -> str:
        return preds[min(idx, len(preds) - 1)]

    results: set = set()
    frontier = {src}
    for hop in range(1, max_hops + 1):
        fixed = pick(hop - 1)
        new_frontier: set = set()
        for s in frontier:
            for o in out.get((s, fixed), set()):
                results.add((s, fixed, o))
                if hop < max_hops and not out.get((o, pick(hop))):
                    continue
                new_frontier.add(o)
        frontier = new_frontier
    return results


def _lie_about_counters(iris_obj):
    """Stored counters that disagree with the rows: too high for X, and a P-counter
    on X that says it has P edges. A reader that still reads them answers wrongly."""
    iris_obj.set("5", "^KG", "deg", 0, X)
    iris_obj.set("7", "^KG", "degp", 0, X, P)


def _bfs(iris_obj, src: str) -> list:
    raw = iris_obj.classMethodValue(
        "Graph.KG.TraversalBFS", "BFSFastJson", src, json.dumps([P, Q]), 2, "", "out", ""
    )
    return json.loads(raw)


@pytest.fixture
def traversal_env(iris_connection):
    _require_iris(iris_connection)
    import iris

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    iris_obj = iris.createIRIS(iris_connection)
    _wipe(iris_connection)

    for node_id in NODES:
        engine.create_node(node_id, labels=["Ivg243Trav"], properties={"name": node_id})
    for s, p, o in EDGES:
        engine.create_edge(s, p, o)
    # The removal is the drift the spec is about: X loses its only P edge.
    engine.delete_edge(*REMOVED)
    # A full rebuild makes ^KG and ^NKG match the rows; the counters are then lied to.
    iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")
    _lie_about_counters(iris_obj)

    yield iris_obj, iris_connection
    _wipe(iris_connection)
    # The rows are gone; rebuild the derived stores from them so no ^KG residue outlives the test
    # (the derived-store oracle reads every graph and reports leftovers as ghosts).
    iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")


def test_bfs_predicate_sequence_matches_rows(traversal_env):
    """BFS with a predicate sequence, after X lost its only P edge, equals the oracle."""
    iris_obj, conn = traversal_env
    expected = _oracle_bfs(_sql_edges(conn), A, [P, Q], 2)
    got = {(item["s"], item["p"], item["o"]) for item in _bfs(iris_obj, A)}
    assert got == expected


def test_bfs_from_the_node_that_lost_its_predicate_is_empty(traversal_env):
    """X has no P edge any more, so a P-first walk from X finds nothing even though
    the stored counter says it has P edges."""
    iris_obj, conn = traversal_env
    assert _sql_out_count(conn, X, P) == 0
    assert _bfs(iris_obj, X) == []


def test_khop_count_is_counted_from_rows(traversal_env):
    """KHopCount returns the row count, not the stored counter (5 for X, 7 on P)."""
    iris_obj, conn = traversal_env
    assert int(iris_obj.classMethodValue("Graph.KG.TraversalKHop", "KHopCount", X)) == _sql_out_count(conn, X)
    assert int(iris_obj.classMethodValue("Graph.KG.TraversalKHop", "KHopCount", X, P)) == _sql_out_count(conn, X, P)


def test_deg2p_is_the_sum_of_middle_node_counts_from_rows(traversal_env):
    """deg2p(S, P) = P-degree of X (now 0) + P-degree of W (2) = 2. The lie on X would give 9."""
    iris_obj, conn = traversal_env
    expected = _sql_out_count(conn, X, P) + _sql_out_count(conn, W, P)
    assert expected == 2
    got = int(iris_obj.getString("^KG", "deg2p", 0, S, P) or 0)
    assert got == expected


def test_nkg_merged_degree_matches_rows(traversal_env):
    """^NKG(-3, s) is the merged out-degree of s. X's count is the Q edge only."""
    iris_obj, conn = traversal_env
    for node in (X, S, W, A):
        idx = iris_obj.getString("^NKG", "$NI", node)
        assert idx not in (None, ""), f"{node} is not interned in ^NKG"
        got = int(iris_obj.getString("^NKG", -3, int(idx)) or 0)
        assert got == _sql_out_count(conn, node), node
