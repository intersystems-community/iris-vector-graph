import os
import time
import json
import pytest
import iris
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.cypher import set_schema_prefix

def get_connection():
    host = os.environ.get('IRIS_HOST', 'localhost')
    port = int(os.environ.get('IRIS_PORT', 1972))
    namespace = os.environ.get('IRIS_NAMESPACE', 'USER')
    username = os.environ.get('IRIS_USERNAME', '_SYSTEM')
    password = os.environ.get('IRIS_PASSWORD', 'SYS')
    return iris.connect(f"{host}:{port}/{namespace}", username, password)

def test_large_scale_stress_e2e():
    """E2E Stress Test for v1.5.0 optimizations."""
    conn = get_connection()
    cursor = conn.cursor()
    
    print("\n--- Starting v1.5.0 E2E Stress Test ---")
    
    # 1. Setup Schema with Columnar Storage
    print("Initializing Columnar Schema...")
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
        except:
            pass
    
    # Create schema
    for stmt in GraphSchema.get_base_schema_sql().split(';'):
        if stmt.strip():
            cursor.execute(stmt)
    GraphSchema.ensure_indexes(cursor)
    set_schema_prefix('Graph_KG')
    
    engine = IRISGraphEngine(conn)
    
    # 2. Bulk Load 20,000 entities
    # Each entity has 2 labels and 5 properties
    entity_count = 20000
    print(f"Bulk Loading {entity_count} entities (with labels and properties)...")
    
    start_time = time.time()
    
    # Use Bulk Loading tools from 1.4.4
    GraphSchema.disable_indexes(cursor)
    
    # Insert Nodes
    node_sql = GraphSchema.get_bulk_insert_sql('nodes')
    nodes = [[f"node:{i}"] for i in range(entity_count)]
    cursor.executemany(node_sql, nodes)
    
    # Insert Labels
    label_sql = GraphSchema.get_bulk_insert_sql('rdf_labels')
    labels = []
    for i in range(entity_count):
        labels.append([f"node:{i}", "Entity"])
        labels.append([f"node:{i}", f"Type_{i % 10}"])
    cursor.executemany(label_sql, labels)
    
    # Insert Properties
    prop_sql = GraphSchema.get_bulk_insert_sql('rdf_props')
    props = []
    for i in range(entity_count):
        props.append([f"node:{i}", "name", f"Name_{i}"])
        props.append([f"node:{i}", "status", "active" if i % 2 == 0 else "inactive"])
        props.append([f"node:{i}", "value", i])
        props.append([f"node:{i}", "json_data", json.dumps({"id": i, "meta": "stress-test"})])
        props.append([f"node:{i}", "long_val", "x" * 1000]) # Test large values from 1.4.7
    cursor.executemany(prop_sql, props)
    
    print("Rebuilding Indexes...")
    GraphSchema.rebuild_indexes(cursor)
    conn.commit()
    
    load_duration = time.time() - start_time
    print(f"Load complete in {load_duration:.2f}s ({ (entity_count*8)/load_duration:.0f} records/s)")
    
    # 3. Benchmark Optimized Query (10,000 results)
    print("\nBenchmarking Optimized Cypher Query (Return 10,000 nodes with full props)...")
    # Query without parameters to isolate the issue
    query = "MATCH (n) RETURN n LIMIT 10000"
    
    # Print generated SQL for debugging
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    parsed = parse_query(query)
    sql_obj = translate_to_sql(parsed)
    sql_str = sql_obj.sql if isinstance(sql_obj.sql, str) else sql_obj.sql[0]
    print(f"DEBUG SQL: {sql_str}")
    
    # Warm up
    engine.execute_cypher(query)
    
    runs = 5
    total_query_time = 0
    for i in range(runs):
        start_q = time.time()
        result = engine.execute_cypher(query)
        total_query_time += (time.time() - start_q)
        assert len(result['rows']) == 10000
        
    avg_query_time = (total_query_time / runs) * 1000
    print(f"Average Query Latency: {avg_query_time:.2f}ms")
    
    # 4. Verify native JSON structure
    row = result['rows'][0]
    # row[0]=id, row[1]=labels (JSON), row[2]=props (JSON)
    props_json = json.loads(row[2])
    assert any(p['key'] == 'status' and p['value'] == 'active' for p in props_json)
    print("Data Integrity Verified (Native JSON_OBJECT correct)")
    
    print("\n--- Stress Test Passed ---")
    conn.close()

if __name__ == "__main__":
    test_large_scale_stress_e2e()
