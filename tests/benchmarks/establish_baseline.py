import iris
import json
import time
import random
import math
from typing import List, Tuple, Dict

class RMATGenerator:
    def __init__(self, a=0.57, b=0.19, c=0.19, d=0.05):
        self.a = a
        self.b = b
        self.c = c
        self.d = d

    def generate_edges(self, num_nodes: int, num_edges: int) -> List[Tuple[int, int]]:
        scale = math.ceil(math.log2(num_nodes))
        edges = []
        for _ in range(num_edges):
            u, v = 1, 1
            for _ in range(scale):
                r = random.random()
                if r < self.a: pass
                elif r < self.a + self.b: v += 2**(scale - _ - 1)
                elif r < self.a + self.b + self.c: u += 2**(scale - _ - 1)
                else:
                    u += 2**(scale - _ - 1)
                    v += 2**(scale - _ - 1)
            u = (u - 1) % num_nodes
            v = (v - 1) % num_nodes
            edges.append((u, v))
        return edges

def setup_data(num_nodes, num_edges):
    print(f"Generating and Loading {num_edges} edges...")
    gen = RMATGenerator()
    edges = gen.generate_edges(num_nodes, num_edges)
    
    # Use iris module for SQL
    try:
        conn = iris.connect(hostname='localhost', port=1972, namespace='USER', username='_SYSTEM', password='SYS')
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM SQLUser.rdf_edges")
        cursor.execute("DELETE FROM SQLUser.rdf_props")
        cursor.execute("DELETE FROM SQLUser.rdf_labels")
        cursor.execute("DELETE FROM SQLUser.nodes")
        
        print("Populating nodes...")
        for i in range(num_nodes):
            cursor.execute("INSERT INTO SQLUser.nodes (node_id) VALUES (?)", [f"node_{i}"])
            
        print("Populating edges...")
        for s, o in edges:
            cursor.execute("INSERT INTO SQLUser.rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, '{}')", 
                           [f"node_{s}", "related_to", f"node_{o}"])
        
        conn.commit()
        conn.close()
        
        print("Rebuilding index...")
        iris.cls("Graph.KG.Traversal").BuildKG()
        print("Setup complete.")
    except Exception as e:
        print(f"Setup FAILED: {e}")

def bfs_json_baseline(srcId, max_hops):
    g = iris.gref("^KG")
    src = str(srcId)
    
    seen = set()
    seen.add(src)
    frontier = [src]
    out = iris.cls('%DynamicArray')._New()
    path_id = 0

    for hop in range(1, max_hops + 1):
        if not frontier: break
        next_front = set()
        
        for s in frontier:
            p = ""
            while True:
                p = g.next("out", s, p)
                if p == "": break
                o = ""
                while True:
                    o = g.next("out", s, p, o)
                    if o == "": break
                    path_id += 1
                    # Baseline uses JSON round-trip
                    stepObj = {"id": path_id, "step": hop, "s": s, "p": p, "o": o}
                    out._Push(iris.cls('%DynamicObject')._FromJSON(json.dumps(stepObj)))
                    next_front.add(o)
        
        new_nodes = next_front - seen
        seen.update(new_nodes)
        frontier = list(new_nodes)
    return out

def run_benchmark():
    print("Establish IRIS Baseline...")
    setup_data(10000, 50000)
    
    depths = [1, 2, 3, 4, 5, 6]
    results = {}
    
    for depth in depths:
        print(f"Benchmarking Depth {depth}...")
        latencies = []
        counts = []
        for _ in range(3):
            start = time.time()
            res = bfs_json_baseline("node_0", depth)
            latencies.append(time.time() - start)
            counts.append(res._Size())
        
        avg_lat = sum(latencies) / len(latencies)
        avg_cnt = sum(counts) / len(counts)
        print(f"  Depth {depth}: {avg_lat:.4f}s, {avg_cnt} edges reached")
        results[depth] = {"latency": avg_lat, "count": avg_cnt}
        
    with open("/tmp/iris_baseline.json", "w") as f:
        json.dump(results, f)

if __name__ == "__main__":
    run_benchmark()
