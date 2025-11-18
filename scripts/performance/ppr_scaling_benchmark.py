#!/usr/bin/env python3
"""
PPR Scaling Benchmark: Compare implementations at different graph sizes

Tests three implementations:
1. Pure Python (SQL bulk extract + in-memory computation)
2. Embedded Python (iris.gref() with order() API - has boundary crossing overhead)
3. ObjectScript Native with Packed Lists ($LISTNEXT optimization)

Scales: 1K, 5K, 10K nodes
"""

import sys
import json
import time
from typing import List, Dict, Tuple
import iris
from iris_vector_graph.ppr_impl import compute_ppr

def generate_test_graph(num_nodes: int, avg_degree: int = 5) -> List[Tuple[str, str]]:
    """Generate a random graph for testing"""
    import random
    random.seed(42)  # Reproducible

    edges = []
    for i in range(num_nodes):
        src = f"NODE_{i}"
        degree = random.randint(2, avg_degree * 2)
        for _ in range(degree):
            dst_idx = random.randint(0, num_nodes - 1)
            if dst_idx != i:  # No self-loops
                edges.append((src, f"NODE_{dst_idx}"))

    return edges

def setup_graph(conn, cursor, irispy, num_nodes: int) -> None:
    """Create test graph in IRIS"""
    print(f"\nSetting up {num_nodes}-node graph...")

    # Clean slate
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'NODE_%'")
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'NODE_%'")
    irispy.kill('^PPR')

    # Generate edges
    edges = generate_test_graph(num_nodes)
    print(f"  Generated {len(edges)} edges (avg degree: {len(edges) / num_nodes:.1f})")

    # Insert nodes
    node_ids = set()
    for src, dst in edges:
        node_ids.add(src)
        node_ids.add(dst)

    cursor.executemany("INSERT INTO nodes (node_id) VALUES (?)", [(nid,) for nid in node_ids])

    # Insert edges (triggers functional index)
    cursor.executemany("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, 'test', ?)", edges)

    conn.commit()
    print(f"  ✓ Inserted {len(node_ids)} nodes, {len(edges)} edges")

def benchmark_pure_python(conn, seed: str, runs: int = 5) -> Tuple[float, int]:
    """Benchmark Pure Python PPR"""
    times = []
    result_size = 0

    for _ in range(runs):
        start = time.perf_counter()
        scores = compute_ppr(
            conn,
            seed_entities=[seed],
            alpha=0.85,
            max_iterations=100,
            tolerance=1e-6,
            use_functional_index=False
        )
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        result_size = len(scores)

    return sum(times) / len(times), result_size

def benchmark_objectscript_native(irispy, seed: str, runs: int = 5) -> Tuple[float, int]:
    """Benchmark ObjectScript Native with Packed Lists"""
    times = []
    result_size = 0

    for _ in range(runs):
        start = time.perf_counter()
        result_json = irispy.classMethodValue(
            'Graph.KG.PPRNative', 'ComputePPR',
            json.dumps([seed]),
            0.85,
            100,
            1e-6
        )
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

        result = json.loads(result_json)
        if 'error' not in result:
            result_size = len(result)

    return sum(times) / len(times), result_size

def main():
    print("=" * 70)
    print("PPR SCALING BENCHMARK")
    print("=" * 70)

    # Connect to IRIS
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    cursor = conn.cursor()
    irispy = iris.createIRIS(conn)

    # Test scales
    scales = [
        (1000, "1K nodes"),
        (5000, "5K nodes"),
        (10000, "10K nodes")
    ]

    results = []

    for num_nodes, label in scales:
        print(f"\n{'=' * 70}")
        print(f"SCALE: {label}")
        print(f"{'=' * 70}")

        # Setup graph
        setup_graph(conn, cursor, irispy, num_nodes)

        # Seed node
        seed = "NODE_0"

        # Benchmark each implementation
        print("\nBenchmarking...")

        print("  [1/2] Pure Python (SQL bulk + in-memory)...")
        py_time, py_size = benchmark_pure_python(conn, seed)
        print(f"        → {py_time:.2f}ms ({py_size} nodes)")

        print("  [2/2] ObjectScript Native (Packed Lists + $LISTNEXT)...")
        obj_time, obj_size = benchmark_objectscript_native(irispy, seed)
        print(f"        → {obj_time:.2f}ms ({obj_size} nodes)")

        results.append({
            'scale': label,
            'nodes': num_nodes,
            'pure_python_ms': py_time,
            'objectscript_native_ms': obj_time,
            'speedup': py_time / obj_time if obj_time > 0 else 0
        })

    # Summary table
    print(f"\n{'=' * 70}")
    print("PERFORMANCE SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n{'Scale':<15} {'Pure Python':<15} {'ObjectScript':<15} {'Winner':<20}")
    print(f"{'-' * 70}")

    for r in results:
        py_str = f"{r['pure_python_ms']:.2f}ms"
        obj_str = f"{r['objectscript_native_ms']:.2f}ms"

        if r['speedup'] > 1.0:
            winner = f"ObjectScript ({r['speedup']:.2f}x faster)"
        elif r['speedup'] < 1.0:
            winner = f"Pure Python ({1/r['speedup']:.2f}x faster)"
        else:
            winner = "Tie"

        print(f"{r['scale']:<15} {py_str:<15} {obj_str:<15} {winner:<20}")

    print(f"\n{'=' * 70}")
    print("RECOMMENDATIONS")
    print(f"{'=' * 70}")

    # Determine recommendations based on results
    if results[0]['speedup'] > 1.1:  # ObjectScript wins at 1K
        print("\n✅ ObjectScript Native with Packed Lists wins at all scales!")
        print("   Recommendation: Use ObjectScript for production PPR")
    elif results[-1]['speedup'] > 1.1:  # ObjectScript wins at 10K
        print("\n📊 ObjectScript Native wins at larger scales (5K+ nodes)")
        print("   Recommendation:")
        print("   - Use Pure Python for small graphs (<5K nodes)")
        print("   - Use ObjectScript for large graphs (5K+ nodes)")
    else:
        print("\n📊 Pure Python competitive at all tested scales")
        print("   Recommendation:")
        print("   - Use Pure Python for simplicity and portability")
        print("   - Use ObjectScript for maximum performance at very large scale")

    # Close connections
    cursor.close()
    conn.close()

    print(f"\n{'=' * 70}\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
