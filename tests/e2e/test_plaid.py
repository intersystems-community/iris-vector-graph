import random
import pytest
from iris_vector_graph.engine import IRISGraphEngine


DIM = 16
N_DOCS = 6
N_TOKENS = 4


@pytest.fixture(scope="module")
def engine(iris_connection):
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def plaid_docs():
    rng = random.Random(77)
    return [
        {"id": f"plaid_doc_{i}", "tokens": [[rng.gauss(0, 1) for _ in range(DIM)] for _ in range(N_TOKENS)]}
        for i in range(N_DOCS)
    ]


@pytest.fixture(scope="module")
def built_index(engine, plaid_docs):
    engine.plaid_drop("plaid_test")
    info = engine.plaid_build("plaid_test", plaid_docs, n_clusters=3, dim=DIM)
    yield info
    engine.plaid_drop("plaid_test")


@pytest.mark.requires_database
@pytest.mark.e2e
class TestPlaid:
    def test_plaid_build_and_search(self, engine, built_index, plaid_docs):
        assert built_index.get("indexed", 0) == N_DOCS

    def test_plaid_search_returns_ranked_results(self, engine, built_index):
        rng = random.Random(88)
        query = [[rng.gauss(0, 1) for _ in range(DIM)] for _ in range(N_TOKENS)]
        results = engine.plaid_search("plaid_test", query, k=3)
        assert len(results) <= 3
        assert len(results) > 0
        scores = [r[1] if isinstance(r, (list, tuple)) else r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_plaid_insert_appears_in_search(self, engine, built_index):
        rng = random.Random(99)
        new_doc_id = "plaid_new"
        tokens = [[rng.gauss(0, 1) for _ in range(DIM)] for _ in range(N_TOKENS)]
        info_before = engine.plaid_info("plaid_test")
        engine.plaid_insert("plaid_test", new_doc_id, tokens)
        info_after = engine.plaid_info("plaid_test")
        assert info_after.get("indexed", 0) == info_before.get("indexed", 0) + 1
        results = engine.plaid_search("plaid_test", tokens, k=N_DOCS + 1)
        ids = [r[0] if isinstance(r, (list, tuple)) else r["id"] for r in results]
        assert new_doc_id in ids

    def test_plaid_info_returns_type_and_counts(self, engine, built_index):
        info = engine.plaid_info("plaid_test")
        assert info.get("type") == "plaid"
        assert "indexed" in info
        assert "dim" in info
        assert "nlist" in info

    def test_plaid_drop_removes_all_data(self, engine):
        engine.plaid_build("plaid_drop_test", [
            {"id": "d1", "tokens": [[0.1] * DIM] * N_TOKENS}
        ], n_clusters=1, dim=DIM)
        engine.plaid_drop("plaid_drop_test")
        info = engine.plaid_info("plaid_drop_test")
        assert info == {}
