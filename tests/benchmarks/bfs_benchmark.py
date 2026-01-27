import iris
import json
import time
import argparse
import sys
import os
from typing import List, Dict

# Add parent directory to sys.path to allow importing from iris_vector_graph
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

def get_iris_interface():
    """Get the correct IRIS interface depending on environment"""
    try:
        import iris
        if hasattr(iris, 'cls'):
            return iris
        return iris
    except ImportError:
        print("IRIS module not found.")
        sys.exit(1)

def setup_iris():
    """Establish connection to IRIS (for Client SDK)"""
    import iris
    try:
        conn = iris.connect(
            hostname='localhost',
            port=1972,
            namespace='USER',
            username='_SYSTEM',
            password='SYS'
        )
        return conn
    except Exception:
        return None

def load_synthetic_graph(conn, csv_file: str, num_nodes: int):
    """Load the CSV graph into SQLUser.rdf_edges and SQLUser.nodes"""
    cursor = conn.cursor()
    print(f"Cleaning existing graph data...")
    cursor.execute("DELETE FROM SQLUser.rdf_edges")
    cursor.execute("DELETE FROM SQLUser.rdf_props")
    cursor.execute("DELETE FROM SQLUser.rdf_labels")
    cursor.execute("DELETE FROM SQLUser.nodes")
    
    print(f"Populating nodes table with {num_nodes} nodes...")
    for i in range(num_nodes):
        cursor.execute("INSERT INTO SQLUser.nodes (node_id) VALUES (?)", [f"node_{i}"])
    
    print(f"Loading synthetic graph from {csv_file}...")
    with open(csv_file, 'r') as f:
        next(f) # skip header
        count = 0
        for line in f:
            s, p, o = line.strip().split(',')
            cursor.execute(
                "INSERT INTO SQLUser.rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, '{}')",
                [s, p, o]
            )
            count += 1
            if count % 5000 == 0:
                print(f"  Loaded {count} edges...")
    
    conn.commit()
    print(f"Successfully loaded {count} edges.")

def rebuild_kg_index(iris_obj):
    """Call the BuildKG method to populate the functional index globals"""
    print("Rebuilding KG functional index...")
    start_time = time.time()
    iris_obj.cls("Graph.KG.Traversal").BuildKG()
    duration = time.time() - start_time
    print(f"Index rebuild complete in {duration:.2f} seconds.")
    return duration

def benchmark_bfs(iris_obj, src_node: str, depths: List[int], runs: int = 3) -> Dict:
    """Benchmark BFS_JSON at various depths"""
    results = {}
    preds = iris_obj.cls('%DynamicArray')._New()
    
    for depth in depths:
        print(f"Benchmarking Depth {depth}...")
        latencies = []
        edge_counts = []
        
        for r in range(runs):
            start_time = time.time()
            res_da = iris_obj.cls("Graph.KG.Traversal").BFS_JSON(src_node, preds, depth)
            duration = time.time() - start_time
            latencies.append(duration)
            edge_counts.append(res_da._Size())
            
        avg_latency = sum(latencies) / runs
        avg_edges = sum(edge_counts) / runs
        teps = avg_edges / avg_latency if avg_latency > 0 else 0
        
        results[depth] = {
            "avg_latency": avg_latency,
            "avg_edges": avg_edges,
            "teps": teps
        }
        print(f"  Depth {depth}: {avg_latency:.4f}s, {avg_edges:.0f} edges, {teps:.0f} TEPS")
        
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BFS Performance Benchmark")
    parser.add_argument("--nodes", type=int, default=10000, help="Number of nodes")
    parser.add_argument("--edges", type=int, default=50000, help="Number of edges")
    parser.add_argument("--depth", type=int, default=3, help="Max depth to benchmark")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per depth")
    parser.add_argument("--skip-load", action="store_true", help="Skip data loading")
    
    if len(sys.argv) > 1:
        args = parser.parse_args()
    else:
        args = parser.parse_args([])

    iris_obj = get_iris_interface()
    csv_file = f"/tmp/synthetic_{args.nodes}_{args.edges}.csv"
    
    if not args.skip_load:
        from graph_gen import RMATGenerator, save_as_csv
        gen = RMATGenerator()
        edges = gen.generate_edges(args.nodes, args.edges)
        save_as_csv(edges, csv_file)
    
    conn = setup_iris()
    
    try:
        if not args.skip_load and conn:
            load_synthetic_graph(conn, csv_file, args.nodes)
            rebuild_kg_index(iris_obj)
        elif not args.skip_load:
            print("Cannot load data: No DB-API connection available.")
        
        depths = list(range(1, args.depth + 1))
        results = benchmark_bfs(iris_obj, "node_0", depths, args.runs)
        
        output_file = "/tmp/benchmarks.json"
        with open(output_file, 'w') as f:
            json.dump({
                "config": vars(args),
                "results": results,
                "timestamp": time.time()
            }, f, indent=2)
        print(f"\nFinal results saved to {output_file}")
        
    finally:
        if conn:
            conn.close()
        if os.path.exists(csv_file):
            os.remove(csv_file)
