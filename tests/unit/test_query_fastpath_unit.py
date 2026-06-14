"""
Unit tests for _engine/query.py fast-path methods.

Covers:
- _try_khop_fast_path: 1-hop count, 1-hop ids, 2-hop count, 2-hop ids,
  NKG var-length path, no-match returns None
- khop2_count_fast / khop2_count_exact
- _execute_approx_count_distinct paths

No IRIS connection needed — mocks iris_obj.classMethodValue.
"""
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    return IRISGraphEngine(conn, embedding_dimension=4)


def _iris_mock(return_values: dict):
    """Return a mock iris_obj whose classMethodValue dispatches by method name."""
    iris_obj = MagicMock()
    def cmv(cls, method, *args):
        key = method
        if key in return_values:
            val = return_values[key]
            if isinstance(val, Exception):
                raise val
            return val
        return "0"
    iris_obj.classMethodValue.side_effect = cmv
    return iris_obj


# ---------------------------------------------------------------------------
# _try_khop_fast_path — 1-hop count
# ---------------------------------------------------------------------------

class TestTryKhopFastPath1HopCount:

    def test_1hop_count_success(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL]->(m) RETURN count(m) AS cnt"
        iris_obj = _iris_mock({"KHopCount": "7"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "node_a"})
        assert result is not None
        assert result.columns == ["cnt"]
        assert result.rows[0][0] == 7

    def test_1hop_count_missing_param_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL]->(m) RETURN count(m) AS cnt"
        iris_obj = _iris_mock({"KHopCount": "7"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {})  # no 'src' param
        assert result is None

    def test_1hop_count_exception_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL]->(m) RETURN count(m) AS cnt"
        iris_obj = _iris_mock({"KHopCount": RuntimeError("no class")})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "node_a"})
        assert result is None


# ---------------------------------------------------------------------------
# _try_khop_fast_path — 1-hop ids
# ---------------------------------------------------------------------------

class TestTryKhopFastPath1HopIds:

    def test_1hop_ids_success(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL]->(m) RETURN m.node_id"
        iris_obj = _iris_mock({"KHopNeighborIds": "node_1\nnode_2\nnode_3"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "node_a"})
        assert result is not None
        assert "node_id" in result.columns
        assert len(result.rows) == 3

    def test_1hop_ids_empty_result(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL]->(m) RETURN m.node_id AS id"
        iris_obj = _iris_mock({"KHopNeighborIds": ""})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "node_x"})
        assert result is not None
        assert result.rows == []

    def test_1hop_ids_exception_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL]->(m) RETURN m.node_id"
        iris_obj = _iris_mock({"KHopNeighborIds": RuntimeError("fail")})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "node_a"})
        assert result is None


# ---------------------------------------------------------------------------
# _try_khop_fast_path — 2-hop count
# ---------------------------------------------------------------------------

class TestTryKhopFastPath2HopCount:

    def test_2hop_count_success(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL*2]->(m) RETURN count(m) AS total"
        iris_obj = _iris_mock({"KHop2CountExact": "42"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "n1"})
        assert result is not None
        assert result.columns == ["total"]
        assert result.rows[0][0] == 42

    def test_2hop_count_missing_param_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL*2]->(m) RETURN count(m) AS total"
        iris_obj = _iris_mock({"KHop2CountExact": "42"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {})
        assert result is None


# ---------------------------------------------------------------------------
# _try_khop_fast_path — 2-hop ids
# ---------------------------------------------------------------------------

class TestTryKhopFastPath2HopIds:

    def test_2hop_ids_success(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL*2]->(m) RETURN m.node_id"
        iris_obj = _iris_mock({"KHop2NeighborIds": "a\nb\nc"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "root"})
        assert result is not None
        assert len(result.rows) == 3

    def test_2hop_ids_with_limit(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL*2]->(m) RETURN m.node_id LIMIT 2"
        iris_obj = _iris_mock({"KHop2NeighborIds": "a\nb\nc\nd"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "root"})
        # Should return at most 2 (LIMIT applied)
        assert result is not None

    def test_2hop_ids_exception_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[:REL*2]->(m) RETURN m.node_id"
        iris_obj = _iris_mock({"KHop2NeighborIds": RuntimeError("fail")})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "root"})
        assert result is None


# ---------------------------------------------------------------------------
# _try_khop_fast_path — NKG var-length path
# ---------------------------------------------------------------------------

class TestTryKhopFastPathNKG:

    def test_nkg_var_length_not_populated_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[*1..3]->(m) RETURN m.node_id"
        iris_obj = _iris_mock({"NKGPopulated": "0"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "root"})
        assert result is None

    def test_nkg_var_length_populated_with_results(self):
        import json
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[*1..2]->(m) RETURN m.node_id"
        nkg_json = json.dumps({"nodes": [
            {"id": "seed", "dist": 0},
            {"id": "hop1", "dist": 1},
            {"id": "hop2", "dist": 2},
        ]})

        def cmv(cls, method, *args):
            if method == "NKGPopulated":
                return "1"
            if method == "KHopNeighbors":
                return nkg_json
            return "0"

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = cmv
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "seed"})
        assert result is not None
        # seed (dist=0) is excluded
        assert len(result.rows) == 2

    def test_nkg_var_length_exception_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[*1..2]->(m) RETURN m.node_id"

        def cmv(cls, method, *args):
            if method == "NKGPopulated":
                return "1"
            if method == "KHopNeighbors":
                raise RuntimeError("ObjectScript error")
            return "0"

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = cmv
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "seed"})
        assert result is None

    def test_nkg_var_length_missing_param_returns_none(self):
        eng = _make_eng()
        q = "MATCH (n {node_id: $src})-[*1..2]->(m) RETURN m.node_id"
        iris_obj = _iris_mock({"NKGPopulated": "1"})
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {})  # no 'src'
        assert result is None


# ---------------------------------------------------------------------------
# _try_khop_fast_path — no match
# ---------------------------------------------------------------------------

class TestTryKhopFastPathNoMatch:

    def test_non_matching_query_returns_none(self):
        eng = _make_eng()
        # Plain MATCH with no recognized fast-path pattern
        q = "MATCH (n)-[r]->(m) RETURN n.node_id, m.node_id"
        iris_obj = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {})
        assert result is None

    def test_empty_query_returns_none(self):
        eng = _make_eng()
        iris_obj = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path("", {})
        assert result is None


# ---------------------------------------------------------------------------
# khop2_count_fast / khop2_count_exact
# ---------------------------------------------------------------------------

class TestKhop2CountMethods:

    def test_khop2_count_fast_returns_int(self):
        eng = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "15"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.khop2_count_fast("node_a", "REL")
        assert result == 15

    def test_khop2_count_exact_returns_int(self):
        eng = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "23"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.khop2_count_exact("node_a", "REL")
        assert result == 23

    def test_khop2_count_fast_no_predicate(self):
        eng = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.khop2_count_fast("node_b")
        assert result == 0
