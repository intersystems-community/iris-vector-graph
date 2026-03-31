"""Unit and e2e tests for Cypher CASE WHEN expression support."""
import os
import uuid
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix


class TestCaseWhenUnit:

    @pytest.fixture(autouse=True)
    def setup(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_searched_case_in_return(self):
        q = parse_query("MATCH (n) RETURN CASE WHEN n.score > 0.9 THEN 'high' ELSE 'low' END AS tier")
        r = translate_to_sql(q)
        assert "CASE WHEN" in r.sql
        assert "THEN" in r.sql
        assert "ELSE" in r.sql
        assert "END" in r.sql

    def test_simple_case_equality(self):
        q = parse_query("MATCH (n) RETURN CASE n.type WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 0 END AS score")
        r = translate_to_sql(q)
        assert "CASE" in r.sql
        assert "WHEN" in r.sql
        assert "END" in r.sql

    def test_case_without_else(self):
        q = parse_query("MATCH (n) RETURN CASE WHEN n.active = 1 THEN 'yes' END AS result")
        r = translate_to_sql(q)
        assert "CASE WHEN" in r.sql
        assert "END" in r.sql

    def test_case_in_where_clause(self):
        q = parse_query("MATCH (n) WHERE CASE WHEN n.type = 'A' THEN n.score ELSE 0 END > 0.5 RETURN n")
        r = translate_to_sql(q)
        assert "CASE WHEN" in r.sql

    def test_case_in_order_by(self):
        q = parse_query("MATCH (n) RETURN n ORDER BY CASE WHEN n.priority = 'high' THEN 1 ELSE 2 END")
        r = translate_to_sql(q)
        assert "CASE WHEN" in r.sql


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestCaseWhenE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        yield

    def test_case_sql_executes_without_error(self):
        result = self.engine.execute_cypher(
            "MATCH (n) RETURN CASE WHEN 1 = 1 THEN 'yes' ELSE 'no' END AS tag LIMIT 1"
        )
        assert "rows" in result
