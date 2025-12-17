"""
Contract tests for kg_PERSONALIZED_PAGERANK API.

Tests the API contract defined in specs/005-bidirectional-ppr/contracts/kg_personalized_pagerank.md

TDD: These tests should FAIL until implementation is complete.
"""

import pytest
from typing import Dict


# Mark all tests as requiring live database
pytestmark = pytest.mark.requires_database


class TestPPRContractSignature:
    """Test API contract signature compliance."""

    @pytest.fixture
    def engine(self, iris_connection):
        """Get IRISGraphEngine instance."""
        from iris_vector_graph import IRISGraphEngine
        return IRISGraphEngine(iris_connection)

    def test_method_exists(self, engine):
        """Contract: kg_PERSONALIZED_PAGERANK method exists on IRISGraphEngine."""
        assert hasattr(engine, 'kg_PERSONALIZED_PAGERANK')
        assert callable(getattr(engine, 'kg_PERSONALIZED_PAGERANK'))

    def test_returns_dict(self, iris_connection, engine):
        """Contract: Returns Dict[str, float] mapping entity_id to score."""
        cursor = iris_connection.cursor()

        # Setup minimal test data
        try:
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_TEST:%' OR o_id LIKE 'CONTRACT_TEST:%'")
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_TEST:%'")
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_TEST:A'])
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_TEST:B'])
            cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                          ['CONTRACT_TEST:A', 'connects', 'CONTRACT_TEST:B'])
            iris_connection.commit()

            # Execute
            result = engine.kg_PERSONALIZED_PAGERANK(seed_entities=['CONTRACT_TEST:A'])

            # Verify return type
            assert isinstance(result, dict), "Should return a dictionary"
            for key, value in result.items():
                assert isinstance(key, str), "Keys should be strings (entity IDs)"
                assert isinstance(value, float), "Values should be floats (scores)"

        finally:
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_TEST:%' OR o_id LIKE 'CONTRACT_TEST:%'")
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_TEST:%'")
            iris_connection.commit()


class TestPPRContractParameters:
    """Test API contract parameter handling."""

    @pytest.fixture
    def engine(self, iris_connection):
        """Get IRISGraphEngine instance."""
        from iris_vector_graph import IRISGraphEngine
        return IRISGraphEngine(iris_connection)

    def test_seed_entities_required(self, engine):
        """Contract: seed_entities parameter is required."""
        with pytest.raises(TypeError):
            engine.kg_PERSONALIZED_PAGERANK()  # Missing required argument

    def test_empty_seed_entities_raises_error(self, engine):
        """Contract: Empty seed_entities raises ValueError."""
        with pytest.raises(ValueError, match="at least one entity"):
            engine.kg_PERSONALIZED_PAGERANK(seed_entities=[])

    def test_negative_reverse_weight_raises_error(self, engine, iris_connection):
        """Contract: Negative reverse_edge_weight raises ValueError."""
        # Setup minimal data
        cursor = iris_connection.cursor()
        try:
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_TEST:%'")
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_TEST:A'])
            iris_connection.commit()

            with pytest.raises(ValueError, match="non-negative"):
                engine.kg_PERSONALIZED_PAGERANK(
                    seed_entities=['CONTRACT_TEST:A'],
                    bidirectional=True,
                    reverse_edge_weight=-0.5,
                )
        finally:
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_TEST:%'")
            iris_connection.commit()

    def test_default_parameters(self, engine, iris_connection):
        """Contract: Default parameters match specification."""
        # Setup minimal data
        cursor = iris_connection.cursor()
        try:
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_TEST:%'")
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_TEST:A'])
            iris_connection.commit()

            # Should not raise with only seed_entities
            result = engine.kg_PERSONALIZED_PAGERANK(seed_entities=['CONTRACT_TEST:A'])
            assert isinstance(result, dict)

        finally:
            cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_TEST:%'")
            iris_connection.commit()


class TestPPRContractBidirectional:
    """Test bidirectional parameter contract."""

    @pytest.fixture
    def engine(self, iris_connection):
        """Get IRISGraphEngine instance."""
        from iris_vector_graph import IRISGraphEngine
        return IRISGraphEngine(iris_connection)

    @pytest.fixture
    def setup_directional_graph(self, iris_connection):
        """Create graph: A -> B (unidirectional)."""
        cursor = iris_connection.cursor()

        cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_DIR:%' OR o_id LIKE 'CONTRACT_DIR:%'")
        cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_DIR:%'")

        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_DIR:A'])
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_DIR:B'])
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                      ['CONTRACT_DIR:A', 'connects', 'CONTRACT_DIR:B'])
        iris_connection.commit()

        yield

        cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_DIR:%' OR o_id LIKE 'CONTRACT_DIR:%'")
        cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_DIR:%'")
        iris_connection.commit()

    def test_bidirectional_false_is_default(self, engine, setup_directional_graph):
        """Contract: bidirectional=False is the default (backward compatible)."""
        # From B, should NOT reach A without bidirectional
        scores = engine.kg_PERSONALIZED_PAGERANK(seed_entities=['CONTRACT_DIR:B'])

        # A should not be reachable or have zero score
        a_score = scores.get('CONTRACT_DIR:A', 0)
        assert a_score == 0, "A should not be reachable with forward-only traversal"

    def test_bidirectional_true_enables_reverse(self, engine, setup_directional_graph):
        """Contract: bidirectional=True enables reverse edge traversal."""
        # From B, SHOULD reach A with bidirectional
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_DIR:B'],
            bidirectional=True,
            reverse_edge_weight=1.0,
        )

        # A should be reachable via reverse edge
        assert 'CONTRACT_DIR:A' in scores, "A should be reachable via reverse edge"
        assert scores['CONTRACT_DIR:A'] > 0, "A should have positive score"


class TestPPRContractReverseWeight:
    """Test reverse_edge_weight parameter contract."""

    @pytest.fixture
    def engine(self, iris_connection):
        """Get IRISGraphEngine instance."""
        from iris_vector_graph import IRISGraphEngine
        return IRISGraphEngine(iris_connection)

    @pytest.fixture
    def setup_weighted_graph(self, iris_connection):
        """Create simple A -> B graph for weight testing."""
        cursor = iris_connection.cursor()

        cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_WT:%' OR o_id LIKE 'CONTRACT_WT:%'")
        cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_WT:%'")

        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_WT:A'])
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_WT:B'])
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                      ['CONTRACT_WT:A', 'connects', 'CONTRACT_WT:B'])
        iris_connection.commit()

        yield

        cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_WT:%' OR o_id LIKE 'CONTRACT_WT:%'")
        cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_WT:%'")
        iris_connection.commit()

    def test_weight_1_0_full_contribution(self, engine, setup_weighted_graph):
        """Contract: reverse_edge_weight=1.0 gives full reverse edge contribution."""
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_WT:B'],
            bidirectional=True,
            reverse_edge_weight=1.0,
        )

        assert 'CONTRACT_WT:A' in scores
        assert scores['CONTRACT_WT:A'] > 0

    def test_weight_0_0_no_contribution(self, engine, setup_weighted_graph):
        """Contract: reverse_edge_weight=0.0 gives no reverse edge contribution."""
        scores_weight_zero = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_WT:B'],
            bidirectional=True,
            reverse_edge_weight=0.0,
        )

        scores_forward = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_WT:B'],
            bidirectional=False,
        )

        # Results should be equivalent
        assert scores_weight_zero.get('CONTRACT_WT:A', 0) == scores_forward.get('CONTRACT_WT:A', 0)

    def test_reduced_weight_reduces_score(self, engine, setup_weighted_graph):
        """Contract: Lower weight results in lower reverse edge contribution."""
        scores_full = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_WT:B'],
            bidirectional=True,
            reverse_edge_weight=1.0,
        )

        scores_half = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_WT:B'],
            bidirectional=True,
            reverse_edge_weight=0.5,
        )

        # Half weight should give lower score
        assert scores_half.get('CONTRACT_WT:A', 0) < scores_full.get('CONTRACT_WT:A', 0)


class TestPPRContractBackwardCompatibility:
    """Test backward compatibility with existing code."""

    @pytest.fixture
    def engine(self, iris_connection):
        """Get IRISGraphEngine instance."""
        from iris_vector_graph import IRISGraphEngine
        return IRISGraphEngine(iris_connection)

    @pytest.fixture
    def setup_simple_graph(self, iris_connection):
        """Create simple graph for backward compatibility testing."""
        cursor = iris_connection.cursor()

        cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_BC:%' OR o_id LIKE 'CONTRACT_BC:%'")
        cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_BC:%'")

        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_BC:A'])
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['CONTRACT_BC:B'])
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                      ['CONTRACT_BC:A', 'connects', 'CONTRACT_BC:B'])
        iris_connection.commit()

        yield

        cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'CONTRACT_BC:%' OR o_id LIKE 'CONTRACT_BC:%'")
        cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'CONTRACT_BC:%'")
        iris_connection.commit()

    def test_works_without_new_parameters(self, engine, setup_simple_graph):
        """Contract: Existing code without bidirectional/reverse_edge_weight still works."""
        # This simulates existing code that doesn't know about new parameters
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_BC:A'],
            damping_factor=0.85,
            max_iterations=10,
        )

        assert isinstance(scores, dict)
        # B should be reachable via forward edge
        assert 'CONTRACT_BC:B' in scores or len(scores) > 0

    def test_reverse_weight_ignored_when_bidirectional_false(self, engine, setup_simple_graph):
        """Contract: reverse_edge_weight is ignored when bidirectional=False."""
        scores_with_weight = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_BC:A'],
            bidirectional=False,
            reverse_edge_weight=99.0,  # Should be ignored
        )

        scores_without = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=['CONTRACT_BC:A'],
            bidirectional=False,
        )

        # Results should be identical
        assert scores_with_weight == scores_without
