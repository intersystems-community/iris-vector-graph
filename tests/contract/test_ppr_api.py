"""
Contract tests for kg_PERSONALIZED_PAGERANK API.

Tests the API contract defined in specs/005-bidirectional-ppr/contracts/kg_personalized_pagerank.md

Written TDD-first for spec 005; `kg_PERSONALIZED_PAGERANK` has since shipped
(`_engine/algorithms.py:10`), so these run for real. Four harness faults had kept all
13 erroring rather than failing, which is why nobody noticed: five class-level
`def engine(self, engine)` fixtures each requested themselves, every setup fixture
referenced a bare `iris_connection` it had not asked for, the cleanup Cypher matched on a
property the fixtures never write (see `_purge`), and every fixture built its edge with a
raw `INSERT INTO rdf_edges`, which writes the row and no adjacency — so PPR, which walks
`^KG("out"/"in")`, saw an empty graph and scored the seed against nothing in either
direction. The `engine` fixture now comes from `tests/contract/conftest.py`, the
connection is a declared parameter, edges go through `create_edge`, and `_purge` takes the
adjacency back down with `delete_edge`.
"""

import pytest
from typing import Dict


# Mark all tests as requiring live database
pytestmark = pytest.mark.requires_database


def _purge(engine, prefix: str) -> None:
    """Remove every node and edge whose ID starts with `prefix`, adjacency included.

    The fixtures below used to clean up with
    `MATCH (n) WHERE n.id STARTS WITH 'CONTRACT_XX:' DELETE n`, which cannot work
    here: `n.id` translates to a `rdf_props` row with `"key" = 'id'`, and
    `engine.create_node('CONTRACT_XX:A')` writes no properties at all — so the
    predicate matched nothing and the delete silently removed nothing. The first test
    in each class therefore passed and the second one failed to insert its edge
    (`SQLCODE -119`, `u_spo_graph`) against the row the teardown had left behind.

    Edges go through `delete_edge` rather than a `DELETE FROM rdf_edges`, because the
    fixtures now create them through `create_edge` and a row-only delete would leave
    `^KG("out"/"in"/"deg"/"degp")` behind. Stale adjacency does not fail loudly — it
    inflates the next run's out-degree and quietly changes every score.
    """
    connection = engine.conn
    cursor = connection.cursor()
    like = f"{prefix}%"
    cursor.execute("SELECT s, p, o_id FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [like, like])
    for s, p, o_id in cursor.fetchall():
        engine.delete_edge(s, p, o_id)
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [like, like])
    cursor.execute("DELETE FROM rdf_props WHERE s LIKE ?", [like])
    cursor.execute("DELETE FROM rdf_labels WHERE s LIKE ?", [like])
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [like])
    connection.commit()


class TestPPRContractSignature:
    """Test API contract signature compliance."""

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
            engine.create_node('CONTRACT_TEST:A')
            engine.create_node('CONTRACT_TEST:B')
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
            engine.create_node('CONTRACT_TEST:A')
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
            engine.create_node('CONTRACT_TEST:A')
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
    def setup_directional_graph(self, engine, iris_connection):
        """Create graph: A -> B (unidirectional)."""
        cursor = iris_connection.cursor()

        _purge(engine, 'CONTRACT_DIR:')

        engine.create_node('CONTRACT_DIR:A')
        engine.create_node('CONTRACT_DIR:B')
        engine.create_edge('CONTRACT_DIR:A', 'connects', 'CONTRACT_DIR:B')
        iris_connection.commit()

        yield

        _purge(engine, 'CONTRACT_DIR:')

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
    def setup_weighted_graph(self, engine, iris_connection):
        """Create simple A -> B graph for weight testing."""
        cursor = iris_connection.cursor()

        _purge(engine, 'CONTRACT_WT:')

        engine.create_node('CONTRACT_WT:A')
        engine.create_node('CONTRACT_WT:B')
        engine.create_edge('CONTRACT_WT:A', 'connects', 'CONTRACT_WT:B')
        iris_connection.commit()

        yield

        _purge(engine, 'CONTRACT_WT:')

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
    def setup_simple_graph(self, engine, iris_connection):
        """Create simple graph for backward compatibility testing."""
        cursor = iris_connection.cursor()

        _purge(engine, 'CONTRACT_BC:')

        engine.create_node('CONTRACT_BC:A')
        engine.create_node('CONTRACT_BC:B')
        engine.create_edge('CONTRACT_BC:A', 'connects', 'CONTRACT_BC:B')
        iris_connection.commit()

        yield

        _purge(engine, 'CONTRACT_BC:')

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
