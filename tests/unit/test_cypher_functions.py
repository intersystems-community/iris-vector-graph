"""Unit tests for Cypher type coercion functions and COUNT(DISTINCT)."""
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix


class TestCastFunctions:

    @pytest.fixture(autouse=True)
    def setup(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_to_integer_emits_cast_integer(self):
        """T002"""
        q = parse_query("MATCH (n:Gene) WHERE toInteger(n.chromosome) = 7 RETURN n")
        r = translate_to_sql(q)
        assert "CAST(" in r.sql
        assert "AS INTEGER" in r.sql

    def test_to_float_emits_cast_double(self):
        """T003"""
        q = parse_query("MATCH (n) WHERE toFloat(n.score) > 0.5 RETURN n")
        r = translate_to_sql(q)
        assert "CAST(" in r.sql
        assert "AS DOUBLE" in r.sql

    def test_to_string_emits_cast_varchar(self):
        """T004"""
        q = parse_query("MATCH (n) RETURN toString(n.count) AS label")
        r = translate_to_sql(q)
        assert "CAST(" in r.sql
        assert "AS VARCHAR" in r.sql

    def test_to_boolean_lowercase_true(self):
        """T005"""
        q = parse_query("MATCH (n) WHERE toBoolean(n.active) = 1 RETURN n")
        r = translate_to_sql(q)
        assert "LOWER(" in r.sql
        assert "'true'" in r.sql.lower() or "true" in r.sql.lower()

    def test_to_boolean_case_insensitive(self):
        """T006"""
        q = parse_query("MATCH (n) RETURN toBoolean(n.flag) AS b")
        r = translate_to_sql(q)
        assert "LOWER(" in r.sql
        assert "'y'" in r.sql or "yes" in r.sql.lower()


class TestCountDistinct:

    @pytest.fixture(autouse=True)
    def setup(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_count_distinct_emits_correctly(self):
        """T008"""
        q = parse_query("MATCH (p:Patient)-[:HAS_ICD]->(icd) RETURN COUNT(DISTINCT icd.code) AS cnt")
        r = translate_to_sql(q)
        assert "COUNT(DISTINCT" in r.sql
