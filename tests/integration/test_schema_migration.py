import pytest
import os
import irisnative
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

@pytest.fixture
def clean_schema(iris_connection):
    """Ensure a clean Graph_KG schema before and after the test"""
    cursor = iris_connection.cursor()
    try:
        cursor.execute("DROP TABLE Graph_KG.kg_NodeEmbeddings_optimized")
    except: pass
    try:
        cursor.execute("DROP TABLE Graph_KG.kg_NodeEmbeddings")
    except: pass
    try:
        cursor.execute("DROP TABLE Graph_KG.rdf_edges")
    except: pass
    try:
        cursor.execute("DROP TABLE Graph_KG.rdf_props")
    except: pass
    try:
        cursor.execute("DROP TABLE Graph_KG.rdf_labels")
    except: pass
    try:
        cursor.execute("DROP TABLE Graph_KG.docs")
    except: pass
    try:
        cursor.execute("DROP TABLE Graph_KG.nodes")
    except: pass
    try:
        cursor.execute("DROP SCHEMA Graph_KG")
    except: pass
    
    yield iris_connection
    
    # Cleanup again
    try:
        cursor.execute("DROP SCHEMA Graph_KG CASCADE")
    except: pass

@pytest.mark.skipif(os.environ.get("SKIP_IRIS_TESTS", "false") == "true", reason="IRIS not available")
def test_schema_migration_v1_to_v2(clean_schema):
    """
    Test migration from a 'v1' schema (VARCHAR 4000) to 'v2' (VARCHAR 64000).
    """
    conn = clean_schema
    cursor = conn.cursor()
    
    # 1. Create a legacy-style schema manually
    cursor.execute("CREATE SCHEMA Graph_KG")
    cursor.execute("CREATE TABLE Graph_KG.nodes(node_id VARCHAR(256) PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE Graph_KG.rdf_labels(s VARCHAR(256), label VARCHAR(128), PRIMARY KEY(s, label))")
    cursor.execute("CREATE TABLE Graph_KG.rdf_props(s VARCHAR(256), \"key\" VARCHAR(128), val VARCHAR(4000), PRIMARY KEY(s, \"key\"))")
    
    # Verify it's 4000
    cursor.execute("SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_props' AND COLUMN_NAME = 'val'")
    length = cursor.fetchone()[0]
    print(f"Initial length: {length}")
    assert length == 4000
    
    # 2. Run engine initialization
    engine = IRISGraphEngine(conn, embedding_dimension=768)
    engine.initialize_schema()
    
    # 3. Verify it was upgraded to 64000
    cursor.execute("SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_props' AND COLUMN_NAME = 'val'")
    length = cursor.fetchone()[0]
    assert length == 64000
    
    # 4. Verify data integrity
    large_val = "x" * 5000
    success = engine.create_node("test:large", labels=["Test"], properties={"data": large_val})
    assert success is True
    
    node = engine.get_node("test:large")
    assert node is not None, "Node not found after creation"
    assert "properties" in node, f"Properties missing from node dict: {node}"
    assert node["properties"]["data"] == large_val

@pytest.mark.skipif(os.environ.get("SKIP_IRIS_TESTS", "false") == "true", reason="IRIS not available")
def test_embedding_dimension_mismatch_warning(clean_schema):
    """
    Test scenario where DB has 768 but engine wants 384.
    Currently this is a 'silent skip' - we should at least detect it.
    """
    conn = clean_schema
    cursor = conn.cursor()
    
    # 1. Init with 768
    engine768 = IRISGraphEngine(conn, embedding_dimension=768)
    engine768.initialize_schema()
    
    # 2. Try to re-init with 384
    engine384 = IRISGraphEngine(conn, embedding_dimension=384)
    # This shouldn't crash, but it won't change the DB either
    engine384.initialize_schema()
    
    # 3. Check what's actually in the DB
    # _get_embedding_dimension uses INFORMATION_SCHEMA and regex to find digits
    db_dim = engine384._get_embedding_dimension()
    
    assert db_dim == 768
    assert db_dim != 384
    
    # 4. Attempting to store 384 in 768 should fail at the DB level
    # (IRIS will throw an error when trying to insert a vector of wrong size)
    with pytest.raises(Exception):
        engine384.store_embedding("test:node", [0.1] * 384)
