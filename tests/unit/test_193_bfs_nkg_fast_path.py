"""
Tests for Spec 193: BFSFastJsonDirect + NKG fast-path for 3-5 hop Cypher queries.

Phase 1: BFSFastJsonDirect correctness (T193-01..03)
Phase 2: _try_khop_fast_path NKG extension (T193-08..10)
"""
import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_test_graph(iris_connection, iris_obj, n_nodes=20, n_edges=60):
    """Insert a small synthetic graph and build ^KG + ^NKG."""
    import random
    rng = random.Random(193)
    cursor = iris_connection.cursor()
    for tbl in ["rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
        try:
            cursor.execute(f"DELETE FROM Graph_KG.{tbl}")
        except Exception:
            pass
    iris_connection.commit()

    nodes = [f"n193_{i}" for i in range(n_nodes)]
    cursor.executemany(
        "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)",
        [[n] for n in nodes],
    )
    iris_connection.commit()

    edges = set()
    for i in range(1, n_nodes):
        edges.add((i, rng.randint(0, i - 1)))
    while len(edges) < n_edges:
        s, o = rng.randint(0, n_nodes - 1), rng.randint(0, n_nodes - 1)
        if s != o:
            edges.add((s, o))

    cursor.executemany(
        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
        [[nodes[s], "R", nodes[o]] for s, o in edges],
    )
    iris_connection.commit()

    iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")
    iris_obj.classMethodValue("Graph.KG.TraversalBuild", "BuildNKG")
    return nodes


# ---------------------------------------------------------------------------
# Phase 1 — BFSFastJsonDirect
# ---------------------------------------------------------------------------

class TestBFSFastJsonDirectUnit:
    """T193-01: empty result returns []."""

    @pytest.mark.skipif(SKIP_IRIS_TESTS, reason="IRIS not available")
    def test_empty_result_returns_empty_array(self, iris_connection):
        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        result_str = str(iris_obj.classMethodValue(
            "Graph.KG.Traversal", "BFSFastJsonDirect",
            "__nonexistent_node_193__", "", 2, "",
        ))
        assert result_str == "[]"


class TestBFSFastJsonDirectE2E:
    """T193-02, T193-03: count and structure parity with BFSFastJson."""

    @pytest.mark.skipif(SKIP_IRIS_TESTS, reason="IRIS not available")
    def test_count_matches_bfsfastjson(self, iris_connection, iris_master_cleanup):
        """BFSFastJsonDirect must return same node count as BFSFastJson."""
        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        nodes = _load_test_graph(iris_connection, iris_obj)

        # Find highest-degree node
        deg = {}
        cursor = iris_connection.cursor()
        cursor.execute("SELECT s, o_id FROM Graph_KG.rdf_edges")
        for s, o in cursor.fetchall():
            deg[s] = deg.get(s, 0) + 1
            deg[o] = deg.get(o, 0) + 1
        seed = max(deg, key=lambda k: deg[k])

        for max_hops in (1, 2, 3):
            orig = json.loads(str(iris_obj.classMethodValue(
                "Graph.KG.Traversal", "BFSFastJson", seed, "", max_hops, "",
            )))
            direct = json.loads(str(iris_obj.classMethodValue(
                "Graph.KG.Traversal", "BFSFastJsonDirect", seed, "", max_hops, "",
            )))
            orig_objects = {(r["s"], r["p"], r["o"], r["step"]) for r in orig}
            direct_objects = {(r["s"], r["p"], r["o"], r["step"]) for r in direct}
            assert orig_objects == direct_objects, (
                f"Mismatch at max_hops={max_hops}: "
                f"BFSFastJson={len(orig)}, BFSFastJsonDirect={len(direct)}"
            )

    @pytest.mark.skipif(SKIP_IRIS_TESTS, reason="IRIS not available")
    def test_json_keys_present(self, iris_connection, iris_master_cleanup):
        """T193-03: each row has s, p, o, w, step keys."""
        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        nodes = _load_test_graph(iris_connection, iris_obj)
        seed = nodes[0]

        result = json.loads(str(iris_obj.classMethodValue(
            "Graph.KG.Traversal", "BFSFastJsonDirect", seed, "", 2, "",
        )))
        if result:
            row = result[0]
            for key in ("s", "p", "o", "w", "step"):
                assert key in row, f"Missing key '{key}' in BFSFastJsonDirect row"
            assert isinstance(row["step"], int)
            assert isinstance(row["w"], (int, float))


# ---------------------------------------------------------------------------
# Phase 2 — NKG fast-path extension
# ---------------------------------------------------------------------------

class TestKhopFastPathUnit:
    """T193-08, T193-09: unit tests for _try_khop_fast_path extension."""

    def _make_engine_with_mock_iris(self, nkg_populated=True):
        """Return (engine, mock_iris_obj) with _iris_obj() patched."""
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = []
        engine = IRISGraphEngine(conn, embedding_dimension=4)

        mock_iris = MagicMock()

        def _side_effect(cls, method, *args):
            if cls == "Graph.KG.Traversal" and method == "NKGPopulated":
                return "1" if nkg_populated else "0"
            if cls == "Graph.KG.NKGAccelTraversal" and method == "KHopNeighbors":
                return json.dumps({
                    "totalNodes": 3,
                    "nodes": [
                        {"id": "src", "dist": 0},
                        {"id": "a", "dist": 1},
                        {"id": "b", "dist": 2},
                    ],
                })
            if cls == "Graph.KG.Traversal" and method == "KHopCount":
                return "3"
            return "0"

        mock_iris.classMethodValue.side_effect = _side_effect
        return engine, mock_iris

    def test_var_length_3hop_intercepted_when_nkg_populated(self):
        """T193-08: 3-hop variable-length pattern routes to NKG fast-path."""
        engine, mock_iris = self._make_engine_with_mock_iris(nkg_populated=True)
        with patch.object(engine, "_iris_obj", return_value=mock_iris):
            result = engine._try_khop_fast_path(
                "MATCH (n {node_id: $x})-[*1..3]->(m) RETURN m.node_id",
                {"x": "src"},
            )
        assert result is not None, "Expected fast-path hit, got None"
        assert result.columns == ["node_id"]
        assert len(result.rows) == 2  # seed excluded

    def test_var_length_returns_none_when_nkg_absent(self):
        """T193-09: falls back when NKG not populated."""
        engine, mock_iris = self._make_engine_with_mock_iris(nkg_populated=False)
        with patch.object(engine, "_iris_obj", return_value=mock_iris):
            result = engine._try_khop_fast_path(
                "MATCH (n {node_id: $x})-[*1..3]->(m) RETURN m.node_id",
                {"x": "src"},
            )
        assert result is None, "Expected fallback (None) when NKG not populated"

    def test_typed_pred_3hop_intercepted(self):
        """Typed predicate variant: MATCH (n {node_id:$x})-[:R*1..3]->(m) RETURN m.node_id"""
        engine, mock_iris = self._make_engine_with_mock_iris(nkg_populated=True)
        with patch.object(engine, "_iris_obj", return_value=mock_iris):
            result = engine._try_khop_fast_path(
                "MATCH (n {node_id: $x})-[:R*1..3]->(m) RETURN m.node_id",
                {"x": "src"},
            )
        assert result is not None

    def test_5hop_intercepted(self):
        """5-hop boundary is handled."""
        engine, mock_iris = self._make_engine_with_mock_iris(nkg_populated=True)
        with patch.object(engine, "_iris_obj", return_value=mock_iris):
            result = engine._try_khop_fast_path(
                "MATCH (n {node_id: $x})-[*1..5]->(m) RETURN m.node_id",
                {"x": "src"},
            )
        assert result is not None

    def test_existing_1hop_pattern_still_works(self):
        """Regression: existing 1-hop exact pattern still routes via original path."""
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = []
        engine = IRISGraphEngine(conn, embedding_dimension=4)
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = "3"
        with patch.object(engine, "_iris_obj", return_value=mock_iris):
            result = engine._try_khop_fast_path(
                "MATCH (n {node_id: $x})-[:R]->(m) RETURN count(m) AS cnt",
                {"x": "src"},
            )
        assert result is not None
        assert result.columns == ["cnt"]
        assert result.rows == [(3,)]


class TestKhopFastPathE2E:
    """T193-10: E2E test against live ivg-iris with NKG built."""

    @pytest.mark.skipif(SKIP_IRIS_TESTS, reason="IRIS not available")
    def test_khop_fast_path_count_matches_bfs_count(self, iris_connection, iris_master_cleanup):
        """NKG fast-path and BFS path return same node count for 3-hop query."""
        import iris as _iris
        from iris_vector_graph.engine import IRISGraphEngine

        iris_obj = _iris.createIRIS(iris_connection)
        nodes = _load_test_graph(iris_connection, iris_obj, n_nodes=30, n_edges=100)

        # Find highest-degree node as seed
        deg = {}
        cursor = iris_connection.cursor()
        cursor.execute("SELECT s, o_id FROM Graph_KG.rdf_edges")
        for s, o in cursor.fetchall():
            deg[s] = deg.get(s, 0) + 1
            deg[o] = deg.get(o, 0) + 1
        seed = max(deg, key=lambda k: deg[k])

        # BFS path count (ground truth) — unique destinations excluding the seed itself
        bfs_raw = json.loads(str(iris_obj.classMethodValue(
            "Graph.KG.Traversal", "BFSFastJson", seed, "", 3, "",
        )))
        bfs_count = len({r["o"] for r in bfs_raw} - {seed})

        # NKG fast-path via execute_cypher
        engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        result = engine.execute_cypher(
            "MATCH (n {node_id: $x})-[*1..3]->(m) RETURN m.node_id",
            parameters={"x": seed},
        )
        nkg_count = len(result.rows)

        assert nkg_count == bfs_count, (
            f"NKG fast-path returned {nkg_count}, BFS returned {bfs_count} for seed={seed}"
        )
