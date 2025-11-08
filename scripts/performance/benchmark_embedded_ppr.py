"""
Performance benchmark: Embedded Python PPR vs Pure Python vs External Client

Tests three implementations:
1. Pure Python (baseline) - SQL extraction + Python computation
2. Embedded Python - iris.gref() within IRIS class method
3. External Client - intersystems_irispython iterator API
"""

import time
import json
import iris
from iris_vector_graph.engine import IRISGraphEngine

def create_test_graph(conn):
    """Create a test graph with 1000 nodes and ~5000 edges."""
    cursor = conn.cursor()

    # Create test nodes (cleanup first)
    print("Creating test graph (1000 nodes, ~5000 edges)...")
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'BENCH_%'")
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'BENCH_%'")
    conn.commit()

    # Insert 1000 test nodes
    for i in range(1000):
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [f"BENCH_NODE_{i}"])

    # Insert ~5000 random edges (avg degree ~5)
    import random
    random.seed(42)
    edge_count = 0
    for i in range(1000):
        num_edges = random.randint(3, 7)
        targets = random.sample(range(1000), num_edges)
        for t in targets:
            if t != i:  # No self-loops
                cursor.execute(
                    "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                    [f"BENCH_NODE_{i}", "connects_to", f"BENCH_NODE_{t}"]
                )
                edge_count += 1

    conn.commit()
    print(f"  Created {edge_count} edges\n")
    cursor.close()

    return "BENCH_NODE_0"

def benchmark_pure_python(seed_node: str, runs: int = 5):
    """Benchmark Pure Python implementation."""
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    engine = IRISGraphEngine(conn)

    times = []
    for _ in range(runs):
        start = time.perf_counter()
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=[seed_node],
            damping_factor=0.85,
            max_iterations=100,
            tolerance=1e-6,
            use_functional_index=False
        )
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    conn.close()

    avg = sum(times) / len(times)
    return avg, min(times), max(times), scores

def benchmark_embedded_python(seed_node: str, runs: int = 5):
    """Benchmark Embedded Python implementation."""
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    irispy = iris.createIRIS(conn)

    times = []
    scores = None

    for _ in range(runs):
        start = time.perf_counter()
        result_json = irispy.classMethodValue(
            'Graph.KG.PPRCompute', 'ComputePPR',
            json.dumps([seed_node]),  # seed_entities as JSON
            0.85,                      # damping_factor
            100,                       # max_iterations
            1e-6                       # tolerance
        )
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

        if scores is None:
            scores = json.loads(result_json)

    conn.close()

    avg = sum(times) / len(times)
    return avg, min(times), max(times), scores

def benchmark_external_client(seed_node: str, runs: int = 5):
    """Benchmark External Client implementation."""
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    engine = IRISGraphEngine(conn)

    times = []
    for _ in range(runs):
        start = time.perf_counter()
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=[seed_node],
            damping_factor=0.85,
            max_iterations=100,
            tolerance=1e-6,
            use_functional_index=True
        )
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    conn.close()

    avg = sum(times) / len(times)
    return avg, min(times), max(times), scores

def main():
    print("=" * 80)
    print("PPR PERFORMANCE BENCHMARK: Three Implementations")
    print("=" * 80)

    # Create test graph
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    seed_node = create_test_graph(conn)
    conn.close()

    print(f"Seed node: {seed_node}")
    print("Running 5 iterations per implementation...\n")

    # Benchmark Pure Python
    print("1. Pure Python (SQL extraction + Python computation)...")
    py_avg, py_min, py_max, py_scores = benchmark_pure_python(seed_node)
    print(f"   Average: {py_avg:.2f}ms  (min: {py_min:.2f}ms, max: {py_max:.2f}ms)")
    print(f"   Nodes: {len(py_scores)}\n")

    # Benchmark Embedded Python
    print("2. Embedded Python (iris.gref() within IRIS)...")
    emb_avg, emb_min, emb_max, emb_scores = benchmark_embedded_python(seed_node)
    print(f"   Average: {emb_avg:.2f}ms  (min: {emb_min:.2f}ms, max: {emb_max:.2f}ms)")
    print(f"   Nodes: {len(emb_scores)}\n")

    # Benchmark External Client
    print("3. External Client (intersystems_irispython iterator)...")
    ext_avg, ext_min, ext_max, ext_scores = benchmark_external_client(seed_node)
    print(f"   Average: {ext_avg:.2f}ms  (min: {ext_min:.2f}ms, max: {ext_max:.2f}ms)")
    print(f"   Nodes: {len(ext_scores)}\n")

    # Compare
    print("=" * 80)
    print("PERFORMANCE COMPARISON")
    print("=" * 80)
    print(f"{'Implementation':<30} {'Time (ms)':<15} {'Speedup':<10}")
    print("-" * 80)
    print(f"{'Pure Python (Baseline)':<30} {py_avg:<15.2f} {'1.00x':<10}")

    emb_speedup = py_avg / emb_avg if emb_avg > 0 else 0
    emb_label = f"{emb_speedup:.2f}x" if emb_speedup > 1 else f"{1/emb_speedup:.2f}x slower"
    print(f"{'Embedded Python':<30} {emb_avg:<15.2f} {emb_label:<10}")

    ext_speedup = py_avg / ext_avg if ext_avg > 0 else 0
    ext_label = f"{ext_speedup:.2f}x" if ext_speedup > 1 else f"{1/ext_speedup:.2f}x slower"
    print(f"{'External Client':<30} {ext_avg:<15.2f} {ext_label:<10}")

    # Verify correctness
    print("\n" + "=" * 80)
    print("CORRECTNESS CHECK")
    print("=" * 80)

    # Compare top 5 nodes from each implementation
    top_py = sorted(py_scores.items(), key=lambda x: -x[1])[:5]
    top_emb = sorted(emb_scores.items(), key=lambda x: -x[1])[:5]
    top_ext = sorted(ext_scores.items(), key=lambda x: -x[1])[:5]

    print("\nTop 5 nodes (Pure Python):")
    for node, score in top_py:
        print(f"  {node}: {score:.6f}")

    print("\nTop 5 nodes (Embedded Python):")
    for node, score in top_emb:
        print(f"  {node}: {score:.6f}")

    print("\nTop 5 nodes (External Client):")
    for node, score in top_ext:
        print(f"  {node}: {score:.6f}")

    # Check if all match
    all_match = (
        set(py_scores.keys()) == set(emb_scores.keys()) == set(ext_scores.keys())
    )

    if all_match:
        max_diff = 0
        for node in py_scores:
            diff_emb = abs(py_scores[node] - emb_scores.get(node, 0))
            diff_ext = abs(py_scores[node] - ext_scores.get(node, 0))
            max_diff = max(max_diff, diff_emb, diff_ext)

        if max_diff < 1e-6:
            print(f"\n✅ All implementations produce IDENTICAL results!")
        else:
            print(f"\n⚠️  Max score difference: {max_diff:.2e}")
    else:
        print(f"\n❌ Different node sets returned")

    # Final recommendation
    print("\n" + "=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)

    if emb_avg < py_avg:
        speedup = py_avg / emb_avg
        print(f"🚀 Use Embedded Python - {speedup:.2f}x faster than baseline!")
    elif emb_avg < ext_avg:
        print(f"✅ Embedded Python is faster than External Client")
        print(f"   Pure Python remains best choice at {py_avg:.2f}ms")
    else:
        print(f"📊 Pure Python remains the best choice at {py_avg:.2f}ms")
        print(f"   Consider ObjectScript implementation for sub-millisecond performance")

if __name__ == '__main__':
    main()
