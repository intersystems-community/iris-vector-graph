"""
Personalized PageRank using IRIS Globals (GraphRAG-style).

This implementation follows the GraphRAG pattern:
1. SQL phase: Query database to build Global structure
2. Globals phase: Use iris.createIRIS() for pointer-chasing during iteration

Approach inspired by IRIS-Global-GraphRAG project.
"""

import iris
from typing import List, Dict, Optional


def build_ppr_global(conn):
    """
    Build ^PPR Global structure from database.

    Globals created:
    - ^PPR("nodes", node_id) = ""
    - ^PPR("out", source, target) = ""
    - ^PPR("in", target, source) = ""
    - ^PPR("outdeg", source) = count
    """
    cursor = conn.cursor()
    irispy = iris.createIRIS(conn)

    # Clear existing Global
    irispy.kill("PPR")

    # Build node list
    cursor.execute("SELECT DISTINCT node_id FROM nodes")
    for (node_id,) in cursor.fetchall():
        irispy.set("", "PPR", "nodes", node_id)
        irispy.set(0, "PPR", "outdeg", node_id)

    # Build edge lists and outdegrees
    cursor.execute("SELECT s, o_id FROM rdf_edges")
    for source, target in cursor.fetchall():
        # Outgoing edges: ^PPR("out", source, target)
        irispy.set("", "PPR", "out", source, target)

        # Incoming edges: ^PPR("in", target, source)
        irispy.set("", "PPR", "in", target, source)

        # Increment outdegree
        current = irispy.get("PPR", "outdeg", source)
        irispy.set(int(current) + 1, "PPR", "outdeg", source)


def compute_ppr_globals(
    conn,
    seed_entities: List[str],
    damping_factor: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6
) -> Dict[str, float]:
    """
    Compute Personalized PageRank using IRIS Globals for pointer chasing.

    Args:
        conn: IRIS database connection
        seed_entities: List of seed entity IDs
        damping_factor: Damping factor (default 0.85)
        max_iterations: Maximum iterations (default 100)
        tolerance: Convergence tolerance (default 1e-6)

    Returns:
        Dict mapping node_id -> PPR score
    """
    if not seed_entities:
        raise ValueError("seed_entities cannot be empty")

    if not (0.0 <= damping_factor <= 1.0):
        raise ValueError(f"damping_factor must be in [0.0, 1.0], got {damping_factor}")

    irispy = iris.createIRIS(conn)

    # Get all nodes from Global (using iterator)
    all_nodes = []
    for node_id, _ in irispy.iterator("PPR", "nodes"):
        all_nodes.append(node_id)

    if not all_nodes:
        return {}

    num_nodes = len(all_nodes)
    seed_set = set(seed_entities)
    uniform_seed_score = 1.0 / len(seed_entities)

    # Initialize scores
    scores = {}
    personalization = {}

    for node_id in all_nodes:
        if node_id in seed_set:
            scores[node_id] = uniform_seed_score
            personalization[node_id] = uniform_seed_score
        else:
            scores[node_id] = 0.0
            personalization[node_id] = 0.0

    # Get outdegrees from Global
    outdegrees = {}
    for node_id in all_nodes:
        outdeg = irispy.get("PPR", "outdeg", node_id)
        outdegrees[node_id] = int(outdeg) if outdeg else 0

    # Build incoming edges using Global iterator (pointer chasing!)
    incoming = {}
    for target in all_nodes:
        incoming[target] = []
        for source, _ in irispy.iterator("PPR", "in", target):
            incoming[target].append(source)

    # Power iteration
    converged = False
    for iteration in range(1, max_iterations + 1):
        prev_scores = scores.copy()
        new_scores = {}

        # Update each node's score
        for node_id in all_nodes:
            walk_score = 0.0

            # Sum contributions from incoming neighbors
            for source_id in incoming[node_id]:
                source_score = prev_scores[source_id]
                source_outdeg = outdegrees[source_id]

                if source_outdeg == 0:
                    # Sink node: distribute uniformly
                    walk_score += source_score / num_nodes
                else:
                    walk_score += source_score / source_outdeg

            # PPR formula: (1 - alpha) * personalization + alpha * walk
            new_scores[node_id] = (
                (1 - damping_factor) * personalization[node_id] +
                damping_factor * walk_score
            )

        # Check convergence
        max_change = max(
            abs(new_scores[node_id] - prev_scores[node_id])
            for node_id in all_nodes
        )

        scores = new_scores

        if max_change < tolerance:
            converged = True
            break

    # Normalize scores to sum to 1.0
    total = sum(scores.values())
    if total > 0:
        scores = {k: v / total for k, v in scores.items()}

    # Filter to non-zero scores
    scores = {k: v for k, v in scores.items() if v > 1e-10}

    return scores
