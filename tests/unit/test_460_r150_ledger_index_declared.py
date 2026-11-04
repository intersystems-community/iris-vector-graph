"""4.6.0 R150: `idx_ledger_correlation` is declared in `Graph.KG.LedgerRevision`, so a reload of the class keeps it.

Setup created it with `CREATE INDEX` (R137), which writes it into the compiled class and nowhere else, so any load of
the class source (`tcp-deploy`, a `$System.OBJ.Load`) dropped it until the next `initialize_schema()`. The class now
declares it under the names `CREATE INDEX` gave it (class index `idxledgercorrelation`, SQL name
`idx_ledger_correlation`), so a namespace's built entries stay where the declaration reads them.

A declaration has a cost a `CREATE INDEX` does not: the compile that adds it to a table already holding rows builds
nothing, and an equality lookup through the empty index answers no row. So setup, finding the index present, calls
`LedgerRevision.BuildCorrelationIndex` on the schema's own ledger class, which builds it when the table has rows and
the index no entry. The reload and the build run live in tests/e2e/test_460_r150_ledger_index_reload_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "LedgerRevision.cls"


def test_the_class_declares_the_index_under_the_names_create_index_gave_it():
    src = CLS.read_text()
    assert re.search(r"^Index idxledgercorrelation On CorrelationId \[ SqlName = idx_ledger_correlation \];$", src, re.M)
    assert "Property CorrelationId As %String(MAXLEN = 256) [ SqlFieldName = correlation_id ];" in src


class _Cursor:
    def __init__(self, index_present: bool):
        self.index_present, self._row, self.log = index_present, None, []

    def execute(self, sql, params=None):
        self.log.append(sql)
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self._row = (1,)
        elif "INFORMATION_SCHEMA.INDEXES" in sql:
            self._row = (1,) if self.index_present else None
        else:
            self._row = None

    def fetchone(self):
        return self._row


def test_a_present_index_is_handed_to_the_builder():
    built = []
    assert GraphSchema.ensure_ledger_index(_Cursor(True), build=lambda: built.append(1)) == "present"
    assert built == [1]


def test_a_created_index_needs_no_build():
    built = []
    cur = _Cursor(False)
    assert GraphSchema.ensure_ledger_index(cur, build=lambda: built.append(1)) == "created"
    assert built == [] and GraphSchema.LEDGER_INDEX_DDL in cur.log


def test_a_failed_build_raises():
    def build():
        raise RuntimeError("Graph.KG.LedgerRevision.BuildCorrelationIndex failed: ERROR #5002")

    with pytest.raises(RuntimeError, match="BuildCorrelationIndex"):
        GraphSchema.ensure_ledger_index(_Cursor(True), build=build)


@pytest.mark.parametrize("schema,cls", [("Graph_KG", "Graph.KG.LedgerRevision"), ("IVGALT", "IVGALT.LedgerRevision")])
def test_setup_builds_on_the_schemas_own_ledger_class(schema, cls):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._schema_prefix = schema
    nat = MagicMock()
    nat.classMethodValue.side_effect = lambda c, m, *a: "1" if m == "BuildCorrelationIndex" else ""
    eng._iris_obj = lambda: nat
    eng._ledger_index_builder()()
    nat.classMethodValue.assert_any_call(cls, "BuildCorrelationIndex")


def test_a_build_status_error_raises_naming_the_method():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._schema_prefix = "Graph_KG"
    nat = MagicMock()
    nat.classMethodValue.side_effect = lambda c, m, *a: "0 boom" if m == "BuildCorrelationIndex" else "ERROR #5002: boom"
    eng._iris_obj = lambda: nat
    with pytest.raises(RuntimeError, match="Graph.KG.LedgerRevision.BuildCorrelationIndex failed: ERROR #5002: boom"):
        eng._ledger_index_builder()()
