"""The native path declines a query over a label mapped to a SQL table (map_sql_table, spec 040).

Graph.KG.Cypher.Exec reads ^KG and the graph tables; a mapped label's rows live in the user's table, so the native
answer to `MATCH (n:Mapped) WHERE n.MRN = $mrn RETURN n.Name` was no rows, with no error, where the SQL path, which
reads the mapped table, answered. Found 2026-10-24 by tests/unit/test_sql_table_bridge.py, failing since native Cypher
became the default.
"""

from unittest.mock import MagicMock

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

MAPPING = {"sql_table": "BridgeTest.Patient", "id_column": "PatientID", "prop_columns": None}


def _engine(mappings):
    e = IRISGraphEngine(MagicMock(), native_cypher=True)
    e._adjacency_indexed_cached = True
    e._in_bulk_load = False
    e._native_privileges = lambda: True
    e._native_executor_cached = True
    e._table_mapping_cache = mappings
    e._run_native_plan = MagicMock(side_effect=lambda plan, params: IVGResult(columns=list(plan["columns"]), rows=[]))
    return e


def test_a_mapped_label_goes_to_the_sql_path():
    e = _engine({"Patient": MAPPING})
    assert e._try_native(parse_query("MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name"), {"mrn": "x"}) is None
    e._run_native_plan.assert_not_called()


def test_a_mapped_label_past_the_first_node_goes_to_the_sql_path():
    e = _engine({"Patient": MAPPING})
    q = "MATCH (a:Person)-[:KNOWS]->(p:Patient) RETURN count(*) AS n"
    assert e._try_native(parse_query(q), {}) is None
    e._run_native_plan.assert_not_called()


def test_an_unmapped_label_stays_native():
    e = _engine({"Patient": MAPPING})
    assert e._try_native(parse_query("MATCH (p:Person) WHERE p.sid = $id RETURN p.firstName"), {"id": "a"}) is not None


def test_no_mappings_stays_native():
    e = _engine({})
    assert e._try_native(parse_query("MATCH (p:Person) WHERE p.sid = $id RETURN p.firstName"), {"id": "a"}) is not None
