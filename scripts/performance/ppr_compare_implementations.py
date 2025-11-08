#!/usr/bin/env python3
"""
Performance comparison of different PPR implementations.

Compares:
1. Current pure Python implementation (baseline)
2. IRIS Globals + Python (GraphRAG-style) - RECOMMENDED
3. SciPy sparse matrix implementation (for reference)
"""

import time
import iris
import numpy as np
from scipy.sparse import csr_matrix
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.ppr_globals import build_ppr_global, compute_ppr_globals
import json


def create_test_graph(conn, num_nodes, avg_degree=5):
    """Create synthetic test graph."""
    cursor = conn.cursor()

    # Clear existing test data
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'PERF:%'")
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'PERF:%'")

    # Create nodes
    node_ids = [f"PERF:N{i:06d}" for i in range(num_nodes)]
    for node_id in node_ids:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node_id])

    # Create edges
    import random
    random.seed(42)

    edge_count = 0
    for i, source_id in enumerate(node_ids):
        num_edges = random.randint(max(1, avg_degree - 2), avg_degree + 2)
        for _ in range(num_edges):
            target_idx = random.randint(0, num_nodes - 1)
            if target_idx != i:
                target_id = node_ids[target_idx]
                cursor.execute(
                    "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, 'links_to', ?)",
                    [source_id, target_id]
                )
                edge_count += 1

    conn.commit()
    print(f"Created {num_nodes} nodes with {edge_count} edges (avg degree: {edge_count/num_nodes:.1f})")
    return node_ids


def cleanup_test_graph(conn):
    """Remove test data."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'PERF:%'")
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'PERF:%'")
    conn.commit()


def benchmark_current_python(engine, seed_entities):
    """Benchmark current pure Python implementation."""
    start = time.time()
    scores = engine.kg_PERSONALIZED_PAGERANK(
        seed_entities=seed_entities,
        damping_factor=0.85,
        max_iterations=50,
        tolerance=1e-6
    )
    elapsed = time.time() - start

    return {
        'implementation': 'Pure Python (Current)',
        'time_ms': elapsed * 1000,
        'num_scores': len(scores),
        'scores_sum': sum(scores.values()),
        'top_score': max(scores.values()) if scores else 0
    }


def benchmark_scipy_sparse(conn, seed_entities, damping_factor=0.85, max_iterations=50, tolerance=1e-6):
    """Benchmark SciPy sparse matrix implementation."""
    cursor = conn.cursor()

    # Build node mapping
    cursor.execute("SELECT DISTINCT node_id FROM nodes WHERE node_id LIKE 'PERF:%'")
    nodes = [row[0] for row in cursor.fetchall()]
    node_to_idx = {node: idx for idx, node in enumerate(nodes)}
    N = len(nodes)

    if N == 0:
        return None

    # Build sparse transition matrix
    start_build = time.time()

    cursor.execute("""
        SELECT s, o_id, COUNT(*) OVER (PARTITION BY s) as outdeg
        FROM rdf_edges
        WHERE s LIKE 'PERF:%'
    """)

    row_indices = []
    col_indices = []
    data = []

    for source, target, outdeg in cursor.fetchall():
        if source in node_to_idx and target in node_to_idx:
            # Transition probability from source to target
            row_indices.append(node_to_idx[target])  # Row = target
            col_indices.append(node_to_idx[source])  # Col = source
            data.append(1.0 / outdeg)  # P(target | source) = 1/outdeg(source)

    # Create CSR matrix (compressed sparse row)
    A = csr_matrix((data, (row_indices, col_indices)), shape=(N, N))
    build_time = time.time() - start_build

    # Initialize scores
    seed_set = set(seed_entities)
    uniform_seed_score = 1.0 / len(seed_entities)

    scores = np.zeros(N)
    personalization = np.zeros(N)

    for i, node_id in enumerate(nodes):
        if node_id in seed_set:
            scores[i] = uniform_seed_score
            personalization[i] = uniform_seed_score

    # Power iteration (vectorized)
    start_iter = time.time()
    converged = False

    for iteration in range(1, max_iterations + 1):
        prev_scores = scores.copy()

        # Vectorized PPR update: scores = (1-α)*p + α*A*scores
        scores = (1 - damping_factor) * personalization + damping_factor * A.dot(scores)

        # Check convergence
        max_change = np.max(np.abs(scores - prev_scores))
        if max_change < tolerance:
            converged = True
            break

    iter_time = time.time() - start_iter
    total_time = build_time + iter_time

    # Normalize
    total = np.sum(scores)
    if total > 0:
        scores = scores / total

    # Convert to dict
    score_dict = {nodes[i]: float(scores[i]) for i in range(N) if scores[i] > 1e-10}

    return {
        'implementation': 'SciPy Sparse Matrix',
        'time_ms': total_time * 1000,
        'build_time_ms': build_time * 1000,
        'iter_time_ms': iter_time * 1000,
        'num_scores': len(score_dict),
        'scores_sum': sum(score_dict.values()),
        'top_score': max(score_dict.values()) if score_dict else 0,
        'iterations': iteration,
        'converged': converged
    }


def benchmark_globals_ppr(conn, seed_entities, damping_factor=0.85, max_iterations=50, tolerance=1e-6):
    """Benchmark IRIS Globals + Python implementation (GraphRAG-style)."""
    # Build Global structure
    start_build = time.time()
    build_ppr_global(conn)
    build_time = time.time() - start_build

    # Compute PPR using Globals
    start_compute = time.time()
    scores = compute_ppr_globals(
        conn,
        seed_entities=seed_entities,
        damping_factor=damping_factor,
        max_iterations=max_iterations,
        tolerance=tolerance
    )
    compute_time = time.time() - start_compute
    total_time = build_time + compute_time

    return {
        'implementation': 'IRIS Globals + Python (GraphRAG)',
        'time_ms': total_time * 1000,
        'build_time_ms': build_time * 1000,
        'compute_time_ms': compute_time * 1000,
        'num_scores': len(scores),
        'scores_sum': sum(scores.values()),
        'top_score': max(scores.values()) if scores else 0
    }


def run_comparison(graph_size):
    """Run comparison for given graph size."""
    print(f"\n{'='*80}")
    print(f"BENCHMARK: {graph_size} nodes")
    print(f"{'='*80}")

    # Connect to IRIS
    conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
    engine = IRISGraphEngine(conn)

    # Create test graph
    print("\nCreating test graph...")
    node_ids = create_test_graph(conn, graph_size, avg_degree=5)
    seed = [node_ids[0]]

    results = []

    # Benchmark 1: Current Python (baseline)
    print("\n1. Benchmarking current Python implementation (baseline)...")
    result1 = benchmark_current_python(engine, seed)
    results.append(result1)
    print(f"   Time: {result1['time_ms']:.2f}ms")

    # Benchmark 2: IRIS Globals + Python (RECOMMENDED)
    print("\n2. Benchmarking IRIS Globals + Python (GraphRAG-style)...")
    result2 = benchmark_globals_ppr(conn, seed)
    results.append(result2)
    print(f"   Total time: {result2['time_ms']:.2f}ms")
    print(f"   - Global build: {result2['build_time_ms']:.2f}ms")
    print(f"   - PPR compute: {result2['compute_time_ms']:.2f}ms")
    print(f"   Speedup vs baseline: {result1['time_ms'] / result2['time_ms']:.1f}x")

    # Benchmark 3: SciPy Sparse (for reference)
    print("\n3. Benchmarking SciPy sparse matrix (reference)...")
    result3 = benchmark_scipy_sparse(conn, seed)
    if result3:
        results.append(result3)
        print(f"   Total time: {result3['time_ms']:.2f}ms")
        print(f"   - Matrix build: {result3['build_time_ms']:.2f}ms")
        print(f"   - Power iteration: {result3['iter_time_ms']:.2f}ms")
        print(f"   Speedup vs baseline: {result1['time_ms'] / result3['time_ms']:.1f}x")

    # Cleanup
    cleanup_test_graph(conn)
    conn.close()

    return results


def main():
    """Run benchmarks for multiple graph sizes."""

    print("PPR IMPLEMENTATION COMPARISON")
    print("=" * 80)
    print("\nComparing implementations:")
    print("1. Pure Python (current baseline)")
    print("2. IRIS Globals + Python (GraphRAG-style - RECOMMENDED)")
    print("3. SciPy Sparse Matrix (reference)")

    all_results = {}

    # Test at different scales
    for size in [1000, 5000, 10000]:
        results = run_comparison(size)
        all_results[size] = results

    # Summary table
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}\n")

    print(f"{'Graph Size':<15} {'Implementation':<30} {'Time (ms)':<15} {'Speedup':<10}")
    print("-" * 80)

    for size in sorted(all_results.keys()):
        results = all_results[size]
        baseline_time = results[0]['time_ms']

        for result in results:
            speedup = baseline_time / result['time_ms'] if result['time_ms'] > 0 else 0
            speedup_str = f"{speedup:.1f}x" if speedup != 1.0 else "baseline"

            print(f"{size:<15} {result['implementation']:<30} {result['time_ms']:<15.2f} {speedup_str:<10}")

    # Save results
    with open('docs/performance/ppr_comparison_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    print(f"\nResults saved to: docs/performance/ppr_comparison_results.json")


if __name__ == "__main__":
    main()
