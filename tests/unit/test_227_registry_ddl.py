"""Spec 227 T020/T021 — the registry becomes a routing table, and quarantine exists.

Two DDL facts, asserted as strings because the column declarations *are* the
guarantee (data-model.md §5, §6):

- The registry gains the four columns a route needs to describe itself honestly —
  the index state IRIS actually reports, the error text when an index was refused,
  and the measured recall with its timestamp — plus the reverse index that turns
  `(graph, model)` into a table name. Spec 230 added a fifth, `kind`, so a pair's
  node route and its edge route are two rows and not two readings of one.
- `embedding_quarantine` holds rows whose graph is unrecoverable. It has no
  `graph_id` (that absence is the point), its `emb` column is declared with no
  length so widths can coexist, and it carries no vector index because nothing
  searches it.

The migration statements are asserted separately: an existing 3.2.0 install gets
the columns by `ALTER TABLE`, and an `ALTER COLUMN` never restates a type.
"""

import re

import pytest

from iris_vector_graph._engine.schema import (
    _EMBEDDING_QUARANTINE_DDL,
    _EMBEDDING_REGISTRY_DDL,
    _REGISTRY_ROUTE_COLUMNS,
)

#: Every column a route describes itself with. The first four are 227's; `kind` is
#: spec 230's (FR-006). One list, because a fresh install and the migration onto a
#: 3.2.0 registry have to reach the same shape whichever spec added the column.
ROUTE_COLUMNS = (
    "index_state",
    "index_error",
    "recall_measured",
    "recall_measured_at",
    "kind",
)


@pytest.fixture
def registry_ddl():
    return _EMBEDDING_REGISTRY_DDL.format(table="Graph_KG.embedding_registry")


@pytest.fixture
def quarantine_ddl():
    return _EMBEDDING_QUARANTINE_DDL.format(table="Graph_KG.embedding_quarantine")


# --- the registry ------------------------------------------------------------


@pytest.mark.parametrize("column", ROUTE_COLUMNS)
def test_fresh_registry_declares_every_route_column(registry_ddl, column):
    assert re.search(rf"\b{column}\b", registry_ddl), (
        f"a fresh install must reach the same shape as the migration; {column} is missing"
    )


def test_registry_key_is_unchanged(registry_ddl):
    """`(table_name, graph_id)` stays the key — 227 makes `graph_id` real, not different."""
    m = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", registry_ddl, re.IGNORECASE)
    assert m, registry_ddl
    cols = [c.strip().lower() for c in m.group(1).split(",")]
    assert cols == ["table_name", "graph_id"]


def test_registry_ddl_has_no_trailing_semicolon(registry_ddl):
    assert not registry_ddl.rstrip().endswith(";")


def test_index_state_is_wide_enough_for_refused(registry_ddl):
    """`refused` is the longest of the three states; a VARCHAR(4) would truncate it."""
    m = re.search(r"index_state\s+VARCHAR\((\d+)\)", registry_ddl, re.IGNORECASE)
    assert m, registry_ddl
    assert int(m.group(1)) >= len("refused")


# --- the migration onto an existing registry ---------------------------------


def test_route_column_migration_names_every_new_column():
    names = {name for name, _ in _REGISTRY_ROUTE_COLUMNS}
    assert names == set(ROUTE_COLUMNS)


def test_route_column_migration_states_a_type_for_each_column():
    """These are ADD COLUMN, where the type is required — unlike ALTER COLUMN."""
    for name, decl in _REGISTRY_ROUTE_COLUMNS:
        assert decl.strip(), name
        assert re.match(r"(VARCHAR\(\d+\)|DOUBLE|TIMESTAMP|INTEGER)", decl.strip(), re.IGNORECASE), (
            f"{name} has no usable type: {decl!r}"
        )


def test_no_route_column_is_declared_not_null():
    """An existing row predates routing and has nothing to put here.

    `NOT NULL` without a default is refused on a non-empty table, and a default
    would claim an index state nobody measured.
    """
    for name, decl in _REGISTRY_ROUTE_COLUMNS:
        assert "not null" not in decl.lower(), f"{name}: {decl}"


# --- quarantine --------------------------------------------------------------


def test_quarantine_has_no_graph_column(quarantine_ddl):
    """The absence is the point: a quarantined row has no graph (FR-039)."""
    assert not re.search(r"\bgraph_id\b", quarantine_ddl, re.IGNORECASE), quarantine_ddl


def test_quarantine_emb_is_unlengthened(quarantine_ddl):
    """Rows of different widths have to coexist, so the column states no width.

    A lengthened column would refuse the very rows this table exists to hold
    (SQLCODE -104, ADR-0005).
    """
    m = re.search(r"emb\s+VECTOR\s*\(([^)]*)\)", quarantine_ddl, re.IGNORECASE)
    assert m, quarantine_ddl
    args = [a.strip() for a in m.group(1).split(",") if a.strip()]
    assert args == ["DOUBLE"], f"emb must declare a type and no length, got {args}"


def test_quarantine_records_the_width_it_no_longer_declares(quarantine_ddl):
    for column in ("dimension", "dtype"):
        assert re.search(rf"\b{column}\b[^,]*NOT\s+NULL", quarantine_ddl, re.IGNORECASE), (
            f"{column} must be present and NOT NULL — it is the only record of the row's width"
        )


def test_quarantine_key_is_an_identity_not_the_node(quarantine_ddl):
    """Two quarantined rows can share a node ID — that ambiguity is why they are here."""
    assert re.search(
        r"q_rowid\s+BIGINT\s+IDENTITY\s+PRIMARY\s+KEY", quarantine_ddl, re.IGNORECASE
    ), quarantine_ddl


def test_quarantine_declares_no_index(quarantine_ddl):
    """No vector index, and no index at all: nothing searches this table (FR-040)."""
    assert not re.search(r"\bINDEX\b", quarantine_ddl, re.IGNORECASE), quarantine_ddl


def test_quarantine_table_is_allowlisted():
    """`initialize_schema` names it through `_t()`, which validates every name.

    An unlisted table does not degrade — `validate_table_name` raises, and schema
    initialisation fails outright. This is the regression 12 existing
    `initialize_schema` tests caught the moment the table was created.
    """
    from iris_vector_graph._engine.schema import EMBEDDING_QUARANTINE_TABLE
    from iris_vector_graph.security import VALID_GRAPH_TABLES

    assert EMBEDDING_QUARANTINE_TABLE in VALID_GRAPH_TABLES


def test_quarantine_reason_is_a_closed_set_in_the_comment(quarantine_ddl):
    """The set is documented next to the column so a report can group by it (FR-030)."""
    for reason in ("ambiguous_graph", "no_node", "resolver_declined"):
        assert reason in quarantine_ddl, reason
