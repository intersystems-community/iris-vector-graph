import iris
import json
import time
import os

def run_os_benchmark():
    print("="*60)
    print("IRIS ObjectScript BFS Benchmark")
    print("="*60)
    
    results = {}
    traversal = iris.cls("Graph.KG.Traversal")
    
    src_node = "node_0"
    runs = 3
    
    # Measure 1 to 6 hops
    for depth in range(1, 7):
        print(f"Benchmarking Depth {depth}...")
        latencies = []
        counts = []
        
        for r in range(runs):
            start = time.time()
            # Call the pure ObjectScript BFS
            res_da = traversal.BFS(src_node, None, depth)
            duration = time.time() - start
            
            latencies.append(duration)
            counts.append(res_da._Size())
            
        avg_lat = sum(latencies) / runs
        avg_cnt = sum(counts) / runs
        print(f"  Depth {depth}: {avg_lat:.4f}s, {avg_cnt:.0f} steps")
        results[str(depth)] = {"avg_latency": avg_lat, "avg_count": avg_cnt}
        
    output_file = "/tmp/iris_os_benchmarks.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_file}")

if __name__ == "__main__":
    run_os_benchmark()
