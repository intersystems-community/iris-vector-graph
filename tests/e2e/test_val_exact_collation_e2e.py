"""rdf_props.val stays EXACT through ensure_indexes, so value equality is case-sensitive.

ensure_indexes ran `ALTER COLUMN val VARCHAR(64000)` on every call, and IRIS resets a
column with no stated collation to SQLUPPER: on a deployed namespace `val = 'JOHN'`
matched 'John'. The Cypher path hid it for values under 128 characters whenever the value
key was present (`val_key` is EXACT), so the raw-SQL assertion is the one that counts.

Run against a scratch namespace, e.g. `IVG_NAMESPACE=IVGFIX`.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _val_parameters(cursor):
    cursor.execute(
        "SELECT Parameters FROM %Dictionary.PropertyDefinition "
        "WHERE parent = 'Graph.KG.rdfprops' AND Name = 'val'"
    )
    row = cursor.fetchone()
    return (row[0] or "").upper() if row else ""


def test_val_is_exact_after_ensure_indexes_and_equality_is_case_sensitive(
    iris_connection, iris_master_cleanup
):
    cur = iris_connection.cursor()
    GraphSchema.ensure_indexes(cur)
    GraphSchema.ensure_indexes(cur)  # the second run must not undo it either
    iris_connection.commit()
    assert "COLLATION,EXACT" in _val_parameters(cur)

    node = f"coll_{uuid.uuid4().hex[:8]}"
    engine = IRISGraphEngine(iris_connection)
    engine.create_node(node, labels=["Probe"], properties={"name": "John"})

    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE s = ? AND val = ?", [node, "JOHN"])
    assert int(cur.fetchone()[0]) == 0
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE s = ? AND val = ?", [node, "John"])
    assert int(cur.fetchone()[0]) == 1

    upper = engine.execute_cypher("MATCH (n:Probe) WHERE n.name = 'JOHN' RETURN n.name")
    exact = engine.execute_cypher("MATCH (n:Probe) WHERE n.name = 'John' RETURN n.name")
    assert getattr(upper, "error", None) is None and getattr(exact, "error", None) is None
    assert upper.rows == [] and [list(r) for r in exact.rows] == [["John"]]
