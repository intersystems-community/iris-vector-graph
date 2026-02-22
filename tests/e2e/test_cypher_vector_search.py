"""
E2E tests for CALL ivg.vector.search(...) YIELD node, score

Full round-trip tests via IRISGraphEngine.execute_cypher(), exercising the
complete stack: Cypher parser → SQL translator → IRIS execution → node hydration.

Container management: iris-devtester manages the 'iris_vector_graph' named container.
Port resolution: IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)
SKIP_IRIS_TESTS defaults to "false" — tests always hit live IRIS.

Constitution Principle IV: mandatory e2e coverage, no hardcoded ports.
"""

import json
import os
import time
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_PREFIX = "ivg_e2e_vs:"


@pytest.fixture(scope="module")
def iris_engine():
    """Connect to the iris_vector_graph container and return an IRISGraphEngine instance.

    Uses iris-devtester for port resolution — no hardcoded ports.
    """
    try:
        from iris_devtester import IRISContainer
    except ImportError:
        pytest.skip("iris-devtester not installed")

    try:
        container = IRISContainer.attach("iris_vector_graph")
        conn = container.get_connection()
    except Exception as e:
        pytest.skip(f"Could not attach to iris_vector_graph container: {e}")

    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(conn, embedding_dimension=3)

    # Ensure schema exists
    try:
        engine.initialize_schema()
    except Exception:
        pass

    yield engine

    conn.close()


@pytest.fixture(scope="module")
def vector_test_nodes(iris_engine):
    """Seed Gene and Drug nodes with 3-d embeddings for e2e vector search tests.

    gene-a: [1.0, 0.0, 0.0]  — most similar to query [1,0,0]
    gene-b: [0.9, 0.1, 0.0]  — second most similar
    gene-c: [0.0, 1.0, 0.0]  — least similar
    drug-a: [0.8, 0.2, 0.0]  — Drug label
    """
    conn = iris_engine.conn
    cursor = conn.cursor()

    nodes = [
        (f"{TEST_PREFIX}gene-a", "Gene", [1.0, 0.0, 0.0]),
        (f"{TEST_PREFIX}gene-b", "Gene", [0.9, 0.1, 0.0]),
        (f"{TEST_PREFIX}gene-c", "Gene", [0.0, 1.0, 0.0]),
        (f"{TEST_PREFIX}drug-a", "Drug", [0.8, 0.2, 0.0]),
    ]

    from iris_vector_graph.cypher.translator import _table

    try:
        for node_id, label, vec in nodes:
            try:
                cursor.execute(
                    f"INSERT INTO {_table('nodes')} (node_id) VALUES (?)", [node_id]
                )
            except Exception:
                pass
            try:
                cursor.execute(
                    f"INSERT INTO {_table('rdf_labels')} (s, label) VALUES (?, ?)",
                    [node_id, label],
                )
            except Exception:
                pass
            vec_json = json.dumps(vec)
            try:
                # NOTE: kg_NodeEmbeddings uses 'id' as PK column (not 'node_id')
                cursor.execute(
                    f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb) "
                    f"SELECT ?, TO_VECTOR(?) WHERE NOT EXISTS "
                    f"(SELECT 1 FROM {_table('kg_NodeEmbeddings')} WHERE id = ?)",
                    [node_id, vec_json, node_id],
                )
            except Exception:
                pass
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # Create HNSW index (idempotent — ignore if already exists)
    try:
        cursor.execute(
            f"CREATE INDEX ivg_e2e_vs_hnsw ON {_table('kg_NodeEmbeddings')} (emb) USING HNSW"
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    yield nodes

    # Cleanup
    node_ids = [n[0] for n in nodes]
    try:
        for node_id in node_ids:
            cursor.execute(
                f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id]
            )
            cursor.execute(
                f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [node_id]
            )
            cursor.execute(
                f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [node_id]
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVectorSearchE2E:
    def test_execute_cypher_returns_results(self, iris_engine, vector_test_nodes):
        """execute_cypher() with CALL returns at least one row."""
        result = iris_engine.execute_cypher(
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 3) "
            "YIELD node, score RETURN node, score"
        )
        assert "rows" in result
        assert len(result["rows"]) > 0

    def test_results_are_ordered_by_score(self, iris_engine, vector_test_nodes):
        """Scores must be in descending order."""
        result = iris_engine.execute_cypher(
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 3) "
            "YIELD node, score RETURN node, score"
        )
        score_col = result["columns"].index("score")
        scores = [float(row[score_col]) for row in result["rows"]]
        assert scores == sorted(scores, reverse=True), f"Out of order: {scores}"

    def test_label_filter_only_returns_genes(self, iris_engine, vector_test_nodes):
        """Gene search must not return Drug nodes."""
        result = iris_engine.execute_cypher(
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 5) "
            "YIELD node, score RETURN node, score"
        )
        node_col = result["columns"].index("node_id")
        node_ids = [row[node_col] for row in result["rows"]]
        drug_ids = [nid for nid in node_ids if nid.startswith(f"{TEST_PREFIX}drug")]
        assert drug_ids == [], f"Drug nodes appeared in Gene search: {drug_ids}"

    def test_limit_caps_result_count(self, iris_engine, vector_test_nodes):
        """Result count must not exceed limit=2."""
        result = iris_engine.execute_cypher(
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 2) "
            "YIELD node, score RETURN node, score"
        )
        assert len(result["rows"]) <= 2

    def test_nearest_node_is_gene_a(self, iris_engine, vector_test_nodes):
        """The closest node to [1,0,0] among Gene nodes must be gene-a."""
        result = iris_engine.execute_cypher(
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 1) "
            "YIELD node, score RETURN node, score"
        )
        assert len(result["rows"]) >= 1
        node_col = result["columns"].index("node_id")
        top_node = result["rows"][0][node_col]
        assert top_node == f"{TEST_PREFIX}gene-a", (
            f"Expected gene-a as nearest, got {top_node}"
        )

    def test_mode2_raises_if_embedding_not_available(self, iris_engine, vector_test_nodes):
        """Mode 2 (text input) raises RuntimeError on IRIS instances without EMBEDDING()."""
        # Force probe to check; if EMBEDDING() IS available this test is a no-op
        if iris_engine._probe_embedding_support():
            pytest.skip("IRIS EMBEDDING() function is available; Mode 2 not testable as error path")
        with pytest.raises(RuntimeError, match="EMBEDDING"):
            iris_engine.execute_cypher(
                "CALL ivg.vector.search('Gene', 'embedding', 'flu symptoms', 5, "
                "{embedding_config: 'my_config'}) YIELD node, score RETURN node, score"
            )

    def test_performance_under_100ms(self, iris_engine, vector_test_nodes):
        """Vector search for top-3 genes must complete in under 100ms."""
        start = time.perf_counter()
        iris_engine.execute_cypher(
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 3) "
            "YIELD node, score RETURN node, score"
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"Vector search took {elapsed_ms:.1f}ms (expected <100ms)"
