"""`get_embedding_dimension(cursor, table_name)` must answer about the table it is asked about.

Guards a live defect reported 2026-09-18 against 3.0.1 and 2.5.1: the method
accepted ``table_name`` and ignored it. The dictionary query hardcoded
``Parent = 'Graph.KG.kgNodeEmbeddings'``, so every call returned the *node*
column's width — including calls about ``Graph_KG.kg_EdgeEmbeddings`` and calls
about tables that cannot exist. The ``INFORMATION_SCHEMA`` fallback was the only
code that parsed ``table_name``, and it is unreachable whenever the dictionary
query succeeds, which is the normal case.

The consumer that hit this lost 1,099 embedding writes an hour for weeks while
every diagnostic ivg offers reported the schema as healthy: the node column was
384 and correct, the edge column was 768 and rejecting every row, and
``get_embedding_dimension(cur, "Graph_KG.kg_EdgeEmbeddings")`` confidently said 384.

The SQL table name is not the class name (``Graph_KG.kg_NodeEmbeddings`` is class
``Graph.KG.kgNodeEmbeddings``), and ``kg_EdgeEmbeddings`` has no hand-written
``.cls`` at all — it is created by DDL, so IRIS generates its class name. So the
class is resolved from the dictionary catalog first and only derived by rule as a
fallback.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.schema import GraphSchema

CATALOG_MARKER = "%Dictionary.CompiledClass"
PROPERTY_MARKER = "%Dictionary.CompiledProperty"
INFO_SCHEMA_MARKER = "INFORMATION_SCHEMA.COLUMNS"


class FakeCursor:
    """Records every statement and answers from a table -> (class, dim) map."""

    def __init__(self, catalog=None, dims=None, catalog_raises=False):
        # catalog: {(schema, table): class_name}
        self.catalog = catalog or {}
        # dims: {class_name: dimension}
        self.dims = dims or {}
        self.catalog_raises = catalog_raises
        self.statements = []
        self._result = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if CATALOG_MARKER in sql:
            if self.catalog_raises:
                raise RuntimeError("SQLCODE -99 no privilege on %Dictionary.CompiledClass")
            self._result = [
                [cls]
                for (schema, table), cls in self.catalog.items()
                if f"'{schema}'" in sql and f"'{table}'" in sql
            ]
        elif PROPERTY_MARKER in sql:
            self._result = [
                [f"MAXLEN,,DATATYPE,%Library.Double,LEN,{dim}"]
                for cls, dim in self.dims.items()
                if f"'{cls}'" in sql
            ]
        elif INFO_SCHEMA_MARKER in sql:
            self._result = []
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def statements_naming(self, needle):
        return [s for s in self.statements if needle in s]


NODE_CATALOG = {
    ("Graph_KG", "kg_NodeEmbeddings"): "Graph.KG.kgNodeEmbeddings",
    ("Graph_KG", "kg_EdgeEmbeddings"): "Graph_KG.kgEdgeEmbeddings",
}
NODE_DIMS = {
    "Graph.KG.kgNodeEmbeddings": 384,
    "Graph_KG.kgEdgeEmbeddings": 768,
}


def test_edge_table_reports_the_edge_width_not_the_node_width():
    """The reproduction from the bug report, as a test.

    Node column at 384, edge column at 768. Asking about the edge table used to
    return 384 — the node width — which is what made the lost writes invisible.
    """
    cur = FakeCursor(catalog=NODE_CATALOG, dims=NODE_DIMS)

    assert GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_EdgeEmbeddings") == 768
    assert GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NodeEmbeddings") == 384


def test_default_argument_still_answers_about_the_node_table():
    """Every existing caller passes no table_name and means the node table."""
    cur = FakeCursor(catalog=NODE_CATALOG, dims=NODE_DIMS)

    assert GraphSchema.get_embedding_dimension(cur) == 384


def test_a_table_that_cannot_exist_returns_none():
    """A table with no class behind it has no width. It must not inherit one."""
    cur = FakeCursor(catalog=NODE_CATALOG, dims=NODE_DIMS)

    assert GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NoSuchTable_at_all") is None


def test_the_dictionary_query_names_the_class_behind_the_requested_table():
    """Not just the return value — the query itself must stop naming the node class."""
    cur = FakeCursor(catalog=NODE_CATALOG, dims=NODE_DIMS)

    GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_EdgeEmbeddings")

    prop_queries = cur.statements_naming(PROPERTY_MARKER)
    assert prop_queries, "no %Dictionary.CompiledProperty query was issued"
    joined = "\n".join(prop_queries)
    assert "Graph_KG.kgEdgeEmbeddings" in joined
    assert "Graph.KG.kgNodeEmbeddings" not in joined


def test_class_is_resolved_from_the_catalog_not_guessed_from_the_table_name():
    """`kg_EdgeEmbeddings` is DDL-created, so its class name is IRIS's to choose.

    Here the catalog says the class is `Graph_KG.kgEdgeEmbeddings` while the
    derivation rule would guess `Graph.KG.kgEdgeEmbeddings`. The catalog wins.
    """
    cur = FakeCursor(catalog=NODE_CATALOG, dims=NODE_DIMS)

    GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_EdgeEmbeddings")

    catalog_queries = cur.statements_naming(CATALOG_MARKER)
    assert catalog_queries, "the class name was guessed without asking the catalog"
    assert "'kg_EdgeEmbeddings'" in catalog_queries[0]
    assert "'Graph_KG'" in catalog_queries[0]


def test_derivation_rule_is_the_fallback_when_the_catalog_cannot_be_read():
    """A namespace that denies %Dictionary.CompiledClass still gets an answer.

    Falls back to the documented rule: schema underscores become dots, table
    underscores are dropped. `Graph_KG.kg_NodeEmbeddings` -> `Graph.KG.kgNodeEmbeddings`.
    """
    cur = FakeCursor(
        catalog=NODE_CATALOG,
        dims={"Graph.KG.kgNodeEmbeddings": 384},
        catalog_raises=True,
    )

    assert GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NodeEmbeddings") == 384


@pytest.mark.parametrize(
    "table_name,expected",
    [
        ("Graph_KG.kg_NodeEmbeddings", "Graph.KG.kgNodeEmbeddings"),
        ("Graph_KG.kg_NodeEmbeddings_optimized", "Graph.KG.kgNodeEmbeddingsoptimized"),
        ("Graph_KG.kg_EdgeEmbeddings", "Graph.KG.kgEdgeEmbeddings"),
        ("Graph_KG.nodes", "Graph.KG.nodes"),
        ("kg_NodeEmbeddings", "Graph.KG.kgNodeEmbeddings"),
    ],
)
def test_derivation_rule_matches_the_classes_on_disk(table_name, expected):
    """The two hand-written classes in `iris_src/src/Graph/KG/` pin this rule:
    `kgNodeEmbeddings.cls` has `SqlTableName = kg_NodeEmbeddings`, and
    `kgNodeEmbeddingsoptimized.cls` has `SqlTableName = kg_NodeEmbeddings_optimized`.
    An unqualified name means the Graph_KG schema.

    `kg_EdgeEmbeddings` has no `.cls` on disk — it is created by the DDL in
    `get_base_schema_sql`, so IRIS picks its class name. Read from
    `%Dictionary.CompiledClass` on `ivg-iris-enterprise` 2026-09-18, IRIS chose
    `Graph.KG.kgEdgeEmbeddings`, which is what this rule derives. That agreement
    is convenient, not guaranteed: `resolve_table_class` asks the catalog first
    precisely because the name is IRIS's to choose, and this rule is only the
    fallback for when the catalog cannot be read.
    """
    assert GraphSchema.derive_class_name(table_name) == expected


def test_resolve_table_class_returns_none_for_an_unknown_table():
    cur = FakeCursor(catalog=NODE_CATALOG, dims=NODE_DIMS)

    assert GraphSchema.resolve_table_class(cur, "Graph_KG.kg_NoSuchTable") is None


def test_a_cursor_that_raises_on_everything_yields_none_not_a_crash():
    cur = MagicMock()
    cur.execute.side_effect = RuntimeError("connection reset")

    assert GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_EdgeEmbeddings") is None


def test_a_table_name_with_injection_characters_is_rejected_quietly():
    """`table_name` reaches a literal-interpolated catalog query, so it is sanitized."""
    cur = FakeCursor(catalog=NODE_CATALOG, dims=NODE_DIMS)

    assert GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg'; DROP TABLE x--") is None
    assert not cur.statements_naming("DROP TABLE")
