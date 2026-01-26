"""
Graph algorithm implementations for Cypher.
"""

from typing import List, Dict, Any, Optional
from .. import ast

def generate_shortest_path_sql(start_node_id: str, end_node_id: str, max_hops: int = 10, all_paths: bool = False) -> str:
    """
    Generate recursive CTE for shortest path between two nodes.
    
    Args:
        start_node_id: Source node ID
        end_node_id: Target node ID
        max_hops: Maximum traversal depth
        all_paths: If True, returns all paths of the same shortest length
    """
    limit_clause = "" if all_paths else "TOP 1"
    
    # Recursive CTE for Breadth-First Search
    sql = f"""
    WITH RECURSIVE bfs (s, o_id, depth, path) AS (
      -- Anchor member: start from the source node
      SELECT s, o_id, 1, CAST(s || '->' || o_id AS VARCHAR(1000))
      FROM rdf_edges 
      WHERE s = ?
      
      UNION ALL
      
      -- Recursive member: traverse to neighbors
      SELECT b.s, e.o_id, b.depth + 1, b.path || '->' || e.o_id
      FROM bfs b 
      JOIN rdf_edges e ON b.o_id = e.s
      WHERE b.depth < ? 
        AND b.path NOT LIKE ('%' || e.o_id || '%') -- Simple cycle detection
    )
    SELECT {limit_clause} path, depth 
    FROM bfs 
    WHERE o_id = ? 
    ORDER BY depth ASC
    """
    return sql
