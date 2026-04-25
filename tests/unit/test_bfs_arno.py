import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
SKIP_ARNO_TESTS = os.environ.get("SKIP_ARNO_TESTS", "true").lower() == "true"


class TestBFSArnoUnit:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = []
        engine = IRISGraphEngine(conn, embedding_dimension=4)
        engine._arno_available = True
        engine._arno_capabilities = {"nkg_data": True, "bfs": True, "algorithms": ["bfs"]}
        return engine

    def test_arno_bfs_path_called_when_bfs_capability_present(self):
        engine = self._make_engine()
        expected = [{"s": "A", "p": "BINDS", "o": "B", "w": 1.0, "step": 1}]

        with patch.object(engine, "_arno_call", return_value=json.dumps(expected)) as mock_call:
            with patch.object(engine, "_detect_arno", return_value=True):
                from iris_vector_graph.cypher.translator import SQLQuery
                vl = {
                    "types": ["BINDS"],
                    "min_hops": 1,
                    "max_hops": 3,
                    "properties": {},
                    "return_path_funcs": [],
                    "src_id_param": "A",
                    "dst_id_param": None,
                    "source_var": "a",
                    "source_alias": "n0",
                    "target_var": "b",
                    "target_alias": "n1",
                    "direction": "out",
                    "shortest": False,
                    "all_shortest": False,
                }
                sq = SQLQuery(
                    sql="",
                    parameters=[["A"]],
                    var_length_paths=[vl],
                )
                with patch.object(engine, "get_nodes", return_value=[{"id": "B", "labels": ["Gene"]}]):
                    result = engine._execute_var_length_cypher(sq)

        mock_call.assert_called_once_with(
            "Graph.KG.NKGAccel", "BFSJson", "A", '["BINDS"]', 3, 0
        )
        assert result["rows"] is not None

    def test_fallback_to_bfsfast_when_no_bfs_capability(self):
        engine = self._make_engine()
        engine._arno_capabilities = {"nkg_data": True, "algorithms": []}

        fallback_result = [{"s": "A", "p": "BINDS", "o": "B", "w": 1.0, "step": 1}]

        with patch("iris_vector_graph.engine._call_classmethod", return_value=json.dumps(fallback_result)):
            with patch.object(engine, "_detect_arno", return_value=False):
                from iris_vector_graph.cypher.translator import SQLQuery
                vl = {
                    "types": [], "min_hops": 1, "max_hops": 2, "properties": {},
                    "return_path_funcs": [], "src_id_param": "A", "dst_id_param": None,
                    "source_var": "a", "source_alias": "n0", "target_var": "b",
                    "target_alias": "n1", "direction": "out", "shortest": False, "all_shortest": False,
                }
                sq = SQLQuery(sql="", parameters=[["A"]], var_length_paths=[vl])
                with patch.object(engine, "get_nodes", return_value=[{"id": "B", "labels": []}]):
                    result = engine._execute_var_length_cypher(sq)

        assert result is not None

    def test_arno_bfs_error_falls_back_to_bfsfast(self):
        engine = self._make_engine()

        fallback_result = [{"s": "A", "p": "BINDS", "o": "B", "w": 1.0, "step": 1}]

        with patch.object(engine, "_arno_call", side_effect=RuntimeError("Arno not loaded")):
            with patch.object(engine, "_detect_arno", return_value=True):
                with patch("iris_vector_graph.engine._call_classmethod", return_value=json.dumps(fallback_result)):
                    from iris_vector_graph.cypher.translator import SQLQuery
                    vl = {
                        "types": [], "min_hops": 1, "max_hops": 2, "properties": {},
                        "return_path_funcs": [], "src_id_param": "A", "dst_id_param": None,
                        "source_var": "a", "source_alias": "n0", "target_var": "b",
                        "target_alias": "n1", "direction": "out", "shortest": False, "all_shortest": False,
                    }
                    sq = SQLQuery(sql="", parameters=[["A"]], var_length_paths=[vl])
                    with patch.object(engine, "get_nodes", return_value=[{"id": "B", "labels": []}]):
                        result = engine._execute_var_length_cypher(sq)

        assert result is not None

    def test_max_results_derived_from_sql_limit(self):
        engine = self._make_engine()
        captured = {}

        def capture_call(cls, method, *args):
            captured["args"] = args
            return "[]"

        with patch.object(engine, "_arno_call", side_effect=capture_call):
            with patch.object(engine, "_detect_arno", return_value=True):
                from iris_vector_graph.cypher.translator import SQLQuery
                vl = {
                    "types": [], "min_hops": 1, "max_hops": 2, "properties": {},
                    "return_path_funcs": [], "src_id_param": "A", "dst_id_param": None,
                    "source_var": "a", "source_alias": "n0", "target_var": "b",
                    "target_alias": "n1", "direction": "out", "shortest": False, "all_shortest": False,
                }
                sq = SQLQuery(
                    sql="SELECT n FROM Stage1 LIMIT 100",
                    parameters=[["A"]],
                    var_length_paths=[vl],
                )
                engine._execute_var_length_cypher(sq)

        assert captured.get("args") is not None
        assert captured["args"][-1] == 100


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.skipif(SKIP_ARNO_TESTS, reason="SKIP_ARNO_TESTS=true — Arno .so not loaded")
class TestBFSArnoE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        self.engine.initialize_schema()
        self._run = uuid.uuid4().hex[:8]
        yield
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'bfs079_{self._run}%' OR o_id LIKE 'bfs079_{self._run}%'"
            )
            cursor.execute(f"DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'bfs079_{self._run}%'")
            cursor.execute(f"DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'bfs079_{self._run}%'")
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def _node(self, suffix, label="Gene"):
        nid = f"bfs079_{self._run}_{suffix}"
        self.engine.create_node(nid, labels=[label])
        return nid

    def _edge(self, s, p, o):
        self.engine.create_edge(s, p, o)

    def test_bfs_arno_correctness(self):
        a = self._node("A")
        b = self._node("B")
        c = self._node("C")
        d = self._node("D")
        self._edge(a, "BINDS", b)
        self._edge(b, "BINDS", c)
        self._edge(c, "BINDS", d)

        result_arno = self.engine.execute_cypher(
            "MATCH (x)-[r*1..3]->(y) WHERE x.id = $id RETURN y.id",
            {"id": a},
        )
        arno_ids = {row[0] for row in result_arno["rows"]}

        self.engine._arno_available = False
        self.engine._arno_capabilities = {}

        result_fallback = self.engine.execute_cypher(
            "MATCH (x)-[r*1..3]->(y) WHERE x.id = $id RETURN y.id",
            {"id": a},
        )
        fallback_ids = {row[0] for row in result_fallback["rows"]}

        assert arno_ids == fallback_ids, f"Arno result {arno_ids} != fallback {fallback_ids}"

    def test_bfs_arno_perf(self):
        import time, statistics
        hub = self._node("hub")
        for i in range(20):
            mid = self._node(f"mid{i}")
            leaf = self._node(f"leaf{i}")
            self._edge(hub, "BINDS", mid)
            self._edge(mid, "BINDS", leaf)

        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            self.engine.execute_cypher(
                "MATCH (x)-[r*1..3]->(y) WHERE x.id = $id RETURN y.id",
                {"id": hub},
            )
            times.append((time.perf_counter() - t0) * 1000)

        med = statistics.median(times)
        assert med < 30, f"Arno BFS p50={med:.1f}ms exceeds 30ms target"

    def test_bfs_arno_predicate_filter(self):
        a = self._node("pred_a")
        b_binds = self._node("pred_b_binds")
        b_regs = self._node("pred_b_regs")
        self._edge(a, "BINDS", b_binds)
        self._edge(a, "REGULATES", b_regs)

        result = self.engine.execute_cypher(
            "MATCH (x)-[r:BINDS*1..2]->(y) WHERE x.id = $id RETURN y.id",
            {"id": a},
        )
        ids = {row[0] for row in result["rows"]}
        assert b_binds in ids
        assert b_regs not in ids

    def test_bfs_arno_fallback(self):
        a = self._node("fb_a")
        b = self._node("fb_b")
        self._edge(a, "BINDS", b)

        self.engine._arno_available = False
        self.engine._arno_capabilities = {}

        result = self.engine.execute_cypher(
            "MATCH (x)-[r*1..2]->(y) WHERE x.id = $id RETURN y.id",
            {"id": a},
        )
        ids = {row[0] for row in result["rows"]}
        assert b in ids

    def test_bfs_arno_max_results(self):
        hub = self._node("max_hub")
        for i in range(20):
            n = self._node(f"max_leaf{i}")
            self._edge(hub, "BINDS", n)

        result = self.engine.execute_cypher(
            "MATCH (x)-[r*1..1]->(y) WHERE x.id = $id RETURN y.id LIMIT 5",
            {"id": hub},
        )
        assert len(result["rows"]) <= 5
