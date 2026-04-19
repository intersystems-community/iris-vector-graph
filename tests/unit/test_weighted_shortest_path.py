import json
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestWeightedShortestPathE2E:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.engine = IRISGraphEngine(iris_connection)
        self._run = uuid.uuid4().hex[:8]
        yield
        self._cleanup()

    def _n(self, label):
        return f"wsp_{label}_{self._run}"

    def _cleanup(self):
        cursor = self.engine.conn.cursor()
        prefix = f"wsp_%_{self._run}"
        for table in ["Graph_KG.rdf_edges", "Graph_KG.nodes"]:
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE s LIKE ? OR o_id LIKE ?",
                    [prefix, prefix],
                )
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM {table} WHERE node_id LIKE ?", [prefix])
            except Exception:
                pass
        try:
            self.engine.conn.commit()
        except Exception:
            pass

    def _add_weighted_edge(self, src, pred, dst, weight):
        self.engine.create_node(src)
        self.engine.create_node(dst)
        cursor = self.engine.conn.cursor()
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) "
            "SELECT ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id IS NULL)",
            [src, pred, dst, src, pred, dst],
        )
        self.engine.conn.commit()
        iris_obj = self.engine._iris_obj()
        iris_obj.classMethodVoid(
            "Graph.KG.EdgeScan", "WriteAdjacency", src, pred, dst, str(weight)
        )

    def test_weighted_prefers_lower_cost_longer_path(self):
        A, B, C = self._n("A"), self._n("B"), self._n("C")
        self._add_weighted_edge(A, "R", B, 10.0)
        self._add_weighted_edge(A, "R", C, 1.0)
        self._add_weighted_edge(C, "R", B, 1.0)

        q = (
            f"CALL ivg.shortestPath.weighted('{A}', '{B}', 'weight', 99, 5) "
            f"YIELD path, totalCost RETURN path, totalCost"
        )
        result = self.engine.execute_cypher(q)
        assert len(result["rows"]) == 1, f"Expected 1 row, got {result['rows']}"
        path_json = json.loads(result["rows"][0][0])
        total_cost = result["rows"][0][1]
        assert abs(total_cost - 2.0) < 0.01, f"Expected totalCost=2.0, got {total_cost}"
        assert C in path_json["nodes"], f"Expected C in path, got {path_json['nodes']}"
        assert B not in path_json["nodes"][:-1], "B should only appear at end"

    def test_weighted_no_path_returns_empty(self):
        A, B = self._n("X"), self._n("Y")
        self.engine.create_node(A)
        self.engine.create_node(B)
        q = (
            f"CALL ivg.shortestPath.weighted('{A}', '{B}', 'weight', 99, 5) "
            f"YIELD path, totalCost RETURN path, totalCost"
        )
        result = self.engine.execute_cypher(q)
        assert result["rows"] == []

    def test_weighted_source_equals_target(self):
        A = self._n("A")
        self.engine.create_node(A)
        q = (
            f"CALL ivg.shortestPath.weighted('{A}', '{A}', 'weight', 99, 5) "
            f"YIELD path, totalCost RETURN path, totalCost"
        )
        result = self.engine.execute_cypher(q)
        assert len(result["rows"]) == 1
        path_json = json.loads(result["rows"][0][0])
        assert path_json["length"] == 0
        assert path_json["totalCost"] == 0.0

    def test_weighted_fallback_to_unit_weight(self):
        A, B = self._n("A"), self._n("B")
        self._add_weighted_edge(A, "R", B, 1.0)
        q = (
            f"CALL ivg.shortestPath.weighted('{A}', '{B}', 'nonexistent_prop', 99, 5) "
            f"YIELD path, totalCost RETURN path, totalCost"
        )
        result = self.engine.execute_cypher(q)
        assert len(result["rows"]) == 1, (
            f"Should find path with unit weight fallback, got {result['rows']}"
        )
