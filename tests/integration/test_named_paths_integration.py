"""Integration tests for named path SQL translation (Principle IV)."""
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix


class TestNamedPathSQLTranslation:

    def setup_method(self):
        set_schema_prefix("Graph_KG")

    def teardown_method(self):
        set_schema_prefix("")

    def test_return_p_produces_json_object_sql(self):
        """T022a: translate() for MATCH p = (a)-[r]->(b) RETURN p produces JSON_OBJECT SQL"""
        q = parse_query("MATCH p = (a)-[r]->(b) RETURN p")
        result = translate_to_sql(q)
        sql = result.sql
        assert "JSON_OBJECT(" in sql
        assert "'nodes'" in sql
        assert "'rels'" in sql
        assert "JSON_ARRAY(" in sql
        assert "Graph_KG.nodes" in sql
        assert "Graph_KG.rdf_edges" in sql

    def test_path_functions_produce_correct_sql(self):
        """T022b: translate() for path functions produces correct SQL fragments"""
        q = parse_query("MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN length(p) AS hops, nodes(p) AS ns, relationships(p) AS rs")
        result = translate_to_sql(q)
        sql = result.sql
        assert "2 AS hops" in sql
        assert "JSON_ARRAY(" in sql
        assert "node_id" in sql
        assert ".p" in sql
