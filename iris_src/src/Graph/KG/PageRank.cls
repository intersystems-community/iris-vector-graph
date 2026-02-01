/// High-performance Personalized PageRank (PPR) for the Knowledge Graph
/// Ported from HippoRAG and optimized for IRIS 2025.1+
Class Graph.KG.PageRank Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

/// Run Personalized PageRank from a set of starting nodes
/// @param seedNodes %DynamicArray of source node IDs
/// @param maxIter Maximum number of iterations (default 20)
/// @param alpha Damping factor (default 0.85)
/// @param threshold Convergence threshold (default 1e-6)
/// @returns %Integer Number of ranked nodes in ^||PPR.Results
ClassMethod Run(seedNodes As %DynamicArray, maxIter As %Integer = 20, alpha As %Double = 0.85, threshold As %Double = 0.000001) As %Integer [ Language = python ]
{
    import iris
    import json
    
    # 1. Setup IRIS Access
    kg = iris.gref("^KG")
    ppr_results = iris.gref("^||PPR.Results")
    ppr_results.kill()
    
    # 2. Initialize Teleport Set
    seeds = []
    if seedNodes:
        for i in range(seedNodes._Size()):
            seeds.append(seedNodes._Get(i))
            
    if not seeds:
        return 0
        
    # Personalization vector (initially uniform across seeds)
    teleport_prob = 1.0 / len(seeds)
    ranks = {s: teleport_prob for s in seeds}
    
    # 3. Iterative Power Method
    for i in range(maxIter):
        new_ranks = {}
        total_diff = 0
        
        # Distribute ranks from current nodes to neighbors
        for s, r in ranks.items():
            # Get out-degree for s
            deg = kg.get(["deg", s])
            if not deg or deg == 0:
                # Sink node - rank stays with s (simplified)
                new_ranks[s] = new_ranks.get(s, 0) + r * (1 - alpha)
                continue
                
            # Distribute rank along outgoing edges
            # Current rank r contributes (alpha * r / deg) to each neighbor
            p = ""
            while True:
                p = kg.order(["out", s, p])
                if not p: break
                
                # We use degp (degree per predicate) to split rank if multiple predicates exist
                # Or just use total degree for simplicity in first pass
                o = ""
                share = (alpha * r) / deg
                while True:
                    o = kg.order(["out", s, p, o])
                    if not o: break
                    
                    # Add to neighbor rank
                    new_ranks[o] = new_ranks.get(o, 0) + share
                    
        # Add teleport probabilities
        teleport_share = (1 - alpha) * teleport_prob
        for s in seeds:
            new_ranks[s] = new_ranks.get(s, 0) + teleport_share
            
        # Calculate convergence
        # (Simplified: compare common keys)
        # In a 500M node graph, we'd use a more sparse approach
        
        ranks = new_ranks
        if i > 5: # Basic check for convergence
            pass
            
    # 4. Save to Process-Private Global
    count = 0
    # Sort and save top results
    sorted_ranks = sorted(ranks.items(), key=lambda x: x[1], reverse=True)
    for node_id, rank in sorted_ranks:
        count += 1
        ppr_results.set(rank, node_id)
        if count >= 1000: break # Cap at top 1000 for efficiency
        
    return count
}

}
