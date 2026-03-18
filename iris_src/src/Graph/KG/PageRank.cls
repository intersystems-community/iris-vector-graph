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
            
            deg = int(deg)
                
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

/// Run PPR and return results as a JSON string.
/// Self-contained implementation that does not rely on process-private globals,
/// making it safe to call via $CLASSMETHOD or the native API bridge.
/// 
/// @param seedJson   JSON array of seed node IDs, e.g. '["A","B"]'
/// @param alpha      Damping factor (default 0.85)
/// @param maxIter    Max iterations (default 20)
/// @param bidir      1 = bidirectional edges, 0 = forward only (reserved)
/// @param revWeight  Weight for reverse edges when bidir=1 (reserved)
/// @returns JSON array: [{"id":"node","score":0.12}, ...] sorted desc by score
ClassMethod RunJson(seedJson As %String, alpha As %Double = 0.85, maxIter As %Integer = 20, bidir As %Integer = 0, revWeight As %Double = 1.0) As %String [ Language = python ]
{
    import iris
    import json

    try:
        seeds = json.loads(seedJson) if seedJson else []
    except Exception:
        return "[]"

    if not seeds:
        return "[]"

    kg = iris.gref("^KG")

    teleport_prob = 1.0 / len(seeds)
    ranks = {s: teleport_prob for s in seeds}

    for iteration in range(int(maxIter)):
        new_ranks = {}

        for s, r in ranks.items():
            deg = kg.get(["deg", s])
            if not deg or deg == 0:
                new_ranks[s] = new_ranks.get(s, 0) + r * (1 - float(alpha))
                continue

            deg = int(deg)
            p = ""
            while True:
                p = kg.order(["out", s, p])
                if not p:
                    break
                o = ""
                share = (float(alpha) * r) / deg
                while True:
                    o = kg.order(["out", s, p, o])
                    if not o:
                        break
                    new_ranks[o] = new_ranks.get(o, 0) + share

        teleport_share = (1 - float(alpha)) * teleport_prob
        for s in seeds:
            new_ranks[s] = new_ranks.get(s, 0) + teleport_share

        ranks = new_ranks

    sorted_ranks = sorted(ranks.items(), key=lambda x: x[1], reverse=True)
    results = [{"id": str(nid), "score": float(score)} for nid, score in sorted_ranks[:1000]]

    return json.dumps(results)
}

}
