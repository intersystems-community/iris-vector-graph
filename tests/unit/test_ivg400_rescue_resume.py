"""A rescue left behind by a failed restore has to be resumable, and duplicates collapse.

Found migrating a real 3.2.0-era install to 4.0.0.

Bug 1 — the restore could not recover once it failed. Staging only runs while the
v3.2.0 class `Graph.KG.Edge` exists, and the class is deleted before the restore. So
when the restore failed, `RdfEdgesRescueError` told the operator to re-run
`initialize_schema()` — and on that re-run staging returned ``None`` (no class), the
restore was a no-op, the base DDL script had created an empty `rdf_edges`, and the run
reported success next to a full `rdf_edges__ivg400rescue`. Resuming also has to get
past what that re-run leaves in the way: an empty DDL `Graph_KG.rdf_edges` (the rebuild
refuses with SQLCODE -201) and the `SQLUser.rdf_edges` compatibility view (which makes
`DROP TABLE` fail with SQLCODE -321, and dangles after the class is deleted).

Bug 3 — v3.2.0 bulk loads wrote full duplicate rows. `bulk_loader.py` inserted with
`%NOINDEX %NOCHECK`, which skips the class's `uspo` unique index, and the staged
`graph_id` normalisation folds NULL and CHAR(0) together. The restore's single
INSERT…SELECT then hit `u_spo_graph (s, p, o_id, graph_id)` on the first duplicate and
the whole restore failed (IRIS rolls the statement back: zero rows placed), which is
bug 1. The measured install carried 44,884 duplicate edges.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.schema import (
    RESCUE_STAGING_TABLE,
    RESCUE_UNPLACED_TABLE,
    GraphSchema,
    IRISCapabilities,
    RdfEdgesRescueError,
)

STAGING = f"Graph_KG.{RESCUE_STAGING_TABLE}"
UNPLACED = f"Graph_KG.{RESCUE_UNPLACED_TABLE}"


class RescueCursor:
    """Answers the rescue's probes from a small description of the namespace.

    Counts are routed by the first table named after ``FROM`` and by whether the
    statement filters — the rescue asks each question in exactly one shape.
    """

    def __init__(
        self,
        *,
        class_present: bool = False,
        rescue_table: bool = True,
        edges_table: bool = False,
        staged: int = 5,
        restored: int = 3,
        collapsed: int = 2,
        unplaced: int = 0,
        foreign: int = 0,
    ):
        self.statements: list[str] = []
        self.class_present = class_present
        self.rescue_table = rescue_table
        self.edges_table = edges_table
        self.staged = staged
        self.restored = restored
        self.collapsed = collapsed
        self.unplaced = unplaced
        self.foreign = foreign
        self.fail_on: tuple[str, ...] = ()
        self._answer: list = []

    def execute(self, sql, params=None):  # noqa: D102
        self.statements.append(sql)
        low = " ".join(sql.lower().split())
        for needle in self.fail_on:
            if needle.lower() in low:
                raise RuntimeError(f"[SQLCODE: <-119>:<UNIQUE constraint failed>] on {needle}")
        self._answer = []
        if "%dictionary.compiledclass" in low:
            self._answer = [(1 if self.class_present else 0,)]
            return
        if "information_schema.tables" in low:
            if f"'{RESCUE_STAGING_TABLE.lower()}'" in low:
                self._answer = [(1 if self.rescue_table else 0,)]
            elif "'rdf_edges'" in low:
                self._answer = [(1 if self.edges_table else 0,)]
            else:
                self._answer = [(0,)]
            return
        if not low.startswith("select count(*)"):
            return
        m = re.match(r"select count\(\*\) from graph_kg\.(\w+)(.*)$", low)
        table, rest = (m.group(1), m.group(2)) if m else ("", "")
        filtered = " where " in f" {rest} "
        if table == RESCUE_UNPLACED_TABLE.lower():
            self._answer = [(self.unplaced,)]
        elif table == RESCUE_STAGING_TABLE.lower():
            self._answer = [(self.collapsed if filtered else self.staged,)]
        elif table == "rdf_edges":
            self._answer = [(self.foreign if filtered else self.restored,)]

    def fetchone(self):  # noqa: D102
        return self._answer[0] if self._answer else None

    def fetchall(self):  # noqa: D102
        return list(self._answer)

    def close(self):  # noqa: D102
        pass

    def matching(self, needle: str) -> list[str]:
        n = " ".join(needle.lower().split())
        return [s for s in self.statements if n in " ".join(s.lower().split())]

    def index_of(self, needle: str) -> int:
        n = " ".join(needle.lower().split())
        for i, s in enumerate(self.statements):
            if n in " ".join(s.lower().split()):
                return i
        raise AssertionError(f"no statement matching {needle!r} in {self.statements!r}")


def _deploy(cursor):
    with patch("iris_vector_graph.schema._call_classmethod", return_value=1):
        with patch.object(
            GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()
        ):
            GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))


# ---------------------------------------------------------------------------
# Bug 1: resume
# ---------------------------------------------------------------------------


class TestAPendingRescueIsResumed:
    def test_restore_with_nothing_staged_resumes_a_left_behind_rescue_table(self):
        """The exact re-run the error message asks for: no class, rows still staged."""
        cursor = RescueCursor(class_present=False, rescue_table=True)
        result = GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert cursor.matching("CREATE TABLE Graph_KG.rdf_edges("), cursor.statements
        assert cursor.matching("INSERT INTO Graph_KG.rdf_edges "), cursor.statements
        assert cursor.matching(f"DROP TABLE {STAGING}"), cursor.statements
        assert result is not None
        assert result["staged"] == 5

    def test_deploy_resumes_when_the_class_is_already_gone(self):
        cursor = RescueCursor(class_present=False, rescue_table=True)
        _deploy(cursor)
        assert cursor.matching("INSERT INTO Graph_KG.rdf_edges "), cursor.statements
        assert cursor.matching(f"DROP TABLE {STAGING}"), cursor.statements

    def test_no_rescue_table_means_no_ddl_and_no_dml(self):
        cursor = RescueCursor(class_present=False, rescue_table=False)
        assert GraphSchema.restore_rescued_rdf_edges(cursor, None) is None
        writes = [
            s
            for s in cursor.statements
            if re.match(r"\s*(create|drop|insert|update|delete|alter)\b", s, re.I)
        ]
        assert writes == [], writes

    def test_a_rescue_table_beside_a_live_class_is_left_to_staging(self):
        """While the class exists its extent is the authoritative copy; do not rebuild over it."""
        cursor = RescueCursor(class_present=True, rescue_table=True)
        assert GraphSchema.restore_rescued_rdf_edges(cursor, None) is None
        assert not cursor.matching("CREATE TABLE Graph_KG.rdf_edges(")
        assert not cursor.matching("DROP TABLE")

    def test_initialize_schema_resumes_even_without_the_objectscript_deploy(self):
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
                        "iris_vector_graph.schema.GraphSchema.restore_rescued_rdf_edges",
                        return_value=None,
                    ) as restore:
                        engine.initialize_schema(auto_deploy_objectscript=False)
        restore.assert_called_once()
        assert restore.call_args.args[1] is None

    def test_initialize_schema_reraises_a_failed_resume(self):
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
                        "iris_vector_graph.schema.GraphSchema.restore_rescued_rdf_edges",
                        side_effect=RdfEdgesRescueError("still staged"),
                    ):
                        with pytest.raises(RdfEdgesRescueError):
                            engine.initialize_schema(auto_deploy_objectscript=False)


class TestWhatTheReRunLeavesInTheWay:
    def test_an_empty_leftover_rdf_edges_is_dropped_before_the_rebuild(self):
        cursor = RescueCursor(edges_table=True, foreign=0)
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        # `DROP TABLE Graph_KG.rdf_edges` is a prefix of the staging drop; find the exact one.
        exact = [
            i
            for i, s in enumerate(cursor.statements)
            if s.strip().lower() == "drop table graph_kg.rdf_edges"
        ]
        assert exact, cursor.statements
        drop = exact[0]
        assert drop < cursor.index_of("CREATE TABLE Graph_KG.rdf_edges(")

    def test_the_compat_view_is_dropped_before_the_leftover_table(self):
        """SQLCODE -321: a view over the table blocks DROP TABLE."""
        cursor = RescueCursor(edges_table=True, foreign=0)
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        exact = [
            i
            for i, s in enumerate(cursor.statements)
            if s.strip().lower() == "drop table graph_kg.rdf_edges"
        ]
        assert cursor.index_of("DROP VIEW SQLUser.rdf_edges") < exact[0]

    def test_the_compat_view_is_rebuilt_over_the_new_table(self):
        """A view compiled against the deleted class dangles (-30) and blocks re-creation."""
        cursor = RescueCursor(edges_table=False)
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert cursor.index_of("DROP VIEW SQLUser.rdf_edges") < cursor.index_of(
            "CREATE TABLE Graph_KG.rdf_edges("
        )
        assert cursor.index_of("CREATE VIEW SQLUser.rdf_edges") > cursor.index_of(
            "INSERT INTO Graph_KG.rdf_edges "
        )

    def test_a_missing_view_does_not_stop_the_restore(self):
        cursor = RescueCursor(edges_table=False)
        cursor.fail_on = ("DROP VIEW SQLUser.rdf_edges",)
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert cursor.matching(f"DROP TABLE {STAGING}")

    def test_a_leftover_holding_rows_the_rescue_lacks_is_never_dropped(self):
        cursor = RescueCursor(edges_table=True, foreign=7)
        with pytest.raises(RdfEdgesRescueError, match="7"):
            GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert not [
            s for s in cursor.statements if s.strip().lower() == "drop table graph_kg.rdf_edges"
        ]
        assert not cursor.matching(f"DROP TABLE {STAGING}")

    def test_the_leftover_check_compares_all_five_columns_exactly(self):
        cursor = RescueCursor(edges_table=True, foreign=0)
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        probe = [
            s
            for s in cursor.statements
            if re.match(r"(?is)\s*select count\(\*\) from graph_kg\.rdf_edges\s+\w+\s+where", s)
        ]
        assert probe, cursor.statements
        body = probe[0].lower()
        assert STAGING.lower() in body
        for col in ("s", "p", "o_id", "graph_id"):
            assert f".{col} =" in body, body
        assert "%exact" in body and "qualifiers" in body


class TestStagingNeverDropsAPendingRescue:
    def test_a_pending_rescue_is_appended_to_not_replaced(self):
        """Dropping it would lose every row an earlier run staged and then deleted."""
        cursor = RescueCursor(class_present=True, rescue_table=True, staged=5)
        GraphSchema.stage_class_owned_rdf_edges(cursor)
        assert not cursor.matching(f"DROP TABLE {STAGING}"), cursor.statements
        assert not cursor.matching(f"CREATE TABLE {STAGING}"), cursor.statements
        insert = cursor.matching(f"INSERT INTO {STAGING}")
        assert insert and "not exists" in insert[0].lower(), cursor.statements

    def test_no_pending_rescue_stages_from_scratch(self):
        cursor = RescueCursor(class_present=True, rescue_table=False, staged=5)
        assert GraphSchema.stage_class_owned_rdf_edges(cursor) == 5
        assert cursor.matching(f"CREATE TABLE {STAGING}"), cursor.statements


# ---------------------------------------------------------------------------
# Bug 3: duplicates collapse
# ---------------------------------------------------------------------------


class TestDuplicatesCollapse:
    def test_the_insert_keeps_one_row_per_unique_key(self):
        cursor = RescueCursor()
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        insert = cursor.matching("INSERT INTO Graph_KG.rdf_edges ")[0].lower()
        assert "not exists" in insert
        assert "d.%id < r.%id" in insert, insert
        for col in ("s", "p", "o_id", "graph_id"):
            assert f"d.{col} = r.{col}" in insert, insert

    def test_null_graph_ids_are_normalised_before_the_dedupe(self):
        cursor = RescueCursor()
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        update = cursor.matching(f"UPDATE {STAGING}")
        assert update, cursor.statements
        assert "graph_id is null" in update[0].lower()
        assert cursor.index_of(f"UPDATE {STAGING}") < cursor.index_of(
            "INSERT INTO Graph_KG.rdf_edges "
        )

    def test_the_collapsed_count_compares_qualifiers_exactly(self):
        """Default VARCHAR collation is SQLUPPER: '{"A":1}' = '{"a":1}' without %EXACT."""
        cursor = RescueCursor()
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        count = [
            s
            for s in cursor.statements
            if re.match(rf"(?is)\s*select count\(\*\) from {re.escape(STAGING)}\s+\w+\s+where", s)
        ]
        assert count, cursor.statements
        assert "%exact(d.qualifiers) = %exact(r.qualifiers)" in count[0].lower()

    def test_collapsed_rows_balance_the_accounting(self, caplog):
        cursor = RescueCursor(staged=5, restored=3, collapsed=2, unplaced=0)
        with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
            result = GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert result == {
            "staged": 5,
            "restored": 3,
            "collapsed": 2,
            "quarantined": 0,
        }
        assert cursor.matching(f"DROP TABLE {STAGING}")
        assert not cursor.matching(f"CREATE TABLE {UNPLACED}"), "nothing needed quarantine"
        assert "collapsed" in caplog.text and "2" in caplog.text

    def test_collapsed_plus_quarantined_balance_too(self):
        cursor = RescueCursor(staged=5, restored=2, collapsed=1, unplaced=2)
        result = GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert result["quarantined"] == 2 and result["collapsed"] == 1
        assert cursor.matching(f"CREATE TABLE {UNPLACED}")
        assert cursor.matching(f"DROP TABLE {STAGING}")

    def test_an_unbalanced_count_still_raises_and_keeps_the_rescue(self):
        cursor = RescueCursor(staged=5, restored=3, collapsed=1, unplaced=0)
        with pytest.raises(RdfEdgesRescueError, match="collapsed"):
            GraphSchema.restore_rescued_rdf_edges(cursor, None)
        assert not cursor.matching(f"DROP TABLE {STAGING}")

    def test_conflicting_duplicates_are_quarantined_not_collapsed(self):
        """Same key, different qualifiers: u_spo_graph admits one; the other is kept aside."""
        cursor = RescueCursor(staged=5, restored=3, collapsed=1, unplaced=1)
        GraphSchema.restore_rescued_rdf_edges(cursor, None)
        moved = cursor.matching(f"INSERT INTO {UNPLACED}")[0].lower()
        assert "not (exists" in moved  # unresolvable endpoints, as before
        assert "%exact(d.qualifiers) = %exact(r.qualifiers)" in moved  # and conflicts
