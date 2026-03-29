"""E2E tests for VecIndex RP-tree ANN vector search against live IRIS."""
import os
import random
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

INDEX_NAME = "test_vec_e2e"
DIM = 32


def _rand_vec(dim=DIM):
    return [random.gauss(0, 1) for _ in range(dim)]


class TestVecIndex:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(self.conn, embedding_dimension=DIM)
        try:
            self.engine.vec_drop(INDEX_NAME)
        except Exception:
            pass
        yield
        try:
            self.engine.vec_drop(INDEX_NAME)
        except Exception:
            pass

    def test_create_index(self):
        result = self.engine.vec_create_index(INDEX_NAME, DIM, "cosine")
        assert result.get("status") == "created" or "name" in result

    def test_insert_and_info(self):
        self.engine.vec_create_index(INDEX_NAME, DIM, "cosine")
        self.engine.vec_insert(INDEX_NAME, "doc1", _rand_vec())
        self.engine.vec_insert(INDEX_NAME, "doc2", _rand_vec())
        info = self.engine.vec_info(INDEX_NAME)
        assert int(info.get("count", 0)) >= 2

    def test_build_and_search(self):
        self.engine.vec_create_index(INDEX_NAME, DIM, "cosine")
        target = _rand_vec()
        self.engine.vec_insert(INDEX_NAME, "target", target)
        for i in range(20):
            self.engine.vec_insert(INDEX_NAME, f"noise_{i}", _rand_vec())
        build_result = self.engine.vec_build(INDEX_NAME)
        assert "trees" in build_result or "status" in build_result

        results = self.engine.vec_search(INDEX_NAME, target, k=5)
        assert isinstance(results, list)
        assert len(results) >= 1
        ids = [r.get("id") or r.get("doc_id") for r in results]
        assert "target" in ids, f"Expected 'target' in top-5, got {ids}"

    def test_bulk_insert(self):
        self.engine.vec_create_index(INDEX_NAME, DIM, "cosine")
        items = [{"id": f"bulk_{i}", "embedding": _rand_vec()} for i in range(10)]
        count = self.engine.vec_bulk_insert(INDEX_NAME, items)
        assert count == 10
        info = self.engine.vec_info(INDEX_NAME)
        assert int(info.get("count", 0)) >= 10

    def test_drop_index(self):
        self.engine.vec_create_index(INDEX_NAME, DIM, "cosine")
        self.engine.vec_insert(INDEX_NAME, "doc1", _rand_vec())
        self.engine.vec_drop(INDEX_NAME)
        info = self.engine.vec_info(INDEX_NAME)
        assert int(info.get("count", 0)) == 0 or "error" in info

    def test_search_returns_scores(self):
        self.engine.vec_create_index(INDEX_NAME, DIM, "cosine")
        v = _rand_vec()
        self.engine.vec_insert(INDEX_NAME, "scored", v)
        for i in range(10):
            self.engine.vec_insert(INDEX_NAME, f"other_{i}", _rand_vec())
        self.engine.vec_build(INDEX_NAME)
        results = self.engine.vec_search(INDEX_NAME, v, k=3)
        assert len(results) >= 1
        first = results[0]
        assert "score" in first or "distance" in first or "similarity" in first

    def test_l2_metric(self):
        self.engine.vec_create_index(INDEX_NAME, DIM, "l2")
        v = _rand_vec()
        self.engine.vec_insert(INDEX_NAME, "l2_doc", v)
        for i in range(10):
            self.engine.vec_insert(INDEX_NAME, f"l2_noise_{i}", _rand_vec())
        self.engine.vec_build(INDEX_NAME)
        results = self.engine.vec_search(INDEX_NAME, v, k=3)
        assert len(results) >= 1
