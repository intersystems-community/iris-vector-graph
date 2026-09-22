"""`arno_master_cleanup` must actually empty the database, not merely try to.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_arno_master_cleanup.py

The same defect as `iris_master_cleanup` (see tests/integration/test_master_cleanup.py),
one fixture over: the clear was a hand-kept list of eight tables followed by
`iris.createIRIS(arno_iris_connection)`, and that connection is a dbapi
connection the session-level `createIRIS` monkeypatch does not redirect — so the
call reached the original, which rejects a dbapi connection, inside
`contextlib.suppress`. The `Do ##class(Graph.KG.Traversal).BuildKG()` line
below it never ran either: IRIS SQL rejects a `Do` statement at prepare time
with SQLCODE -51, also suppressed.

The assertions here are pure SQL, deliberately. Reading `^KG` needs the native
API, which is what was broken; a probe that depends on it could not tell a
working clear from a broken one.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


PROBE = "arnoprobe_graph"


def _native(dbapi_conn):
    """A native handle on the arno connection's database.

    Opened here rather than taken from the connection because that is exactly
    what the fixture could not do: `iris.createIRIS` is monkeypatched to redirect
    only the session connection, and the original rejects a dbapi connection.
    """
    import iris

    native = iris.connect(
        hostname=dbapi_conn.hostname,
        port=dbapi_conn.port,
        namespace=dbapi_conn.namespace,
        username="_SYSTEM",
        password="SYS",
    )
    return iris.createIRIS(native), native


def test_a_seed_the_next_test_must_not_see(arno_iris_connection, arno_master_cleanup):
    """Both halves: a row, and the adjacency global that the row's edge implies."""
    cursor = arno_iris_connection.cursor()
    iris_obj, native = _native(arno_iris_connection)
    try:
        # The endpoints are registered first because spec 227 put a composite
        # foreign key on `rdf_edges` — `(graph_id, s)` and `(graph_id, o_id)` both
        # reference `nodes (graph_id, node_id)` — so an edge naming nodes that are
        # not in its own graph is refused with SQLCODE -121. The seed is about the
        # cleanup, not about dangling edges.
        for node_id in ("arnoprobe_s", "arnoprobe_o"):
            cursor.execute(
                "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
                [node_id, PROBE],
            )
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) "
            "VALUES ('arnoprobe_s', 'PROBES', 'arnoprobe_o', ?)",
            [PROBE],
        )
        arno_iris_connection.commit()
        iris_obj.set(1, "^KG", "out", PROBE, "arnoprobe_s", "PROBES", "arnoprobe_o")
        assert iris_obj.get("^KG", "out", PROBE, "arnoprobe_s", "PROBES", "arnoprobe_o") is not None
    finally:
        cursor.close()
        native.close()


def test_the_previous_test_left_no_rows_and_no_adjacency(
    arno_iris_connection, arno_master_cleanup
):
    cursor = arno_iris_connection.cursor()
    iris_obj, native = _native(arno_iris_connection)
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = 'arnoprobe_s'"
        )
        assert int(cursor.fetchone()[0]) == 0, (
            "arno_master_cleanup did not clear the tables"
        )
        assert iris_obj.get("^KG", "out", PROBE, "arnoprobe_s", "PROBES", "arnoprobe_o") is None, (
            "arno_master_cleanup reported success without killing ^KG — the Arno "
            "tests run against the previous test's adjacency"
        )
    finally:
        cursor.close()
        native.close()
