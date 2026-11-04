"""R223: a vector query of the wrong width raises ``ValueError`` naming both widths; it never answers ``[]``.

Walkthrough 2 (W14): a 3-wide query against 4-wide vectors logged "Server-side kg_KNN_VEC failed: ... SQLCODE <-257>
Cannot perform vector operation on vectors of different lengths ... Falling back to client-side logic" and answered
``[]``, which reads as "no neighbours". ``_assert_query_width`` refuses the query only when the embedding registry's
route records a width; the walkthrough's route recorded none (a registry row adopted without a declared width, or a
registry this database cannot read), so the query reached IRIS, and the fallback chain swallowed IRIS's refusal.

Now the check reads the column's declared width from the catalog when the route has none, and a ``-257`` that still
reaches the server path raises (with the stored vectors' width) instead of falling back.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

_MISMATCH = (
    "[SQLCODE: <-257>:<Cannot perform vector operation on vectors of different lengths>] [Location: <ServerLoop>]"
)


def _engine(route_dimension=None, catalog_width=None, knn_error=None, stored_row="1,0,0,0"):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = conn.cursor.return_value
    executed = []

    def execute(sql, params=None):
        executed.append(sql)
        if "VECTOR_COSINE" in sql and knn_error is not None:
            raise Exception(knn_error)
        cur.fetchone.return_value = (stored_row,) if "SELECT TOP 1 emb" in sql else None
        cur.fetchall.return_value = []

    cur.execute.side_effect = execute
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng.vector_dtype = "DOUBLE"
    route = None
    if route_dimension is not None or route_dimension is None:
        route = MagicMock(dimension=route_dimension, dtype="DOUBLE", table_name="kg_NodeEmbeddings")
    eng._route_for_read = MagicMock(return_value=("kg_NodeEmbeddings", route))
    eng._kg_KNN_VEC_python_optimized = MagicMock(return_value=[])
    return eng, executed, catalog_width


def test_a_route_without_a_width_is_checked_against_the_catalog():
    eng, executed, _ = _engine(route_dimension=None)
    with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=4):
        with pytest.raises(ValueError) as exc:
            eng.kg_KNN_VEC(json.dumps([1.0, 0.0, 0.0]), k=2)
    msg = str(exc.value)
    assert "3 dimensions" in msg and "4-dimensional" in msg
    assert not any("VECTOR_COSINE" in s for s in executed)
    eng._kg_KNN_VEC_python_optimized.assert_not_called()


def test_iris_refusing_the_width_raises_and_names_both_widths():
    eng, executed, _ = _engine(route_dimension=None, knn_error=_MISMATCH)
    with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=None):
        with pytest.raises(ValueError) as exc:
            eng.kg_KNN_VEC(json.dumps([1.0, 0.0, 0.0]), k=2)
    msg = str(exc.value)
    assert "3 dimensions" in msg and "4-dimensional" in msg and "-257" in msg
    eng._kg_KNN_VEC_python_optimized.assert_not_called()


def test_another_server_failure_still_takes_the_fallback():
    eng, _, _ = _engine(route_dimension=4, knn_error="[SQLCODE: <-359>:<User defined function not found>]")
    assert eng.kg_KNN_VEC(json.dumps([1.0, 0.0, 0.0, 0.0]), k=2) == []
    eng._kg_KNN_VEC_python_optimized.assert_called_once()


def test_the_right_width_runs_the_query():
    eng, executed, _ = _engine(route_dimension=None)
    with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=4):
        assert eng.kg_KNN_VEC(json.dumps([1.0, 0.0, 0.0, 0.0]), k=2) == []
    assert any("VECTOR_COSINE" in s for s in executed)


def test_search_nodes_by_vector_raises_too():
    eng, _, _ = _engine(route_dimension=None, knn_error=_MISMATCH)
    eng._probe_native_vec = MagicMock(return_value=True)
    with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=None):
        with pytest.raises(ValueError, match="3 dimensions"):
            eng.search_nodes_by_vector([1.0, 0.0, 0.0], k=2)
