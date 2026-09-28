"""`score_threshold` on the two cosine searches must produce SQL IRIS will prepare.

Both `edge_vector_search` and the generic `vector_search` appended
`HAVING score >= <float>` after `ORDER BY score DESC`. IRIS refuses that at
Prepare with `SQLCODE -25` ("Input (HAVING) encountered after end of query"), so
every call with a threshold failed. The unit test that covered the branch mocked
the cursor and only checked that a list came back, and the integration test
caught the error text and skipped.

The threshold now filters in `WHERE` on the cosine expression, and the value is a
bind parameter rather than text spliced into the statement.
"""

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.description = [("id",), ("score",)]
    conn.cursor.return_value = cursor
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    return eng, cursor


def _last_sql(cursor):
    sql, params = cursor.execute.call_args[0]
    return " ".join(sql.split()), list(params)


def _edge_search(eng, **kw):
    with patch.object(
        IRISGraphEngine, "_route_for_read", return_value=("kg_EdgeEmbeddings", None)
    ):
        return eng.edge_vector_search([0.1, 0.2, 0.3, 0.4], top_k=5, **kw)


class TestEdgeVectorSearchThreshold:
    def test_no_having_clause(self):
        eng, cur = _engine()
        _edge_search(eng, score_threshold=0.5)
        sql, _ = _last_sql(cur)
        assert "HAVING" not in sql.upper()

    def test_nothing_follows_order_by(self):
        eng, cur = _engine()
        _edge_search(eng, score_threshold=0.5)
        sql, _ = _last_sql(cur)
        assert sql.rstrip().upper().endswith("ORDER BY SCORE DESC")

    def test_threshold_is_in_where_and_bound(self):
        eng, cur = _engine()
        _edge_search(eng, score_threshold=0.5)
        sql, params = _last_sql(cur)
        where = sql.upper().split(" WHERE ", 1)[1].split(" ORDER BY ")[0]
        assert "VECTOR_COSINE" in where and ">= ?" in where
        assert "0.5" not in sql
        assert params.count(0.5) == 1
        assert sql.count("?") == len(params)

    def test_no_threshold_leaves_statement_unchanged(self):
        eng, cur = _engine()
        _edge_search(eng)
        sql, params = _last_sql(cur)
        assert "VECTOR_COSINE" not in sql.upper().split(" WHERE ", 1)[1]
        assert sql.count("?") == len(params)

    def test_non_numeric_threshold_is_refused(self):
        eng, _ = _engine()
        with pytest.raises((TypeError, ValueError)):
            _edge_search(eng, score_threshold="0.5) OR (1=1")


class TestVectorSearchThreshold:
    def _search(self, eng, **kw):
        return eng.vector_search(
            "Graph_KG.kg_NodeEmbeddings", "emb", [0.1, 0.2, 0.3, 0.4], top_k=5, **kw
        )

    def test_no_having_clause(self):
        eng, cur = _engine()
        self._search(eng, score_threshold=0.25)
        sql, _ = _last_sql(cur)
        assert "HAVING" not in sql.upper()
        assert sql.rstrip().upper().endswith("ORDER BY SCORE DESC")

    def test_threshold_bound_without_graph(self):
        eng, cur = _engine()
        self._search(eng, score_threshold=0.25)
        sql, params = _last_sql(cur)
        assert " WHERE " in sql.upper() and ">= ?" in sql
        assert "0.25" not in sql
        assert sql.count("?") == len(params)

    def test_threshold_combines_with_graph(self):
        eng, cur = _engine()
        self._search(eng, score_threshold=0.25, graph="g1")
        sql, params = _last_sql(cur)
        assert sql.upper().count(" WHERE ") == 1
        assert " AND " in sql.upper()
        assert "g1" in params and 0.25 in params
        assert sql.count("?") == len(params)

    def test_non_numeric_threshold_is_refused(self):
        eng, _ = _engine()
        with pytest.raises((TypeError, ValueError)):
            self._search(eng, score_threshold="1 OR 1=1")
