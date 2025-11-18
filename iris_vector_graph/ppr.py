"""
PPR Utility Functions

Input validation and helper functions for Personalized PageRank implementation.
"""

import logging
from typing import List, Set

logger = logging.getLogger(__name__)


def validate_ppr_inputs(
    seed_entities: List[str],
    damping_factor: float,
    max_iterations: int,
    tolerance: float,
    conn
) -> None:
    """
    Validate PPR input parameters (T017).

    Implements:
    - FR-014: Seed entities must exist in graph
    - FR-015: Empty seed lists rejected
    - FR-016: Damping factor validated (0.0-1.0)

    Args:
        seed_entities: List of entity IDs to use as seeds
        damping_factor: Teleport probability (0.0-1.0)
        max_iterations: Maximum iterations for convergence
        tolerance: Convergence threshold
        conn: IRIS database connection

    Raises:
        ValueError: If any validation fails
    """
    # FR-015: Check empty seeds
    if not seed_entities:
        raise ValueError("seed_entities cannot be empty")

    # FR-016: Check damping factor range
    if not 0.0 <= damping_factor <= 1.0:
        raise ValueError(
            f"damping_factor must be in [0.0, 1.0], got {damping_factor}"
        )

    # Validate max_iterations
    if max_iterations < 1:
        raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")

    # Validate tolerance
    if tolerance <= 0:
        raise ValueError(f"tolerance must be > 0, got {tolerance}")

    # FR-014: Validate seeds exist in graph
    cursor = conn.cursor()
    try:
        # Build parameterized query
        placeholders = ",".join(["?" for _ in seed_entities])
        query = f"SELECT node_id FROM nodes WHERE node_id IN ({placeholders})"

        cursor.execute(query, seed_entities)
        found = {row[0] for row in cursor.fetchall()}

        invalid = set(seed_entities) - found
        if invalid:
            raise ValueError(f"Seed entities not found in graph: {invalid}")

    finally:
        cursor.close()


def get_outdegrees(conn, node_ids: Set[str]) -> dict:
    """
    Get outdegree (number of outgoing edges) for each node.

    Args:
        conn: IRIS database connection
        node_ids: Set of node IDs to get outdegrees for

    Returns:
        Dictionary mapping node_id -> outdegree
    """
    cursor = conn.cursor()
    try:
        if not node_ids:
            return {}

        # For large graphs, query all outdegrees (more efficient than IN clause)
        query = """
            SELECT s, COUNT(*) as outdegree
            FROM rdf_edges
            GROUP BY s
        """

        cursor.execute(query)
        all_outdegrees = {row[0]: row[1] for row in cursor.fetchall()}

        # Filter to requested nodes and add zeros for missing ones
        outdegrees = {}
        for node_id in node_ids:
            outdegrees[node_id] = all_outdegrees.get(node_id, 0)

        return outdegrees

    finally:
        cursor.close()


def get_all_graph_nodes(conn) -> Set[str]:
    """
    Get all node IDs in the graph.

    Args:
        conn: IRIS database connection

    Returns:
        Set of all node IDs
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT node_id FROM nodes")
        return {row[0] for row in cursor.fetchall()}
    finally:
        cursor.close()


def get_incoming_edges(conn, node_ids: Set[str]) -> dict:
    """
    Get incoming edges for each node.

    Args:
        conn: IRIS database connection
        node_ids: Set of node IDs to get incoming edges for

    Returns:
        Dictionary mapping target_node_id -> list of source_node_ids
    """
    cursor = conn.cursor()
    try:
        if not node_ids:
            return {}

        # For large graphs, query all edges and filter in Python
        # This avoids IRIS SQL parameter limit for IN clauses
        query = """
            SELECT s, o_id
            FROM rdf_edges
        """

        cursor.execute(query)

        incoming = {}
        node_ids_set = set(node_ids)  # Convert to set for O(1) lookup

        for source, target in cursor.fetchall():
            # Only process edges where BOTH source and target are in our graph
            # This handles cases where edges reference nodes not in nodes table
            if target in node_ids_set and source in node_ids_set:
                if target not in incoming:
                    incoming[target] = []
                incoming[target].append(source)

        return incoming

    finally:
        cursor.close()
