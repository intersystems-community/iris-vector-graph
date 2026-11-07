"""4.6.0 R312: setup tunes the namespace's own IVG Schema, and a TUNE TABLE that fails on a table that is there says so.

`initialize_schema()` called `GraphSchema.tune_tables(cursor)` with no schema, so on a non-default IVG Schema it ran
`TUNE TABLE Graph_KG.*`: tables that do not exist there. Each failure was logged at DEBUG, and setup's status reported
the `Graph_KG.*` names (all True when an unrelated Graph_KG also existed, all False otherwise), while the IVGALT tables
were never tuned: edge inserts plan the composite foreign keys as an extent scan (spec 230: 10.2 ms against 0.23 ms per
edge). Found by the spec 262 speed gate (edge_write_row +120% on IVGALT, +17% on Graph_KG).
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from iris_vector_graph.schema import GraphSchema

PKG = Path(__file__).resolve().parents[2] / "iris_vector_graph"


def test_every_call_of_tune_tables_names_its_schema():
    """Structural: a call without `schema=` tunes Graph_KG whatever the engine's IVG Schema is."""
    missing = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "tune_tables"
                    and not any(k.arg == "schema" for k in node.keywords)):
                missing.append(f"{path.relative_to(PKG.parent)}:{node.lineno}")
    assert missing == []


class _Cursor:
    def __init__(self, errors):
        self.errors = errors

    def execute(self, sql, params=None):
        for needle, message in self.errors.items():
            if needle in sql:
                raise RuntimeError(message)


def test_a_failed_tune_of_a_table_that_exists_is_a_warning(caplog):
    cursor = _Cursor({"rdf_edges": "[SQLCODE: <-400>:<Fatal error occurred>] lock timeout"})
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph.schema"):
        status = GraphSchema.tune_tables(cursor, tables=["nodes", "rdf_edges"], schema="IVGALT")
    assert status == {"IVGALT.nodes": True, "IVGALT.rdf_edges": False}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "IVGALT.rdf_edges" in warnings[0].getMessage()


def test_a_table_this_install_lacks_stays_quiet(caplog):
    cursor = _Cursor({"ledger_revisions": "[SQLCODE: <-30>:<Table 'IVGALT.LEDGER_REVISIONS' not found>]"})
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph.schema"):
        status = GraphSchema.tune_tables(cursor, tables=["ledger_revisions"], schema="IVGALT")
    assert status == {"IVGALT.ledger_revisions": False}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
