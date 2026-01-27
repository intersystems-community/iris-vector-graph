import random
import math
from typing import List, Tuple

class RMATGenerator:
    """
    R-MAT (Recursive Matrix) Graph Generator
    Produces scale-free graphs with power-law degree distributions.
    """
    def __init__(self, a=0.57, b=0.19, c=0.19, d=0.05):
        # Default Kronecker parameters from Graph500
        self.a = a
        self.b = b
        self.c = c
        self.d = d
        assert math.isclose(a + b + c + d, 1.0), "Probabilities must sum to 1.0"

    def generate_edges(self, num_nodes: int, num_edges: int) -> List[Tuple[int, int]]:
        """
        Generates edges using the R-MAT algorithm.
        """
        scale = math.ceil(math.log2(num_nodes))
        edges = []
        
        for _ in range(num_edges):
            u, v = 1, 1
            for _ in range(scale):
                r = random.random()
                if r < self.a:
                    pass
                elif r < self.a + self.b:
                    v += 2**(scale - _ - 1)
                elif r < self.a + self.b + self.c:
                    u += 2**(scale - _ - 1)
                else:
                    u += 2**(scale - _ - 1)
                    v += 2**(scale - _ - 1)
            
            # Map back to num_nodes range
            u = (u - 1) % num_nodes
            v = (v - 1) % num_nodes
            edges.append((u, v))
            
        return edges

def save_as_csv(edges: List[Tuple[int, int]], filename: str):
    with open(filename, 'w') as f:
        f.write("s,p,o_id\n")
        for s, o in edges:
            f.write(f"node_{s},related_to,node_{o}\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate synthetic scale-free graph")
    parser.add_argument("--nodes", type=int, default=10000, help="Number of nodes")
    parser.add_argument("--edges", type=int, default=100000, help="Number of edges")
    parser.add_argument("--output", type=str, default="synthetic_graph.csv", help="Output CSV file")
    
    args = parser.parse_args()
    
    print(f"Generating R-MAT graph: {args.nodes} nodes, {args.edges} edges...")
    gen = RMATGenerator()
    edges = gen.generate_edges(args.nodes, args.edges)
    
    print(f"Saving to {args.output}...")
    save_as_csv(edges, args.output)
    print("Done.")
