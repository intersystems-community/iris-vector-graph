import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine

def test_engine_init_with_dimension():
    """Verify that IRISGraphEngine accepts and uses an explicit embedding_dimension."""
    conn = MagicMock()
    engine = IRISGraphEngine(conn, embedding_dimension=128)
    assert engine.embedding_dimension == 128
    assert engine._get_embedding_dimension() == 128

@patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension")
def test_engine_dimension_auto_detection_failure_inference(mock_get_dim):
    """Verify that IRISGraphEngine infers dimension from input if auto-detection fails."""
    # Setup mocks to fail auto-detection
    mock_get_dim.return_value = None
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = None  # INFORMATION_SCHEMA check fails
    
    engine = IRISGraphEngine(conn)
    
    # Mock _assert_node_exists to pass
    with patch.object(engine, "_assert_node_exists"):
        # Test store_embedding inference
        embedding = [0.1, 0.2, 0.3]
        engine.store_embedding("node:1", embedding)
        
        assert engine.embedding_dimension == 3
        
        # Test store_embeddings inference (should already be set now)
        items = [{"node_id": "node:2", "embedding": [0.4, 0.5, 0.6]}]
        engine.store_embeddings(items)
        assert engine.embedding_dimension == 3

def test_engine_dimension_mismatch():
    """Verify that dimension mismatch raises ValueError."""
    conn = MagicMock()
    engine = IRISGraphEngine(conn, embedding_dimension=3)
    
    with patch.object(engine, "_assert_node_exists"):
        with pytest.raises(ValueError, match="Embedding dimension mismatch"):
            engine.store_embedding("node:1", [0.1, 0.2])
