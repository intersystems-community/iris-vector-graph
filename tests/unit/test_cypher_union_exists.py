"""Unit tests for Cypher UNION/UNION ALL and EXISTS {} pattern predicate."""
import os
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class TestUnion:

    @pytest.fixture(autouse=True)
    def setup(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_union_parses_two_branches(self):
        q = parse_query("MATCH (g:Gene) RETURN g.name AS n UNION MATCH (d:Drug) RETURN d.name AS n")
        assert hasattr(q, "union_queries") and len(q.union_queries) == 1

    def test_union_all_preserves_all_flag(self):
        q = parse_query("MATCH (g:Gene) RETURN g.name UNION ALL MATCH (d:Drug) RETURN d.name")
        assert q.union_queries[0]["all"] is True

    def test_union_generates_sql_union(self):
        q = parse_query("MATCH (g:Gene) RETURN g.name AS n UNION MATCH (d:Drug) RETURN d.name AS n")
        r = translate_to_sql(q)
        assert " UNION " in r.sql
        assert "UNION ALL" not in r.sql

    def test_union_all_generates_sql_union_all(self):
        q = parse_query("MATCH (g:Gene) RETURN g.name AS n UNION ALL MATCH (d:Drug) RETURN d.name AS n")
        r = translate_to_sql(q)
        assert "UNION ALL" in r.sql

    def test_three_way_union(self):
        q = parse_query("MATCH (g:Gene) RETURN g.name AS n UNION MATCH (d:Drug) RETURN d.name AS n UNION MATCH (p:Protein) RETURN p.name AS n")
        r = translate_to_sql(q)
        assert r.sql.count("UNION") == 2


class TestExistsPattern:

    @pytest.fixture(autouse=True)
    def setup(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_exists_pattern_emits_sql_exists(self):
        q = parse_query("MATCH (g:Gene) WHERE EXISTS { (g)-[:TARGETED_BY]->(:Drug) } RETURN g.name")
        r = translate_to_sql(q)
        assert "EXISTS (" in r.sql or "EXISTS(" in r.sql

    def test_exists_negated_not_exists(self):
        q = parse_query("MATCH (g:Gene) WHERE NOT EXISTS { (g)-[:TARGETED_BY]->(:Drug) } RETURN g.name")
        r = translate_to_sql(q)
        assert "NOT EXISTS" in r.sql


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestUnionE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)

    def test_union_executes_and_combines(self):
        result = self.engine.execute_cypher(
            "MATCH (n:Gene) RETURN n.name AS entity UNION MATCH (n:Drug) RETURN n.name AS entity"
        )
        assert "rows" in result
