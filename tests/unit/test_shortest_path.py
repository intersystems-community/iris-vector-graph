import json
import os
from unittest.mock import MagicMock

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine():
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    return engine


class TestShortestPathUnit:

    def _get_vl(self, query_str):
        from iris_vector_graph.cypher.parser import parse_query

        result = parse_query(query_str)
        match_clause = result.query_parts[0].clauses[0]
        return match_clause.named_paths[0].pattern.relationships[0].variable_length

    def test_shortestpath_parses_without_error(self):
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH p = shortestPath((a {id:'hla-a*02:01'})-[*..8]-(b {id:'DOID:162'})) RETURN p"
        result = parse_query(q)
        assert result is not None
        vl = self._get_vl(q)
        assert vl is not None
        assert vl.shortest is True
        assert vl.all_shortest is False
        assert vl.max_hops == 8

    def test_all_shortest_paths_parses(self):
        q = "MATCH p = allShortestPaths((a {id:'x'})-[*..6]-(b {id:'y'})) RETURN p"
        vl = self._get_vl(q)
        assert vl.all_shortest is True
        assert vl.shortest is False
        assert vl.max_hops == 6

    def test_shortestpath_without_max_hops_defaults_to_5(self):
        q = "MATCH p = shortestPath((a {id:'x'})--(b {id:'y'})) RETURN p"
        vl = self._get_vl(q)
        assert vl.max_hops == 5
        assert vl.shortest is True

    def test_shortestpath_translate_sets_shortest_flag(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH p = shortestPath((a {id:'x'})-[*..5]-(b {id:'y'})) RETURN p"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        assert len(sql_obj.var_length_paths) == 1
        vl = sql_obj.var_length_paths[0]
        assert vl["shortest"] is True
        assert vl["all_shortest"] is False
        assert vl["direction"] == "both"
        assert vl["src_id_param"] == "x"
        assert vl["dst_id_param"] == "y"

    def test_shortestpath_param_refs_stored(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to})) RETURN p"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {"from": "A", "to": "B"})
        vl = sql_obj.var_length_paths[0]
        assert vl["src_id_param"] == "$from"
        assert vl["dst_id_param"] == "$to"

    def test_length_p_return_func_detected(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH p = shortestPath((a {id:'x'})-[*..5]-(b {id:'y'})) RETURN length(p) AS hops"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        assert "length" in sql_obj.var_length_paths[0]["return_path_funcs"]

    def test_nodes_p_return_func_detected(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH p = shortestPath((a {id:'x'})-[*..5]-(b {id:'y'})) RETURN nodes(p)"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        assert "nodes" in sql_obj.var_length_paths[0]["return_path_funcs"]

    def test_all_shortest_paths_translate_sets_all_shortest_flag(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH p = allShortestPaths((a {id:'x'})-[*..5]-(b {id:'y'})) RETURN p"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        assert sql_obj.var_length_paths[0]["all_shortest"] is True
        assert sql_obj.var_length_paths[0]["shortest"] is False


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestShortestPathE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        import uuid
        from iris_vector_graph.engine import IRISGraphEngine

        self.engine = IRISGraphEngine(iris_connection)
        self._run = uuid.uuid4().hex[:8]
        yield
        self._cleanup()

    def _cleanup(self):
        cursor = self.engine.conn.cursor()
        for nid in [f"spA_{self._run}", f"spB_{self._run}", f"spC_{self._run}",
                    f"spD_{self._run}", f"spE_{self._run}"]:
            try:
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [nid])
                cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?", [nid, nid])
            except Exception:
                pass
        try:
            self.engine.conn.commit()
        except Exception:
            pass

    def _add_node(self, nid):
        self.engine.execute_cypher(f"CREATE (n {{id: '{nid}'}})")

    def _add_edge(self, src, rel, dst):
        self.engine.execute_cypher(
            f"MATCH (a {{id: '{src}'}}), (b {{id: '{dst}'}}) CREATE (a)-[:{rel}]->(b)"
        )

    def _build_kg(self):
        from iris_vector_graph.schema import _call_classmethod
        try:
            _call_classmethod(self.engine.conn, "Graph.KG.Traversal", "BuildKG")
        except Exception:
            pass

    def _n(self, letter):
        return f"sp{letter}_{self._run}"

    def test_shortestpath_chain_graph(self):
        a, b, c, d, e = self._n("A"), self._n("B"), self._n("C"), self._n("D"), self._n("E")
        for nid in [a, b, c, d, e]:
            self._add_node(nid)
        for src, dst in [(a, b), (b, c), (c, d), (d, e)]:
            self._add_edge(src, "CONNECTS", dst)
        self._build_kg()

        q = f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..5]-(y {{id:'{e}'}})) RETURN p"
        result = self.engine.execute_cypher(q)
        import json
        assert len(result["rows"]) == 1
        path = json.loads(result["rows"][0][0])
        assert path["length"] == 4
        assert path["nodes"][0] == a
        assert path["nodes"][-1] == e

    def test_shortestpath_no_path_returns_empty(self):
        a, b = self._n("A"), self._n("B")
        self._add_node(a)
        self._add_node(b)

        q = f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..5]-(y {{id:'{b}'}})) RETURN p"
        result = self.engine.execute_cypher(q)
        assert result["rows"] == []

    def test_shortestpath_same_node_returns_zero_length(self):
        a = self._n("A")
        self._add_node(a)

        q = f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..5]-(y {{id:'{a}'}})) RETURN p"
        result = self.engine.execute_cypher(q)
        import json
        assert len(result["rows"]) == 1
        path = json.loads(result["rows"][0][0])
        assert path["length"] == 0
        assert path["nodes"] == [a]
        assert path["rels"] == []

    def test_length_p_end_to_end(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        for nid in [a, b, c]:
            self._add_node(nid)
        self._add_edge(a, "CONNECTS", b)
        self._add_edge(b, "CONNECTS", c)
        self._build_kg()

        q = f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..5]-(y {{id:'{c}'}})) RETURN length(p) AS hops"
        result = self.engine.execute_cypher(q)
        assert len(result["rows"]) == 1
        assert result["rows"][0][0] == 2

    def test_all_shortest_paths_diamond(self):
        a, b, c, d = self._n("A"), self._n("B"), self._n("C"), self._n("D")
        for nid in [a, b, c, d]:
            self._add_node(nid)
        self._add_edge(a, "CONNECTS", b)
        self._add_edge(b, "CONNECTS", c)
        self._add_edge(a, "CONNECTS", d)
        self._add_edge(d, "CONNECTS", c)
        self._build_kg()

        q = f"MATCH p = allShortestPaths((x {{id:'{a}'}})-[*..4]-(y {{id:'{c}'}})) RETURN p"
        result = self.engine.execute_cypher(q)
        import json
        assert len(result["rows"]) == 2
        for row in result["rows"]:
            path = json.loads(row[0])
            assert path["length"] == 2

    def test_all_shortest_paths_single_path(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        for nid in [a, b, c]:
            self._add_node(nid)
        self._add_edge(a, "CONNECTS", b)
        self._add_edge(b, "CONNECTS", c)
        self._build_kg()

        q = f"MATCH p = allShortestPaths((x {{id:'{a}'}})-[*..5]-(y {{id:'{c}'}})) RETURN p"
        result = self.engine.execute_cypher(q)
        assert len(result["rows"]) == 1

    def test_shortestpath_directed_vs_undirected(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        for nid in [a, b, c]:
            self._add_node(nid)
        self._add_edge(a, "CONNECTS", b)
        self._add_edge(b, "CONNECTS", c)
        self._build_kg()

        q_directed = f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..3]->(y {{id:'{c}'}})) RETURN p"
        result_directed = self.engine.execute_cypher(q_directed)
        assert len(result_directed["rows"]) == 1

        q_undirected_rev = f"MATCH p = shortestPath((x {{id:'{c}'}})-[*..3]-(y {{id:'{a}'}})) RETURN p"
        result_undirected = self.engine.execute_cypher(q_undirected_rev)
        assert len(result_undirected["rows"]) == 1

        q_directed_rev = f"MATCH p = shortestPath((x {{id:'{c}'}})-[*..3]->(y {{id:'{a}'}})) RETURN p"
        result_directed_rev = self.engine.execute_cypher(q_directed_rev)
        assert result_directed_rev["rows"] == []
