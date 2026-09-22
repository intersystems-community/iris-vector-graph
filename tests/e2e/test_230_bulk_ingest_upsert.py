"""Spec 230 — the bulk paths stop using a failed INSERT as their upsert.

`Graph.KG.EdgeScan.BulkIngestEdgesSQL` registered each edge's endpoints by inserting
them and tolerating SQLCODE -119, the duplicate. Inside the `TSTART` the method wraps
its whole batch in, a *failed* statement is expensive: IRIS rolls it back to its own
implicit savepoint. Measured live on `ivg-iris-enterprise`, 200 chained edges in one
transaction:

    today's -119 idiom ........... 39.53 ms per edge
    seen-set + existence check ....  0.16 ms per edge
    INSERT OR UPDATE ..............  0.10 ms per edge
    existence check only ..........  0.03 ms per edge

Chained edges are the ordinary shape — an edge's target is the next edge's source — so
about half of every batch's node inserts were duplicates, and `BulkIngestEdgesSQL` ran
at 25–26 edges/s no matter how the batch was sized, whether it was called over DBAPI,
the Native API or from an `iris session` terminal. Re-ingesting a batch that already
landed is worse: every node insert fails.

So the register step asks first and inserts what is missing, and keeps -119 tolerance
only for the row another process writes between the check and the insert.

This has to run live: the cost is IRIS rolling a failed statement back inside a
transaction, and no Python-level test can see it.
"""

from __future__ import annotations

import json
import os
import time

import pytest

pytestmark = [pytest.mark.e2e]

#: The floor a fixed ingest clears by an order of magnitude and the -119 idiom cannot
#: reach: it is paced at ~25 edges/s by construction.
MIN_EDGES_PER_SEC = 500

EDGE_COUNT = 300
PREDICATE = "IVG230_UPSERT"


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "the cost is IRIS rolling a failed INSERT back inside a transaction; "
            "SKIP_IRIS_TESTS=true cannot observe it — start ivg-iris-enterprise "
            "with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: the subject is a transaction's cost.")


@pytest.fixture
def native(iris_connection):
    _require_iris(iris_connection)
    import iris as iris_mod

    return iris_mod.createIRIS(iris_connection)


@pytest.fixture
def pfx(iris_connection):
    """A prefix no other row shares, cleaned up afterwards."""
    prefix = f"ivg230:upsert:{int(time.time() * 1000)}"
    yield prefix
    cursor = iris_connection.cursor()
    for table, column in (
        ("Graph_KG.rdf_edges", "s"),
        ("Graph_KG.rdf_labels", "s"),
        ("Graph_KG.rdf_props", "s"),
        ("Graph_KG.nodes", "node_id"),
    ):
        try:
            cursor.execute(
                f"DELETE FROM {table} WHERE {column} %STARTSWITH ?", [prefix]
            )
        except Exception:
            pass
    try:
        iris_connection.commit()
    except Exception:
        pass


def _chained_edges(pfx, count=EDGE_COUNT):
    """An edge's target is the next edge's source, so half the node inserts repeat."""
    return [
        {"s": f"{pfx}:n{i}", "p": PREDICATE, "o": f"{pfx}:n{i + 1}"}
        for i in range(count)
    ]


def _ingest(native, edges):
    from iris_vector_graph.schema import _call_classmethod_large

    t0 = time.perf_counter()
    n = int(
        str(
            _call_classmethod_large(
                native,
                "Graph.KG.EdgeScan",
                "BulkIngestEdgesSQL",
                json.dumps(edges),
                PREDICATE,
            )
        )
    )
    return n, time.perf_counter() - t0


def _count(conn, sql, params):
    cursor = conn.cursor()
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return int(row[0]) if row else 0


class TestBulkIngestUpsert:
    def test_repeated_endpoints_do_not_pace_the_ingest(self, iris_connection, native, pfx):
        edges = _chained_edges(pfx)
        n, seconds = _ingest(native, edges)
        rate = len(edges) / seconds

        assert n == len(edges), f"ingest counted {n} of {len(edges)} edges"
        assert rate > MIN_EDGES_PER_SEC, (
            f"{rate:.0f} edges/s over {len(edges)} chained edges "
            f"({seconds * 1000 / len(edges):.2f} ms each) — a failed duplicate INSERT "
            "inside the transaction costs ~40ms, which is what this floor catches"
        )

    def test_re_ingesting_the_same_batch_is_idempotent_and_still_fast(
        self, iris_connection, native, pfx
    ):
        """The second pass is the worst case: every node insert would be a duplicate."""
        edges = _chained_edges(pfx)
        _ingest(native, edges)
        iris_connection.commit()

        edge_rows = _count(
            iris_connection,
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ?",
            [pfx],
        )
        node_rows = _count(
            iris_connection,
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id %STARTSWITH ?",
            [pfx],
        )

        n, seconds = _ingest(native, edges)
        iris_connection.commit()
        rate = len(edges) / seconds

        assert rate > MIN_EDGES_PER_SEC, (
            f"re-ingest ran at {rate:.0f} edges/s — every endpoint already exists, "
            "so this is the shape the -119 idiom was slowest on"
        )
        assert (
            _count(
                iris_connection,
                "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ?",
                [pfx],
            )
            == edge_rows
        ), "re-ingest duplicated edge rows"
        assert (
            _count(
                iris_connection,
                "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id %STARTSWITH ?",
                [pfx],
            )
            == node_rows
        ), "re-ingest duplicated node rows"

    def test_every_endpoint_lands_in_the_default_graph(self, iris_connection, native, pfx):
        """Not NULL: a NULL `graph_id` gives the default graph a second spelling."""
        edges = _chained_edges(pfx, count=20)
        _ingest(native, edges)
        iris_connection.commit()

        unscoped = _count(
            iris_connection,
            "SELECT COUNT(*) FROM Graph_KG.nodes "
            "WHERE node_id %STARTSWITH ? AND graph_id IS NULL",
            [pfx],
        )
        assert unscoped == 0, f"{unscoped} endpoint rows written with a NULL graph_id"
        scoped = _count(
            iris_connection,
            "SELECT COUNT(*) FROM Graph_KG.nodes "
            "WHERE node_id %STARTSWITH ? AND graph_id = ''",
            [pfx],
        )
        assert scoped == len(edges) + 1, f"{scoped} of {len(edges) + 1} endpoints placed"


class TestBulkIngestNodesUpsert:
    def test_repeated_ids_and_labels_do_not_pace_the_node_ingest(
        self, iris_connection, native, pfx
    ):
        from iris_vector_graph.schema import _call_classmethod_large

        nodes = [
            {"id": f"{pfx}:bn{i}", "labels": ["Ivg230Upsert"], "props": {"k": i}}
            for i in range(200)
        ]
        payload = json.dumps(nodes)

        for attempt in ("first", "second"):
            t0 = time.perf_counter()
            n = int(
                str(
                    _call_classmethod_large(
                        native, "Graph.KG.EdgeScan", "BulkIngestNodesSQL", payload
                    )
                )
            )
            seconds = time.perf_counter() - t0
            rate = len(nodes) / seconds
            assert n == len(nodes), f"{attempt} pass counted {n} of {len(nodes)} nodes"
            assert rate > MIN_EDGES_PER_SEC, (
                f"{attempt} pass ran at {rate:.0f} nodes/s "
                f"({seconds * 1000 / len(nodes):.2f} ms each)"
            )
        iris_connection.commit()

        for table in ("rdf_labels", "rdf_props"):
            unscoped = _count(
                iris_connection,
                f"SELECT COUNT(*) FROM Graph_KG.{table} "
                "WHERE s %STARTSWITH ? AND graph_id IS NULL",
                [pfx],
            )
            assert unscoped == 0, (
                f"{unscoped} {table} rows written with a NULL graph_id — the default "
                "graph would have two spellings in one table"
            )
            placed = _count(
                iris_connection,
                f"SELECT COUNT(*) FROM Graph_KG.{table} "
                "WHERE s %STARTSWITH ? AND graph_id = ''",
                [pfx],
            )
            assert placed == len(nodes), f"{placed} of {len(nodes)} {table} rows placed"
