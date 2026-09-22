"""Spec 227 — the two legacy embedding tables are re-keyed in the fresh-install DDL.

`Graph_KG.kg_NodeEmbeddings` and `kg_NodeEmbeddings_optimized` were
`id VARCHAR(256) %EXACT PRIMARY KEY` + `emb`, with no graph column at all. Under
4.0.0 they are the *default route*: one graph's vectors for one model, keyed the
same way a generated route is (contracts/sql-schema.md §2), so that the procedure
in §1 and a generated statement against a route have the same column names.

`id` becomes `node_id` and the primary key becomes an identity. Both matter:

- `node_id` because a node ID is no longer unique on its own, so a column called
  `id` would name something that is not an identifier.
- `emb_rowid BIGINT IDENTITY PRIMARY KEY` because IRIS only accepts an HNSW index
  on a table whose IdKey is a single integer identity (research R3). Keeping
  `node_id` as the primary key would make the index illegal, and a route with no
  ANN index is the thing FR-018 is trying to avoid.

Uniqueness moves to `(graph_id, node_id)`: one vector per node per route, two
graphs' copies of one node ID coexisting.

The `.cls` half of this re-key (`iris_src/src/Graph/KG/kgNodeEmbeddings.cls`) is
tasks T018/T019 and is asserted by the container gate, not here — this file only
pins the DDL that a DDL-only namespace gets.
"""

import re

import pytest

from iris_vector_graph.schema import GraphSchema

EMBEDDING_TABLES = ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized")


@pytest.fixture
def ddl():
    return GraphSchema.get_base_schema_sql(embedding_dimension=384)


def _table_block(ddl: str, table: str) -> str:
    """The text of one CREATE TABLE statement.

    Matched on the exact table name followed by `(` so
    `kg_NodeEmbeddings` does not also match `kg_NodeEmbeddings_optimized`.
    """
    m = re.search(
        rf"CREATE TABLE\s+Graph_KG\.{re.escape(table)}\s*\((.*?)\n\);",
        ddl,
        re.IGNORECASE | re.DOTALL,
    )
    assert m, f"no CREATE TABLE for {table} in the base DDL"
    return m.group(1)


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_the_embedding_table_has_a_graph_column(ddl, table):
    block = _table_block(ddl, table)
    assert re.search(r"graph_id\s+VARCHAR\(256\)\s+%EXACT\s+NOT NULL\s+DEFAULT\s+''", block), (
        f"{table} has no graph_id: {block}"
    )


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_the_id_column_is_named_node_id(ddl, table):
    block = _table_block(ddl, table)
    assert re.search(r"\bnode_id\s+VARCHAR\(256\)\s+%EXACT\s+NOT NULL", block), block
    assert not re.search(r"^\s*id\s+VARCHAR", block, re.MULTILINE), (
        f"{table} still declares a bare `id` column; a node ID is no longer an "
        f"identifier on its own: {block}"
    )


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_the_primary_key_is_an_integer_identity(ddl, table):
    """HNSW legality (research R3), not taste."""
    block = _table_block(ddl, table)
    assert re.search(r"emb_rowid\s+BIGINT\s+IDENTITY\s+PRIMARY KEY", block, re.IGNORECASE), (
        f"{table} needs a single integer identity IdKey or IRIS refuses the HNSW "
        f"index: {block}"
    )


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_uniqueness_is_scoped_to_the_graph(ddl, table):
    block = _table_block(ddl, table)
    m = re.search(r"UNIQUE\s*\(([^)]*)\)", block, re.IGNORECASE)
    assert m, f"{table} declares no uniqueness at all: {block}"
    cols = [c.strip().lower() for c in m.group(1).split(",")]
    assert cols == ["graph_id", "node_id"], (
        f"{table} must hold one vector per node per graph, got UNIQUE {cols}"
    )


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_the_vector_column_keeps_its_declared_width(ddl, table):
    """The width is the enforcement point (ADR-0005): declared here, not on TO_VECTOR."""
    block = _table_block(ddl, table)
    assert re.search(r"emb\s+VECTOR\(DOUBLE,\s*384\)", block), block


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_the_embedding_table_references_the_composite_node_key(ddl, table):
    """The reference is composite, not dropped (FR-008, `schema.py` DDL comment).

    This test used to assert the opposite — that no `FOREIGN KEY` is declared here — on
    the reasoning that a single-column reference to `node_id` has nothing unique left to
    point at once 227 re-keyed `nodes`. That premise is right and the conclusion was
    wrong: the fix is to reference the composite key, not to give up referential
    integrity. Without it an embedding can be written for a node that does not exist, and
    a node holding one can be deleted.

    The column order matters: IRIS requires the referencing order to match the referenced
    constraint's own order (`uq_nodes_graph_node` is `(graph_id, node_id)`) and otherwise
    refuses the DDL with `SQLCODE -121`.
    """
    block = _table_block(ddl, table)
    m = re.search(
        r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+([\w.]+)\s*\(([^)]*)\)",
        block,
        re.IGNORECASE,
    )
    assert m, f"{table} declares no reference to nodes: {block}"
    referencing = [c.strip().lower() for c in m.group(1).split(",")]
    referenced = [c.strip().lower() for c in m.group(3).split(",")]
    assert referencing == ["graph_id", "node_id"], referencing
    assert m.group(2).lower() == "graph_kg.nodes", m.group(2)
    assert referenced == ["graph_id", "node_id"], (
        f"the referenced order must match uq_nodes_graph_node or IRIS refuses with -121, "
        f"got {referenced}"
    )
