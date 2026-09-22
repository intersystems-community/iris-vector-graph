"""Spec 230 — an iFind index whose generated helper class is gone is repaired.

Found by running the T074 gate after the fifth defect was fixed: every text leg on
an upgraded install answered

    [SQLCODE: <-149>] Index method Graph.KG.docsivg400.idxdocstextifindFind(...)
    failed ... <CLASS DOES NOT EXIST> idxdocstextifindEmbedded+2^Graph.KG.docsivg400.1

`%FIND search_index(...)` reaches an iFind index through a *generated* class
(`<owner>.<hash>`, `GeneratedBy = '<owner>.CLS'`, extending `%iFind.Find.Basic`) that
the class compiler writes as a side effect of compiling the owner. Measured live on
`ivg-iris-enterprise`: a second `$SYSTEM.OBJ.CompilePackage("Graph.KG", "ck-d")` — the
recompile the `^KG` re-key step runs, and the same compile a `LoadDir` deploy runs —
deletes that compiled helper and does *not* write a new one, because the owner class it
would be regenerated from is already up to date. The index definition survives in
`%Dictionary.CompiledIndex`, so nothing reports a problem; only a query fails, and it
fails at Open with a class name no caller has heard of.

Compiling the owner class on its own always regenerates the helper, so the repair is a
single `$SYSTEM.OBJ.Compile(owner, "ck-d")` per index left without one. Both of the
schema's iFind indexes are affected — `idx_docs_text_ifind` (the `kg_TXT` leg) and
`idx_props_val_ifind` (property text search).
"""

from __future__ import annotations

import pytest

from iris_vector_graph import schema as schema_mod

DOCS = "Graph.KG.docsivg400"
PROPS = "Graph.KG.rdfprops"


class FakeCursor:
    """Answers the detection query from `rows`, the re-check from `helpers_after`."""

    def __init__(self, rows, helpers_after=None, fail_detect=None):
        #: `(owner, sql_index_name, helper_count)` triples the detection query returns.
        self.rows = list(rows)
        #: `{owner: count}` the per-owner re-check answers after a compile.
        self.helpers_after = dict(helpers_after or {})
        self.fail_detect = fail_detect
        self.executed: list = []
        self._pending = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "CompiledIndex" in sql:
            if self.fail_detect is not None:
                raise self.fail_detect
            self._pending = list(self.rows)
            return
        # The re-check: one count for the owner named in the parameters.
        owner = str((params or [""])[0]).split(".CLS")[0]
        self._pending = [(self.helpers_after.get(owner, 0),)]

    def fetchall(self):
        rows, self._pending = self._pending, []
        return rows

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def close(self):
        pass


@pytest.fixture
def compiled(monkeypatch):
    """Records every `%SYSTEM.OBJ` call the repair makes instead of making it.

    The recorded tuple keeps the target the call was made *through*, because that is
    the part a live server is fussy about: a compile is a Native API call, and a
    DB-API cursor is not a connection — `createIRIS` rejects one outright.
    """
    calls: list = []

    def fake(target, class_name, method_name, *args):
        calls.append((target, class_name, method_name, args))
        return 1

    monkeypatch.setattr(schema_mod, "_call_classmethod", fake)
    return calls


class TestARepairedIndexIsSearchableAgain:
    def test_an_index_with_no_helper_is_recompiled(self, compiled):
        cursor = FakeCursor([(DOCS, "idx_docs_text_ifind", 0)], helpers_after={DOCS: 1})
        result = schema_mod.repair_ifind_helpers(cursor)
        assert result == {"idx_docs_text_ifind": True}
        assert [(c[1], c[2], c[3]) for c in compiled] == [
            ("%SYSTEM.OBJ", "Compile", (DOCS, "ck-d"))
        ]

    def test_the_owner_class_is_what_gets_compiled(self, compiled):
        """Not the index, and not the package — the package compile is the cause."""
        cursor = FakeCursor([(PROPS, "idx_props_val_ifind", 0)], helpers_after={PROPS: 1})
        schema_mod.repair_ifind_helpers(cursor)
        assert [(c[1], c[2], c[3]) for c in compiled] == [
            ("%SYSTEM.OBJ", "Compile", (PROPS, "ck-d"))
        ]

    def test_every_broken_index_is_repaired_not_just_the_first(self, compiled):
        cursor = FakeCursor(
            [(DOCS, "idx_docs_text_ifind", 0), (PROPS, "idx_props_val_ifind", 0)],
            helpers_after={DOCS: 1, PROPS: 1},
        )
        result = schema_mod.repair_ifind_helpers(cursor)
        assert result == {"idx_docs_text_ifind": True, "idx_props_val_ifind": True}
        assert [c[3][0] for c in compiled] == [DOCS, PROPS]


class TestTheCompileGoesThroughAConnection:
    """A compile is a Native API call, and `createIRIS` will not take a cursor.

    Measured against `ivg-iris-enterprise`: a live DB-API cursor exposes no connection
    at all — no ``_connection``, no ``connection`` — so a repair handed only a cursor
    raises ``TypeError: argument 1 must be irissdk.IRISConnection, not Cursor`` and
    reports every index unrepairable. The caller that knows the connection passes it.
    """

    def test_the_connection_is_what_the_compile_is_made_through(self, compiled):
        conn = object()
        cursor = FakeCursor([(DOCS, "idx_docs_text_ifind", 0)], helpers_after={DOCS: 1})
        schema_mod.repair_ifind_helpers(cursor, conn=conn)
        assert compiled[0][0] is conn

    def test_without_a_connection_the_cursor_is_tried(self, compiled):
        """A wrapped cursor that carries its own connection still works."""
        cursor = FakeCursor([(DOCS, "idx_docs_text_ifind", 0)], helpers_after={DOCS: 1})
        schema_mod.repair_ifind_helpers(cursor)
        assert compiled[0][0] is cursor


class TestAHealthyIndexIsLeftAlone:
    def test_no_compile_when_the_helper_is_there(self, compiled):
        cursor = FakeCursor([(DOCS, "idx_docs_text_ifind", 1)])
        assert schema_mod.repair_ifind_helpers(cursor) == {"idx_docs_text_ifind": True}
        assert compiled == []

    def test_a_schema_with_no_ifind_index_reports_nothing(self, compiled):
        assert schema_mod.repair_ifind_helpers(FakeCursor([])) == {}
        assert compiled == []

    def test_the_schema_asked_about_is_the_one_passed(self):
        cursor = FakeCursor([])
        schema_mod.repair_ifind_helpers(cursor, schema="Other_Schema")
        assert cursor.executed[0][1] == ["Other_Schema"]


class TestAFailedRepairIsReportedNotRaised:
    def test_a_compile_that_raises_leaves_the_index_false(self, monkeypatch):
        def boom(*_a, **_kw):
            raise RuntimeError("no native connection")

        monkeypatch.setattr(schema_mod, "_call_classmethod", boom)
        cursor = FakeCursor([(DOCS, "idx_docs_text_ifind", 0)])
        assert schema_mod.repair_ifind_helpers(cursor) == {"idx_docs_text_ifind": False}

    def test_a_compile_that_does_not_help_leaves_the_index_false(self, compiled):
        """The re-check is the verdict, not the compiler's return value."""
        cursor = FakeCursor([(DOCS, "idx_docs_text_ifind", 0)], helpers_after={DOCS: 0})
        assert schema_mod.repair_ifind_helpers(cursor) == {"idx_docs_text_ifind": False}
        assert compiled  # it did try

    def test_a_server_that_refuses_the_detection_query_reports_nothing(self, compiled):
        """An old server, or a cursor with no %Dictionary access: not fatal."""
        cursor = FakeCursor([], fail_detect=RuntimeError("Table not found"))
        assert schema_mod.repair_ifind_helpers(cursor) == {}
        assert compiled == []

    def test_rows_too_short_to_be_an_index_are_skipped(self, compiled):
        """A cursor answering a different question must not break schema setup.

        Found by the release gate: `tests/unit/test_230_docs_index_deferral.py` drives
        `initialize_schema` through a fake cursor that returns one-column rows to every
        query, and the repair raised `IndexError: tuple index out of range` out of a
        function documented never to raise — taking four unrelated tests with it.
        """
        cursor = FakeCursor([("just-one-column",), (DOCS, "idx_docs_text_ifind", 1)])
        assert schema_mod.repair_ifind_helpers(cursor) == {"idx_docs_text_ifind": True}
        assert compiled == []


class TestKgTxtSaysWhichFailureItHit:
    """The two iFind failures are not the same problem, and the operator's move differs.

    `-151`/`-359` mean no index and nothing to do about it — the `LIKE` fallback is the
    answer. `-149` means the index is there and its generated helper is not, which is
    repairable in one call, so the warning has to name the repair instead of claiming
    the index is "unavailable".
    """

    def _engine_answering(self, ifind_error: str):
        from unittest.mock import MagicMock

        from iris_vector_graph import IRISGraphEngine

        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.description = []
        cursor.fetchone.return_value = None
        engine = IRISGraphEngine(conn, embedding_dimension=4)

        calls = {"n": 0}

        def execute(sql, params=None):
            if "search_index" in sql:
                calls["n"] += 1
                raise RuntimeError(ifind_error)

        cursor.execute.side_effect = execute
        cursor.fetchall.return_value = []
        return engine, calls

    def test_a_missing_index_reports_the_fallback(self, caplog):
        engine, calls = self._engine_answering("[SQLCODE: <-151>] index not found")
        with caplog.at_level("WARNING"):
            assert engine.kg_TXT("anything", k=3) == []
        assert calls["n"] == 1
        assert "falling back to LIKE" in caplog.text
        assert "Compile" not in caplog.text

    def test_a_broken_helper_names_the_repair(self, caplog):
        engine, _ = self._engine_answering(
            "[SQLCODE: <-149>] Index method Graph.KG.docsivg400."
            "idxdocstextifindFind failed <CLASS DOES NOT EXIST>"
        )
        with caplog.at_level("WARNING"):
            assert engine.kg_TXT("anything", k=3) == []
        assert "initialize_schema" in caplog.text or "Compile" in caplog.text, (
            "a repairable index has to be reported as repairable — otherwise the "
            "operator reads 'unavailable' and rebuilds an index that is already there"
        )


class TestTheRekeyRepairsWhatItsOwnRecompileBreaks:
    """`rekey_kg_node_stores` compiles `Graph.KG` — the compile that does the damage."""

    def _conn(self):
        from tests.unit.migration_fakes_230 import kg_stores_conn

        return kg_stores_conn(
            props=[{"s": "t:n1", "key": "name", "val": "Alice", "graph_id": ""}],
            labels=[{"s": "t:n1", "label": "Person", "graph_id": ""}],
            edges=[{"s": "t:n1", "p": "knows", "o_id": "t:n2", "graph_id": ""}],
            flat_entries={("label", "Person", "t:n1"): ""},
        )

    @staticmethod
    def _record(monkeypatch, seen: list):
        from iris_vector_graph.migrations import kg_node_stores

        def fake(cursor, conn=None, schema="Graph_KG"):
            seen.append((conn, schema))
            return {}

        monkeypatch.setattr(kg_node_stores, "repair_ifind_helpers", fake)
        return kg_node_stores

    def test_the_repair_runs_for_the_schema_being_migrated(self, monkeypatch):
        seen: list = []
        kg_node_stores = self._record(monkeypatch, seen)
        conn = self._conn()
        kg_node_stores.rekey_kg_node_stores(conn, schema="Graph_KG")
        assert seen == [(conn, "Graph_KG")]

    def test_a_dry_run_repairs_too_because_it_recompiles_too(self, monkeypatch):
        seen: list = []
        kg_node_stores = self._record(monkeypatch, seen)
        conn = self._conn()
        kg_node_stores.rekey_kg_node_stores(conn, dry_run=True)
        assert seen == [(conn, "Graph_KG")]
