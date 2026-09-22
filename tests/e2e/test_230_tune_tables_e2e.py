"""Spec 230 — the install tells IRIS about the tables it built, and the plans improve.

This gate cannot be met in Python: what it checks is the *query plan* IRIS picks. IVG
creates `Graph_KG.nodes` with `pk_nodes_graph (node_id, graph_id)` and
`uq_nodes_graph_node (graph_id, node_id)`, and never ran `TUNE TABLE` anywhere — not at
the end of `initialize_schema`, not after the 4.0.0 migration re-keys the table. Found
by the T074 gate chasing `test_bulk_ingest_throughput_10k` at 20 edges/s: on this
install, `WHERE node_id = ? AND graph_id = ?` — the shape every graph-scoped read
issues, and the shape both of `rdf_edges`' composite foreign keys check on every edge
insert — planned as

    Read master map Graph_KG.nodes.IDKEY, looping on ID

a scan, measured at 5.3ms against 46,343 rows and 10.2ms per edge insert. One
`TUNE TABLE Graph_KG.nodes` moved the same lookup to
`Read index map Graph_KG.nodes.pk_nodes_graph, using the given node_id and graph_id`
and the insert to 0.23ms. `TUNE TABLE` does two things that both matter after a
re-key: it collects statistics that describe the rows actually there, and it discards
the cached plans that were prepared before the current indexes existed.

The statement it leaves behind is checkable two ways, and this file checks both:

* a table IRIS has no statistics for is reported as such in its own plan
  (`Table X is not tuned.`), so a scratch table proves `tune_tables` collected them;
* after `initialize_schema`, the graph-scoped lookup on the real `nodes` reads an index
  map and its plan carries no such warning.

Restoring the original bad state on demand is not available: `ClearTableStats` leaves
the plan and its cost untouched here, so a test that cleared statistics and expected a
scan would assert against IRIS rather than against this fix.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.e2e]

SCOPED_LOOKUP = (
    "SELECT 1 FROM Graph_KG.nodes "
    "WHERE node_id = 'ivg230:tune:probe' AND graph_id = ''"
)
MASTER_MAP = "Read master map Graph_KG.nodes.IDKEY"
INDEX_MAP = "Read index map Graph_KG.nodes."
NOT_TUNED = "is not tuned"

PROBE_TABLE = "tune_probe_230"


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "the subject is a query plan IRIS picks from table statistics; "
            "SKIP_IRIS_TESTS=true cannot observe it — start ivg-iris-enterprise "
            "with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: the subject is the SQL optimizer.")


def _plan(conn, sql: str) -> str:
    cursor = conn.cursor()
    cursor.execute("EXPLAIN " + sql)
    return "\n".join(str(row[0]) for row in cursor.fetchall())


class TestTuneTablesE2E:
    def test_initialize_schema_reports_the_tables_it_tuned(self, iris_connection):
        _require_iris(iris_connection)
        from iris_vector_graph.engine import IRISGraphEngine

        engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        status = engine.initialize_schema(auto_deploy_objectscript=False)
        assert "tuned" in status, sorted(status)
        for table in ("nodes", "rdf_edges", "rdf_labels", "rdf_props"):
            assert status["tuned"][f"Graph_KG.{table}"] is True, status["tuned"]

    def test_the_graph_scoped_node_lookup_reads_an_index_and_is_tuned(
        self, iris_connection
    ):
        _require_iris(iris_connection)
        from iris_vector_graph.engine import IRISGraphEngine

        engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        engine.initialize_schema(auto_deploy_objectscript=False)

        plan = _plan(iris_connection, SCOPED_LOOKUP)
        assert INDEX_MAP in plan, f"not reading an index map:\n{plan}"
        assert MASTER_MAP not in plan, f"still scanning the master map:\n{plan}"
        assert NOT_TUNED not in plan, f"nodes reports itself untuned:\n{plan}"

    def test_tune_tables_collects_statistics_for_a_table_that_has_none(
        self, iris_connection
    ):
        """A table IRIS has no statistics for says so in every plan it appears in."""
        _require_iris(iris_connection)
        from iris_vector_graph.schema import GraphSchema

        cursor = iris_connection.cursor()
        query = (
            f"SELECT 1 FROM Graph_KG.{PROBE_TABLE} "
            "WHERE node_id = 'tp5' AND graph_id = ''"
        )
        try:
            cursor.execute(f"DROP TABLE Graph_KG.{PROBE_TABLE}")
        except Exception:
            pass
        cursor.execute(
            f"""CREATE TABLE Graph_KG.{PROBE_TABLE}(
                graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
                node_id  VARCHAR(256) %EXACT NOT NULL,
                CONSTRAINT pk_{PROBE_TABLE} PRIMARY KEY (node_id, graph_id)
            )"""
        )
        try:
            for i in range(200):
                cursor.execute(
                    f"INSERT INTO Graph_KG.{PROBE_TABLE} (node_id, graph_id) "
                    "VALUES (?, '')",
                    [f"tp{i}"],
                )
            iris_connection.commit()

            before = _plan(iris_connection, query)
            assert NOT_TUNED in before, (
                "a table with no statistics is expected to report itself untuned, so "
                f"the tune has something to change:\n{before}"
            )

            status = GraphSchema.tune_tables(iris_connection.cursor(), tables=[PROBE_TABLE])
            assert status[f"Graph_KG.{PROBE_TABLE}"] is True, status
            iris_connection.commit()

            after = _plan(iris_connection, query)
            assert NOT_TUNED not in after, f"still reports itself untuned:\n{after}"
        finally:
            try:
                cursor.execute(f"DROP TABLE Graph_KG.{PROBE_TABLE}")
                iris_connection.commit()
            except Exception:
                pass
