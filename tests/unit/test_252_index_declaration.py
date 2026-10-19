"""Spec 252, T011: ensure_indexes declares the two adjacency functional indexes.

Only where Graph.KG.AdjIndex and Graph.KG.LabelIndex are compiled (a DDL-only namespace has no
callbacks to run), only once the ekey column exists (the edge index reads it), only when the index is
not there yet, and with a warning, because CREATE INDEX builds it over every existing row.
"""

import logging

from iris_vector_graph.schema import GraphSchema

EDGE_DDL = "CREATE INDEX idx_edges_adj ON Graph_KG.rdf_edges (graph_id, s, p, o_id, qualifiers, ekey) AS Graph.KG.AdjIndex"
LABEL_DDL = "CREATE INDEX idx_labels_adj ON Graph_KG.rdf_labels (graph_id, s, label) AS Graph.KG.LabelIndex"


class _Cursor:
    def __init__(self, classes=2, ekey=True, present=()):
        self.classes, self.ekey, self.present, self.sql, self._row = classes, ekey, set(present), [], None

    def execute(self, sql, args=None):
        self.sql.append(sql)
        if "%Dictionary.CompiledClass" in sql:
            self._row = (self.classes,)
        elif "COLUMN_NAME = 'ekey'" in sql:
            self._row = (1,) if self.ekey else None
        elif "INFORMATION_SCHEMA.INDEXES" in sql:
            name = args[0] if args else ""
            self._row = (1,) if name in self.present else None
        elif "backfill_temporal_rows" in sql:
            self._row = (3,)

    def fetchone(self):
        return self._row

    def ddl(self):
        return [s for s in self.sql if s.startswith("CREATE INDEX")]


def test_both_indexes_are_declared_on_a_deployed_namespace(caplog):
    cur = _Cursor()
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
        status = GraphSchema.ensure_adjacency_index(cur)
    assert cur.ddl() == [EDGE_DDL, LABEL_DDL]
    assert status == {"idx_edges_adj": "created", "idx_labels_adj": "created", "temporal_rows_backfilled": 3}
    assert any("rebuild" in r.getMessage().lower() for r in caplog.records)


def test_nothing_is_declared_without_the_callback_classes():
    cur = _Cursor(classes=1)
    assert GraphSchema.ensure_adjacency_index(cur) == {"skipped": "Graph.KG.AdjIndex / LabelIndex not compiled"}
    assert cur.ddl() == []


def test_nothing_is_declared_before_the_ekey_column_exists():
    cur = _Cursor(ekey=False)
    assert "skipped" in GraphSchema.ensure_adjacency_index(cur)
    assert cur.ddl() == []


def test_a_declared_index_is_left_alone(caplog):
    cur = _Cursor(present={"idx_edges_adj", "idx_labels_adj"})
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
        status = GraphSchema.ensure_adjacency_index(cur)
    assert cur.ddl() == []
    assert status == {"idx_edges_adj": "present", "idx_labels_adj": "present"}
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_ensure_indexes_runs_it_after_the_ekey_migration():
    import inspect

    src = inspect.getsource(GraphSchema.ensure_indexes)
    assert src.index("_spo_constraint_status") < src.index("ensure_adjacency_index")


def test_temporal_rows_are_backfilled_before_the_edge_index_is_built():
    # ADR-0009: CREATE INDEX builds ^KG from rows, so a pre-ADR-0009 temporal edge needs its row first
    cur = _Cursor()
    status = GraphSchema.ensure_adjacency_index(cur)
    backfill = [i for i, s in enumerate(cur.sql) if "backfill_temporal_rows" in s]
    edge = [i for i, s in enumerate(cur.sql) if s == EDGE_DDL]
    assert backfill and edge and backfill[0] < edge[0]
    assert status["temporal_rows_backfilled"] == 3


def test_no_backfill_once_the_edge_index_exists():
    cur = _Cursor(present={"idx_edges_adj", "idx_labels_adj"})
    GraphSchema.ensure_adjacency_index(cur)
    assert not [s for s in cur.sql if "backfill_temporal_rows" in s]
