"""4.6.0 R243: the GraphSchema helpers setup and the bulk loaders run name the engine's IVG Schema, not ``Graph_KG``.

``bootstrap_kg_global`` counted ``Graph_KG.rdf_edges``; ``disable_indexes`` dropped its seven indexes by bare name
(which in a RAG namespace finds RAG's) and ``rebuild_indexes`` recreated them on ``Graph_KG.*``, which does not exist
there, so a RAG bulk load of 500 nodes or more left ``RAG.rdf_labels`` and ``RAG.rdf_props`` with none of them (measured
on 2026.3: seven before, none after). ``tune_tables`` tuned ``Graph_KG`` after a large ``bulk_load_session``.

The statements a plain cursor receives are the schema's: a cursor given to a helper outside setup does not rewrite.
Live: tests/e2e/test_460_r243_bootstrap_alt_schema_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from iris_vector_graph.schema import GraphSchema

BULK_INDEXES = {
    "idx_labels_s": "rdf_labels", "idx_labels_label": "rdf_labels", "idx_labels_s_label": "rdf_labels",
    "idx_props_s": "rdf_props", "idx_props_key": "rdf_props", "idx_props_s_key": "rdf_props",
    "idx_props_key_valkey": "rdf_props",
}


def _sql(cursor) -> list:
    return [c.args[0] for c in cursor.execute.call_args_list]


def test_bootstrap_counts_the_schemas_edges():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(2,)]
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=[None, 1, 1, 1]):
        assert GraphSchema.bootstrap_kg_global(cursor, conn=object(), schema="RAG") is True
    assert _sql(cursor) == ["SELECT COUNT(*) FROM RAG.rdf_edges"]


def test_bootstrap_defaults_to_graph_kg():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(0,)]
    with patch("iris_vector_graph.schema._call_classmethod", return_value=None):
        assert GraphSchema.bootstrap_kg_global(cursor) is False
    assert _sql(cursor) == ["SELECT COUNT(*) FROM Graph_KG.rdf_edges"]


def test_disable_indexes_drops_each_index_on_the_schemas_table():
    cursor = MagicMock()
    status = GraphSchema.disable_indexes(cursor, schema="RAG")
    assert status == {name: True for name in BULK_INDEXES}
    assert _sql(cursor) == [f"DROP INDEX {name} ON RAG.{table}" for name, table in BULK_INDEXES.items()]


def test_rebuild_indexes_creates_them_on_the_schemas_tables():
    cursor = MagicMock()
    with patch.object(GraphSchema, "_props_has_value_key_column", return_value=True) as probe:
        status = GraphSchema.rebuild_indexes(cursor, schema="RAG")
    assert status == {name: True for name in BULK_INDEXES}
    sql = _sql(cursor)
    assert len(sql) == len(BULK_INDEXES)
    for (name, table), stmt in zip(BULK_INDEXES.items(), sql):
        assert stmt.startswith(f"CREATE INDEX {name} ON RAG.{table} ("), stmt
    assert "Graph_KG" not in " ".join(sql)
    # the value-key probe reads the same schema's rdf_props
    assert probe.call_args.kwargs.get("schema") == "RAG"


def test_props_value_key_probe_reads_the_schema():
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    assert GraphSchema._props_has_value_key_column(cursor, schema="RAG") is True
    assert "'RAG'" in _sql(cursor)[0] and "'Graph_KG'" not in _sql(cursor)[0]


def _rag_engine():
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.__dict__["_schema_prefix"] = "RAG"
    return engine


def test_dimension_fallback_reads_the_schemas_node_vectors():
    engine = _rag_engine()
    engine.embedding_dimension = None
    engine.__dict__["_conn"] = MagicMock()
    with patch.object(GraphSchema, "get_embedding_dimension", return_value=8) as get:
        assert engine._get_embedding_dimension() == 8
    assert get.call_args.args[1] == "RAG.kg_NodeEmbeddings"
