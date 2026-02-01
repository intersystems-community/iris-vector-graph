"""
Graph algorithm implementations for Cypher.

Note: IRIS does not support recursive CTEs, so path-finding is implemented
as application-level BFS that issues iterative queries.
"""

from typing import List, Dict, Any
from ..translator import _table


def generate_neighbors_sql(direction: str = "outgoing") -> str:
    """
    Generate SQL to find neighbors of a set of nodes.
    
    Args:
        direction: 'outgoing', 'incoming', or 'both'
        
    Returns:
        SQL query with placeholder for node IDs
    """
    edges_tbl = _table('rdf_edges')
    
    if direction == "outgoing":
        return f"SELECT DISTINCT o_id AS neighbor, s AS source, p AS rel_type FROM {edges_tbl} WHERE s = ?"
    elif direction == "incoming":
        return f"SELECT DISTINCT s AS neighbor, o_id AS source, p AS rel_type FROM {edges_tbl} WHERE o_id = ?"
    else:  # both
        return f"""
        SELECT DISTINCT o_id AS neighbor, s AS source, p AS rel_type FROM {edges_tbl} WHERE s = ?
        UNION
        SELECT DISTINCT s AS neighbor, o_id AS source, p AS rel_type FROM {edges_tbl} WHERE o_id = ?
        """


def generate_batch_neighbors_sql(node_count: int, direction: str = "outgoing") -> str:
    """
    Generate SQL to find neighbors of multiple nodes in one query.
    
    Args:
        node_count: Number of source nodes
        direction: 'outgoing', 'incoming', or 'both'
        
    Returns:
        SQL query with placeholders for node IDs
    """
    edges_tbl = _table('rdf_edges')
    placeholders = ", ".join(["?"] * node_count)
    
    if direction == "outgoing":
        return f"SELECT DISTINCT o_id AS neighbor, s AS source, p AS rel_type FROM {edges_tbl} WHERE s IN ({placeholders})"
    elif direction == "incoming":
        return f"SELECT DISTINCT s AS neighbor, o_id AS source, p AS rel_type FROM {edges_tbl} WHERE o_id IN ({placeholders})"
    else:  # both
        return f"""
        SELECT DISTINCT o_id AS neighbor, s AS source, p AS rel_type FROM {edges_tbl} WHERE s IN ({placeholders})
        UNION
        SELECT DISTINCT s AS neighbor, o_id AS source, p AS rel_type FROM {edges_tbl} WHERE o_id IN ({placeholders})
        """


def find_shortest_path_bfs(
    cursor,
    start_node_id: str,
    end_node_id: str,
    max_hops: int = 10,
    direction: str = "outgoing",
    all_paths: bool = False
) -> List[Dict[str, Any]]:
    """
    Find shortest path(s) between two nodes using iterative BFS.
    
    IRIS does not support recursive CTEs, so we implement BFS at the application level
    by issuing iterative queries to expand the frontier.
    
    Args:
        cursor: Database cursor
        start_node_id: Source node ID
        end_node_id: Target node ID
        max_hops: Maximum traversal depth
        direction: 'outgoing', 'incoming', or 'both'
        all_paths: If True, returns all paths of the same shortest length
        
    Returns:
        List of path dictionaries with 'path' and 'depth' keys
    """
    if start_node_id == end_node_id:
        return [{"path": [start_node_id], "depth": 0, "relationships": []}]
    
    # BFS state: queue of (current_node, path, relationships)
    visited = {start_node_id}
    queue = [(start_node_id, [start_node_id], [])]
    results = []
    found_depth = None
    
    for depth in range(1, max_hops + 1):
        if not queue:
            break
            
        # If we already found a path and this depth exceeds it, stop
        if found_depth is not None and depth > found_depth:
            break
        
        # Get all nodes in current frontier
        frontier_nodes = [node for node, _, _ in queue]
        
        # Batch query for neighbors
        if direction == "both":
            sql = generate_batch_neighbors_sql(len(frontier_nodes), direction)
            cursor.execute(sql, frontier_nodes + frontier_nodes)
        else:
            sql = generate_batch_neighbors_sql(len(frontier_nodes), direction)
            cursor.execute(sql, frontier_nodes)
        
        neighbors_by_source = {}
        for row in cursor.fetchall():
            neighbor, source, rel_type = row[0], row[1], row[2]
            if source not in neighbors_by_source:
                neighbors_by_source[source] = []
            neighbors_by_source[source].append((neighbor, rel_type))
        
        next_queue = []
        for current_node, path, relationships in queue:
            for neighbor, rel_type in neighbors_by_source.get(current_node, []):
                if neighbor == end_node_id:
                    # Found a path!
                    new_path = path + [neighbor]
                    new_rels = relationships + [rel_type]
                    results.append({
                        "path": new_path,
                        "depth": depth,
                        "relationships": new_rels
                    })
                    found_depth = depth
                    if not all_paths:
                        return results
                elif neighbor not in visited:
                    visited.add(neighbor)
                    next_queue.append((neighbor, path + [neighbor], relationships + [rel_type]))
        
        queue = next_queue
    
    return results


def find_all_paths(
    cursor,
    start_node_id: str,
    end_node_id: str,
    min_hops: int = 1,
    max_hops: int = 5,
    direction: str = "outgoing"
) -> List[Dict[str, Any]]:
    """
    Find all paths between two nodes within hop constraints.
    
    Uses DFS with iterative SQL queries. For large graphs, consider
    limiting max_hops to avoid combinatorial explosion.
    
    Args:
        cursor: Database cursor
        start_node_id: Source node ID
        end_node_id: Target node ID
        min_hops: Minimum path length
        max_hops: Maximum path length
        direction: 'outgoing', 'incoming', or 'both'
        
    Returns:
        List of path dictionaries
    """
    results = []
    
    def dfs(current: str, path: List[str], rels: List[str], depth: int):
        if depth > max_hops:
            return
            
        if current == end_node_id and depth >= min_hops:
            results.append({
                "path": path.copy(),
                "depth": depth,
                "relationships": rels.copy()
            })
            # Don't return - continue searching for longer paths
            if depth >= max_hops:
                return
        
        # Get neighbors
        sql = generate_neighbors_sql(direction)
        if direction == "both":
            cursor.execute(sql, [current, current])
        else:
            cursor.execute(sql, [current])
        
        for row in cursor.fetchall():
            neighbor, _, rel_type = row[0], row[1], row[2]
            if neighbor not in path:  # Avoid cycles
                path.append(neighbor)
                rels.append(rel_type)
                dfs(neighbor, path, rels, depth + 1)
                path.pop()
                rels.pop()
    
    dfs(start_node_id, [start_node_id], [], 0)
    return results
