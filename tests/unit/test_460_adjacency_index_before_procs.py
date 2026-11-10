"""4.6.0 R126: `ensure_adjacency_index` does not try to declare the edge index before the IVG Schema's procedures exist.

Its first step for the edge index is `SELECT <schema>.backfill_temporal_rows()`, a procedure of the schema class
`<schema>.SqlProcs`. On a 4.5.x namespace upgraded to a non-default schema (RAG), setup's first call (in
`ensure_indexes`, before the deploy) found 4.5.x's `Graph.KG.AdjIndex`/`LabelIndex` compiled and no RAG.SqlProcs,
called the procedure, and logged "adjacency functional index not declared" with SQLCODE -359; the call after the
deploy then declared both indexes. It now skips, saying why, until that class is compiled.

The cursor here is the SchemaCursor setup uses, over a stand-in that answers the catalog probes: so the class it asks
for is the one the rewrite names for RAG.
"""

from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.schema_name import SchemaCursor


class _Catalog:
    """Answers the probes `ensure_adjacency_index` sends; `compiled` is the set of compiled class names."""

    def __init__(self, compiled):
        self.compiled, self.sql, self._row = set(compiled), [], None

    def execute(self, sql, args=None):
        self.sql.append(sql)
        if "%Dictionary.CompiledIndex" in sql:
            self._row = None  # the index kind probe (4.7.0): no index of the name
        elif "%Dictionary.CompiledClass" in sql:
            names = [n for n in ("Graph.KG.AdjIndex", "Graph.KG.LabelIndex", "RAG.SqlProcs", "Graph.KG.SqlProcs")
                     if f"'{n}'" in sql]
            self._row = (sum(1 for n in names if n in self.compiled),)
        elif "COLUMN_NAME = 'ekey'" in sql:
            self._row = (1,)
        elif "COLUMN_NAME = 'graph_id'" in sql:
            self._row = (2,)
        elif "INFORMATION_SCHEMA.INDEXES" in sql:
            self._row = None
        elif "backfill_temporal_rows" in sql:
            schema = sql.split("SELECT ", 1)[1].split(".", 1)[0]
            if ("RAG.SqlProcs" if schema == "RAG" else "Graph.KG.SqlProcs") not in self.compiled:
                raise RuntimeError(f"[SQLCODE: <-359>] User defined SQL function '{schema.upper()}."
                                   "BACKFILL_TEMPORAL_ROWS' does not exist")
            self._row = (0,)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


CODE = {"Graph.KG.AdjIndex", "Graph.KG.LabelIndex"}


def test_before_the_schema_procedures_are_compiled_it_skips_and_calls_nothing():
    catalog = _Catalog(CODE)
    status = GraphSchema.ensure_adjacency_index(SchemaCursor(catalog, "RAG"))
    assert status == {"skipped": "RAG.SqlProcs (the IVG Schema's procedures) not compiled yet"}
    assert not [s for s in catalog.sql if "backfill_temporal_rows" in s or s.startswith("CREATE INDEX")]


def test_once_they_are_it_backfills_and_declares_both_indexes():
    catalog = _Catalog(CODE | {"RAG.SqlProcs"})
    status = GraphSchema.ensure_adjacency_index(SchemaCursor(catalog, "RAG"))
    assert status == {"temporal_rows_backfilled": 0, "idx_edges_adj": "created", "idx_labels_adj": "created"}
    assert [s for s in catalog.sql if s.startswith("CREATE INDEX")] == [
        "CREATE INDEX idx_edges_adj ON RAG.rdf_edges (graph_id, s, p, o_id, qualifiers, ekey) AS Graph.KG.AdjIndex",
        "CREATE INDEX idx_labels_adj ON RAG.rdf_labels (graph_id, s, label) AS Graph.KG.LabelIndex",
    ]


def test_the_default_schema_asks_for_its_own_class():
    catalog = _Catalog(CODE | {"Graph.KG.SqlProcs"})
    status = GraphSchema.ensure_adjacency_index(catalog)
    assert status["idx_edges_adj"] == "created"
