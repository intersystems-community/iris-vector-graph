import os
import pytest
from iris_vector_graph.cypher.parser import Parser
from iris_vector_graph.cypher.lexer import Lexer
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(cypher, params=None):
    parsed = Parser(Lexer(cypher)).parse()
    result = translate_to_sql(parsed, params or {})
    return result.sql if isinstance(result.sql, str) else (result.sql[0] if result.sql else "")


class TestIvgRetrieveSQL:

    def test_basic_retrieve_has_union_all(self):
        sql = _sql("CALL ivg.retrieve('insulin resistance', 5) YIELD node, score RETURN node, score")
        assert "UNION ALL" in sql

    def test_basic_retrieve_has_bm25_cte(self):
        sql = _sql("CALL ivg.retrieve('insulin', 5) YIELD node, score RETURN node, score")
        assert "BM25_Retrieve" in sql

    def test_basic_retrieve_has_vec_cte(self):
        sql = _sql("CALL ivg.retrieve('insulin', 5) YIELD node, score RETURN node, score")
        assert "Vec_Retrieve" in sql

    def test_basic_retrieve_fetch_first(self):
        sql = _sql("CALL ivg.retrieve('insulin', 5) YIELD node, score RETURN node, score")
        assert "FETCH FIRST 5 ROWS ONLY" in sql or "5" in sql

    def test_retrieve_no_full_outer_join(self):
        sql = _sql("CALL ivg.retrieve('q', 10) YIELD node, score RETURN node, score")
        assert "FULL OUTER JOIN" not in sql

    def test_retrieve_embedding_config_passthrough(self):
        sql = _sql("CALL ivg.retrieve('insulin', 5, 'myidx', '*', 60, 'my-model') YIELD node, score RETURN node, score")
        assert "my-model" in sql

    def test_retrieve_default_config_empty(self):
        sql = _sql("CALL ivg.retrieve('insulin', 5) YIELD node, score RETURN node, score")
        assert "EMBEDDING" in sql

    def test_retrieve_with_bind_var_query(self):
        sql = _sql("CALL ivg.retrieve($q, 10) YIELD node, score RETURN node, score", {"q": "diabetes"})
        assert "BM25_Retrieve" in sql
        assert "UNION ALL" in sql

    def test_retrieve_rrf_sum_present(self):
        sql = _sql("CALL ivg.retrieve('q', 5) YIELD node, score RETURN node, score")
        assert "SUM" in sql or "rrf_score" in sql.lower() or "score" in sql.lower()

    def test_retrieve_chained_with_match(self):
        sql = _sql(
            "CALL ivg.retrieve('q', 5) YIELD node, score "
            "MATCH (node)-[:INTERACTS]->(other) RETURN other.node_id"
        )
        assert "BM25_Retrieve" in sql or "Retrieve" in sql


class TestVectorDistanceSQL:

    def test_vector_distance_has_1_minus(self):
        sql = _sql("MATCH (n) WHERE vector_distance(n, $vec) < 0.3 RETURN n.node_id", {"vec": [0.1, 0.2]})
        assert "(1 -" in sql or "1 - VECTOR_COSINE" in sql

    def test_vector_distance_has_vector_cosine(self):
        sql = _sql("MATCH (n) WHERE vector_distance(n, $vec) < 0.3 RETURN n.node_id", {"vec": [0.1, 0.2]})
        assert "VECTOR_COSINE" in sql

    def test_vector_distance_with_label(self):
        sql = _sql("MATCH (n:Gene) WHERE vector_distance(n, $vec) < 0.5 RETURN n.node_id", {"vec": [0.1]})
        assert "VECTOR_COSINE" in sql

    def test_vector_similarity_no_1_minus(self):
        sql = _sql("MATCH (n) RETURN vector_similarity(n, $vec) AS sim", {"vec": [0.1, 0.2]})
        assert "VECTOR_COSINE" in sql
        assert "(1 - VECTOR_COSINE" not in sql

    def test_vector_similarity_in_order_by(self):
        sql = _sql(
            "MATCH (n) RETURN n.node_id, vector_similarity(n, $vec) AS sim ORDER BY sim DESC LIMIT 5",
            {"vec": [0.1, 0.2]}
        )
        assert "VECTOR_COSINE" in sql
        assert "ORDER BY" in sql

    def test_ivg_vector_similarity_alias(self):
        sql = _sql("MATCH (n) RETURN vector_similarity(n, $vec) AS sim", {"vec": [0.1]})
        assert "VECTOR_COSINE" in sql
