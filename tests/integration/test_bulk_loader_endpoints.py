"""A bulk load cannot leave an edge whose endpoints are not registered nodes.

4.0.0 gave `Graph_KG.rdf_edges` composite foreign keys — `fk_edges_source` and
`fk_edges_dest` onto `Graph_KG.nodes (graph_id, node_id)` — so an edge needs both
endpoints registered in the same graph. `engine.create_edge` does that.
`BulkLoader.load_edges` did not, and measured against this container it failed in
two different directions depending on one default:

  * `use_noindex=True` (the default): `INSERT %NOINDEX %NOCHECK` skips the check, so
    the row landed with no endpoints at all and `load_edges` reported
    `{'edges': 1, ...}`. Nothing could join it.
  * `use_noindex=False`: every row was rejected with `SQLCODE -121 ... Foreign Key
    Constraint 'fk_edges_dest'`, which `_executemany_batched` logged at ERROR level
    and turned into `{'edges': 0, ...}` with no exception.

These run against the live container because the guarantee is in the constraints.
"""

import os

import pytest

from iris_vector_graph.bulk_loader import BulkLoader
from iris_vector_graph.exceptions import BulkLoadError


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = "BLEP"


def _purge(conn):
    cursor = conn.cursor()
    try:
        # %NOINDEX on the delete predicate as well: a row this loader wrote with
        # %NOINDEX is invisible to the index the ordinary predicate would use, so
        # without the hint the cleanup silently leaves it behind.
        cursor.execute(
            f"DELETE FROM Graph_KG.rdf_edges WHERE %NOINDEX s %STARTSWITH '{PREFIX}' "
            f"OR %NOINDEX o_id %STARTSWITH '{PREFIX}'"
        )
        cursor.execute(
            f"DELETE FROM Graph_KG.rdf_labels WHERE %NOINDEX s %STARTSWITH '{PREFIX}'"
        )
        cursor.execute(
            f"DELETE FROM Graph_KG.rdf_props WHERE %NOINDEX s %STARTSWITH '{PREFIX}'"
        )
        cursor.execute(
            f"DELETE FROM Graph_KG.nodes WHERE %NOINDEX node_id %STARTSWITH '{PREFIX}'"
        )
        conn.commit()
    finally:
        cursor.close()


@pytest.fixture
def loader(engine):
    _purge(engine.conn)
    yield BulkLoader(engine.conn)
    _purge(engine.conn)


def _count(conn, sql):
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        return cursor.fetchone()[0]
    finally:
        cursor.close()


class TestEndpointsExistAfterABulkLoad:

    def test_noindex_load_registers_both_endpoints(self, loader, engine):
        stats = loader.load_edges(
            [(f"{PREFIX}:a", "KNOWS", f"{PREFIX}:b", None)],
            use_noindex=True,
            skip_existing=False,
        )
        assert stats["edges"] == 1
        assert stats["endpoints_registered"] == 2
        registered = _count(
            engine.conn,
            "SELECT COUNT(*) FROM Graph_KG.nodes "
            f"WHERE node_id IN ('{PREFIX}:a', '{PREFIX}:b') AND graph_id = ''",
        )
        assert registered == 2

    def test_no_edge_is_left_dangling(self, loader, engine):
        loader.load_edges(
            [
                (f"{PREFIX}:a", "KNOWS", f"{PREFIX}:b", None),
                (f"{PREFIX}:b", "KNOWS", f"{PREFIX}:c", None),
            ],
            use_noindex=True,
            skip_existing=False,
        )
        # `%NOINDEX` on the edge scan so this sees the rows the load actually wrote.
        dangling = _count(
            engine.conn,
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges e WHERE %NOINDEX e.s %STARTSWITH "
            f"'{PREFIX}' AND (NOT EXISTS (SELECT 1 FROM Graph_KG.nodes n WHERE "
            "%NOINDEX n.node_id = e.s AND n.graph_id = e.graph_id) OR NOT EXISTS "
            "(SELECT 1 FROM Graph_KG.nodes n2 WHERE %NOINDEX n2.node_id = e.o_id "
            "AND n2.graph_id = e.graph_id))",
        )
        assert dangling == 0

    def test_the_checked_insert_path_is_accepted(self, loader, engine):
        """Without registration this same call was rejected wholesale with -121."""
        stats = loader.load_edges(
            [(f"{PREFIX}:a", "KNOWS", f"{PREFIX}:b", None)],
            use_noindex=False,
            skip_existing=False,
        )
        assert stats["edges"] == 1
        present = _count(
            engine.conn,
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges "
            f"WHERE s = '{PREFIX}:a' AND p = 'KNOWS' AND o_id = '{PREFIX}:b'",
        )
        assert present == 1

    def test_endpoints_from_load_nodes_are_not_registered_twice(self, loader, engine):
        """`load_nodes(use_noindex=True)` leaves rows the index cannot see.

        The endpoint scan reads with `%NOINDEX` for exactly this case: an ordinary
        equality read returns nothing for a `%NOINDEX`-inserted node, so the scan
        would call every one of them missing and `%NOCHECK` would let the duplicate
        land — two rows with the same (node_id, graph_id) under a primary key that
        says one.
        """
        loader.load_nodes(
            [(f"{PREFIX}:a", {}), (f"{PREFIX}:b", {})],
            skip_existing=False,
            use_noindex=True,
        )
        stats = loader.load_edges(
            [(f"{PREFIX}:a", "KNOWS", f"{PREFIX}:b", None)],
            use_noindex=True,
            skip_existing=False,
        )
        assert stats["endpoints_registered"] == 0
        copies = _count(
            engine.conn,
            "SELECT COUNT(*) FROM Graph_KG.nodes "
            f"WHERE %NOINDEX node_id = '{PREFIX}:a'",
        )
        assert copies == 1


class TestRejectionsReachTheCaller:

    def test_a_refused_edge_raises_instead_of_reporting_zero(self, loader, engine):
        """An edge whose graph does not hold its endpoint has to fail loudly.

        `load_edges` registers endpoints in the default graph, so the way to reach
        the foreign key now is to name a graph it did not write to: this inserts the
        edge row directly with the checked path, which is what the loader does once
        registration is skipped.
        """
        cursor = engine.conn.cursor()
        try:
            with pytest.raises(BulkLoadError) as exc:
                loader._executemany_batched(
                    cursor,
                    "INSERT INTO Graph_KG.rdf_edges (graph_id, s, p, o_id) "
                    "VALUES (?, ?, ?, ?)",
                    [["bulk-endpoint-probe", f"{PREFIX}:x", "KNOWS", f"{PREFIX}:y"]],
                    "Edges",
                )
            assert "-121" in str(exc.value)
            assert exc.value.failed == 1
        finally:
            cursor.close()

        landed = _count(
            engine.conn,
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges "
            f"WHERE %NOINDEX s = '{PREFIX}:x'",
        )
        assert landed == 0
