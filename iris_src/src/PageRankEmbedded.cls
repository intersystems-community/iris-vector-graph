Class PageRankEmbedded Extends %RegisteredObject
{

/// Internal helper for PageRank computation to avoid code duplication
ClassMethod _computePagerankCore(nodeFilter As %String, maxIterations As %Integer, dampingFactor As %Numeric, seedEntities As %String, bidirectional As %Boolean, reverseEdgeWeight As %Numeric, convergenceThreshold As %Numeric = 0) As %List [ Internal, Language = python ]
{
    import iris
    import json

    # Validate reverseEdgeWeight
    if reverseEdgeWeight < 0:
        raise ValueError(f"reverseEdgeWeight must be non-negative, got: {reverseEdgeWeight}")

    # Step 1: Get all nodes from SQL
    cursor = iris.sql.exec("SELECT node_id FROM nodes WHERE node_id LIKE ?", nodeFilter)
    nodes = [row[0] for row in cursor]
    num_nodes = len(nodes)

    if num_nodes == 0:
        return [{}, 0, True, {"num_nodes": 0, "num_edges": 0}]

    # Step 2: Build adjacency and in_edges in one pass (Optimization!)
    cursor = iris.sql.exec("SELECT s, o_id FROM rdf_edges WHERE s LIKE ? AND o_id LIKE ?", nodeFilter, nodeFilter)

    in_edges = {}
    out_degree = {}
    num_forward_edges = 0

    for src, dst in cursor:
        num_forward_edges += 1
        
        # Forward edge: weight = 1.0
        if dst not in in_edges:
            in_edges[dst] = []
        in_edges[dst].append((src, 1.0))
        out_degree[src] = out_degree.get(src, 0) + 1

        if bidirectional and reverseEdgeWeight > 0:
            # Reverse edge: weight = reverseEdgeWeight
            if src not in in_edges:
                in_edges[src] = []
            in_edges[src].append((dst, reverseEdgeWeight))
            out_degree[dst] = out_degree.get(dst, 0) + 1

    # Initialize out_degree for nodes with no outgoing edges
    for node in nodes:
        if node not in out_degree:
            out_degree[node] = 0

    # Step 3: Initialize PageRank scores
    seeds = set()
    if seedEntities and seedEntities.strip():
        try:
            seeds = set(json.loads(seedEntities))
        except json.JSONDecodeError:
            pass

    valid_seeds = [s for s in seeds if s in nodes]
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

    # Step 4: Iterative computation
    converged = False
    iterations_performed = 0

    for iteration in range(maxIterations):
        new_ranks = {}
        max_diff = 0.0

        for node in nodes:
            rank = teleport_prob if node in seed_set else 0.0

            if node in in_edges:
                for src, weight in in_edges[node]:
                    if out_degree.get(src, 0) > 0:
                        rank += dampingFactor * weight * (ranks[src] / out_degree[src])

            new_ranks[node] = rank
            if convergenceThreshold > 0:
                max_diff = max(max_diff, abs(rank - ranks.get(node, 0)))

        ranks = new_ranks
        iterations_performed = iteration + 1

        if convergenceThreshold > 0 and max_diff < convergenceThreshold:
            converged = True
            break
            
    metadata = {
        "num_nodes": num_nodes,
        "num_edges": num_forward_edges
    }

    return [ranks, iterations_performed, converged, metadata]
}

/// Compute Personalized PageRank using embedded Python with direct global access
ClassMethod ComputePageRank(nodeFilter As %String = "%", maxIterations As %Integer = 10, dampingFactor As %Numeric = 0.85, seedEntities As %String = "", bidirectional As %Boolean = 0, reverseEdgeWeight As %Numeric = 1.0) As %DynamicArray [ Language = python ]
{
    import iris
    import logging

    logger = logging.getLogger("PageRankEmbedded")

    # Call shared core logic
    res = iris.cls('PageRankEmbedded')._computePagerankCore(nodeFilter, maxIterations, dampingFactor, seedEntities, bidirectional, reverseEdgeWeight)
    ranks = res[0]

    # Create result array sorted by rank
    results_list = sorted(ranks.items(), key=lambda x: x[1], reverse=True)

    # Convert to IRIS %DynamicArray
    results = iris.cls('%DynamicArray')._New()
    for node_id, pagerank_score in results_list:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("nodeId", node_id)
        obj._Set("pagerank", pagerank_score)
        results._Push(obj)

    return results
}

/// Compute PageRank with progress tracking
ClassMethod ComputePageRankWithMetrics(nodeFilter As %String = "%", maxIterations As %Integer = 10, dampingFactor As %Numeric = 0.85, convergenceThreshold As %Numeric = 0.0001, seedEntities As %String = "", bidirectional As %Boolean = 0, reverseEdgeWeight As %Numeric = 1.0) As %DynamicObject [ Language = python ]
{
    import iris
    import time
    import logging

    logger = logging.getLogger("PageRankEmbedded")
    start_time = time.time()

    # Call shared core logic
    res = iris.cls('PageRankEmbedded')._computePagerankCore(nodeFilter, maxIterations, dampingFactor, seedEntities, bidirectional, reverseEdgeWeight, convergenceThreshold)
    ranks, iterations_performed, converged, metadata = res[0], res[1], res[2], res[3]

    elapsed_ms = (time.time() - start_time) * 1000

    # Build results array
    results_list = sorted(ranks.items(), key=lambda x: x[1], reverse=True)

    results_array = iris.cls('%DynamicArray')._New()
    for node_id, pagerank_score in results_list:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("nodeId", node_id)
        obj._Set("pagerank", pagerank_score)
        results_array._Push(obj)

    # Build final result object
    result = iris.cls('%DynamicObject')._New()
    result._Set("results", results_array)
    result._Set("iterations", iterations_performed)
    result._Set("convergence", converged)
    result._Set("elapsed_ms", elapsed_ms)
    result._Set("num_nodes", metadata["num_nodes"])
    result._Set("num_edges", metadata["num_edges"])
    result._Set("bidirectional", bidirectional)
    result._Set("reverseEdgeWeight", reverseEdgeWeight)

    return result
}

}
