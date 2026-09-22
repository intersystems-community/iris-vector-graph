"""Spec 230 — deleting `Graph.KG.Edge` must not take `Graph_KG.rdf_edges` with it.

v3.2.0 shipped `Graph.KG.Edge` as `[ Final, DdlAllowed, SqlTableName = rdf_edges ]`,
so on a real 3.2.0 install the class *owns* the table: the rows live in that class's
extent, and `initialize_schema`'s `CREATE TABLE Graph_KG.rdf_edges` was declined as
already present. Spec 227 deleted the class, and deploy deletes it from the namespace
too — which drops the extent. Measured against a 3.2.0 install in namespace `IVG320`:
every edge row gone, `Graph_KG.rdf_edges` unresolvable (SQLCODE -30), and the `^KG`
re-key step then refusing the whole upgrade because the table it reads has no
`graph_id` column — it has no columns, because it has no table.

The committed `tests/fixtures/snapshots/ivg-3.2.0.zip` fixture could not catch this:
it rebuilds 3.2.0's shape from DDL, so its `rdf_edges` is DDL-owned and the class
that made this destructive is the one thing the fixture does not have.

So deploy stages the rows into a DDL-owned table first, deletes the class, re-creates
`rdf_edges` from the canonical 4.0.0 declaration, and restores them — refusing to drop
the staging table until the counts agree.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.schema import (
    RDF_EDGES_DDL,
    RESCUE_STAGING_TABLE,
    RESCUE_UNPLACED_TABLE,
    GraphSchema,
    IRISCapabilities,
    RdfEdgesRescueError,
)


class FakeCursor:
    """Records every statement and answers the count probes the rescue makes."""

    def __init__(
        self,
        *,
        class_present: bool = True,
        staged: int = 4,
        restored: int = 4,
        unplaced: int = 0,
    ):
        self.statements: list[str] = []
        self._class_present = class_present
        self._staged = staged
        self._restored = restored
        self._unplaced = unplaced
        self._answer = None
        #: Substrings of statements this cursor refuses, the way IRIS refuses them.
        self.fail_on: tuple[str, ...] = ()

    def execute(self, sql, params=None):  # noqa: D102
        self.statements.append(sql)
        lowered = sql.lower()
        for needle in self.fail_on:
            if needle.lower() in lowered:
                raise RuntimeError(
                    "<SQL ERROR>; Details: [SQLCODE: <-314>:<Foreign key references "
                    f"non-unique key/column collection>] on {needle}"
                )
        if "%dictionary.compiledclass" in lowered:
            self._answer = [(1 if self._class_present else 0,)]
        elif "count(*)" not in lowered:
            self._answer = []
        # The three table names overlap as substrings, so test the longest first.
        elif RESCUE_UNPLACED_TABLE.lower() in lowered:
            self._answer = [(self._unplaced,)]
        elif RESCUE_STAGING_TABLE.lower() in lowered:
            self._answer = [(self._staged,)]
        elif "rdf_edges" in lowered:
            self._answer = [(self._restored,)]
        else:
            self._answer = []

    def fetchone(self):  # noqa: D102
        return self._answer[0] if self._answer else None

    def fetchall(self):  # noqa: D102
        return list(self._answer or [])

    def close(self):  # noqa: D102
        pass

    def matching(self, needle: str) -> list[str]:
        return [s for s in self.statements if needle.lower() in s.lower()]

    def index_of(self, needle: str) -> int:
        for i, s in enumerate(self.statements):
            if needle.lower() in s.lower():
                return i
        raise AssertionError(f"no statement matching {needle!r} in {self.statements!r}")


class TestStaging:
    def test_absent_class_stages_nothing(self):
        """A fresh 4.0.0 install has no `Graph.KG.Edge`, so there is nothing to rescue."""
        cursor = FakeCursor(class_present=False)
        assert GraphSchema.stage_class_owned_rdf_edges(cursor) is None
        assert not cursor.matching("CREATE TABLE"), cursor.statements

    def test_present_class_stages_the_rows_and_reports_the_count(self):
        cursor = FakeCursor(class_present=True, staged=4)
        assert GraphSchema.stage_class_owned_rdf_edges(cursor) == 4
        created = cursor.matching(f"CREATE TABLE Graph_KG.{RESCUE_STAGING_TABLE}")
        assert created, cursor.statements
        copied = cursor.matching(f"INSERT INTO Graph_KG.{RESCUE_STAGING_TABLE}")
        assert copied, cursor.statements
        # The copy reads the columns a class-owned table actually has — no `edge_id`,
        # which is the column spec 227 added and the reason the class had to go.
        assert "edge_id" not in copied[0].lower()
        # v3.2.0 declared `graph_id NOT NULL DEFAULT $c(0)`, so a default-graph edge
        # can arrive as NULL or as CHAR(0). Both are the default graph, not a third
        # one, and both must read as '' — the spelling 4.0.0's FK to
        # `nodes (graph_id, node_id)` compares against.
        assert "nullif(graph_id" in copied[0].lower()
        assert "char(0)" in copied[0].lower()

    def test_staging_comes_before_the_class_is_deleted(self):
        """Order is the whole point: after the delete there is nothing left to read."""
        cursor = FakeCursor(class_present=True)
        seen: list[str] = []

        def record(_native, cls, method, *args):
            seen.append(f"{cls}.{method}({args[0] if args else ''})")
            if (cls, method) == ("%SYSTEM.OBJ", "Delete"):
                # By now the rows must already be staged.
                assert cursor.matching(f"INSERT INTO Graph_KG.{RESCUE_STAGING_TABLE}"), (
                    "Graph.KG.Edge is being deleted before its rows were staged"
                )
            return 1

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=record):
            with patch.object(
                GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()
            ):
                GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))

        assert any("%SYSTEM.OBJ.Delete(Graph.KG.Edge)" in s for s in seen), seen
        # And restored afterwards.
        assert cursor.matching("INSERT INTO Graph_KG.rdf_edges"), cursor.statements


class TestRestore:
    def test_restore_recreates_the_table_from_the_canonical_ddl(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        created = cursor.matching("CREATE TABLE Graph_KG.rdf_edges")
        assert created, cursor.statements
        # One declaration, not a second spelling that can drift from a fresh install.
        assert created[0].strip().rstrip(";") == RDF_EDGES_DDL.strip().rstrip(";")

    def test_restore_copies_the_rows_back_and_drops_the_staging_table(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert cursor.matching("INSERT INTO Graph_KG.rdf_edges"), cursor.statements
        assert cursor.index_of("INSERT INTO Graph_KG.rdf_edges") < cursor.index_of(
            f"DROP TABLE Graph_KG.{RESCUE_STAGING_TABLE}"
        )

    def test_a_short_restore_raises_and_keeps_the_staging_table(self):
        """The staging table is the only surviving copy — it outlives a bad restore."""
        cursor = FakeCursor(class_present=True, staged=4, restored=3, unplaced=0)
        with pytest.raises(RdfEdgesRescueError, match="rdf_edges"):
            GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert not cursor.matching(f"DROP TABLE Graph_KG.{RESCUE_STAGING_TABLE}"), (
            "the staging table was dropped after a restore that lost rows"
        )

    def test_nothing_staged_means_nothing_restored(self):
        cursor = FakeCursor(class_present=False)
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert cursor.statements == []


class TestRestorePlacesWhatItCanAndQuarantinesTheRest:
    """4.0.0's `rdf_edges` FK is composite; v3.2.0's was `nodes (node_id)` alone.

    So a 3.2.0 install can legally hold an edge in graph `g` whose endpoint row sits
    in the default graph: graph-blind FK, graph-bearing rows. Inserting that edge into
    the 4.0.0 table fails `fk_edges_dest` (SQLCODE -121) and — before this — took the
    whole restore down with it, leaving the namespace with no `rdf_edges` at all and
    the failure logged at DEBUG. Spec 227 Story 5's rule applies: place what resolves,
    quarantine what does not, delete nothing, guess nothing.
    """

    def test_the_insert_only_places_rows_whose_endpoints_resolve(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        insert = cursor.matching("INSERT INTO Graph_KG.rdf_edges ")
        assert insert, cursor.statements
        body = insert[0].lower()
        # Both endpoints, checked against the graph the edge claims.
        assert body.count("exists") == 2, insert[0]
        assert "graph_kg.nodes" in body

    def test_rows_that_do_not_resolve_are_quarantined_not_deleted(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=3, unplaced=1)
        GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        created = cursor.matching(f"CREATE TABLE Graph_KG.{RESCUE_UNPLACED_TABLE}")
        assert created, cursor.statements
        moved = cursor.matching(f"INSERT INTO Graph_KG.{RESCUE_UNPLACED_TABLE}")
        assert moved, cursor.statements
        # The negation of the placement predicate, i.e. exactly its complement —
        # `EXISTS` never answers NULL, so no row can fall between the two tables.
        assert "not (exists" in moved[0].lower()
        # Accounted for, so the staging copy can go.
        assert cursor.matching(f"DROP TABLE Graph_KG.{RESCUE_STAGING_TABLE}")

    def test_the_quarantine_table_is_named_in_the_warning(self, caplog):
        cursor = FakeCursor(class_present=True, staged=4, restored=3, unplaced=1)
        with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
            GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert RESCUE_UNPLACED_TABLE in caplog.text
        assert "1" in caplog.text

    def test_rows_lost_between_the_two_tables_still_raise(self):
        """3 placed + 0 quarantined out of 4 staged is a lost row, not a choice."""
        cursor = FakeCursor(class_present=True, staged=4, restored=3, unplaced=0)
        with pytest.raises(RdfEdgesRescueError):
            GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert not cursor.matching(f"DROP TABLE Graph_KG.{RESCUE_STAGING_TABLE}")


class TestTheRebuiltTableCanDeclareItsForeignKeys:
    """The rebuild runs *before* the `nodes` re-key, so it has to prepare its own key.

    Measured against namespace `IVG320`: 4 rows staged, `Graph.KG.Edge` deleted, then
    `CREATE TABLE Graph_KG.rdf_edges` failed with

        SQLCODE -314: Foreign Key 'FK_EDGES_SOURCE' references non-unique column(s)
        in table 'GRAPH_KG.NODES', column(s) 'GRAPH_ID,NODE_ID'

    because v3.2.0's `nodes` carries `uq_nodes_nodeid UNIQUE (node_id)` and nothing on
    `(graph_id, node_id)`. The composite key arrives later, in the embeddings
    migration's `_rekey_children` — which `upgrade_to_4_0_0` runs after
    `initialize_schema` has already deleted the class. So the restore adds the unique
    key itself, tolerantly: on a 3.2.0 install it is the missing prerequisite, and on
    any later install it already exists and the ALTER is a no-op.
    """

    def test_the_unique_key_is_added_before_the_table_is_rebuilt(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert cursor.index_of("ADD CONSTRAINT uq_nodes_graph_node") < cursor.index_of(
            "CREATE TABLE Graph_KG.rdf_edges"
        ), cursor.statements

    def test_the_added_key_is_the_composite_the_foreign_keys_reference(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        altered = cursor.matching("ADD CONSTRAINT uq_nodes_graph_node")
        assert altered, cursor.statements
        assert "graph_kg.nodes" in altered[0].lower()
        assert "unique (graph_id, node_id)" in altered[0].lower()

    def test_an_existing_unique_key_does_not_stop_the_restore(self):
        """On a 4.0.0 install the ALTER fails "already exists" — that is not a failure."""
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        cursor.fail_on = ("ADD CONSTRAINT uq_nodes_graph_node",)
        GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert cursor.matching("CREATE TABLE Graph_KG.rdf_edges"), cursor.statements
        assert cursor.matching(f"DROP TABLE Graph_KG.{RESCUE_STAGING_TABLE}")


class TestAFailedRestoreRaisesByName:
    """Any failure in the restore has to reach the caller, not only a short count.

    The call site re-raises `RdfEdgesRescueError` and logs every other exception at
    DEBUG as "expected in Docker". The -314 above was an ordinary `ProgrammingError`,
    so it took that second path: `initialize_schema` returned `tables_created: True`
    and `objectscript_deployed: True` over a namespace with no edge table at all, the
    only copy of the edges sitting in a staging table nothing had mentioned.
    """

    def test_a_failing_rebuild_raises_a_rescue_error(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        cursor.fail_on = ("CREATE TABLE Graph_KG.rdf_edges(",)
        with pytest.raises(RdfEdgesRescueError) as excinfo:
            GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        message = str(excinfo.value)
        assert RESCUE_STAGING_TABLE in message
        assert "4" in message

    def test_a_failing_rebuild_keeps_the_staging_table(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        cursor.fail_on = ("CREATE TABLE Graph_KG.rdf_edges(",)
        with pytest.raises(RdfEdgesRescueError):
            GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert not cursor.matching(f"DROP TABLE Graph_KG.{RESCUE_STAGING_TABLE}")

    def test_a_failing_row_copy_raises_too(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        cursor.fail_on = ("INSERT INTO Graph_KG.rdf_edges ",)
        with pytest.raises(RdfEdgesRescueError):
            GraphSchema.restore_rescued_rdf_edges(cursor, 4)

    def test_the_underlying_error_is_kept_as_the_cause(self):
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        cursor.fail_on = ("CREATE TABLE Graph_KG.rdf_edges(",)
        with pytest.raises(RdfEdgesRescueError) as excinfo:
            GraphSchema.restore_rescued_rdf_edges(cursor, 4)
        assert excinfo.value.__cause__ is not None
        assert "SQLCODE" in str(excinfo.value.__cause__)

    def test_deploy_lets_the_rescue_error_through(self):
        """Deploy swallows its own failures; it must not swallow this one."""
        cursor = FakeCursor(class_present=True, staged=4, restored=4)
        cursor.fail_on = ("CREATE TABLE Graph_KG.rdf_edges(",)
        with patch("iris_vector_graph.schema._call_classmethod", return_value=1):
            with patch.object(
                GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()
            ):
                with pytest.raises(RdfEdgesRescueError):
                    GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))


class TestARescueFailureIsNotSwallowed:
    """`initialize_schema`'s auto-deploy is best-effort; the rescue inside it is not.

    The call site logs a failed deploy at DEBUG as "expected in Docker". A rescue that
    raised there left the measured 3.2.0 install with `Graph_KG.rdf_edges` missing and
    `initialize_schema` still reporting `objectscript_deployed: True`.
    """

    def test_initialize_schema_reraises_a_rescue_failure(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        cursor.description = [("node_id", None)]

        from iris_vector_graph.engine import IRISGraphEngine

        engine = IRISGraphEngine(conn, embedding_dimension=4)

        boom = RdfEdgesRescueError("rescued 4 row(s) but restored 0")
        with patch("iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=""):
            with patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"):
                with patch(
                    "iris_vector_graph.schema.GraphSchema.get_procedures_sql_list",
                    return_value=[],
                ):
                    with patch(
                        "iris_vector_graph.schema.GraphSchema.deploy_objectscript_classes",
                        side_effect=boom,
                    ):
                        with pytest.raises(RdfEdgesRescueError):
                            engine.initialize_schema(auto_deploy_objectscript=True)

    def test_an_ordinary_deploy_failure_is_still_best_effort(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        cursor.description = [("node_id", None)]

        from iris_vector_graph.engine import IRISGraphEngine

        engine = IRISGraphEngine(conn, embedding_dimension=4)

        with patch("iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=""):
            with patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"):
                with patch(
                    "iris_vector_graph.schema.GraphSchema.get_procedures_sql_list",
                    return_value=[],
                ):
                    with patch(
                        "iris_vector_graph.schema.GraphSchema.deploy_objectscript_classes",
                        side_effect=RuntimeError("no /tmp/src in this container"),
                    ):
                        status = engine.initialize_schema(auto_deploy_objectscript=True)
        assert status["tables_created"] is True


class TestDeployStillWorksWhenThereIsNothingToRescue:
    def test_a_fresh_install_is_untouched(self):
        cursor = FakeCursor(class_present=False)
        with patch("iris_vector_graph.schema._call_classmethod", return_value=1):
            with patch.object(
                GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()
            ):
                GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))
        assert not cursor.matching("CREATE TABLE Graph_KG.rdf_edges"), cursor.statements
        assert not cursor.matching("DROP TABLE"), cursor.statements

    def test_a_staging_failure_does_not_stop_the_deploy(self):
        """Deploy is not the place to die: the caller's own probes report the state."""
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("no such table")
        with patch("iris_vector_graph.schema._call_classmethod", return_value=1):
            with patch.object(
                GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()
            ):
                caps = GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))
        assert isinstance(caps, IRISCapabilities)
