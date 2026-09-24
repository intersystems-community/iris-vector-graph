"""Spec 231 US5: `SHOW CONSTRAINTS` reports the `fhir_bridges` key that exists.

The probe looked for a class `Graph.KG.FHIRBridge`, which no release has shipped;
`fhir_bridges` is DDL-declared (`schema.py`) and its projected class is
`Graph.KG.fhirbridges`. The reported row also named columns the table does not have
(`external_id`, `bridge_type`, `node_id`) and a constraint `pk_fhir_bridges`. The
table's key is `pk_bridge PRIMARY KEY (fhir_code, kg_node_id)`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine


def _eng(exists: int):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (exists,)
    cursor.fetchall.return_value = []
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    return eng, cursor


def test_probes_the_table_not_a_class():
    eng, cursor = _eng(1)
    eng._show_constraints()
    sql = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
    assert "Graph.KG.FHIRBridge" not in sql
    assert "INFORMATION_SCHEMA.TABLES" in sql
    assert "fhir_bridges" in sql


def test_reports_real_primary_key():
    eng, _ = _eng(1)
    rows = eng._show_constraints().rows
    row = next(r for r in rows if r[0] == "fhir_bridge_unique")
    assert row[4] == ["fhir_code", "kg_node_id"]
    assert row[5] == "pk_bridge"


def test_absent_table_reports_nothing():
    eng, _ = _eng(0)
    names = [r[0] for r in eng._show_constraints().rows]
    assert "fhir_bridge_unique" not in names
