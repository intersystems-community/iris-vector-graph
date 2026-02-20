import pytest
import json
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine

@pytest.fixture
def mock_conn():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    return conn

@pytest.fixture
def engine(mock_conn):
    return IRISGraphEngine(mock_conn)

def test_create_node_transactional(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    
    node_id = "test-node"
    labels = ["L1", "L2"]
    properties = {"name": "Test", "val": 123}
    
    success = engine.create_node(node_id, labels, properties)
    
    assert success is True
    # Verify transaction boundaries
    cursor.execute.assert_any_call("START TRANSACTION")
    cursor.execute.assert_any_call("COMMIT")
    
    # Verify inserts
    # Node insert
    assert any("INSERT INTO" in call[0][0] and "nodes" in call[0][0] for call in cursor.execute.call_args_list)
    
    # Labels and props batch inserts
    assert cursor.executemany.call_count == 2
    
    # Labels verify
    label_call = next(c for c in cursor.executemany.call_args_list if "rdf_labels" in c[0][0])
    assert label_call[0][1] == [["test-node", "L1"], ["test-node", "L2"]]
    
    # Props verify — 3 rows: 'name', 'val', plus auto-injected 'id'
    prop_call = next(c for c in cursor.executemany.call_args_list if "rdf_props" in c[0][0])
    prop_keys = [row[1] for row in prop_call[0][1]]
    assert len(prop_call[0][1]) == 3
    assert "id" in prop_keys, "create_node must store 'id' in rdf_props for Cypher queryability"
    assert "name" in prop_keys
    assert "val" in prop_keys

def test_create_node_rollback_on_failure(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    cursor.executemany.side_effect = Exception("DB Error")
    
    node_id = "test-node"
    labels = ["L1"]
    
    success = engine.create_node(node_id, labels, {})
    
    assert success is False
    cursor.execute.assert_any_call("ROLLBACK")

def test_bulk_create_nodes_batching(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    
    nodes = [
        {"id": "n1", "labels": ["A"], "properties": {"p": 1}},
        {"id": "n2", "labels": ["B"], "properties": {"q": 2}}
    ]
    
    result = engine.bulk_create_nodes(nodes, disable_indexes=False)
    
    assert result == ["n1", "n2"]
    # Check executemany for nodes, labels, and props
    # bulk_create_nodes uses 3 executemany calls
    assert cursor.executemany.call_count == 3
    mock_conn.commit.assert_called()
