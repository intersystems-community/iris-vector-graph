"""Unit tests for named path bindings in Cypher parser and translator."""
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix
from iris_vector_graph.cypher import ast


class TestNamedPathParsing:

    def test_parse_1hop_named_path(self):
        """T008: MATCH p = (a)-[r]->(b) RETURN p"""
        q = parse_query("MATCH p = (a)-[r]->(b) RETURN p")
        match = q.query_parts[0].clauses[0]
        assert len(match.named_paths) == 1
        np = match.named_paths[0]
        assert np.variable == "p"
        assert len(np.pattern.nodes) == 2
        assert len(np.pattern.relationships) == 1

    def test_parse_2hop_named_path(self):
        """T009: MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN p"""
        q = parse_query("MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN p")
        np = q.query_parts[0].clauses[0].named_paths[0]
        assert np.variable == "p"
        assert len(np.pattern.nodes) == 3
        assert len(np.pattern.relationships) == 2

    def test_parse_3hop_named_path(self):
        """T009a: MATCH p = (a)-[r1]->(b)-[r2]->(c)-[r3]->(d) RETURN p"""
        q = parse_query("MATCH p = (a)-[r1]->(b)-[r2]->(c)-[r3]->(d) RETURN p")
        np = q.query_parts[0].clauses[0].named_paths[0]
        assert np.variable == "p"
        assert len(np.pattern.nodes) == 4
        assert len(np.pattern.relationships) == 3

    def test_parse_unnamed_pattern_unchanged(self):
        """T010: MATCH (a)-[r]->(b) RETURN a — no named path"""
        q = parse_query("MATCH (a)-[r]->(b) RETURN a")
        match = q.query_parts[0].clauses[0]
        assert len(match.named_paths) == 0
        assert len(match.patterns) == 1

    def test_parse_named_path_with_where(self):
        """T020: MATCH p = (a)-[r]->(b) WHERE a.name = 'X' RETURN nodes(p)"""
        q = parse_query("MATCH p = (a)-[r]->(b) WHERE a.name = 'X' RETURN nodes(p)")
        match = q.query_parts[0].clauses[0]
        assert len(match.named_paths) == 1
        assert match.named_paths[0].variable == "p"

    def test_parse_named_path_with_typed_rel(self):
        """T021: MATCH p = (a)-[r:KNOWS]->(b) RETURN relationships(p)"""
        q = parse_query("MATCH p = (a)-[r:KNOWS]->(b) RETURN relationships(p)")
        np = q.query_parts[0].clauses[0].named_paths[0]
        assert np.pattern.relationships[0].types == ["KNOWS"]


class TestNamedPathTranslation:

    @pytest.fixture(autouse=True)
    def setup_schema(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_return_p_emits_json_object(self):
        """T011: RETURN p emits JSON path object with JSON_ARRAY for nodes and rels"""
        q = parse_query("MATCH p = (a)-[r]->(b) RETURN p")
        result = translate_to_sql(q)
        sql = result.sql
        assert "nodes" in sql
        assert "rels" in sql
        assert "JSON_ARRAY" in sql
        assert "node_id" in sql

    def test_length_p_emits_integer(self):
        """T014: length(p) on 2-hop path translates to integer literal"""
        q = parse_query("MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN length(p)")
        result = translate_to_sql(q)
        assert "2" in result.sql

    def test_nodes_p_emits_json_array(self):
        """T015: nodes(p) emits JSON_ARRAY of node_id columns"""
        q = parse_query("MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN nodes(p)")
        result = translate_to_sql(q)
        sql = result.sql
        assert "JSON_ARRAY" in sql
        assert sql.count("node_id") >= 3

    def test_relationships_p_emits_json_array(self):
        """T016: relationships(p) emits JSON_ARRAY of edge predicate columns"""
        q = parse_query("MATCH p = (a)-[r1]->(b)-[r2]->(c) RETURN relationships(p)")
        result = translate_to_sql(q)
        sql = result.sql
        assert "JSON_ARRAY" in sql
        assert ".p" in sql

    def test_invalid_path_ref_raises_error(self):
        """T017: nodes(x) where x is not a path raises error"""
        q = parse_query("MATCH (a)-[r]->(b) RETURN nodes(a)")
        with pytest.raises(Exception):
            translate_to_sql(q)
