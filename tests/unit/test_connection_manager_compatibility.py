"""
Test IRISGraphEngine compatibility with ConnectionManager from iris-vector-rag.

This test verifies that IRISGraphEngine can accept both:
1. Direct IRIS connections (iris.connect())
2. ConnectionManager objects (iris-vector-rag)
"""

import pytest
import iris
from unittest.mock import Mock, MagicMock
from iris_vector_graph import IRISGraphEngine


class TestConnectionManagerCompatibility:
    """Test IRISGraphEngine works with different connection types."""

    def test_direct_connection(self):
        """Test IRISGraphEngine with direct IRIS connection."""
        # Create mock connection WITHOUT get_connection method
        mock_conn = Mock(spec=['cursor'])
        mock_conn.cursor = Mock(return_value=Mock())

        # Should work with direct connection
        engine = IRISGraphEngine(mock_conn)

        assert engine.conn == mock_conn
        assert engine.connection_manager is None
        assert engine._is_managed_connection is False

    def test_connection_manager(self):
        """Test IRISGraphEngine with ConnectionManager (iris-vector-rag)."""
        # Create mock ConnectionManager
        mock_conn = Mock()
        mock_conn.cursor = Mock(return_value=Mock())

        mock_manager = Mock()
        mock_manager.get_connection = Mock(return_value=mock_conn)

        # Should detect ConnectionManager and call get_connection()
        engine = IRISGraphEngine(mock_manager)

        # Verify get_connection was called
        mock_manager.get_connection.assert_called_once()

        # Verify engine state
        assert engine.conn == mock_conn
        assert engine.connection_manager == mock_manager
        assert engine._is_managed_connection is True

    def test_kg_neighborhood_expansion_with_manager(self):
        """Test kg_NEIGHBORHOOD_EXPANSION works with ConnectionManager."""
        # Create mock connection with cursor
        mock_cursor = Mock()
        mock_cursor.execute = Mock()
        mock_cursor.fetchall = Mock(return_value=[])

        mock_conn = Mock()
        mock_conn.cursor = Mock(return_value=mock_cursor)

        # Create mock ConnectionManager
        mock_manager = Mock()
        mock_manager.get_connection = Mock(return_value=mock_conn)

        # Initialize engine with ConnectionManager
        engine = IRISGraphEngine(mock_manager)

        # This should NOT raise AttributeError: 'ConnectionManager' object has no attribute 'cursor'
        try:
            result = engine.kg_NEIGHBORHOOD_EXPANSION(
                entity_list=["test_entity"],
                expansion_depth=1,
                confidence_threshold=500
            )

            # If we get here, the bug is fixed!
            assert isinstance(result, list)
            print("✅ kg_NEIGHBORHOOD_EXPANSION works with ConnectionManager")

        except AttributeError as e:
            pytest.fail(f"❌ Bug not fixed: {e}")

    def test_kg_personalized_pagerank_with_manager(self):
        """Test kg_PERSONALIZED_PAGERANK works with ConnectionManager."""
        # Create mock connection with cursor
        mock_cursor = Mock()
        mock_cursor.execute = Mock()
        mock_cursor.fetchall = Mock(return_value=[
            ("test_node", 1000),  # Mock node exists in graph
        ])
        mock_cursor.close = Mock()

        mock_conn = Mock()
        mock_conn.cursor = Mock(return_value=mock_cursor)

        # Create mock ConnectionManager
        mock_manager = Mock()
        mock_manager.get_connection = Mock(return_value=mock_conn)

        # Initialize engine with ConnectionManager
        engine = IRISGraphEngine(mock_manager)

        # This should NOT raise AttributeError
        try:
            # Note: This will still fail validation (no nodes in mock DB)
            # but should NOT fail with AttributeError on .cursor()
            result = engine.kg_PERSONALIZED_PAGERANK(
                seed_entities=["test_node"],
                damping_factor=0.85,
                max_iterations=10
            )

            # If we get here without AttributeError, the connection handling works
            print("✅ kg_PERSONALIZED_PAGERANK connection handling works")

        except AttributeError as e:
            if "cursor" in str(e):
                pytest.fail(f"❌ Bug not fixed: {e}")
            # Other errors (like validation) are expected
        except ValueError:
            # Expected: validation errors from mock data
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
