"""Unit and e2e tests for Cypher variable-length path patterns [*1..3]."""
import os
import uuid
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix
from iris_vector_graph.cypher import ast

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class TestVarLengthParser:

    def test_parses_typed_var_length(self):
        q = parse_query("MATCH (a)-[:REL*1..3]->(b) RETURN b")
        rel = q.query_parts[0].clauses[0].patterns[0].relationships[0]
        assert rel.variable_length is not None
        assert rel.variable_length.min_hops == 1
        assert rel.variable_length.max_hops == 3

    def test_parses_untyped_var_length(self):
        q = parse_query("MATCH (a)-[*2..4]->(b) RETURN b")
        rel = q.query_parts[0].clauses[0].patterns[0].relationships[0]
        assert rel.variable_length.min_hops == 2
        assert rel.variable_length.max_hops == 4

    def test_parses_star_only(self):
        q = parse_query("MATCH (a)-[*]->(b) RETURN b")
        rel = q.query_parts[0].clauses[0].patterns[0].relationships[0]
        assert rel.variable_length is not None

    def test_exact_hops(self):
        q = parse_query("MATCH (a)-[:R*2..2]->(b) RETURN b")
        rel = q.query_parts[0].clauses[0].patterns[0].relationships[0]
        assert rel.variable_length.min_hops == 2
        assert rel.variable_length.max_hops == 2

    def test_fixed_pattern_unaffected(self):
        q = parse_query("MATCH (a)-[:REL]->(b) RETURN b")
        rel = q.query_parts[0].clauses[0].patterns[0].relationships[0]
        assert rel.variable_length is None


class TestVarLengthTranslator:

    @pytest.fixture(autouse=True)
    def setup(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_var_length_detected_in_translation(self):
        q = parse_query("MATCH (a {id: 'SRC'})-[:REL*1..3]->(b) RETURN b")
        r = translate_to_sql(q)
        assert r.sql is not None
        assert r.var_length_paths is not None and len(r.var_length_paths) > 0

    def test_fixed_path_no_var_length_metadata(self):
        q = parse_query("MATCH (a)-[:REL]->(b) RETURN b")
        r = translate_to_sql(q)
        assert not r.var_length_paths


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestVarLengthE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        self.PREFIX = f"VLP_{uuid.uuid4().hex[:6]}"
        nodes = [f"{self.PREFIX}:N{i}" for i in range(5)]
        for nid in nodes:
            self.engine.create_node(nid)
        for i in range(4):
            self.engine.create_edge(nodes[i], "NEXT", nodes[i+1])
        try:
            irispy = self._irispy()
            irispy.classMethodVoid("Graph.KG.Traversal", "BuildKG")
        except Exception as e:
            pytest.skip(f"BuildKG failed: {e}")
        self.nodes = nodes
        yield
        p = f"{self.PREFIX}%"
        cursor = iris_connection.cursor()
        cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        iris_connection.commit()

    def _irispy(self):
        try:
            import iris
            return iris.createIRIS(self.conn)
        except TypeError:
            import intersystems_iris
            return intersystems_iris.createIRIS(self.conn)

    def test_var_length_returns_reachable_nodes(self):
        result = self.engine.execute_cypher(
            f"MATCH (a)-[:NEXT*1..3]->(b) WHERE a.id = '{self.nodes[0]}' RETURN b.id"
        )
        node_ids = {r[0] if isinstance(r, (list, tuple)) else r for r in result["rows"]}
        assert self.nodes[1] in node_ids
        assert self.nodes[3] in node_ids

    def test_var_length_min_hop_respected(self):
        result = self.engine.execute_cypher(
            f"MATCH (a)-[:NEXT*2..3]->(b) WHERE a.id = '{self.nodes[0]}' RETURN b.id"
        )
        node_ids = {r[0] if isinstance(r, (list, tuple)) else r for r in result["rows"]}
        assert self.nodes[1] not in node_ids
        assert self.nodes[2] in node_ids
