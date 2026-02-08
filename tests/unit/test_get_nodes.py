import pytest
import json
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine

@pytest.fixture
def mock_conn():
    conn = MagicMock()
    # Mock cursor and its methods
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    return conn

@pytest.fixture
def engine(mock_conn):
    return IRISGraphEngine(mock_conn)

def test_get_nodes_batching(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    
    # Setup mock data for fetchall
    # labels query result
    labels_rows = [
        ("node-1", "LabelA"),
        ("node-1", "LabelB"),
        ("node-2", "LabelC")
    ]
    # properties query result
    props_rows = [
        ("node-1", "name", "Node 1"),
        ("node-1", "meta", '{"key": "val"}'),
        ("node-2", "name", "Node 2")
    ]
    
    # Configure mock to return different results for sequential calls
    cursor.fetchall.side_effect = [
        labels_rows,  # First call for labels
        props_rows,   # Second call for properties
        []            # Third call for empty nodes (if any)
    ]
    
    node_ids = ["node-1", "node-2"]
    nodes = engine.get_nodes(node_ids)
    
    # Assertions
    assert len(nodes) == 2
    
    node1 = next(n for n in nodes if n["id"] == "node-1")
    assert "LabelA" in node1["labels"]
    assert "LabelB" in node1["labels"]
    assert node1["name"] == "Node 1"
    assert node1["meta"] == {"key": "val"}
    
    node2 = next(n for n in nodes if n["id"] == "node-2")
    assert node2["labels"] == ["LabelC"]
    assert node2["name"] == "Node 2"
    
    # Verify exact number of queries (excluding potential empty node check)
    # Actually, the implementation calls fetchall() for labels, then for properties.
    assert cursor.execute.call_count >= 2
    
    # Verify the SQL used batching
    last_query = cursor.execute.call_args_list[0][0][0]
    assert "IN (?,?)" in last_query

def test_get_nodes_handles_missing_nodes(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    
    # Mock labels and props only for node-1
    cursor.fetchall.side_effect = [
        [("node-1", "LabelA")], # labels
        [("node-1", "name", "Node 1")], # props
        [("node-1",)] # nodes existence check for empty ones (not really needed if node-1 found)
    ]
    
    node_ids = ["node-1", "missing-node"]
    
    # For missing-node, it will be considered empty.
    # The code will then check existence in 'nodes' table for 'missing-node'.
    # We mock it to NOT exist.
    cursor.fetchall.side_effect = [
        [("node-1", "LabelA")], # labels
        [("node-1", "name", "Node 1")], # props
        [("node-1",)] # nodes existence check result: only node-1 exists
    ]
    
    nodes = engine.get_nodes(node_ids)
    
    assert len(nodes) == 1
    assert nodes[0]["id"] == "node-1"

def test_get_nodes_empty_input(engine):
    assert engine.get_nodes([]) == []
