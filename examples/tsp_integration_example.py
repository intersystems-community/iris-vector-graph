"""
Example: TSP Integration with Biomedical Demo API

This shows how TSP could be added as an endpoint to the existing biomedical demo.

New endpoint: POST /bio/pathway/optimize
- Input: List of protein IDs
- Output: Optimal tour through proteins with distances
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import time
from datetime import datetime

# This would go in src/iris_demo_server/routes/biomedical.py


class TSPRequest(BaseModel):
    """Request to solve TSP on protein network"""

    protein_ids: List[str] = Field(
        ...,
        description="List of protein IDs (ENSP format or full STRING format)",
        min_items=3,
        max_items=50,
        example=["ENSP00000269305", "ENSP00000275493", "ENSP00000288602"]
    )

    method: str = Field(
        default="greedy",
        description="TSP algorithm: 'greedy', 'christofides', or '2opt'",
        example="christofides"
    )

    weight_property: str = Field(
        default="combined_score",
        description="Edge property to use as weight",
        example="combined_score"
    )

    return_to_start: bool = Field(
        default=True,
        description="Whether tour should return to starting protein"
    )


class ProteinInRoute(BaseModel):
    """Protein node in TSP route"""

    protein_id: str = Field(..., description="ENSP protein ID")
    name: str = Field(..., description="Protein name")
    position: int = Field(..., description="Position in route (1-indexed)")
    distance_to_next: Optional[float] = Field(None, description="Distance to next protein in route")


class TSPResponse(BaseModel):
    """Response from TSP solver"""

    route: List[ProteinInRoute] = Field(..., description="Ordered list of proteins in optimal tour")
    total_cost: float = Field(..., description="Total cost of tour")
    method_used: str = Field(..., description="Algorithm used to solve")
    execution_time_ms: int = Field(..., description="Time taken to solve (milliseconds)")
    graph_stats: dict = Field(..., description="Statistics about the protein graph")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# Example route handler (would be added to biomedical.py)
router = APIRouter(prefix="/bio", tags=["biomedical"])


@router.post("/pathway/optimize", response_model=TSPResponse)
async def optimize_pathway(request: TSPRequest):
    """
    Find optimal tour through specified proteins using TSP algorithms.

    This endpoint solves the Traveling Salesman Problem on a protein interaction
    network, finding the shortest "tour" that visits all specified proteins.

    **Use Cases**:
    - Find optimal order to target proteins in drug development
    - Understand sequential interaction pathways
    - Plan multi-target therapeutic strategies

    **Algorithms**:
    - **greedy**: Fast, ~1-2x optimal, O(n²) - recommended for quick results
    - **christofides**: Guaranteed ≤1.5x optimal, O(n³) - best balance
    - **2opt**: Improves greedy solution iteratively - best quality for small graphs

    **Example Request**:
    ```json
    {
        "protein_ids": ["ENSP00000269305", "ENSP00000275493", "ENSP00000288602"],
        "method": "christofides",
        "return_to_start": true
    }
    ```
    """
    start_time = time.time()

    try:
        # Get IRIS client (this would use existing connection from app state)
        from iris_demo_server.services.iris_biomedical_client import IRISBiomedicalClient
        import os

        iris_client = IRISBiomedicalClient(
            host=os.getenv("IRIS_HOST", "localhost"),
            port=int(os.getenv("IRIS_PORT", 1972)),
            namespace=os.getenv("IRIS_NAMESPACE", "USER"),
            username=os.getenv("IRIS_USER", "_SYSTEM"),
            password=os.getenv("IRIS_PASSWORD", "SYS")
        )

        # Import TSP solver (would be in iris_vector_graph_core after implementation)
        import networkx as nx

        # Convert protein IDs to full STRING format
        full_protein_ids = [
            f"protein:9606.{pid}" if not pid.startswith("protein:") else pid
            for pid in request.protein_ids
        ]

        # Build graph from IRIS data
        cursor = iris_client.conn.cursor()

        placeholders = ','.join(['?' for _ in full_protein_ids])
        query = f"""
            SELECT s, o_id, qualifiers
            FROM rdf_edges
            WHERE s IN ({placeholders}) AND o_id IN ({placeholders})
        """

        cursor.execute(query, full_protein_ids + full_protein_ids)
        edges = cursor.fetchall()

        if not edges:
            raise HTTPException(
                status_code=404,
                detail=f"No interactions found between specified proteins"
            )

        # Build NetworkX graph
        G = nx.Graph()
        G.add_nodes_from(full_protein_ids)

        for source, target, qualifiers_json in edges:
            try:
                qualifiers = eval(qualifiers_json) if qualifiers_json else {}
                confidence = float(qualifiers.get(request.weight_property, 500))
                weight = 1000 - confidence  # High confidence = low cost
            except:
                weight = 500

            G.add_edge(source, target, weight=weight)

        # Check connectivity and get largest component if needed
        if not nx.is_connected(G):
            largest_cc = max(nx.connected_components(G), key=len)
            G = G.subgraph(largest_cc).copy()

        if len(G.nodes()) < 3:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient connected proteins (found {len(G.nodes())}, need ≥3)"
            )

        # Make complete graph with shortest paths
        complete_G = nx.Graph()
        complete_G.add_nodes_from(G.nodes())

        all_pairs = dict(nx.all_pairs_dijkstra_path_length(G, weight='weight'))
        for source, lengths in all_pairs.items():
            for target, distance in lengths.items():
                if source != target:
                    complete_G.add_edge(source, target, weight=distance)

        # Solve TSP
        if request.method == "greedy":
            route = _solve_greedy(complete_G, list(complete_G.nodes())[0])
        elif request.method == "christofides":
            route = nx.approximation.christofides(complete_G, weight='weight')
        elif request.method == "2opt":
            initial_route = _solve_greedy(complete_G, list(complete_G.nodes())[0])
            route = _solve_two_opt(complete_G, initial_route)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown method: {request.method}")

        # Calculate total cost
        total_cost = sum(
            complete_G[route[i]][route[i+1]]['weight']
            for i in range(len(route)-1)
        )
        if request.return_to_start:
            total_cost += complete_G[route[-1]][route[0]]['weight']

        # Get protein names
        route_proteins = []
        for idx, protein_id in enumerate(route, 1):
            cursor.execute("""
                SELECT val FROM rdf_props
                WHERE s = ? AND key = 'preferred_name'
            """, (protein_id,))

            result = cursor.fetchone()
            name = result[0] if result else protein_id.split('.')[-1]

            # Distance to next protein
            if idx < len(route):
                dist_to_next = complete_G[route[idx-1]][route[idx]]['weight']
            elif request.return_to_start:
                dist_to_next = complete_G[route[-1]][route[0]]['weight']
            else:
                dist_to_next = None

            route_proteins.append(ProteinInRoute(
                protein_id=protein_id.split('.')[-1],  # Return ENSP format
                name=name,
                position=idx,
                distance_to_next=dist_to_next
            ))

        execution_time_ms = int((time.time() - start_time) * 1000)

        return TSPResponse(
            route=route_proteins,
            total_cost=total_cost,
            method_used=request.method,
            execution_time_ms=execution_time_ms,
            graph_stats={
                "input_proteins": len(request.protein_ids),
                "connected_proteins": len(G.nodes()),
                "direct_interactions": len(edges),
                "complete_graph_edges": len(complete_G.edges())
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TSP solver error: {str(e)}")


def _solve_greedy(G: nx.Graph, start_node: str) -> List[str]:
    """Greedy nearest neighbor"""
    route = [start_node]
    unvisited = set(G.nodes()) - {start_node}
    current = start_node

    while unvisited:
        nearest = min(
            unvisited,
            key=lambda node: G[current][node]['weight'] if G.has_edge(current, node) else float('inf')
        )
        route.append(nearest)
        unvisited.remove(nearest)
        current = nearest

    return route


def _solve_two_opt(G: nx.Graph, route: List[str], max_iterations: int = 1000) -> List[str]:
    """2-opt improvement"""
    def route_cost(r):
        return sum(G[r[i]][r[i+1]]['weight'] for i in range(len(r)-1))

    improved = True
    iteration = 0

    while improved and iteration < max_iterations:
        improved = False
        iteration += 1

        for i in range(1, len(route) - 1):
            for j in range(i + 1, len(route)):
                new_route = route[:i] + route[i:j][::-1] + route[j:]

                if route_cost(new_route) < route_cost(route):
                    route = new_route
                    improved = True
                    break

            if improved:
                break

    return route


# Example curl commands:

"""
# Test with 5 cancer-related proteins (greedy - fastest)
curl -X POST http://localhost:8200/bio/pathway/optimize \
  -H 'Content-Type: application/json' \
  -d '{
    "protein_ids": [
      "ENSP00000269305",
      "ENSP00000275493",
      "ENSP00000288602",
      "ENSP00000350283",
      "ENSP00000344818"
    ],
    "method": "greedy"
  }'

# Test with Christofides algorithm (best balance)
curl -X POST http://localhost:8200/bio/pathway/optimize \
  -H 'Content-Type: application/json' \
  -d '{
    "protein_ids": [
      "ENSP00000269305",
      "ENSP00000275493",
      "ENSP00000288602",
      "ENSP00000350283",
      "ENSP00000344818",
      "ENSP00000361021",
      "ENSP00000263967",
      "ENSP00000257904"
    ],
    "method": "christofides",
    "return_to_start": true
  }'

# Test 2-opt for better quality
curl -X POST http://localhost:8200/bio/pathway/optimize \
  -H 'Content-Type: application/json' \
  -d '{
    "protein_ids": [
      "ENSP00000269305",
      "ENSP00000275493",
      "ENSP00000288602",
      "ENSP00000350283",
      "ENSP00000344818"
    ],
    "method": "2opt"
  }'
"""


# Example response:
"""
{
  "route": [
    {
      "protein_id": "ENSP00000269305",
      "name": "TP53",
      "position": 1,
      "distance_to_next": 234.5
    },
    {
      "protein_id": "ENSP00000350283",
      "name": "KRAS",
      "position": 2,
      "distance_to_next": 412.3
    },
    {
      "protein_id": "ENSP00000275493",
      "name": "EGFR",
      "position": 3,
      "distance_to_next": 156.7
    },
    {
      "protein_id": "ENSP00000288602",
      "name": "BRCA1",
      "position": 4,
      "distance_to_next": 389.2
    },
    {
      "protein_id": "ENSP00000344818",
      "name": "MYC",
      "position": 5,
      "distance_to_next": 201.8
    }
  ],
  "total_cost": 1394.5,
  "method_used": "christofides",
  "execution_time_ms": 47,
  "graph_stats": {
    "input_proteins": 5,
    "connected_proteins": 5,
    "direct_interactions": 8,
    "complete_graph_edges": 10
  },
  "timestamp": "2025-10-26T10:30:45.123456"
}
"""
