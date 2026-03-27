"""Integration tests for CALL { ... } subquery SQL translation (Principle IV)."""
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix


class TestSubqueryCallSQLTranslation:

    def setup_method(self):
        set_schema_prefix("Graph_KG")

    def teardown_method(self):
        set_schema_prefix("")

    def test_independent_subquery_produces_cte_sql(self):
        """T023"""
        q = parse_query("CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name")
        result = translate_to_sql(q)
        sql = result.sql
        assert "SubQuery" in sql
        assert "AS" in sql
        assert "Graph_KG.nodes" in sql
        assert "name" in sql

    def test_correlated_subquery_produces_coalesce_scalar(self):
        """T024"""
        q = parse_query(
            "MATCH (p:Protein) "
            "CALL { WITH p MATCH (p)-[:INTERACTS_WITH]->(q) RETURN count(q) AS deg } "
            "RETURN p.id, deg"
        )
        result = translate_to_sql(q)
        sql = result.sql
        assert "COALESCE" in sql
        assert "deg" in sql

    def test_scope_violation_import_nonexistent_var_raises_error(self):
        """T024a"""
        q = parse_query(
            "CALL { WITH x MATCH (x)-[]->(y) RETURN count(y) AS cnt } RETURN cnt"
        )
        with pytest.raises(ValueError, match="not defined"):
            translate_to_sql(q)
