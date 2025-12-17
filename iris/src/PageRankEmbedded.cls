/// PageRank using IRIS Embedded Python with direct global access
///
/// This implementation uses iris.sql.exec() for in-process execution,
/// with optional iris.gref() for direct global access.
///
/// Expected performance: 10-50x faster than client-side Python
/// - 1K nodes: 10-50ms
/// - 10K nodes: 100-500ms
/// - 100K nodes: 1-5 seconds (vs 50-60s Python baseline!)
///
/// ⚠️ CRITICAL CONSTRAINT: This is for PURE GRAPH operations only
///
/// DO NOT USE for vector similarity operations:
/// - Vector k-NN search REQUIRES SQL (HNSW index is SQL-coupled)
/// - VECTOR_DOT_PRODUCT MUST be in SQL queries
/// - Vector embedding insertion MUST go through SQL
///
/// Use embedded Python for:
/// ✅ Pure graph algorithms (PageRank, Connected Components, BFS)
/// ✅ Graph traversals and aggregations
/// ✅ Building secondary graph indexes
///
/// Use SQL for:
/// ❌ Vector similarity search (HNSW index)
/// ❌ Any VECTOR_DOT_PRODUCT operations
/// ❌ Hybrid vector + graph queries (vector portion)
///
/// For hybrid workflows, use SQL for vectors, then embedded Python for graph:
///   1. SQL: Vector k-NN search → seed nodes
///   2. Embedded Python: Graph expansion + PageRank on subgraph
///   3. SQL: Re-rank by vector similarity (optional)
///
/// Reference: docs/architecture/embedded_python_architecture.md
/// Reference: ../rag-templates HybridGraphRAG implementation
///
/// Usage:
///   set results = ##class(PageRankEmbedded).ComputePageRank("PAGERANK:%", 10, 0.85)
///   do results.%ToJSON()
Class PageRankEmbedded Extends %RegisteredObject
{

/// Compute Personalized PageRank using embedded Python with direct global access
///
/// Parameters:
///   nodeFilter - LIKE pattern for filtering nodes (e.g., "PAGERANK:%")
///   maxIterations - Maximum number of iterations (default 10)
///   dampingFactor - Damping factor (default 0.85)
///   seedEntities - JSON array of seed entity IDs for personalization (default "" = all nodes equal)
///   bidirectional - Enable reverse edge traversal (default 0 = forward only)
///   reverseEdgeWeight - Weight multiplier for reverse edges (default 1.0)
///
/// Returns:
///   %DynamicArray of objects with nodeId and pagerank fields
ClassMethod ComputePageRank(
    nodeFilter As %String = "%",
    maxIterations As %Integer = 10,
    dampingFactor As %Numeric = 0.85,
    seedEntities As %String = "",
    bidirectional As %Boolean = 0,
    reverseEdgeWeight As %Numeric = 1.0
) As %DynamicArray [ Language = python ]
{
    import iris
    import json
    import logging

    logger = logging.getLogger("PageRankEmbedded")

    # Log parameters per FR-008: Observability
    logger.debug(f"ComputePageRank called: nodeFilter={nodeFilter}, maxIterations={maxIterations}, "
                 f"dampingFactor={dampingFactor}, bidirectional={bidirectional}, "
                 f"reverseEdgeWeight={reverseEdgeWeight}, seedEntities={seedEntities[:100] if seedEntities else 'none'}")

    # Validate reverseEdgeWeight
    if reverseEdgeWeight < 0:
        raise ValueError(f"reverseEdgeWeight must be non-negative, got: {reverseEdgeWeight}")

    # Parse seed entities from JSON
    seeds = set()
    if seedEntities and seedEntities.strip():
        try:
            seeds = set(json.loads(seedEntities))
        except json.JSONDecodeError:
            pass  # Empty or invalid JSON = no seeds

    # Step 1: Get all nodes from SQL
    cursor = iris.sql.exec("SELECT node_id FROM nodes WHERE node_id LIKE ?", nodeFilter)
    nodes = [row[0] for row in cursor]
    num_nodes = len(nodes)

    if num_nodes == 0:
        return iris.cls('%DynamicArray')._New()

    # Step 2: Build adjacency list from forward edges
    cursor = iris.sql.exec("""
        SELECT s, o_id
        FROM rdf_edges
        WHERE s LIKE ? AND o_id LIKE ?
    """, nodeFilter, nodeFilter)

    adjacency = {}  # source -> [targets]
    in_edges = {}   # target -> [(source, weight)]
    out_degree = {}

    for src, dst in cursor:
        if src not in adjacency:
            adjacency[src] = []
        adjacency[src].append(dst)

        # Forward edge: weight = 1.0
        if dst not in in_edges:
            in_edges[dst] = []
        in_edges[dst].append((src, 1.0))

        out_degree[src] = out_degree.get(src, 0) + 1

    # Step 2b: Build reverse edges if bidirectional mode enabled
    if bidirectional and reverseEdgeWeight > 0:
        # Query edges in reverse direction (o_id -> s)
        cursor = iris.sql.exec("""
            SELECT o_id, s
            FROM rdf_edges
            WHERE s LIKE ? AND o_id LIKE ?
        """, nodeFilter, nodeFilter)

        for o_id, s in cursor:
            # Reverse edge: o_id -> s with weighted contribution
            if s not in in_edges:
                in_edges[s] = []
            in_edges[s].append((o_id, reverseEdgeWeight))

            # Count reverse edges for out_degree
            out_degree[o_id] = out_degree.get(o_id, 0) + 1

    # Initialize out_degree for nodes with no outgoing edges
    for node in nodes:
        if node not in out_degree:
            out_degree[node] = 0

    # Step 3: Initialize PageRank scores (Personalized if seeds provided)
    if seeds:
        # Personalized PageRank: bias toward seed entities
        valid_seeds = [s for s in seeds if s in set(nodes)]
        if valid_seeds:
            seed_count = len(valid_seeds)
            ranks = {node: (1.0 / seed_count if node in valid_seeds else 0.0) for node in nodes}
        else:
            # No valid seeds found, fall back to uniform
            initial_rank = 1.0 / num_nodes
            ranks = {node: initial_rank for node in nodes}
    else:
        # Standard PageRank: uniform initial distribution
        initial_rank = 1.0 / num_nodes
        ranks = {node: initial_rank for node in nodes}

    # Step 4: Iterative computation with personalization
    if seeds:
        valid_seeds = [s for s in seeds if s in set(nodes)]
        seed_set = set(valid_seeds) if valid_seeds else set(nodes)
        seed_count = len(seed_set)
        teleport_prob = (1.0 - dampingFactor) / seed_count
    else:
        seed_set = set(nodes)
        teleport_prob = (1.0 - dampingFactor) / num_nodes

    for iteration in range(maxIterations):
        new_ranks = {}

        for node in nodes:
            # Teleport: jump to seed nodes (personalized) or any node (standard)
            if node in seed_set:
                rank = teleport_prob
            else:
                rank = 0.0

            # Add contributions from incoming edges (with weights)
            if node in in_edges:
                for src, weight in in_edges[node]:
                    if out_degree.get(src, 0) > 0:
                        rank += dampingFactor * weight * (ranks[src] / out_degree[src])

            new_ranks[node] = rank

        ranks = new_ranks

    # Step 5: Create result array sorted by rank
    results_list = sorted(ranks.items(), key=lambda x: x[1], reverse=True)

    # Convert to IRIS %DynamicArray
    results = iris.cls('%DynamicArray')._New()
    for node_id, pagerank_score in results_list:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("nodeId", node_id)
        obj._Set("pagerank", pagerank_score)
        results._Push(obj)

    # Log completion per FR-008: Observability
    logger.debug(f"ComputePageRank completed: {len(results_list)} nodes, bidirectional={bidirectional}")

    return results
}

/// Compute PageRank with progress tracking
///
/// Returns a %DynamicObject with:
///   - results: Array of PageRank scores
///   - iterations: Number of iterations performed
///   - convergence: Whether algorithm converged
///   - elapsed_ms: Time taken in milliseconds
///   - bidirectional: Whether bidirectional mode was used
ClassMethod ComputePageRankWithMetrics(
    nodeFilter As %String = "%",
    maxIterations As %Integer = 10,
    dampingFactor As %Numeric = 0.85,
    convergenceThreshold As %Numeric = 0.0001,
    seedEntities As %String = "",
    bidirectional As %Boolean = 0,
    reverseEdgeWeight As %Numeric = 1.0
) As %DynamicObject [ Language = python ]
{
    import iris
    import json
    import time
    import logging

    logger = logging.getLogger("PageRankEmbedded")
    start_time = time.time()

    # Log parameters per FR-008: Observability
    logger.debug(f"ComputePageRankWithMetrics called: bidirectional={bidirectional}, "
                 f"reverseEdgeWeight={reverseEdgeWeight}")

    # Validate reverseEdgeWeight
    if reverseEdgeWeight < 0:
        raise ValueError(f"reverseEdgeWeight must be non-negative, got: {reverseEdgeWeight}")

    # Parse seed entities from JSON
    seeds = set()
    if seedEntities and seedEntities.strip():
        try:
            seeds = set(json.loads(seedEntities))
        except json.JSONDecodeError:
            pass

    cursor = iris.sql.exec("SELECT node_id FROM nodes WHERE node_id LIKE ?", nodeFilter)
    nodes = [row[0] for row in cursor]
    num_nodes = len(nodes)

    if num_nodes == 0:
        result = iris.cls('%DynamicObject')._New()
        result.results = iris.cls('%DynamicArray')._New()
        result.iterations = 0
        result.convergence = True
        result.elapsed_ms = 0
        result.bidirectional = bidirectional
        return result

    cursor = iris.sql.exec("""
        SELECT s, o_id
        FROM rdf_edges
        WHERE s LIKE ? AND o_id LIKE ?
    """, nodeFilter, nodeFilter)

    adjacency = {}
    in_edges = {}
    out_degree = {}

    for src, dst in cursor:
        if src not in adjacency:
            adjacency[src] = []
        adjacency[src].append(dst)

        if dst not in in_edges:
            in_edges[dst] = []
        in_edges[dst].append((src, 1.0))  # Forward edge weight = 1.0

        out_degree[src] = out_degree.get(src, 0) + 1

    # Build reverse edges if bidirectional mode enabled
    if bidirectional and reverseEdgeWeight > 0:
        cursor = iris.sql.exec("""
            SELECT o_id, s
            FROM rdf_edges
            WHERE s LIKE ? AND o_id LIKE ?
        """, nodeFilter, nodeFilter)

        for o_id, s in cursor:
            if s not in in_edges:
                in_edges[s] = []
            in_edges[s].append((o_id, reverseEdgeWeight))
            out_degree[o_id] = out_degree.get(o_id, 0) + 1

    for node in nodes:
        if node not in out_degree:
            out_degree[node] = 0

    # Initialize PageRank scores (Personalized if seeds provided)
    if seeds:
        valid_seeds = [s for s in seeds if s in set(nodes)]
        if valid_seeds:
            seed_count = len(valid_seeds)
            ranks = {node: (1.0 / seed_count if node in valid_seeds else 0.0) for node in nodes}
            seed_set = set(valid_seeds)
            teleport_prob = (1.0 - dampingFactor) / seed_count
        else:
            initial_rank = 1.0 / num_nodes
            ranks = {node: initial_rank for node in nodes}
            seed_set = set(nodes)
            teleport_prob = (1.0 - dampingFactor) / num_nodes
    else:
        initial_rank = 1.0 / num_nodes
        ranks = {node: initial_rank for node in nodes}
        seed_set = set(nodes)
        teleport_prob = (1.0 - dampingFactor) / num_nodes

    converged = False
    iterations_performed = 0

    for iteration in range(maxIterations):
        new_ranks = {}
        max_diff = 0.0

        for node in nodes:
            if node in seed_set:
                rank = teleport_prob
            else:
                rank = 0.0

            if node in in_edges:
                for src, weight in in_edges[node]:
                    if out_degree.get(src, 0) > 0:
                        rank += dampingFactor * weight * (ranks[src] / out_degree[src])

            new_ranks[node] = rank
            max_diff = max(max_diff, abs(rank - ranks.get(node, 0)))

        ranks = new_ranks
        iterations_performed = iteration + 1

        # Check convergence
        if max_diff < convergenceThreshold:
            converged = True
            break

    elapsed_ms = (time.time() - start_time) * 1000

    # Build results
    results_list = sorted(ranks.items(), key=lambda x: x[1], reverse=True)

    results_array = iris.cls('%DynamicArray')._New()
    for node_id, pagerank_score in results_list:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("nodeId", node_id)
        obj._Set("pagerank", pagerank_score)
        results_array._Push(obj)

    result = iris.cls('%DynamicObject')._New()
    result._Set("results", results_array)
    result._Set("iterations", iterations_performed)
    result._Set("convergence", converged)
    result._Set("elapsed_ms", elapsed_ms)
    result._Set("num_nodes", num_nodes)
    result._Set("num_edges", len([(s, d) for s in adjacency for d in adjacency[s]]))
    result._Set("bidirectional", bidirectional)
    result._Set("reverseEdgeWeight", reverseEdgeWeight)

    logger.debug(f"ComputePageRankWithMetrics completed: {num_nodes} nodes, {iterations_performed} iterations, "
                 f"bidirectional={bidirectional}, elapsed={elapsed_ms:.2f}ms")

    return result
}

}
