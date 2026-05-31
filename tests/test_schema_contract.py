"""Schema-contract test: pins the Graph_KG SQL table column sets so that schema
drift (a renamed/dropped column, a projection-class mismatch, an assumed-but-
missing column like the rdf_edges.weight case) fails loudly in CI instead of
surfacing as a runtime <ARGUMENT ERROR> or silent data loss.

Asserts the actual INFORMATION_SCHEMA.COLUMNS column set for every core
Graph_KG table against the contract below. If you intentionally change the DDL
in schema.py, update EXPECTED_COLUMNS in the same commit — that is the point.
"""
import pytest

# Canonical column sets, in no particular order. Keys are bare table names
# under the Graph_KG schema. Values are the column NAMES as IRIS reports them
# via INFORMATION_SCHEMA.COLUMNS (note: a BIGINT IDENTITY PRIMARY KEY projects
# as "ID", not its DDL name — see rdf_edges).
EXPECTED_COLUMNS = {
    "nodes": {"node_id", "created_at"},
    "rdf_labels": {"s", "label"},
    "rdf_props": {"s", "key", "val"},
    "rdf_edges": {"ID", "s", "p", "o_id", "qualifiers"},
    "kg_NodeEmbeddings": {"id", "emb", "metadata"},
    "kg_NodeEmbeddings_optimized": {"id", "emb", "metadata"},
    "kg_EdgeEmbeddings": {"s", "p", "o_id", "emb"},
    "docs": {"id", "text"},
    "fhir_bridges": {
        "fhir_code", "kg_node_id", "fhir_code_system",
        "bridge_type", "confidence", "source_cui",
    },
    "rdf_reifications": {"reifier_id", "edge_id"},
    "table_mappings": {
        "label", "sql_table", "id_column", "prop_columns", "registered_at",
    },
    "relationship_mappings": {
        "predicate", "source_label", "target_label", "target_fk",
        "via_table", "via_source", "via_target",
    },
}


def _actual_columns(conn, table):
    cur = conn.cursor()
    cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ? "
        "ORDER BY ORDINAL_POSITION",
        [table],
    )
    return {row[0] for row in cur.fetchall()}


@pytest.mark.parametrize("table,expected", sorted(EXPECTED_COLUMNS.items()))
def test_table_column_contract(iris_connection, table, expected):
    actual = _actual_columns(iris_connection, table)
    if not actual:
        pytest.skip(f"Graph_KG.{table} not deployed in this container")
    missing = expected - actual
    extra = actual - expected
    assert not missing, (
        f"Graph_KG.{table} is MISSING expected columns {sorted(missing)}. "
        f"Actual columns: {sorted(actual)}. "
        f"If the DDL changed intentionally, update EXPECTED_COLUMNS."
    )
    assert not extra, (
        f"Graph_KG.{table} has UNEXPECTED columns {sorted(extra)}. "
        f"Actual columns: {sorted(actual)}. "
        f"If the DDL changed intentionally, update EXPECTED_COLUMNS."
    )


def test_rdf_edges_has_no_weight_column(iris_connection):
    """Regression guard for the Bug-N/O/P/Q cascade: edge weight is stored in
    the `qualifiers` DynamicObject (qualifiers.weight), NOT as a SQL column.
    Code that assumes a `weight` column is wrong and will fail at runtime.
    """
    cols = _actual_columns(iris_connection, "rdf_edges")
    if not cols:
        pytest.skip("Graph_KG.rdf_edges not deployed")
    assert "weight" not in cols, (
        "Graph_KG.rdf_edges has a 'weight' column — the contract is that edge "
        "weight lives in qualifiers (JSON). Either the DDL drifted or a bulk "
        "method silently added it. Reconcile schema.py and EdgeScan.cls."
    )


def test_all_contract_tables_exist(iris_connection):
    core = ["nodes", "rdf_labels", "rdf_props", "rdf_edges"]
    cur = iris_connection.cursor()
    deployed = []
    for t in core:
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ?",
            [t],
        )
        if cur.fetchone()[0]:
            deployed.append(t)
    assert deployed == core, (
        f"Core Graph_KG tables missing: {set(core) - set(deployed)}. "
        f"initialize_schema() may have failed partway."
    )
