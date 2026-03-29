"""E2E tests for PLAID multi-vector retrieval against live IRIS."""
import math
import os
import random
import time
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

DIM = 32
INDEX = "plaid_e2e"


def _make_docs(n_docs=50, n_tokens=10, dim=DIM):
    random.seed(42)
    return [{"id": f"doc_{i}", "tokens": [[random.gauss(0, 1) for _ in range(dim)] for _ in range(n_tokens)]} for i in range(n_docs)]


def _brute_maxsim(query_tokens, doc_tokens):
    score = 0.0
    for qt in query_tokens:
        max_dot = -999999
        for dt in doc_tokens:
            dot = sum(a * b for a, b in zip(qt, dt))
            if dot > max_dot:
                max_dot = dot
        score += max_dot
    return score


class TestPLAIDSearchE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(self.conn, embedding_dimension=DIM)
        try:
            self.engine.plaid_drop(INDEX)
        except Exception:
            pass
        yield
        try:
            self.engine.plaid_drop(INDEX)
        except Exception:
            pass

    def test_build_creates_valid_index(self):
        """T029"""
        docs = _make_docs(50, 10, DIM)
        result = self.engine.plaid_build(INDEX, docs, dim=DIM)
        assert result["nDocs"] == 50
        assert result["nCentroids"] > 0
        assert result["totalTokens"] == 500

    def test_search_returns_expected_top_result(self):
        """T030"""
        docs = _make_docs(50, 10, DIM)
        self.engine.plaid_build(INDEX, docs, dim=DIM)
        query_tokens = docs[0]["tokens"][:4]
        results = self.engine.plaid_search(INDEX, query_tokens, k=5)
        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert "doc_0" in ids

    def test_search_recall_vs_brute_force(self):
        """T031"""
        docs = _make_docs(50, 10, DIM)
        self.engine.plaid_build(INDEX, docs, dim=DIM)
        random.seed(99)
        query_tokens = [[random.gauss(0, 1) for _ in range(DIM)] for _ in range(4)]
        gt_scores = [(d["id"], _brute_maxsim(query_tokens, d["tokens"])) for d in docs]
        gt_scores.sort(key=lambda x: -x[1])
        gt_top10 = set(x[0] for x in gt_scores[:10])
        results = self.engine.plaid_search(INDEX, query_tokens, k=10, nprobe=8)
        plaid_top10 = set(r["id"] for r in results[:10])
        recall = len(gt_top10 & plaid_top10) / 10.0
        assert recall >= 0.5, f"Recall@10={recall:.0%}, expected ≥50%"

    def test_search_latency(self):
        """T032"""
        docs = _make_docs(50, 10, DIM)
        self.engine.plaid_build(INDEX, docs, dim=DIM)
        query_tokens = [[random.gauss(0, 1) for _ in range(DIM)] for _ in range(4)]
        self.engine.plaid_search(INDEX, query_tokens, k=5)
        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            self.engine.plaid_search(INDEX, query_tokens, k=5)
            times.append((time.perf_counter() - t0) * 1000)
        median = sorted(times)[5]
        assert median < 100, f"Median latency {median:.1f}ms, expected <100ms"

    def test_insert_makes_doc_searchable(self):
        """T033"""
        docs = _make_docs(20, 10, DIM)
        self.engine.plaid_build(INDEX, docs, dim=DIM)
        random.seed(123)
        new_tokens = [[random.gauss(0, 1) for _ in range(DIM)] for _ in range(10)]
        self.engine.plaid_insert(INDEX, "inserted_doc", new_tokens)
        results = self.engine.plaid_search(INDEX, new_tokens[:4], k=10)
        ids = [r["id"] for r in results]
        assert "inserted_doc" in ids
