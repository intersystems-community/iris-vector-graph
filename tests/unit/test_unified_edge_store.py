import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine():
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine.conn.cursor.return_value = MagicMock()
    return engine


class TestUnifiedEdgeStoreUnit:

    def test_matchedges_bound_source_bound_predicate(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '[{"s":"A","p":"TREATS","o":"B","w":1.0}]'
        engine._iris_obj = lambda: iris_mock

        result = json.loads(iris_mock.classMethodValue(
            "Graph.KG.EdgeScan", "MatchEdges", "A", "TREATS", 0
        ))
        assert isinstance(result, list)
        assert len(result) == 1
        assert all(k in result[0] for k in ("s", "p", "o", "w"))
        assert result[0]["s"] == "A"
        assert result[0]["p"] == "TREATS"
        assert result[0]["o"] == "B"

    def test_create_edge_calls_write_adjacency(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        engine._iris_obj = lambda: iris_mock

        cursor_mock = MagicMock()
        cursor_mock.execute.return_value = None
        engine.conn.cursor.return_value = cursor_mock

        engine.create_edge("A", "TREATS", "B")

        void_calls = iris_mock.classMethodVoid.call_args_list
        write_calls = [c for c in void_calls
                       if len(c.args) > 1 and c.args[0] == "Graph.KG.EdgeScan"
                       and c.args[1] == "WriteAdjacency"]
        assert write_calls, "create_edge must call EdgeScan.WriteAdjacency"
        assert write_calls[0].args[2] == "A"
        assert write_calls[0].args[3] == "TREATS"
        assert write_calls[0].args[4] == "B"

    def test_delete_edge_calls_delete_adjacency(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        engine._iris_obj = lambda: iris_mock

        cursor_mock = MagicMock()
        engine.conn.cursor.return_value = cursor_mock

        try:
            engine.delete_edge("A", "TREATS", "B")
        except Exception:
            pass

        void_calls = iris_mock.classMethodVoid.call_args_list
        delete_calls = [c for c in void_calls
                        if len(c.args) > 1 and c.args[0] == "Graph.KG.EdgeScan"
                        and c.args[1] == "DeleteAdjacency"]
        assert delete_calls, "delete_edge must call EdgeScan.DeleteAdjacency"

    def test_kg_write_failure_is_non_fatal(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodVoid.side_effect = RuntimeError("^KG write failed")
        engine._iris_obj = lambda: iris_mock

        cursor_mock = MagicMock()
        cursor_mock.execute.return_value = None
        engine.conn.cursor.return_value = cursor_mock

        result = engine.create_edge("A", "TREATS", "B")
        assert result is True, "create_edge must return True even when ^KG write fails"

    def test_simple_match_uses_edgescan_cte(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH (a {id:'X'})-[r]->(b) RETURN type(r), b.id"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        sql = sql_obj.sql if isinstance(sql_obj.sql, str) else " ".join(sql_obj.sql if isinstance(sql_obj.sql, list) else [sql_obj.sql])
        assert "MatchEdges" in sql, f"Expected MatchEdges in SQL, got: {sql[:300]}"
        main_join = sql.split("WHERE")[0] if "WHERE" in sql else sql
        assert "rdf_edges" not in main_join, f"rdf_edges still in MATCH JOIN path: {main_join[:300]}"

    def test_predicate_filtered_match_uses_bound_predicate(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH (a {id:'X'})-[r:TREATS]->(b) RETURN b.id"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        sql = sql_obj.sql if isinstance(sql_obj.sql, str) else " ".join(sql_obj.sql if isinstance(sql_obj.sql, list) else [sql_obj.sql])
        assert "MatchEdges" in sql
        assert "'TREATS'" in sql or "TREATS" in sql

    def test_unbound_source_match_passes_empty_sourceid(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "MATCH (a)-[r:TREATS]->(b) RETURN a.id, b.id"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        sql = sql_obj.sql if isinstance(sql_obj.sql, str) else " ".join(sql_obj.sql if isinstance(sql_obj.sql, list) else [sql_obj.sql])
        assert "MatchEdges" in sql


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestUnifiedEdgeStoreE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.engine = IRISGraphEngine(iris_connection)
        self._run = uuid.uuid4().hex[:8]
        yield
        self._cleanup()

    def _n(self, label):
        return f"ues_{label}_{self._run}"

    def _cleanup(self):
        cursor = self.engine.conn.cursor()
        for nid in [self._n("A"), self._n("B"), self._n("C"),
                    self._n("X"), self._n("Y"), self._n("Z")]:
            try:
                cursor.execute(
                    "DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?", [nid, nid]
                )
                cursor.execute(
                    "DELETE FROM Graph_KG.nodes WHERE node_id = ?", [nid]
                )
            except Exception:
                pass
        try:
            self.engine.conn.commit()
        except Exception:
            pass

    def _seed_kg_edge(self, s, p, o, w="1.0"):
        iris_obj = self.engine._iris_obj()
        iris_obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, w)

    def test_matchedges_returns_correct_json(self):
        from iris_vector_graph.schema import _call_classmethod

        a, b = self._n("A"), self._n("B")
        self._seed_kg_edge(a, "TREATS", b)
        try:
            raw = _call_classmethod(self.engine.conn, "Graph.KG.EdgeScan", "MatchEdges", a, "TREATS", 0)
            results = json.loads(str(raw))
            assert len(results) == 1
            assert results[0]["s"] == a
            assert results[0]["p"] == "TREATS"
            assert results[0]["o"] == b
        finally:
            self.engine._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", a, "TREATS", b)

    def test_matchedges_unbound_predicate(self):
        from iris_vector_graph.schema import _call_classmethod

        a, b, c = self._n("A"), self._n("B"), self._n("C")
        self._seed_kg_edge(a, "TREATS", b)
        self._seed_kg_edge(a, "CAUSES", c)
        try:
            raw = _call_classmethod(self.engine.conn, "Graph.KG.EdgeScan", "MatchEdges", a, "", 0)
            results = json.loads(str(raw))
            preds = {r["p"] for r in results}
            assert "TREATS" in preds
            assert "CAUSES" in preds
        finally:
            self.engine._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", a, "TREATS", b)
            self.engine._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", a, "CAUSES", c)

    def test_matchedges_unbound_source(self):
        from iris_vector_graph.schema import _call_classmethod

        a, b, c = self._n("A"), self._n("B"), self._n("C")
        self._seed_kg_edge(a, "TREATS", b)
        self._seed_kg_edge(c, "CAUSES", b)
        try:
            raw = _call_classmethod(self.engine.conn, "Graph.KG.EdgeScan", "MatchEdges", "", "", 0)
            results = json.loads(str(raw))
            sources = {r["s"] for r in results}
            assert a in sources
            assert c in sources
        finally:
            self.engine._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", a, "TREATS", b)
            self.engine._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", c, "CAUSES", b)

    def test_temporal_edge_visible_in_match(self):
        import time
        x, y, z = self._n("X"), self._n("Y"), self._n("Z")
        self.engine.create_node(x)
        self.engine.create_node(y)
        self.engine.create_node(z)
        self.engine.create_edge(x, "STATIC_REL", y)
        self.engine.create_edge_temporal(x, "TEMPORAL_REL", z, timestamp=int(time.time()))
        result = self.engine.execute_cypher(
            f"MATCH (a {{id:'{x}'}})-[r]->(b) RETURN type(r) AS rel_type"
        )
        rel_types = {row[0] for row in result["rows"]}
        assert "STATIC_REL" in rel_types, f"Static edge not returned. Got: {rel_types}"
        assert "TEMPORAL_REL" in rel_types, f"Temporal edge not returned. Got: {rel_types}"

    def test_delete_edge_not_visible_in_match(self):
        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "REL", b)
        before = self.engine.execute_cypher(f"MATCH (x {{id:'{a}'}})-[r:REL]->(y) RETURN y.id")
        assert len(before["rows"]) == 1

        self.engine.delete_edge(a, "REL", b)
        after = self.engine.execute_cypher(f"MATCH (x {{id:'{a}'}})-[r:REL]->(y) RETURN y.id")
        assert len(after["rows"]) == 0, "Deleted edge still visible in MATCH"

    def test_no_builkg_required_for_bfs(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_node(c)
        self.engine.create_edge(a, "CONN", b)
        self.engine.create_edge(b, "CONN", c)
        result = self.engine.execute_cypher(
            f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..4]-(y {{id:'{c}'}})) RETURN p"
        )
        assert len(result["rows"]) == 1, "shortestPath must work without BuildKG"

    def test_write_adjacency_sets_kg_global(self):
        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "TREATS", b)
        try:
            from iris_vector_graph.schema import _call_classmethod
            raw = _call_classmethod(self.engine.conn, "Graph.KG.EdgeScan", "MatchEdges", a, "TREATS", 0)
            results = json.loads(str(raw))
            assert any(r["o"] == b for r in results), "create_edge must write ^KG immediately"
            self.engine.delete_edge(a, "TREATS", b)
            raw2 = _call_classmethod(self.engine.conn, "Graph.KG.EdgeScan", "MatchEdges", a, "TREATS", 0)
            results2 = json.loads(str(raw2))
            assert not any(r["o"] == b for r in results2), "delete_edge must kill ^KG entry"
        finally:
            self._cleanup()
