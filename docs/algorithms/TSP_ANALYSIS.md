# Traveling Salesman Problem (TSP) on IRIS Graph Database

## Overview

The Traveling Salesman Problem: Given a set of nodes and weighted edges, find the shortest route that visits each node exactly once and returns to the start.

**Complexity**: NP-hard - no polynomial-time exact solution for large graphs
**Practical Approaches**: Heuristics and approximations for non-trivial graphs

---

## Neo4j Approach

### 1. **APOC Library** (Awesome Procedures on Cypher)

```cypher
// Greedy nearest neighbor heuristic
MATCH (start:City {name: 'New York'})
CALL apoc.path.expandConfig(start, {
    relationshipFilter: "ROAD",
    minLevel: 1,
    maxLevel: 10,
    uniqueness: "NODE_GLOBAL",
    bfs: false
}) YIELD path
RETURN path, reduce(dist = 0, r in relationships(path) | dist + r.distance) AS totalDistance
ORDER BY totalDistance ASC
LIMIT 1
```

### 2. **Graph Data Science (GDS) Library**

```cypher
// Create graph projection
CALL gds.graph.project(
  'tsp-graph',
  'City',
  'ROAD',
  {relationshipProperties: 'distance'}
)

// Run TSP approximation (Christofides algorithm - 1.5x optimal)
CALL gds.alpha.tsp.stream('tsp-graph', {
  startNode: id(start),
  relationshipWeightProperty: 'distance'
})
YIELD index, nodeId, cost
RETURN gds.util.asNode(nodeId).name AS city, cost
```

### 3. **Custom Cypher Heuristics**

**Greedy Nearest Neighbor**:
```cypher
// Start from a node, always pick nearest unvisited neighbor
MATCH (start:City {name: 'New York'})
WITH start, collect(distinct c) AS cities
UNWIND cities AS city
MATCH path = shortestPath((start)-[:ROAD*]-(city))
WITH cities, start, city,
     reduce(dist = 0, r in relationships(path) | dist + r.distance) AS distance
ORDER BY distance ASC
RETURN city.name, distance
```

**2-opt Improvement**:
- Take initial tour, repeatedly swap edges to reduce total distance
- Implemented as custom Java procedure for performance

---

## IRIS Implementation Approaches

### Approach 1: **Embedded Python + NetworkX** (RECOMMENDED for prototyping)

**Advantages**:
- Leverage mature NetworkX TSP algorithms
- Fast prototyping
- Access to multiple heuristics (greedy, 2-opt, Christofides, genetic)

**Implementation**:

```python
# iris_vector_graph_core/algorithms/tsp.py
import networkx as nx
import iris
from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class IRISTSPSolver:
    """TSP solver using IRIS graph data and NetworkX algorithms"""

    def __init__(self, connection):
        self.conn = connection

    def solve_tsp(
        self,
        node_ids: List[str],
        method: str = "greedy",
        weight_property: str = "confidence",
        return_to_start: bool = True
    ) -> Tuple[List[str], float]:
        """
        Solve TSP for given nodes using IRIS graph data

        Args:
            node_ids: List of node IDs to visit
            method: 'greedy', '2opt', 'christofides', 'genetic'
            weight_property: Edge property to use as weight (default: confidence)
            return_to_start: Whether route should return to starting node

        Returns:
            (route, total_cost) where route is list of node IDs
        """
        # 1. Build NetworkX graph from IRIS data
        G = self._build_networkx_graph(node_ids, weight_property)

        # 2. Create complete graph with shortest paths between all pairs
        complete_G = self._make_complete_graph(G, node_ids)

        # 3. Solve TSP using selected method
        route = self._solve_with_method(complete_G, method, node_ids)

        # 4. Calculate total cost
        total_cost = self._calculate_route_cost(complete_G, route, return_to_start)

        return route, total_cost

    def _build_networkx_graph(self, node_ids: List[str], weight_property: str) -> nx.Graph:
        """Build NetworkX graph from IRIS rdf_edges data"""
        cursor = self.conn.cursor()

        # Get all edges between nodes in node_ids
        placeholders = ','.join(['?' for _ in node_ids])
        query = f"""
            SELECT s, o_id, qualifiers
            FROM rdf_edges
            WHERE s IN ({placeholders}) AND o_id IN ({placeholders})
        """

        cursor.execute(query, node_ids + node_ids)
        edges = cursor.fetchall()

        G = nx.Graph()
        G.add_nodes_from(node_ids)

        for source, target, qualifiers_json in edges:
            # Parse weight from qualifiers (e.g., STRING confidence score)
            try:
                qualifiers = eval(qualifiers_json) if qualifiers_json else {}
                # Convert confidence (0-1000) to distance (lower confidence = higher cost)
                confidence = float(qualifiers.get(weight_property, 500))
                weight = 1000 - confidence  # Invert: high confidence = low cost
            except:
                weight = 500  # Default weight

            G.add_edge(source, target, weight=weight)

        return G

    def _make_complete_graph(self, G: nx.Graph, node_ids: List[str]) -> nx.Graph:
        """Create complete graph with shortest path distances between all pairs"""
        complete_G = nx.Graph()
        complete_G.add_nodes_from(node_ids)

        # Compute all-pairs shortest paths
        for source in node_ids:
            lengths = nx.single_source_dijkstra_path_length(G, source, weight='weight')
            for target, distance in lengths.items():
                if target in node_ids and source != target:
                    complete_G.add_edge(source, target, weight=distance)

        return complete_G

    def _solve_with_method(
        self,
        G: nx.Graph,
        method: str,
        node_ids: List[str]
    ) -> List[str]:
        """Apply selected TSP algorithm"""

        if method == "greedy":
            # Greedy nearest neighbor (fast, 1-2x optimal)
            return self._greedy_tsp(G, node_ids[0])

        elif method == "2opt":
            # 2-opt improvement (better, slower)
            initial_route = self._greedy_tsp(G, node_ids[0])
            return self._two_opt(G, initial_route)

        elif method == "christofides":
            # Christofides algorithm (1.5x optimal guarantee)
            return nx.approximation.christofides(G, weight='weight')

        elif method == "genetic":
            # Genetic algorithm (good for larger graphs)
            return self._genetic_tsp(G, node_ids)

        else:
            raise ValueError(f"Unknown method: {method}")

    def _greedy_tsp(self, G: nx.Graph, start_node: str) -> List[str]:
        """Greedy nearest neighbor heuristic"""
        route = [start_node]
        unvisited = set(G.nodes()) - {start_node}
        current = start_node

        while unvisited:
            # Find nearest unvisited neighbor
            nearest = min(
                unvisited,
                key=lambda node: G[current][node]['weight'] if G.has_edge(current, node) else float('inf')
            )
            route.append(nearest)
            unvisited.remove(nearest)
            current = nearest

        return route

    def _two_opt(self, G: nx.Graph, route: List[str], max_iterations: int = 1000) -> List[str]:
        """2-opt improvement heuristic"""
        improved = True
        iteration = 0

        while improved and iteration < max_iterations:
            improved = False
            iteration += 1

            for i in range(1, len(route) - 1):
                for j in range(i + 1, len(route)):
                    # Try reversing route[i:j]
                    new_route = route[:i] + route[i:j][::-1] + route[j:]

                    # Calculate cost improvement
                    old_cost = self._calculate_route_cost(G, route, return_to_start=True)
                    new_cost = self._calculate_route_cost(G, new_route, return_to_start=True)

                    if new_cost < old_cost:
                        route = new_route
                        improved = True
                        break

                if improved:
                    break

        return route

    def _genetic_tsp(self, G: nx.Graph, node_ids: List[str],
                     population_size: int = 100,
                     generations: int = 500) -> List[str]:
        """Genetic algorithm for TSP"""
        import random

        def create_individual():
            """Random tour"""
            nodes = node_ids.copy()
            random.shuffle(nodes)
            return nodes

        def fitness(route):
            """Negative cost (higher is better)"""
            return -self._calculate_route_cost(G, route, return_to_start=True)

        def crossover(parent1, parent2):
            """Order crossover (OX)"""
            size = len(parent1)
            start, end = sorted(random.sample(range(size), 2))

            child = [None] * size
            child[start:end] = parent1[start:end]

            pointer = end
            for city in parent2[end:] + parent2[:end]:
                if city not in child:
                    child[pointer % size] = city
                    pointer += 1

            return child

        def mutate(route, mutation_rate=0.01):
            """Swap mutation"""
            if random.random() < mutation_rate:
                i, j = random.sample(range(len(route)), 2)
                route[i], route[j] = route[j], route[i]
            return route

        # Initialize population
        population = [create_individual() for _ in range(population_size)]

        for generation in range(generations):
            # Evaluate fitness
            population = sorted(population, key=fitness, reverse=True)

            # Selection and reproduction
            new_population = population[:population_size // 10]  # Keep top 10%

            while len(new_population) < population_size:
                parent1, parent2 = random.sample(population[:population_size // 2], 2)
                child = crossover(parent1, parent2)
                child = mutate(child)
                new_population.append(child)

            population = new_population

        return max(population, key=fitness)

    def _calculate_route_cost(self, G: nx.Graph, route: List[str], return_to_start: bool) -> float:
        """Calculate total cost of a route"""
        cost = 0.0

        for i in range(len(route) - 1):
            if G.has_edge(route[i], route[i + 1]):
                cost += G[route[i]][route[i + 1]]['weight']
            else:
                cost += float('inf')  # No path exists

        if return_to_start and len(route) > 1:
            if G.has_edge(route[-1], route[0]):
                cost += G[route[-1]][route[0]]['weight']
            else:
                cost += float('inf')

        return cost

    def get_route_details(self, route: List[str]) -> List[dict]:
        """Get detailed information about nodes in route"""
        cursor = self.conn.cursor()

        details = []
        for node_id in route:
            cursor.execute("""
                SELECT key, val
                FROM rdf_props
                WHERE s = ?
            """, (node_id,))

            props = {row[0]: row[1] for row in cursor.fetchall()}
            details.append({
                'node_id': node_id,
                'properties': props
            })

        return details


# Example usage with protein interaction network
if __name__ == "__main__":
    import iris
    import os
    from dotenv import load_dotenv

    load_dotenv()

    conn = iris.connect(
        hostname=os.getenv('IRIS_HOST', 'localhost'),
        port=int(os.getenv('IRIS_PORT', 1972)),
        namespace=os.getenv('IRIS_NAMESPACE', 'USER'),
        username=os.getenv('IRIS_USER', '_SYSTEM'),
        password=os.getenv('IRIS_PASSWORD', 'SYS')
    )

    solver = IRISTSPSolver(conn)

    # Example: Find optimal tour through 10 cancer-related proteins
    proteins = [
        "protein:9606.ENSP00000269305",  # TP53
        "protein:9606.ENSP00000275493",  # EGFR
        "protein:9606.ENSP00000288602",  # BRCA1
        "protein:9606.ENSP00000350283",  # KRAS
        "protein:9606.ENSP00000344818",  # MYC
        "protein:9606.ENSP00000361021",  # AKT1
        "protein:9606.ENSP00000263967",  # PTEN
        "protein:9606.ENSP00000257904",  # RB1
        "protein:9606.ENSP00000306474",  # MDM2
        "protein:9606.ENSP00000252699",  # CDKN1A (p21)
    ]

    print("Solving TSP for cancer protein network...")
    print(f"Nodes: {len(proteins)}")

    for method in ["greedy", "2opt", "christofides"]:
        print(f"\n{method.upper()} method:")
        route, cost = solver.solve_tsp(proteins, method=method)

        details = solver.get_route_details(route)
        print(f"Total cost: {cost:.2f}")
        print("Route:")
        for i, node_info in enumerate(details, 1):
            name = node_info['properties'].get('preferred_name', node_info['node_id'])
            print(f"  {i}. {name}")

    conn.close()
```

---

### Approach 2: **IRIS SQL Procedure** (For production performance)

```objectscript
/// TSP solver using IRIS ObjectScript
Class Graph.TSP Extends %RegisteredObject
{

/// Greedy nearest neighbor TSP
ClassMethod SolveGreedy(
    nodeIds As %List,
    weightProperty As %String = "confidence",
    Output route As %List,
    Output cost As %Float) As %Status
{
    Set cost = 0
    Set route = ""
    Set visited = ##class(%ArrayOfDataTypes).%New()

    // Start from first node
    Set current = $ListGet(nodeIds, 1)
    Set $List(route, *+1) = current
    Set visited.SetAt(1, current)

    // Greedy selection
    For i=1:1:($ListLength(nodeIds)-1) {
        Set nearest = ""
        Set minDist = 999999

        // Find nearest unvisited neighbor
        Set sql = "SELECT o_id, qualifiers FROM rdf_edges WHERE s = ?"
        Set stmt = ##class(%SQL.Statement).%New()
        Do stmt.%Prepare(sql)
        Set rs = stmt.%Execute(current)

        While rs.%Next() {
            Set neighbor = rs.%Get("o_id")
            Continue:visited.IsDefined(neighbor)

            // Calculate distance from weight
            Set qualifiers = $ZConvert(rs.%Get("qualifiers"), "I", "JSON")
            Set weight = qualifiers.%Get(weightProperty)
            Set dist = 1000 - weight  // Invert confidence to distance

            If (dist < minDist) {
                Set minDist = dist
                Set nearest = neighbor
            }
        }

        If (nearest '= "") {
            Set $List(route, *+1) = nearest
            Set visited.SetAt(1, nearest)
            Set cost = cost + minDist
            Set current = nearest
        }
    }

    // Return to start
    Set start = $ListGet(nodeIds, 1)
    Set sql = "SELECT qualifiers FROM rdf_edges WHERE s = ? AND o_id = ?"
    Set rs = ##class(%SQL.Statement).%ExecDirect(, sql, current, start)
    If rs.%Next() {
        Set qualifiers = $ZConvert(rs.%Get("qualifiers"), "I", "JSON")
        Set weight = qualifiers.%Get(weightProperty)
        Set cost = cost + (1000 - weight)
    }

    Quit $$$OK
}

}
```

---

### Approach 3: **Hybrid IRIS + Python**

**Best of both worlds**:
- Python for complex algorithms (NetworkX, genetic algorithms)
- IRIS SQL for data retrieval and graph queries
- IRIS ObjectScript for performance-critical path operations

```python
# Use IRIS stored procedures for hot paths
def solve_tsp_hybrid(self, node_ids, method="greedy"):
    """Hybrid approach: IRIS for data, Python for algorithm"""

    # 1. Get graph data from IRIS efficiently
    iris_result = self.conn.execute_stored_proc(
        "Graph.TSP.GetCompleteGraph",
        node_ids
    )

    # 2. Build NetworkX graph from IRIS result
    G = self._iris_result_to_networkx(iris_result)

    # 3. Solve with NetworkX
    route = nx.approximation.christofides(G, weight='weight')

    # 4. Store result back in IRIS for caching
    self.conn.execute_stored_proc(
        "Graph.TSP.CacheResult",
        route,
        self._calculate_route_cost(G, route)
    )

    return route
```

---

## Comparison: Neo4j vs IRIS

| Feature | Neo4j | IRIS |
|---------|-------|------|
| **Native Graph** | Yes (property graph) | Yes (RDF-style with rdf_edges) |
| **TSP Algorithms** | APOC, GDS library | Custom (Python/ObjectScript) |
| **Performance** | Optimized for graph traversal | Optimized for hybrid workloads |
| **Extensibility** | Java procedures | Python, ObjectScript, SQL |
| **Vector Integration** | Separate (Neo4j Vector) | Built-in (HNSW) |
| **Bitemporal** | Custom implementation | Native support |

---

## Use Cases in Biomedical Graph

### 1. **Optimal Pathway Through Drug Targets**
Find shortest "route" through a set of cancer drug targets to understand sequential treatment strategies:

```python
# Find optimal order to target proteins in cancer pathway
drug_targets = [
    "protein:9606.ENSP00000269305",  # TP53
    "protein:9606.ENSP00000275493",  # EGFR
    "protein:9606.ENSP00000350283",  # KRAS
    # ... more targets
]

route, cost = solver.solve_tsp(drug_targets, method="christofides")
print(f"Optimal targeting sequence: {route}")
print(f"Total pathway distance: {cost}")
```

### 2. **Multi-Site Clinical Trial Optimization**
If nodes represent hospitals/clinics with patient populations, TSP finds optimal order for sequential trial phases:

```python
# Optimize clinical trial site visits
trial_sites = ["hospital:mgh", "hospital:mayo", "hospital:jhh", ...]
route, cost = solver.solve_tsp(trial_sites, weight_property="travel_time")
```

### 3. **Protein Complex Assembly Order**
Determine optimal order for proteins to assemble into a complex based on binding affinities:

```python
# Find assembly sequence for ribosome subunits
ribosome_proteins = [...]  # 50+ ribosomal proteins
route, cost = solver.solve_tsp(
    ribosome_proteins,
    weight_property="binding_affinity",
    method="genetic"  # Better for larger graphs
)
```

---

## Scalability Considerations

### For graphs with < 20 nodes:
- **Exact algorithms** possible (branch & bound, dynamic programming)
- Runtime: seconds to minutes
- Use NetworkX `traveling_salesman_problem()` with exact solver

### For graphs with 20-100 nodes:
- **Heuristics recommended**: Greedy, 2-opt, Christofides
- Runtime: milliseconds to seconds
- IRIS embedded Python is sufficient

### For graphs with 100-1000 nodes:
- **Metaheuristics**: Genetic algorithms, simulated annealing
- Runtime: seconds to minutes
- Consider IRIS ObjectScript for critical sections

### For graphs with 1000+ nodes:
- **Approximation algorithms**: Lin-Kernighan, Concorde TSP solver
- Runtime: minutes to hours
- May need external optimization libraries (C++)

---

## Recommended Implementation Path

1. **Start with Python + NetworkX** (fastest to prototype)
   - Implement greedy, 2-opt, Christofides
   - Test on biomedical graph (10-50 proteins)
   - Measure performance

2. **Add caching layer in IRIS**
   - Store computed routes in table
   - Use IRIS SQL for fast lookups
   - Cache invalidation on graph updates

3. **Optimize hot paths with ObjectScript**
   - Move critical graph queries to stored procedures
   - Use IRIS globals for intermediate results
   - Benchmark vs pure Python

4. **Add REST API endpoint**
   - FastAPI route: `/api/tsp/solve`
   - Support multiple algorithms
   - Return route + visualization data for D3.js

5. **Integrate with existing demo**
   - Add TSP visualization to biomedical demo
   - Show optimal pathway through selected proteins
   - Interactive algorithm comparison

---

## Next Steps

**To implement TSP in this repo**:

1. Create `iris_vector_graph_core/algorithms/tsp.py` (see code above)
2. Add dependencies: `networkx>=3.0` already in pyproject.toml ✅
3. Write tests: `tests/unit/test_tsp.py`
4. Add demo endpoint: `src/iris_demo_server/routes/biomedical.py`
5. Update README with TSP examples

**Estimated effort**: 4-6 hours for basic implementation, 1-2 days for full integration with visualization

Would you like me to implement this?
