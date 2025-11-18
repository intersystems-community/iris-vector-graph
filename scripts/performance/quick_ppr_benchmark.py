"""
Quick PPR performance benchmark: Functional Index vs Pure Python

Compares the newly deployed Functional Index implementation against
the baseline Pure Python implementation.
"""

import time
import os
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
        # Each node connects to ~5 random other nodes
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
    print(f"  Created {edge_count} edges")
    cursor.close()

    return "BENCH_NODE_0"  # Return seed node

def benchmark_implementation(seed_node: str, use_functional_index: bool, label: str):
    """Run PPR and measure time."""
    # Connect to IRIS
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    engine = IRISGraphEngine(conn)

    # Run PPR 5 times and average
    times = []
    for i in range(5):
        start = time.perf_counter()
        scores = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=[seed_node],
            damping_factor=0.85,
            max_iterations=100,
            tolerance=1e-6,
            use_functional_index=use_functional_index
        )
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        times.append(elapsed)

    conn.close()

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    print(f"\n{label}:")
    print(f"  Average: {avg_time:.2f}ms")
    print(f"  Min: {min_time:.2f}ms")
    print(f"  Max: {max_time:.2f}ms")
    print(f"  Nodes returned: {len(scores)}")

    return avg_time, scores

def main():
    print("=" * 80)
    print("PPR PERFORMANCE BENCHMARK: Functional Index vs Pure Python")
    print("=" * 80)

    # Create test graph
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    seed_node = create_test_graph(conn)
    conn.close()

    print(f"\nTest case: Synthetic graph benchmark")
    print(f"Seed node: {seed_node}")

    # Benchmark Pure Python (baseline)
    baseline_time, baseline_scores = benchmark_implementation(
        seed_node=seed_node,
        use_functional_index=False,
        label="Pure Python (Baseline)"
    )

    # Benchmark Functional Index
    fi_time, fi_scores = benchmark_implementation(
        seed_node=seed_node,
        use_functional_index=True,
        label="Functional Index (IRIS Globals)"
    )

    # Compare
    speedup = baseline_time / fi_time if fi_time > 0 else 0
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    print(f"Baseline (Pure Python):    {baseline_time:.2f}ms")
    print(f"Functional Index:          {fi_time:.2f}ms")
    print(f"Speedup:                   {speedup:.2f}x")

    if speedup > 1.0:
        print(f"\n🚀 Functional Index is {speedup:.2f}x FASTER!")
    elif speedup < 1.0:
        improvement = (1.0 / speedup)
        print(f"\n⚠️  Baseline is {improvement:.2f}x faster (Functional Index needs optimization)")
    else:
        print(f"\n⚖️  Performance is equivalent")

    # Verify correctness
    print("\nCorrectness Check:")
    top_baseline = sorted(baseline_scores.items(), key=lambda x: -x[1])[:5]
    top_fi = sorted(fi_scores.items(), key=lambda x: -x[1])[:5]

    print("\nTop 5 nodes (Baseline):")
    for node, score in top_baseline:
        print(f"  {node}: {score:.6f}")

    print("\nTop 5 nodes (Functional Index):")
    for node, score in top_fi:
        print(f"  {node}: {score:.6f}")

    # Check if results match
    diff_count = 0
    for node in baseline_scores:
        if node in fi_scores:
            diff = abs(baseline_scores[node] - fi_scores[node])
            if diff > 1e-6:
                diff_count += 1

    if diff_count == 0:
        print("\n✅ Results match exactly!")
    else:
        print(f"\n⚠️  {diff_count} nodes have score differences > 1e-6")

if __name__ == '__main__':
    main()
