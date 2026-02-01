import time
import pytest
import iris
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.cypher import set_schema_prefix

@pytest.mark.performance
def test_ppr_stress_scale(iris_connection):
    """
    Robust stress test for Personalized PageRank (PPR) at scale.
    Target: 10,000 nodes, 50,000 edges.
    """
    conn = iris_connection
    cursor = conn.cursor()
    
    print("\n--- Starting PPR E2E Stress Test (Robust) ---")
    
    # 1. Setup Schema
    print("Setting up Graph_KG Schema...")
    try: cursor.execute("CREATE SCHEMA Graph_KG")
    except: pass
    
    try: cursor.execute("SET OPTION DEFAULT_SCHEMA = Graph_KG")
    except: pass
    
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
        try: cursor.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception as e: print(f"Drop warning: {e}")
    
    from iris_vector_graph.utils import _split_sql_statements
    for stmt in _split_sql_statements(GraphSchema.get_base_schema_sql()):
        if stmt.strip(): cursor.execute(stmt)
            
    GraphSchema.ensure_indexes(cursor)
    set_schema_prefix('Graph_KG')
    
    engine = IRISGraphEngine(conn)
    
    # 2. Bulk Load Graph
    node_count = 10000
    edges_per_node = 5
    print(f"Loading {node_count} nodes and {node_count * edges_per_node} edges...")
    
    start_load = time.time()
    
    # Simple loop for reliability in stress test
    node_sql = "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)"
    edge_sql = "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)"
    
    for i in range(node_count):
        node_id = f"n:{i}"
        cursor.execute(node_sql, [node_id])
        
    for i in range(node_count):
        for j in range(1, edges_per_node + 1):
            target = (i + j) % node_count
            cursor.execute(edge_sql, [f"n:{i}", "LINKED_TO", f"n:{target}"])
            
        if i % 2000 == 0 and i > 0:
            print(f"  ...loaded {i} node edges")
            conn.commit()
            
    conn.commit()
    print(f"Load complete in {time.time() - start_load:.2f}s")
    
    # 3. Benchmark PPR
    print("\nBenchmarking Personalized PageRank (Forward)...")
    # Warm up
    engine.kg_PERSONALIZED_PAGERANK(seed_entities=["n:0"], max_iterations=5)
    
    runs = 5
    total_time = 0
    for i in range(runs):
        start_q = time.time()
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=["n:0"],
            damping_factor=0.85,
            max_iterations=20,
            return_top_k=10
        )
        total_time += (time.time() - start_q)
        assert len(scores) > 0
        
    avg_ms = (total_time / runs) * 1000
    print(f"Average PPR Latency (Forward): {avg_ms:.2f}ms")
    
    # 4. Benchmark Bidirectional PPR
    print("Benchmarking Personalized PageRank (Bidirectional)...")
    total_time_bi = 0
    for i in range(runs):
        start_q = time.time()
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=["n:0"],
            bidirectional=True,
            max_iterations=20,
            return_top_k=10
        )
        total_time_bi += (time.time() - start_q)
        
    avg_ms_bi = (total_time_bi / runs) * 1000
    print(f"Average PPR Latency (Bidirectional): {avg_ms_bi:.2f}ms")
    
    print("\n--- PPR Stress Test Passed ---")
    assert avg_ms < 200, f"PPR too slow: {avg_ms}ms"
