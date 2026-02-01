import time
import json
import pytest
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.cypher import set_schema_prefix

@pytest.mark.performance
def test_large_scale_stress_v1_5(iris_connection):
    """Robust E2E Stress Test for v1.5.0 optimizations."""
    conn = iris_connection
    cursor = conn.cursor()
    
    print("\n--- Starting v1.5.0 E2E Stress Test (Verbose) ---")
    
    # 1. Setup Schema
    print("Setting up Graph_KG Schema...")
    try:
        cursor.execute("CREATE SCHEMA Graph_KG")
    except:
        pass
        
    try:
        cursor.execute("SET OPTION DEFAULT_SCHEMA = Graph_KG")
    except:
        pass
    
    tables = [
        "Graph_KG.kg_NodeEmbeddings_optimized",
        "Graph_KG.kg_NodeEmbeddings",
        "Graph_KG.docs",
        "Graph_KG.rdf_edges",
        "Graph_KG.rdf_props",
        "Graph_KG.rdf_labels",
        "Graph_KG.nodes"
    ]
    for table in tables:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception as e:
            print(f"Drop warning: {e}")
    
    # Create schema (Row storage for stability < 1M rows)
    from iris_vector_graph.utils import _split_sql_statements
    sql = GraphSchema.get_base_schema_sql()
    for stmt in _split_sql_statements(sql):
        if stmt.strip():
            cursor.execute(stmt)
            
    GraphSchema.ensure_indexes(cursor)
    set_schema_prefix('Graph_KG')
    
    engine = IRISGraphEngine(conn)
    
    # 2. Bulk Load 10,000 entities
    entity_count = 10000
    print(f"Loading {entity_count} entities...")
    
    start_time = time.time()
    
    # Simple loop for maximum reliability during stress test
    node_sql = "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)"
    label_sql = "INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)"
    prop_sql = "INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, ?, ?)"
    
    for i in range(entity_count):
        node_id = f"node:{i}"
        cursor.execute(node_sql, [node_id])
        cursor.execute(label_sql, [node_id, "Entity"])
        cursor.execute(prop_sql, [node_id, "name", f"Name_{i}"])
        cursor.execute(prop_sql, [node_id, "status", "active"])
        
        if i % 2000 == 0 and i > 0:
            print(f"  ...loaded {i} nodes")
            conn.commit()
            
    conn.commit()
    
    # Verify counts
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
    node_count_db = cursor.fetchone()[0]
    print(f"Nodes in DB: {node_count_db}")
    
    if node_count_db == 0:
        pytest.fail("Load failed: No nodes in database!")
    
    load_duration = time.time() - start_time
    print(f"Load complete in {load_duration:.2f}s")
    
    # 3. Benchmark Query (5,000 results)
    print("\nBenchmarking Cypher Query (Return 5,000 nodes with props)...")
    query = "MATCH (n) RETURN n LIMIT 5000"
    
    # Warm up
    engine.execute_cypher(query)
    
    runs = 3
    total_query_time = 0
    for i in range(runs):
        start_q = time.time()
        result = engine.execute_cypher(query)
        total_query_time += (time.time() - start_q)
        assert len(result['rows']) == 5000
        
    avg_query_time = (total_query_time / runs) * 1000
    print(f"Average Query Latency: {avg_query_time:.2f}ms")
    
    # 4. Verify data
    row = result['rows'][0]
    print(f"Sample row structure: {row}")
    # row[0]=id, row[1]=labels, row[2]=props
    assert "node:" in row[0]
    print("Data Integrity Verified.")
    
    print("\n--- Stress Test Passed ---")
