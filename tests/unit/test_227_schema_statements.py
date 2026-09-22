"""Spec 227 T011 — the constraint-migration statement builder.

`contracts/sql-schema.md` §4 calls its order "not interchangeable", and it means
it: the five dependent foreign keys hold `uq_nodes_nodeid` in place, so a
`DROP CONSTRAINT uq_nodes_nodeid` issued before them fails, and a `graph_id` added
to `rdf_labels` before the unique swap cannot be referenced by the composite key
that follows.

Two IRIS rules are measured rather than assumed (research R1, R6):

- `ALTER COLUMN graph_id NOT NULL` is accepted. Restating the type is `SQLCODE -25`,
  and restating it *without* `%EXACT` would silently drop the column to default
  collation — a re-key that appears to succeed and then matches the wrong rows.
- A composite foreign key names the referenced columns in the referenced
  constraint's own order. `uq_nodes_graph_node` is `(graph_id, node_id)`, so every
  reference reads `REFERENCES ... (graph_id, node_id)`. The other order is
  `SQLCODE -121`.

The builder is a pure function over the statement list so this order can be
asserted without a database.
"""

import re

import pytest

from iris_vector_graph.schema import GraphSchema

DEPENDENT_FKS = (
    "fk_labels_node",
    "fk_edges_source",
    "fk_edges_dest",
    "fk_emb_node",
    "fk_emb_node_opt",
)


@pytest.fixture
def statements():
    return GraphSchema.get_graph_scope_migration_sql()


def _index_of(statements, pattern):
    rx = re.compile(pattern, re.IGNORECASE)
    for i, s in enumerate(statements):
        if rx.search(s):
            return i
    raise AssertionError(
        f"no statement matches {pattern!r}; statements were:\n"
        + "\n".join(f"  {i}: {s}" for i, s in enumerate(statements))
    )


def test_returns_a_nonempty_ordered_list(statements):
    assert isinstance(statements, list)
    assert statements, "the migration cannot be empty"
    assert all(isinstance(s, str) and s.strip() for s in statements)


def test_no_statement_ends_with_a_semicolon(statements):
    """IRIS rejects a trailing `;` on a statement sent through the DB-API."""
    for s in statements:
        assert not s.rstrip().endswith(";"), s


# --- step order -------------------------------------------------------------


@pytest.mark.parametrize("fk", DEPENDENT_FKS)
def test_every_dependent_fk_is_dropped_before_the_unique_swap(statements, fk):
    drop_fk = _index_of(statements, rf"DROP\s+CONSTRAINT\s+{fk}\b")
    drop_uq = _index_of(statements, r"DROP\s+CONSTRAINT\s+uq_nodes_nodeid\b")
    assert drop_fk < drop_uq, (
        f"{fk} still holds uq_nodes_nodeid in place at statement {drop_uq}"
    )


def test_unique_is_dropped_before_it_is_added(statements):
    assert _index_of(statements, r"DROP\s+CONSTRAINT\s+uq_nodes_nodeid") < _index_of(
        statements, r"ADD\s+CONSTRAINT\s+uq_nodes_graph_node"
    )


def test_new_unique_is_graph_then_node(statements):
    i = _index_of(statements, r"ADD\s+CONSTRAINT\s+uq_nodes_graph_node")
    m = re.search(r"UNIQUE\s*\(([^)]*)\)", statements[i], re.IGNORECASE)
    assert m, statements[i]
    cols = [c.strip().lower() for c in m.group(1).split(",")]
    assert cols == ["graph_id", "node_id"], (
        "column order here is the order every composite FK must repeat: " + statements[i]
    )


def test_labels_and_props_gain_graph_id_after_the_unique_swap(statements):
    swap = _index_of(statements, r"ADD\s+CONSTRAINT\s+uq_nodes_graph_node")
    for table in ("rdf_labels", "rdf_props"):
        add = _index_of(statements, rf"ALTER\s+TABLE\s+\S*{table}\s+ADD\s+COLUMN\s+graph_id")
        assert swap < add, f"{table} gained graph_id before nodes was re-keyed"


def test_fks_are_repointed_after_labels_and_props_are_re_keyed(statements):
    last_rekey = max(
        _index_of(statements, rf"ALTER\s+TABLE\s+\S*{t}\s+ADD\s+CONSTRAINT\s+pk_")
        for t in ("rdf_labels", "rdf_props")
    )
    for fk in ("fk_labels_node", "fk_edges_source", "fk_edges_dest"):
        add = _index_of(statements, rf"ADD\s+CONSTRAINT\s+{fk}\b")
        assert last_rekey < add, f"{fk} was re-pointed before the child PK was re-keyed"


def test_the_backfill_only_claims_a_row_whose_node_is_in_exactly_one_graph(statements):
    """FR-036. An unguarded `MIN(n.graph_id)` answers *a* graph for a node ID two
    graphs hold, and the `NOT NULL` that follows then accepts the guess — the silent
    failure spec 227 exists to close. Guarded, the ambiguous row stays NULL and the
    `NOT NULL` refuses the whole re-key, which is what the migration checks for first.
    """
    for table in ("rdf_labels", "rdf_props"):
        backfill = statements[_index_of(statements, rf"UPDATE\s+\S*{table}\b")]
        assert re.search(
            r"COUNT\(\s*DISTINCT\s+n\.graph_id\s*\)", backfill, re.IGNORECASE
        ), f"{table}'s backfill does not count the node's graphs: {backfill}"
        assert re.search(r"\)\s*=\s*1", backfill), (
            f"{table}'s backfill claims a row whose node is not in exactly one "
            f"graph: {backfill}"
        )


# --- the two measured IRIS rules --------------------------------------------


def test_alter_column_never_restates_the_type(statements):
    """`ALTER COLUMN graph_id VARCHAR(256) ... NOT NULL` is SQLCODE -25 (research R6)."""
    for s in statements:
        if re.search(r"ALTER\s+COLUMN\s+graph_id", s, re.IGNORECASE):
            assert not re.search(r"VARCHAR|CHAR\s*\(|%EXACT", s, re.IGNORECASE), (
                "restating the type is SQLCODE -25, and restating it without %EXACT "
                "silently drops the column to default collation: " + s
            )


def test_not_null_is_set_before_the_default(statements):
    """data-model.md §3: ADD COLUMN → backfill → NOT NULL → SET DEFAULT → re-key."""
    for table in ("rdf_labels", "rdf_props"):
        add = _index_of(statements, rf"ALTER\s+TABLE\s+\S*{table}\s+ADD\s+COLUMN\s+graph_id")
        backfill = _index_of(statements, rf"UPDATE\s+\S*{table}\b")
        notnull = _index_of(
            statements, rf"ALTER\s+TABLE\s+\S*{table}\s+ALTER\s+COLUMN\s+graph_id\s+NOT\s+NULL"
        )
        default = _index_of(
            statements, rf"ALTER\s+TABLE\s+\S*{table}\s+ALTER\s+COLUMN\s+graph_id\s+SET\s+DEFAULT"
        )
        assert add < backfill < notnull < default, (
            f"{table}: NOT NULL before the backfill rejects every existing row"
        )


@pytest.mark.parametrize("fk", ("fk_labels_node", "fk_edges_source", "fk_edges_dest"))
def test_composite_fks_name_referenced_columns_in_constraint_order(statements, fk):
    i = _index_of(statements, rf"ADD\s+CONSTRAINT\s+{fk}\b")
    stmt = statements[i]
    m = re.search(r"REFERENCES\s+\S+\s*\(([^)]*)\)", stmt, re.IGNORECASE)
    assert m, stmt
    cols = [c.strip().lower() for c in m.group(1).split(",")]
    assert cols == ["graph_id", "node_id"], (
        "any other order is SQLCODE -121 (research R1): " + stmt
    )
    local = re.search(r"FOREIGN\s+KEY\s*\(([^)]*)\)", stmt, re.IGNORECASE)
    assert local, stmt
    local_cols = [c.strip().lower() for c in local.group(1).split(",")]
    assert local_cols[0] == "graph_id", (
        "the local column list has to line up positionally with the referenced one: "
        + stmt
    )


def test_child_primary_keys_are_graph_first(statements):
    for table, tail in (("rdf_labels", "label"), ("rdf_props", "key")):
        i = _index_of(statements, rf"ALTER\s+TABLE\s+\S*{table}\s+ADD\s+CONSTRAINT\s+pk_")
        m = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", statements[i], re.IGNORECASE)
        assert m, statements[i]
        cols = [c.strip().strip('"').lower() for c in m.group(1).split(",")]
        assert cols == ["graph_id", "s", tail], statements[i]


def test_the_embedding_fks_are_not_repointed_here(statements):
    """Each route declares its own reference with its own table (§2/§4 comment).

    Not re-added by ALTER because the upgrade rebuilds both default-route tables from
    scratch (`_create_embedding_table`) — the reference is in that CREATE TABLE, which
    is what `test_the_default_route_tables_reference_nodes` below pins.
    """
    for fk in ("fk_emb_node", "fk_emb_node_opt"):
        with pytest.raises(AssertionError):
            _index_of(statements, rf"ADD\s+CONSTRAINT\s+{fk}\b")


@pytest.mark.parametrize(
    "table,fk",
    [
        ("kg_NodeEmbeddings", "fk_emb_node"),
        ("kg_NodeEmbeddings_optimized", "fk_emb_node_opt"),
    ],
)
def test_the_default_route_tables_reference_nodes(table, fk):
    """FR-008: a default-route table points at `nodes` the same way a route does.

    Both tables are the default route (contracts/sql-schema.md §2), and every generated
    route declares `fk_{route} FOREIGN KEY (graph_id, node_id)`. Without this the same
    table shape gets referential integrity only when its name is a hash: an embedding
    for a node that does not exist inserts, and a node holding one deletes. That is
    exactly what the 3.2.0 `fk_emb_node` prevented, and `data-model.md:70-71` re-declares
    it composite rather than dropping it.
    """
    ddl = GraphSchema.get_base_schema_sql(embedding_dimension=8)
    m = re.search(
        rf"CREATE\s+TABLE\s+Graph_KG\.{table}\s*\((.*?)\n\);", ddl, re.IGNORECASE | re.DOTALL
    )
    assert m, f"no CREATE TABLE for {table} in the base schema"
    body = m.group(1)
    assert re.search(
        rf"CONSTRAINT\s+{fk}\s+FOREIGN\s+KEY\s*\(\s*graph_id\s*,\s*node_id\s*\)\s+"
        r"REFERENCES\s+Graph_KG\.nodes\s*\(\s*graph_id\s*,\s*node_id\s*\)",
        body,
        re.IGNORECASE,
    ), body
