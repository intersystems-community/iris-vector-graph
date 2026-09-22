"""A bulk load that lost every row used to report success.

`_executemany_batched` counted a failed batch into a local `errors` variable,
logged it at ERROR level, and returned only the number of rows that went in.
`load_edges` then built its stats dict from that number, so a wholesale
rejection — measured against the live enterprise container as

    SQLCODE -121: Foreign Key Constraint 'fk_edges_dest', Field(s) GRAPH_ID,O_ID
    failed referential integrity check

— came back as ``{'edges': 0, 'elapsed_s': 0.0, 'noindex': False}`` with no
exception. The caller cannot tell that from "nothing new to load".

Duplicate rows are the one failure that is genuinely expected: a re-load of the
same data must stay idempotent. Those are counted as skipped. Everything else
raises `BulkLoadError`.
"""

import pytest
from unittest.mock import MagicMock

from iris_vector_graph.bulk_loader import BulkLoader
from iris_vector_graph.exceptions import BulkLoadError


def _make_loader(batch_size=2):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    return BulkLoader(conn, batch_size=batch_size), conn, cursor


class TestBatchFailureIsNotSilent:

    def test_a_foreign_key_rejection_raises(self):
        loader, conn, cursor = _make_loader()
        cursor.executemany = MagicMock(
            side_effect=Exception(
                "[SQLCODE: <-121>:<FOREIGN KEY constraint failed referential "
                "check upon INSERT of row in referencing table>]"
            )
        )
        with pytest.raises(BulkLoadError) as exc:
            loader._executemany_batched(
                cursor, "INSERT INTO t VALUES (?)", [["r1"], ["r2"]], "Edges"
            )
        # The message has to say which phase died and how much went missing,
        # because the rollback means the database shows no trace of it.
        assert "Edges" in str(exc.value)
        assert "-121" in str(exc.value)
        assert "2" in str(exc.value)

    def test_the_failed_batch_is_rolled_back(self):
        loader, conn, cursor = _make_loader()
        cursor.executemany = MagicMock(side_effect=Exception("FATAL: table not found"))
        with pytest.raises(BulkLoadError):
            loader._executemany_batched(
                cursor, "INSERT INTO t VALUES (?)", [["r1"]], "Nodes"
            )
        conn.rollback.assert_called()

    def test_rows_inserted_before_the_failure_are_reported(self):
        """The count belongs in the exception: the caller has no other way to it."""
        loader, conn, cursor = _make_loader(batch_size=1)
        calls = {"n": 0}

        def executemany(sql, batch):
            calls["n"] += 1
            if calls["n"] == 3:
                raise Exception("SQLCODE -104 vector width")

        cursor.executemany = executemany
        with pytest.raises(BulkLoadError) as exc:
            loader._executemany_batched(
                cursor, "INSERT INTO t VALUES (?)", [["a"], ["b"], ["c"]], "Props"
            )
        assert exc.value.inserted == 2
        assert exc.value.failed == 1


class TestDuplicatesStayTolerated:

    def test_a_unique_violation_falls_back_to_row_at_a_time(self):
        loader, conn, cursor = _make_loader()
        cursor.executemany = MagicMock(side_effect=Exception("-119 UNIQUE"))
        cursor.execute = MagicMock(return_value=None)
        inserted = loader._executemany_batched(
            cursor, "INSERT INTO t VALUES (?)", [["r1"], ["r2"]], "Nodes"
        )
        assert inserted == 2

    def test_a_row_that_is_itself_a_duplicate_is_skipped_not_raised(self):
        loader, conn, cursor = _make_loader()
        cursor.executemany = MagicMock(side_effect=Exception("-119 UNIQUE"))
        cursor.execute = MagicMock(
            side_effect=Exception("[SQLCODE: <-119>:<unique constraint>]")
        )
        inserted = loader._executemany_batched(
            cursor, "INSERT INTO t VALUES (?)", [["r1"]], "Nodes"
        )
        assert inserted == 0

    def test_a_row_that_fails_for_another_reason_still_raises(self):
        """The duplicate branch was the other way this defect could hide: one
        `-119` in the batch put every remaining row on a path that swallowed
        every error, `-121` included."""
        loader, conn, cursor = _make_loader()
        cursor.executemany = MagicMock(side_effect=Exception("-119 UNIQUE"))
        cursor.execute = MagicMock(
            side_effect=Exception("[SQLCODE: <-121>:<FOREIGN KEY constraint failed>]")
        )
        with pytest.raises(BulkLoadError):
            loader._executemany_batched(
                cursor, "INSERT INTO t VALUES (?)", [["r1"]], "Edges"
            )


class TestEdgeEndpointsAreRegistered:
    """4.0.0 gave `rdf_edges` composite foreign keys onto `nodes (graph_id,
    node_id)`, so an edge needs both endpoints registered in its graph. The
    engine's `create_edge` does that; `load_edges` did not, and its default
    `INSERT %NOINDEX %NOCHECK` bypasses the check, so a bulk load landed
    dangling edges that no reader can join.
    """

    def test_missing_endpoints_are_inserted_before_the_edges(self):
        loader, conn, cursor = _make_loader(batch_size=100)
        statements = []

        def executemany(sql, batch):
            statements.append((sql, [list(r) for r in batch]))

        cursor.executemany = executemany
        cursor.fetchall.return_value = []  # no nodes, no existing edges

        loader.load_edges(
            [("A", "KNOWS", "B", None)], use_noindex=False, skip_existing=False
        )

        assert len(statements) == 2, statements
        node_sql, node_rows = statements[0]
        assert "nodes" in node_sql
        assert "%NOCHECK" not in node_sql  # an unindexed endpoint is no endpoint
        # The endpoint insert names `(graph_id, node_id)` since spec 230 FR-003, so
        # the node ID is read by its column position rather than assumed to be first.
        node_id_at = [c.strip() for c in node_sql.split("(")[1].split(")")[0].split(",")].index(
            "node_id"
        )
        assert sorted(r[node_id_at] for r in node_rows) == ["A", "B"]
        assert all(r[0] == "" for r in node_rows), (
            f"the endpoints did not land in the loader's graph: {node_rows}"
        )
        assert "rdf_edges" in statements[1][0]

    def test_endpoints_already_present_are_not_reinserted(self):
        loader, conn, cursor = _make_loader(batch_size=100)
        statements = []
        cursor.executemany = lambda sql, batch: statements.append(sql)
        cursor.fetchall.return_value = [("A",), ("B",)]

        loader.load_edges(
            [("A", "KNOWS", "B", None)], use_noindex=False, skip_existing=False
        )
        assert len(statements) == 1, statements
        assert "rdf_edges" in statements[0]

    def test_the_endpoint_scan_sees_unindexed_rows(self):
        """`load_nodes(use_noindex=True)` leaves rows the ordinary index read
        cannot see — measured on the live container: an equality read of a
        `%NOINDEX`-inserted node returns 0 rows, the same read with `%NOINDEX`
        in the predicate returns 1. Without the hint this scan would think every
        bulk-loaded node is missing and insert it a second time, and `%NOCHECK`
        means nothing would complain.
        """
        loader, conn, cursor = _make_loader(batch_size=100)
        reads = []
        cursor.execute = MagicMock(side_effect=lambda sql, *a: reads.append(sql))
        cursor.executemany = MagicMock(return_value=None)
        cursor.fetchall.return_value = []

        loader.load_edges(
            [("A", "KNOWS", "B", None)], use_noindex=True, skip_existing=True
        )
        scans = [s for s in reads if "nodes" in s and "SELECT" in s.upper()]
        assert scans, reads
        assert all("%NOINDEX" in s for s in scans), scans
        edge_scans = [s for s in reads if "rdf_edges" in s and "SELECT" in s.upper()]
        assert edge_scans, reads
        assert all("%NOINDEX" in s for s in edge_scans), edge_scans


class TestLoadEdgesPropagates:

    def test_a_rejected_edge_batch_raises_and_rolls_back(self):
        loader, conn, cursor = _make_loader(batch_size=100)
        cursor.fetchall.return_value = [("A",), ("B",)]
        cursor.executemany = MagicMock(
            side_effect=Exception("[SQLCODE: <-121>:<FOREIGN KEY>]")
        )
        with pytest.raises(BulkLoadError):
            loader.load_edges(
                [("A", "KNOWS", "B", None)], use_noindex=False, skip_existing=False
            )
        conn.rollback.assert_called()
