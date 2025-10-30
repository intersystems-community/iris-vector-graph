#!/usr/bin/env python3
"""
TSP Demo - Traveling Salesman Problem on IRIS Protein Graph

Demonstrates multiple TSP algorithms on biomedical protein interaction network.
Uses existing STRING database with 10K proteins and 37K interactions.

Usage:
    python scripts/algorithms/tsp_demo.py --proteins 10 --method greedy
    python scripts/algorithms/tsp_demo.py --proteins 15 --method christofides
    python scripts/algorithms/tsp_demo.py --compare-methods
"""

import iris
import os
import sys
import time
import argparse
from dotenv import load_dotenv
from typing import List, Tuple, Dict
import networkx as nx

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))


class ProteinTSPSolver:
    """TSP solver for protein interaction networks using IRIS + NetworkX"""

    def __init__(self, connection):
        self.conn = connection

    def get_random_proteins(self, count: int = 10) -> List[Tuple[str, str]]:
        """Get random proteins from database for TSP demo"""
        cursor = self.conn.cursor()

        # Get random proteins that have interactions (degree > 2)
        cursor.execute("""
            SELECT DISTINCT s
            FROM rdf_edges
            WHERE s LIKE 'protein:%'
            LIMIT ?
        """, (count * 2,))  # Get extras in case some have low connectivity

        proteins = []
        for row in cursor.fetchall():
            protein_id = row[0]

            # Get protein name
            cursor.execute("""
                SELECT val FROM rdf_props
                WHERE s = ? AND key = 'preferred_name'
            """, (protein_id,))

            result = cursor.fetchone()
            if result:
                proteins.append((protein_id, result[0]))

            if len(proteins) >= count:
                break

        return proteins

    def build_protein_graph(self, protein_ids: List[str]) -> nx.Graph:
        """Build NetworkX graph from IRIS protein interaction data"""
        cursor = self.conn.cursor()

        # Get all edges between selected proteins
        placeholders = ','.join(['?' for _ in protein_ids])
        query = f"""
            SELECT s, o_id, qualifiers
            FROM rdf_edges
            WHERE s IN ({placeholders}) AND o_id IN ({placeholders})
        """

        cursor.execute(query, protein_ids + protein_ids)
        edges = cursor.fetchall()

        print(f"  Found {len(edges)} direct interactions between {len(protein_ids)} proteins")

        G = nx.Graph()
        G.add_nodes_from(protein_ids)

        for source, target, qualifiers_json in edges:
            # Parse STRING confidence score from qualifiers
            try:
                qualifiers = eval(qualifiers_json) if qualifiers_json else {}
                # STRING confidence is 0-1000
                confidence = float(qualifiers.get('combined_score', qualifiers.get('score', 500)))
                # Convert to distance: high confidence = low cost
                weight = 1000 - confidence
            except Exception as e:
                print(f"  Warning: Could not parse qualifiers for {source}->{target}: {e}")
                weight = 500  # Default weight

            G.add_edge(source, target, weight=weight, confidence=confidence)

        # Check connectivity
        if not nx.is_connected(G):
            print(f"  Warning: Graph is not fully connected ({nx.number_connected_components(G)} components)")
            # Keep only largest component
            largest_cc = max(nx.connected_components(G), key=len)
            G = G.subgraph(largest_cc).copy()
            print(f"  Using largest component with {len(G.nodes())} nodes")

        return G

    def make_complete_graph(self, G: nx.Graph) -> nx.Graph:
        """Create complete graph with shortest path distances between all pairs"""
        print(f"  Computing all-pairs shortest paths...")
        complete_G = nx.Graph()
        complete_G.add_nodes_from(G.nodes())

        # Compute all-pairs shortest paths using Floyd-Warshall
        try:
            all_pairs = dict(nx.all_pairs_dijkstra_path_length(G, weight='weight'))
        except nx.NetworkXNoPath:
            print("  Warning: Graph has disconnected components, using largest component")
            largest_cc = max(nx.connected_components(G), key=len)
            G = G.subgraph(largest_cc).copy()
            all_pairs = dict(nx.all_pairs_dijkstra_path_length(G, weight='weight'))

        for source, lengths in all_pairs.items():
            for target, distance in lengths.items():
                if source != target:
                    complete_G.add_edge(source, target, weight=distance)

        return complete_G

    def solve_greedy(self, G: nx.Graph, start_node: str) -> Tuple[List[str], float]:
        """Greedy nearest neighbor - O(n^2) - fast but 1-2x optimal"""
        route = [start_node]
        unvisited = set(G.nodes()) - {start_node}
        current = start_node
        total_cost = 0.0

        while unvisited:
            # Find nearest unvisited neighbor
            nearest = None
            min_dist = float('inf')

            for node in unvisited:
                if G.has_edge(current, node):
                    dist = G[current][node]['weight']
                    if dist < min_dist:
                        min_dist = dist
                        nearest = node

            if nearest is None:
                print(f"  Warning: No path from {current} to remaining nodes")
                break

            route.append(nearest)
            total_cost += min_dist
            unvisited.remove(nearest)
            current = nearest

        # Return to start
        if G.has_edge(route[-1], route[0]):
            total_cost += G[route[-1]][route[0]]['weight']

        return route, total_cost

    def solve_christofides(self, G: nx.Graph) -> Tuple[List[str], float]:
        """Christofides algorithm - O(n^3) - guaranteed 1.5x optimal"""
        try:
            route = nx.approximation.christofides(G, weight='weight')
            total_cost = sum(G[route[i]][route[i+1]]['weight'] for i in range(len(route)-1))
            total_cost += G[route[-1]][route[0]]['weight']  # Return to start
            return route, total_cost
        except Exception as e:
            print(f"  Christofides failed: {e}")
            # Fallback to greedy
            return self.solve_greedy(G, list(G.nodes())[0])

    def solve_two_opt(self, G: nx.Graph, initial_route: List[str], max_iterations: int = 1000) -> Tuple[List[str], float]:
        """2-opt improvement - O(n^2 * iterations) - improves greedy solution"""
        route = initial_route.copy()
        improved = True
        iteration = 0

        def route_cost(r):
            cost = sum(G[r[i]][r[i+1]]['weight'] for i in range(len(r)-1))
            cost += G[r[-1]][r[0]]['weight']  # Return to start
            return cost

        while improved and iteration < max_iterations:
            improved = False
            iteration += 1

            for i in range(1, len(route) - 1):
                for j in range(i + 1, len(route)):
                    # Try reversing route[i:j]
                    new_route = route[:i] + route[i:j][::-1] + route[j:]

                    if route_cost(new_route) < route_cost(route):
                        route = new_route
                        improved = True
                        break

                if improved:
                    break

        return route, route_cost(route)

    def get_protein_names(self, protein_ids: List[str]) -> Dict[str, str]:
        """Get human-readable names for proteins"""
        cursor = self.conn.cursor()
        names = {}

        for protein_id in protein_ids:
            cursor.execute("""
                SELECT val FROM rdf_props
                WHERE s = ? AND key = 'preferred_name'
            """, (protein_id,))

            result = cursor.fetchone()
            if result:
                names[protein_id] = result[0]
            else:
                # Extract ENSP ID from full protein ID
                names[protein_id] = protein_id.split('.')[-1] if '.' in protein_id else protein_id

        return names

    def print_route(self, route: List[str], cost: float, names: Dict[str, str]):
        """Pretty print TSP route"""
        print(f"\n  Total Cost: {cost:.2f}")
        print(f"  Route ({len(route)} proteins):")
        for i, protein_id in enumerate(route, 1):
            name = names.get(protein_id, protein_id)
            print(f"    {i:2d}. {name}")
        print(f"    └─> Return to {names.get(route[0], route[0])}")


def main():
    parser = argparse.ArgumentParser(description='TSP Demo on IRIS Protein Network')
    parser.add_argument('--proteins', type=int, default=10, help='Number of proteins (default: 10)')
    parser.add_argument('--method', choices=['greedy', 'christofides', '2opt'], default='greedy')
    parser.add_argument('--compare-methods', action='store_true', help='Compare all methods')
    parser.add_argument('--seed-proteins', nargs='+', help='Specific protein IDs to use')

    args = parser.parse_args()

    # Load environment and connect to IRIS
    load_dotenv()

    print("=" * 80)
    print("TSP Demo - Protein Interaction Network")
    print("=" * 80)

    try:
        conn = iris.connect(
            hostname=os.getenv('IRIS_HOST', 'localhost'),
            port=int(os.getenv('IRIS_PORT', 1972)),
            namespace=os.getenv('IRIS_NAMESPACE', 'USER'),
            username=os.getenv('IRIS_USER', '_SYSTEM'),
            password=os.getenv('IRIS_PASSWORD', 'SYS')
        )
        print("✅ Connected to IRIS")

        solver = ProteinTSPSolver(conn)

        # Get proteins to solve for
        if args.seed_proteins:
            # Use provided protein IDs
            protein_ids = [f"protein:9606.{p}" if not p.startswith('protein:') else p
                          for p in args.seed_proteins]
            proteins = [(pid, "") for pid in protein_ids]
        else:
            # Get random proteins
            print(f"\n📊 Selecting {args.proteins} random proteins...")
            proteins = solver.get_random_proteins(args.proteins)

        protein_ids = [p[0] for p in proteins]
        print(f"Selected proteins: {[p[1] or p[0] for p in proteins[:5]]}...")

        # Build graph
        print(f"\n🔗 Building protein interaction graph...")
        G = solver.build_protein_graph(protein_ids)

        if len(G.nodes()) < 3:
            print("❌ Error: Not enough connected proteins for TSP")
            return

        # Make complete graph (compute shortest paths between all pairs)
        complete_G = solver.make_complete_graph(G)
        print(f"  Complete graph: {len(complete_G.nodes())} nodes, {len(complete_G.edges())} edges")

        # Get protein names for output
        names = solver.get_protein_names(list(complete_G.nodes()))

        if args.compare_methods:
            # Compare all methods
            print(f"\n🧪 Comparing TSP Methods")
            print("=" * 80)

            methods = [
                ('Greedy', lambda: solver.solve_greedy(complete_G, list(complete_G.nodes())[0])),
                ('Christofides', lambda: solver.solve_christofides(complete_G)),
                ('2-opt', lambda: solver.solve_two_opt(
                    complete_G,
                    solver.solve_greedy(complete_G, list(complete_G.nodes())[0])[0]
                ))
            ]

            results = []
            for method_name, method_func in methods:
                print(f"\n{method_name}:")
                start = time.time()
                route, cost = method_func()
                elapsed = (time.time() - start) * 1000

                print(f"  ⏱️  Time: {elapsed:.2f}ms")
                solver.print_route(route, cost, names)

                results.append((method_name, cost, elapsed))

            # Summary comparison
            print("\n" + "=" * 80)
            print("📊 Summary:")
            print(f"{'Method':<15} {'Cost':<15} {'Time (ms)':<15}")
            print("-" * 45)
            for method_name, cost, elapsed in results:
                print(f"{method_name:<15} {cost:<15.2f} {elapsed:<15.2f}")

        else:
            # Run single method
            print(f"\n🎯 Solving TSP using {args.method.upper()} method...")

            start = time.time()

            if args.method == 'greedy':
                route, cost = solver.solve_greedy(complete_G, list(complete_G.nodes())[0])
            elif args.method == 'christofides':
                route, cost = solver.solve_christofides(complete_G)
            elif args.method == '2opt':
                initial_route, _ = solver.solve_greedy(complete_G, list(complete_G.nodes())[0])
                route, cost = solver.solve_two_opt(complete_G, initial_route)

            elapsed = (time.time() - start) * 1000

            print(f"  ⏱️  Solved in {elapsed:.2f}ms")
            solver.print_route(route, cost, names)

        conn.close()
        print("\n✅ Complete")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
