import iris
import json
import time
import sys

def benchmark():
    print("Establish IRIS Baseline (Embedded Python)...")
    
    # 1. Rebuild Index
    print("Rebuilding functional index...")
    start = time.time()
    iris.cls("Graph.KG.Traversal").BuildKG()
    print(f"BuildKG took: {time.time() - start:.2f}s")
    
    # 2. Run Benchmark
    depths = [1, 2, 3, 4, 5, 6]
    results = {}
    preds = iris.cls('%DynamicArray')._New()
    
    for depth in depths:
        print(f"Benchmarking Depth {depth}...")
        latencies = []
        counts = []
        for _ in range(3):
            start = time.time()
            res = iris.cls("Graph.KG.Traversal").BFS_JSON("node_0", preds, depth)
            latencies.append(time.time() - start)
            counts.append(res._Size())
        
        avg_lat = sum(latencies) / len(latencies)
        avg_cnt = sum(counts) / len(counts)
        print(f"  Depth {depth}: {avg_lat:.4f}s, {avg_cnt} edges")
        results[depth] = {"latency": avg_lat, "count": avg_cnt}
        
    with open("/tmp/iris_baseline.json", "w") as f:
        json.dump(results, f)
    print("Results saved to /tmp/iris_baseline.json")

if __name__ == "__main__":
    benchmark()
