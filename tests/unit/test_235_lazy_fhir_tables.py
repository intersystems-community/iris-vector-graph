"""The FHIR-graph tables are created by the first `fhir_graph_register`, not by
`initialize_schema`, so a namespace that never syncs FHIR carries none of them.
`fhir_bridges` and `code_crosswalk` stay in the base script: neither needs a FHIR
repository."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.utils import _split_sql_statements

LAZY = ("fhir_graphs", "fhir_unresolved", "fhir_definitions", "fhir_canonical_refs")
EAGER = ("fhir_bridges", "code_crosswalk")


def _names(sql):
    return {
        m.group(1)
        for s in _split_sql_statements(sql)
        for m in [re.search(r"CREATE TABLE IF NOT EXISTS Graph_KG\.(\w+)", s)]
        if m
    }


@pytest.mark.parametrize("table", LAZY)
def test_base_schema_omits(table):
    assert table not in _names(GraphSchema.get_base_schema_sql(embedding_dimension=4))
    assert re.search(rf"\bGraph_KG\.{table}\b", GraphSchema.get_base_schema_sql(4)) is None


@pytest.mark.parametrize("table", EAGER)
def test_base_schema_keeps(table):
    assert table in _names(GraphSchema.get_base_schema_sql(embedding_dimension=4))


def test_fhir_graph_schema_has_exactly_the_lazy_tables():
    assert _names(GraphSchema.get_fhir_graph_schema_sql()) == set(LAZY)


def _engine():
    conn = MagicMock()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    conn.reset_mock()  # the constructor's own SQL
    native = MagicMock()
    native.classMethodValue.return_value = json.dumps({"status": "ok", "graph_id": "g"})
    return engine, native


def test_register_creates_tables_before_register():
    engine, native = _engine()
    order = []
    engine.conn.cursor.return_value.execute.side_effect = lambda sql, *a: order.append(sql)
    native.classMethodValue.side_effect = lambda *a: order.append(a[1]) or json.dumps(
        {"status": "ok"}
    )
    with patch.object(engine, "_iris_obj", return_value=native):
        engine.fhir_graph_register()
    assert order[-1] == "Register"
    created = _names(";\n".join(s for s in order[:-1]))
    assert created == set(LAZY)
    assert engine.conn.commit.called


def test_register_tolerates_existing_tables():
    engine, native = _engine()

    def execute(sql, *a):
        if sql.lstrip().upper().startswith("CREATE INDEX"):
            raise RuntimeError("[SQLCODE: <-324>:<Index with this name already exists>]")

    engine.conn.cursor.return_value.execute.side_effect = execute
    with patch.object(engine, "_iris_obj", return_value=native):
        engine.fhir_graph_register()
    assert native.classMethodValue.call_args.args[1] == "Register"


def test_register_raises_other_ddl_errors():
    engine, native = _engine()
    engine.conn.cursor.return_value.execute.side_effect = RuntimeError(
        "[SQLCODE: <-99>:<Privilege violation>]"
    )
    with patch.object(engine, "_iris_obj", return_value=native):
        with pytest.raises(RuntimeError, match="-99"):
            engine.fhir_graph_register()
    native.classMethodValue.assert_not_called()


def test_anchors_without_fhir_graphs_table():
    engine, native = _engine()
    engine.conn.cursor.return_value.execute.side_effect = RuntimeError(
        "[SQLCODE: <-30>:<Table or view not found>] [Details: <Table 'GRAPH_KG.FHIR_GRAPHS' not found>]"
    )
    with patch.object(engine, "_iris_obj", return_value=native):
        out = engine.fhir_patient_anchors("p1")
    assert out == {"graphs": [], "anchors": []}
    native.classMethodValue.assert_not_called()


def test_anchors_other_sql_errors_raise():
    engine, native = _engine()
    engine.conn.cursor.return_value.execute.side_effect = RuntimeError("[SQLCODE: <-99>]")
    with patch.object(engine, "_iris_obj", return_value=native):
        with pytest.raises(RuntimeError):
            engine.fhir_patient_anchors("p1")


def test_register_without_fhir_repository_creates_nothing():
    """Register answers "no FHIR repository"; the namespace keeps no empty tables."""
    engine, native = _engine()
    cursor = engine.conn.cursor.return_value
    cursor.fetchone.return_value = (0,)
    native.classMethodValue.return_value = json.dumps(
        {"status": "error", "error": "namespace USER has no FHIR repository"}
    )
    with patch.object(engine, "_iris_obj", return_value=native):
        with pytest.raises(Exception, match="no FHIR repository"):
            engine.fhir_graph_register()
    executed = [c.args[0] for c in cursor.execute.call_args_list]
    assert not any("CREATE" in s for s in executed)
    assert any("HS_FHIRServer" in s for s in executed)
