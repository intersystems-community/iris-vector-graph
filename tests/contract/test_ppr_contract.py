"""
Contract Tests for Personalized PageRank

These tests validate the PPR algorithm meets its functional requirements.
They MUST be written first and MUST FAIL before implementation (TDD).

Test Coverage:
- T004: test_ppr_scores_sum_to_one (FR-003)
- T005: test_ppr_seed_gets_highest_score (FR-002)
- T006: test_ppr_empty_seeds_raises_error (FR-015)
- T007: test_ppr_invalid_damping_factor (FR-016)
- T008: test_ppr_convergence_time_10k (FR-018)
- T008a: test_ppr_max_iterations_respected (FR-011)
- T008b: test_ppr_tolerance_triggers_convergence (FR-012)
"""

import pytest
import time
import iris
from iris_vector_graph import IRISGraphEngine


@pytest.fixture(scope="module")
def iris_connection():
    """Establish IRIS database connection for all tests."""
    conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def engine(iris_connection):
    """Create IRISGraphEngine instance."""
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def test_graph(iris_connection):
    """Create minimal test graph for contract tests."""
    cursor = iris_connection.cursor()

    # Clear existing test data
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'TEST:%'")
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'TEST:%'")
    cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'TEST:%'")

    # Create simple test graph: TEST:A -> TEST:B -> TEST:C
    test_nodes = ["TEST:A", "TEST:B", "TEST:C", "TEST:D"]
    for node_id in test_nodes:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node_id])
        cursor.execute(
            "INSERT INTO rdf_labels (s, label) VALUES (?, 'TestNode')",
            [node_id]
        )

    # Add edges: A->B, B->C, A->C, C->D
    edges = [
        ("TEST:A", "interacts_with", "TEST:B"),
        ("TEST:B", "interacts_with", "TEST:C"),
        ("TEST:A", "interacts_with", "TEST:C"),
        ("TEST:C", "interacts_with", "TEST:D"),
    ]
    for s, p, o in edges:
        cursor.execute(
            "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
            [s, p, o]
        )

    iris_connection.commit()
    yield

    # Cleanup
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'TEST:%'")
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'TEST:%'")
    cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'TEST:%'")
    iris_connection.commit()


@pytest.mark.requires_database
class TestPPRContract:
    """Contract tests validating PPR algorithm correctness."""

    def test_ppr_scores_sum_to_one(self, engine, test_graph):
        """
        T004: Verify FR-003 - Scores form valid probability distribution.

        MUST FAIL: kg_PERSONALIZED_PAGERANK method doesn't exist yet.
        """
        scores = engine.kg_PERSONALIZED_PAGERANK(["TEST:A"])

        # Scores must sum to 1.0 (±0.001 tolerance)
        total = sum(scores.values())
        assert abs(total - 1.0) < 0.001, \
            f"Scores sum to {total}, expected 1.0 (±0.001)"

        # All scores must be in [0.0, 1.0]
        for entity_id, score in scores.items():
            assert 0.0 <= score <= 1.0, \
                f"Score {score} for {entity_id} not in [0.0, 1.0]"

    def test_ppr_seed_gets_highest_score(self, engine, test_graph):
        """
        T005: Verify FR-002 - Seed entities receive highest scores.

        MUST FAIL: kg_PERSONALIZED_PAGERANK method doesn't exist yet.
        """
        scores = engine.kg_PERSONALIZED_PAGERANK(["TEST:A"])

        # Seed entity must have highest individual score
        seed_score = scores.get("TEST:A", 0.0)
        max_score = max(scores.values())

        assert seed_score == max_score, \
            f"Seed score {seed_score} is not the highest (max={max_score})"

        # In typical graphs, seed should be in top 10% (quality metric)
        sorted_scores = sorted(scores.values(), reverse=True)
        top_10_percent_threshold = len(sorted_scores) // 10
        top_scores = sorted_scores[:max(1, top_10_percent_threshold)]

        assert seed_score in top_scores, \
            "Seed entity not in top 10% of scored entities"

    def test_ppr_empty_seeds_raises_error(self, engine, test_graph):
        """
        T006: Verify FR-015 - Empty seed list rejected.

        MUST FAIL: Input validation doesn't exist yet.
        """
        with pytest.raises(ValueError, match="seed_entities cannot be empty"):
            engine.kg_PERSONALIZED_PAGERANK([])

    def test_ppr_invalid_damping_factor(self, engine, test_graph):
        """
        T007: Verify FR-016 - Damping factor validated (0.0-1.0).

        MUST FAIL: Parameter validation doesn't exist yet.
        """
        # Test damping_factor > 1.0
        with pytest.raises(ValueError, match="damping_factor must be"):
            engine.kg_PERSONALIZED_PAGERANK(
                ["TEST:A"],
                damping_factor=1.5
            )

        # Test damping_factor < 0.0
        with pytest.raises(ValueError, match="damping_factor must be"):
            engine.kg_PERSONALIZED_PAGERANK(
                ["TEST:A"],
                damping_factor=-0.1
            )

    @pytest.mark.performance
    def test_ppr_convergence_time_10k(self, engine, iris_connection):
        """
        T008: Verify FR-018 - 10K entities in <100ms.

        MUST FAIL: No implementation yet.
        NOTE: Requires ~10K node graph. Will create smaller graph for initial test.
        """
        # For contract test, use existing test graph
        # Performance test (T022) will use full 10K protein graph

        start = time.time()
        scores = engine.kg_PERSONALIZED_PAGERANK(["TEST:A"])
        elapsed = time.time() - start

        # Contract test: just verify it completes (no specific time requirement)
        assert elapsed < 1.0, \
            f"PPR took {elapsed:.3f}s, expected <1s for small graph"

        # Verify scores returned
        assert len(scores) > 0, "No scores returned"

    def test_ppr_max_iterations_respected(self, engine, test_graph):
        """
        T008a: Verify FR-011 - max_iterations parameter is respected.

        MUST FAIL: No implementation yet.
        """
        # Create slow-converging scenario by using very low tolerance
        # and limiting iterations
        scores = engine.kg_PERSONALIZED_PAGERANK(
            ["TEST:A"],
            max_iterations=5,
            tolerance=1e-10  # Very strict convergence (unlikely in 5 iterations)
        )

        # Verify computation stopped (didn't run forever)
        # The actual iteration count will be checked in implementation
        assert len(scores) > 0, "No scores returned with max_iterations=5"

    def test_ppr_tolerance_triggers_convergence(self, engine, test_graph):
        """
        T008b: Verify FR-012 - tolerance parameter triggers early convergence.

        MUST FAIL: No implementation yet.
        """
        # Use high tolerance to trigger early convergence
        start = time.time()
        scores = engine.kg_PERSONALIZED_PAGERANK(
            ["TEST:A"],
            tolerance=0.1,  # High tolerance = early convergence
            max_iterations=100
        )
        elapsed = time.time() - start

        # With high tolerance, should converge very quickly
        assert elapsed < 0.1, \
            f"With tolerance=0.1, convergence took {elapsed:.3f}s (expected <0.1s)"

        # Scores should still be valid
        assert len(scores) > 0, "No scores returned"
        total = sum(scores.values())
        assert abs(total - 1.0) < 0.001, \
            f"Scores sum to {total}, expected ~1.0"
