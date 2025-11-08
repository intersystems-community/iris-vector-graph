"""
Optimized Personalized PageRank using SciPy sparse matrices.

Key optimizations:
1. NO SQL window functions (those caused the 870ms slowdown)
2. Simple GROUP BY queries only
3. Vectorized NumPy operations
4. CSR sparse matrix for memory efficiency

Expected performance: <50ms for 10K nodes
"""

import numpy as np
from scipy.sparse import csr_matrix
from typing import List, Dict, Optional


def compute_ppr_scipy_optimized(
    conn,
    seed_entities: List[str],
    damping_factor: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6
) -> Dict[str, float]:
    """
    Compute Personalized PageRank using optimized SciPy sparse matrices.

    Optimizations:
    - Simple SQL queries (no window functions)
    - Vectorized matrix operations
    - CSR sparse format for efficient row operations

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

    cursor = conn.cursor()

    # Step 1: Get all nodes (simple query)
    cursor.execute("SELECT DISTINCT node_id FROM nodes ORDER BY node_id")
    nodes = [row[0] for row in cursor.fetchall()]

    if not nodes:
        return {}

    node_to_idx = {node: idx for idx, node in enumerate(nodes)}
    N = len(nodes)

    # Step 2: Build sparse adjacency matrix
    # Use simple queries - NO window functions!

    # Get all edges
    cursor.execute("SELECT s, o_id FROM rdf_edges")
    edges = cursor.fetchall()

    # Count outdegrees in Python (faster than SQL window function!)
    outdegrees = {}
    for source, target in edges:
        outdegrees[source] = outdegrees.get(source, 0) + 1

    # Build sparse matrix
    row_indices = []
    col_indices = []
    data = []

    for source, target in edges:
        if source in node_to_idx and target in node_to_idx:
            outdeg = outdegrees[source]
            # Transition probability from source to target
            row_indices.append(node_to_idx[target])  # Row = target
            col_indices.append(node_to_idx[source])  # Col = source
            data.append(1.0 / outdeg)  # P(target | source)

    # Create CSR matrix (compressed sparse row format)
    A = csr_matrix((data, (row_indices, col_indices)), shape=(N, N))

    # Step 3: Initialize scores
    seed_set = set(seed_entities)
    uniform_seed_score = 1.0 / len(seed_entities)

    scores = np.zeros(N)
    personalization = np.zeros(N)

    for i, node_id in enumerate(nodes):
        if node_id in seed_set:
            scores[i] = uniform_seed_score
            personalization[i] = uniform_seed_score

    # Step 4: Power iteration (VECTORIZED!)
    converged = False
    for iteration in range(1, max_iterations + 1):
        prev_scores = scores.copy()

        # Vectorized PPR update: scores = (1-α)*p + α*A*scores
        # This is ~100x faster than nested Python loops!
        scores = (1 - damping_factor) * personalization + damping_factor * A.dot(scores)

        # Check convergence
        max_change = np.max(np.abs(scores - prev_scores))
        if max_change < tolerance:
            converged = True
            break

    # Step 5: Normalize and filter
    total = np.sum(scores)
    if total > 0:
        scores = scores / total

    # Convert to dict, filter to non-zero
    score_dict = {
        nodes[i]: float(scores[i])
        for i in range(N)
        if scores[i] > 1e-10
    }

    return score_dict
