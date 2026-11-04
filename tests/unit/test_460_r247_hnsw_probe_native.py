"""4.6.0 R247: ``status()`` for ADMIN_GUIDE §3's reading role reads the vector tables' HNSW state.

``GraphSchema.hnsw_indexes`` reads ``%Dictionary.CompiledClass`` / ``CompiledIndex`` in SQL, which these roles may not
(SQLCODE -99), so ``status()`` listed "class lookup failed for Graph_KG.kg_NodeEmbeddings" (and ``_optimized``) and
could not say whether an index was built. Refused, and given a native handle, it now asks
``Graph.KG.Meta.HnswIndexes(schema, table)``, a class method the role may run (``%Native_ClassExecution``) that reads
the dictionary as objects. Live: tests/e2e/test_460_r247_reader_hnsw_status_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.schema import GraphSchema

REFUSED = RuntimeError("[SQLCODE: <-99>:<Privilege violation>] [%msg: <User R is not privileged for the operation>]")


def _refusing_cursor():
    cursor = MagicMock()
    cursor.execute.side_effect = REFUSED
    return cursor


def test_a_refused_dictionary_read_asks_the_server():
    nat = MagicMock()
    nat.classMethodValue.return_value = '[{"name":"idx_f29_ann","properties":"emb"}]'
    errors = []
    found = GraphSchema.hnsw_indexes(_refusing_cursor(), "RAG.kg_NodeEmbeddings_optimized", errors, native=nat)
    assert found == [("idx_f29_ann", "emb")]
    assert errors == []
    nat.classMethodValue.assert_called_once_with("Graph.KG.Meta", "HnswIndexes", "RAG", "kg_NodeEmbeddings_optimized")


def test_no_index_is_an_empty_list():
    nat = MagicMock()
    nat.classMethodValue.return_value = "[]"
    errors = []
    assert GraphSchema.hnsw_indexes(_refusing_cursor(), "Graph_KG.kg_NodeEmbeddings", errors, native=nat) == []
    assert errors == []


def test_a_failing_probe_is_reported():
    nat = MagicMock()
    nat.classMethodValue.side_effect = RuntimeError("<METHOD DOES NOT EXIST>")
    errors = []
    assert GraphSchema.hnsw_indexes(_refusing_cursor(), "Graph_KG.kg_NodeEmbeddings", errors, native=nat) == []
    assert len(errors) == 1 and "HNSW index read failed for Graph_KG.kg_NodeEmbeddings" in errors[0], errors


@pytest.mark.parametrize("native", [None, "given"])
def test_without_a_refusal_the_sql_read_answers(native):
    cursor = MagicMock()
    cursor.fetchone.return_value = ("Graph.KG.kgNodeEmbeddings",)
    cursor.fetchall.return_value = [("idx_ann", "emb")]
    nat = MagicMock() if native else None
    assert GraphSchema.hnsw_indexes(cursor, "Graph_KG.kg_NodeEmbeddings", [], native=nat) == [("idx_ann", "emb")]
    if nat is not None:
        nat.classMethodValue.assert_not_called()


def test_without_a_native_handle_the_refusal_is_still_reported():
    errors = []
    assert GraphSchema.hnsw_indexes(_refusing_cursor(), "Graph_KG.kg_NodeEmbeddings", errors) == []
    assert errors and errors[0].startswith("class lookup failed for Graph_KG.kg_NodeEmbeddings")
