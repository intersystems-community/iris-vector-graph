"""Spec 231 FR-001, FR-012: the three FHIR-graph tables are in the schema script.

They are created with `IF NOT EXISTS` because `initialize_schema` runs the same script
on a fresh namespace and on a 4.0.x one, and a 4.0.x namespace has none of them.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.security import VALID_GRAPH_TABLES
from iris_vector_graph.utils import _split_sql_statements


@pytest.fixture(scope="module")
def statements():
    return _split_sql_statements(GraphSchema.get_base_schema_sql(embedding_dimension=4))


def _create(statements, table):
    hits = [s for s in statements if re.search(rf"CREATE TABLE IF NOT EXISTS Graph_KG\.{table}\s*\(", s)]
    assert len(hits) == 1, f"expected one CREATE TABLE IF NOT EXISTS for {table}, got {len(hits)}"
    return hits[0]


def _columns(ddl):
    body = ddl[ddl.index("(") + 1 : ddl.rindex(")")]
    return {
        line.split()[0]
        for line in (part.strip() for part in body.split("\n"))
        if line and not line.startswith(("CONSTRAINT", "--"))
    }


def test_fhir_graphs(statements):
    ddl = _create(statements, "fhir_graphs")
    assert {
        "graph_id",
        "repo_id",
        "rsrc_schema",
        "search_schema",
        "ver_schema",
        "endpoints",
        "denylist",
        "interval_s",
        "wm_rsrc",
        "wm_ver",
        "task_id",
        "last_sync",
        "last_rebuild",
        "last_error",
        "last_counts",
    } <= _columns(ddl)
    assert re.search(r"PRIMARY KEY\s*\(graph_id\)", ddl)


def test_fhir_unresolved(statements):
    ddl = _create(statements, "fhir_unresolved")
    assert {"graph_id", "source", "param", "target", "reason"} <= _columns(ddl)
    joined = "\n".join(statements)
    # Resync reads the rows a key owns and the rows that point at it.
    assert re.search(r"ON Graph_KG\.fhir_unresolved\s*\(graph_id,\s*source\)", joined)
    assert re.search(r"ON Graph_KG\.fhir_unresolved\s*\(graph_id,\s*target\)", joined)


def test_code_crosswalk(statements):
    ddl = _create(statements, "code_crosswalk")
    assert {
        "code_system_uri",
        "code",
        "target_graph",
        "target_node_id",
        "relation",
        "source",
        "source_version",
        "confidence",
    } <= _columns(ddl)
    # One mapping per (node, code); the key also serves node -> codes lookups.
    assert re.search(
        r"PRIMARY KEY\s*\(target_graph,\s*target_node_id,\s*code_system_uri,\s*code\)", ddl
    )
    joined = "\n".join(statements)
    assert re.search(r"ON Graph_KG\.code_crosswalk\s*\(code_system_uri,\s*code\)", joined)


@pytest.mark.parametrize("table", ["fhir_graphs", "fhir_unresolved", "code_crosswalk"])
def test_allowlisted(table):
    """`_t()` validates every name; an unlisted table raises, it does not degrade."""
    assert table in VALID_GRAPH_TABLES
