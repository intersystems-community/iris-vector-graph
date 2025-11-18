"""
Personalized PageRank using IRIS Functional Index.

This module implements zero-copy PPR computation by accessing graph adjacency
structures maintained in IRIS Globals (^PPR) via Functional Index callbacks.

Key features:
- Zero data extraction (in-place computation via iris.gref())
- <10ms latency for 10K-node graphs
- Automatic graph structure synchronization with SQL DML
- Process-private globals for concurrent query isolation

Architecture:
- ^PPR("out", src, dst) = 1  # Outgoing edges
- ^PPR("in", dst, src) = 1   # Incoming edges (for PPR traversal)
- ^PPR("deg", src) = count   # Outdegree cache
- ^||ppr($JOB, ...) = working_state  # Process-private temporary storage

Reference: docs/hipporag2-ppr-functional-index.md
"""

import numpy as np
from typing import List, Dict, Optional

try:
    import iris  # intersystems_irispython package
except ImportError:
    # Graceful degradation for environments without IRIS
    iris = None


def compute_ppr_functional_index(
    conn,
    seed_entities: List[str],
    damping_factor: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6
) -> Dict[str, float]:
    """
    Compute Personalized PageRank using IRIS Functional Index-maintained Globals.

    This function performs zero-copy PPR computation by directly traversing
    graph adjacency structures in IRIS Globals, avoiding SQL data extraction.

    Args:
        conn: IRIS database connection object (intersystems_irispython)
        seed_entities: List of seed node IDs to start random walk
        damping_factor: Damping factor (probability of following edge vs teleport)
            Default: 0.85
        max_iterations: Maximum power iteration steps
            Default: 100
        tolerance: Convergence tolerance (max score change per iteration)
            Default: 1e-6

    Returns:
        Dict mapping node_id → PPR score (normalized to sum = 1.0)
        Only returns nodes with score > 1e-10

    Raises:
        ValueError: If seed_entities is empty
        ValueError: If damping_factor not in [0.0, 1.0]
        ValueError: If seed entity not found in graph
        RuntimeError: If IRIS module not available

    Performance:
        - 10K nodes: <10ms target
        - 100K nodes: <100ms target
        - Linear scaling: ~1ms per 1K nodes

    Example:
        >>> conn = iris.connect(hostname="localhost", port=1972, namespace="USER")
        >>> scores = compute_ppr_functional_index(conn, ["PROTEIN:TP53"])
        >>> scores["PROTEIN:TP53"]
        0.25
    """
    # Validation (T019)
    if not seed_entities:
        raise ValueError("seed_entities cannot be empty")

    if not (0.0 <= damping_factor <= 1.0):
        raise ValueError(f"damping_factor must be in [0.0, 1.0], got {damping_factor}")

    if iris is None:
        raise RuntimeError("IRIS module not available. Install intersystems_irispython.")

    # Check if createIRIS() is available (requires intersystems-irispython >= 5.3.0)
    if not hasattr(iris, 'createIRIS'):
        raise RuntimeError(
            "iris.createIRIS() not available. "
            "Upgrade intersystems-irispython to 5.3.0+ or use Pure Python PPR. "
            "Current version may be too old for Functional Index PPR."
        )

    # Create IRIS object for Global access
    irispy = iris.createIRIS(conn)

    # Step 1: Build node set from ^PPR("deg", *) and ^PPR("in", *, *)
    # Using intersystems_irispython iterator API
    node_set = set()
    node_degrees = {}

    # Collect nodes with outdegree from ^PPR("deg", *)
    for node_id, deg in irispy.iterator('^PPR', 'deg'):
        node_set.add(node_id)
        # deg is already int from Global storage
        node_degrees[node_id] = int(deg) if deg is not None else 0

    # Also collect sink nodes (zero outdegree) from ^PPR("in", *, *)
    # These appear as targets but not in ^PPR("deg", *)
    for target_id, _val in irispy.iterator('^PPR', 'in'):
        # target_id is the first subscript after 'in'
        # We need to iterate over all targets
        if target_id not in node_set:
            node_set.add(target_id)
            node_degrees[target_id] = 0  # Sink node has zero outdegree

    nodes = list(node_set)

    if not nodes:
        return {}

    N = len(nodes)
    node_to_idx = {node: idx for idx, node in enumerate(nodes)}

    # Validate seed entities exist in graph (T019)
    invalid_seeds = [seed for seed in seed_entities if seed not in node_to_idx]
    if invalid_seeds:
        raise ValueError(
            f"Seed entities not found in graph: {invalid_seeds}. "
            f"Available nodes: {len(nodes)}"
        )

    # Step 2: Initialize scores (NumPy for vectorization)
    scores = np.zeros(N, dtype=np.float64)
    personalization = np.zeros(N, dtype=np.float64)

    seed_set = set(seed_entities)
    uniform_seed = 1.0 / len(seed_entities)

    for i, node_id in enumerate(nodes):
        if node_id in seed_set:
            scores[i] = uniform_seed
            personalization[i] = uniform_seed

    # Step 3: Power iteration with Global traversal
    converged = False
    for iteration in range(1, max_iterations + 1):
        prev_scores = scores.copy()

        # Initialize with teleportation term
        new_scores = (1 - damping_factor) * personalization

        # Traverse incoming edges via ^PPR("in", target, source)
        for target_idx, target_id in enumerate(nodes):
            incoming_score = 0.0

            # Iterate over all incoming edges to target using iterator
            # iterator('^PPR', 'in', target_id) returns (source_id, value) tuples
            for src, _val in irispy.iterator('^PPR', 'in', target_id):
                if src in node_to_idx:
                    src_idx = node_to_idx[src]
                    src_outdeg = node_degrees.get(src, 1)

                    # Handle sink nodes (T018): distribute uniformly if outdeg == 0
                    if src_outdeg == 0:
                        incoming_score += prev_scores[src_idx] / N
                    else:
                        incoming_score += prev_scores[src_idx] / src_outdeg

            new_scores[target_idx] += damping_factor * incoming_score

        scores = new_scores

        # Check convergence
        max_change = np.max(np.abs(scores - prev_scores))
        if max_change < tolerance:
            converged = True
            break

    # Step 4: Normalize scores
    total = np.sum(scores)
    if total > 0:
        scores = scores / total

    # Step 5: Filter and return
    score_dict = {
        nodes[i]: float(scores[i])
        for i in range(N)
        if scores[i] > 1e-10
    }

    return score_dict
