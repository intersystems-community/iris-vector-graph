/// High-performance Personalized PageRank (PPR) for the Knowledge Graph
/// Ported from HippoRAG and optimized for IRIS 2025.1+
Class Graph.KG.PageRank Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

/// Run PPR and return results as a JSON string.
/// Self-contained: uses only iris.gref("^KG") and pure Python dicts.
/// Safe to call via $CLASSMETHOD, native API bridge, or SQL function.
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
