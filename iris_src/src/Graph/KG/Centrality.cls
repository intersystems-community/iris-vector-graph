/// Centrality algorithms for the Knowledge Graph.
/// Pure ObjectScript over ^KG globals — no iris module dependency, safe from any call context.
///
/// Algorithms (spec 162):
///   - DegreeCentralityJson    — out/in/total-degree centrality, normalized to (n-1)
///   - BetweennessJson         — Brandes' algorithm with optional sampling and memory budget
///   - ClosenessJson           — harmonic (default) or classical formula
///   - EigenvectorJson         — power iteration over raw adjacency A (NOT PageRank with α=1)
///
/// Storage (read-only):
///   ^KG("out", pGraph, s, p, o)   outbound edges (shard-0)
///   ^KG("in",  0, o, p, s)   inbound edges (shard-0)
///   Graph.KG.Adj.Degree              total and per-predicate out-degree, counted from ^KG("out")
///   Graph.KG.Adj.NextSource          the node set of outbound sources
///
/// Process-private scratch (killed on exit):
///   ^||CENT(-score, node)    descending sort buffer (shared across algos)
///   ^||BC.*                  Brandes per-source accumulators (Betweenness only)
///   ^||CL.*                  Closeness accumulators
///   ^||EIG.*                 Eigenvector x and xnew vectors
///
/// Durable warning log:
///   ^IVG.warnings("centrality", $H, source) = "BC mem budget exceeded ..."
///
/// Concurrency contract:
///   Reads ^KG live — no snapshot, no lock. Concurrent mutations during a multi-second
///   run may produce inconsistent scores. Run on a quiescent graph for reproducibility.
///   Matches Neo4j GDS contract (FR-021).
Class Graph.KG.Centrality Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

/// Degree Centrality, normalized to (n-1).
///
/// @param direction  "out" (default) | "in" | "both"
/// @param predicate  Empty string = all predicates; otherwise filter to one predicate
/// @param topK       Max rows to return (default 10000; 0 = uncapped)
/// @returns JSON array: [{"id":"node","score":0.42,"degree":17}, ...] sorted desc by score
ClassMethod DegreeCentralityJson(direction As %String = "out", predicate As %String = "", topK As %Integer = 10000) As %String
{
	Set pGraph = 0
    // Count nodes for normalization
    Set nodeCount = 0
    // Enumerate the full node set: outbound sources (Graph.KG.Adj.NextSource) UNION
    // inbound targets (^KG("in",0)). Sink nodes have no outgoing edge and so are not
    // sources, but they must still be scored for direction="in"/"both".
    Kill ^||CENTNODES
    Set node = ""
    For {
        Set node = $Order(^KG("out", pGraph, node))
        Quit:node=""
        Set ^||CENTNODES(node) = ""
    }
    Set node = ""
    For {
        Set node = $Order(^KG("in", pGraph, node))
        Quit:node=""
        Set ^||CENTNODES(node) = ""
    }
    Set nodeCount = 0
    Set node = ""
    For {
        Set node = $Order(^||CENTNODES(node))
        Quit:node=""
        Set nodeCount = nodeCount + 1
    }
    If nodeCount = 0 Return "[]"
    Set norm = 1.0 / $Select(nodeCount > 1: nodeCount - 1, 1: 1)
    
    Kill ^||CENT
    
    // While the whole-graph stamp holds (Graph.KG.Adj.AllWarm) a node's out-degree is one global
    // read; otherwise each node goes through the validated Graph.KG.Adj.Degree.
    Set tWarm = ##class(Graph.KG.Adj).AllWarm(pGraph)
    Set tAll = $Char(1)
    Set node = ""
    For {
        Set node = $Order(^||CENTNODES(node))
        Quit:node=""
        
        Set deg = 0
        If predicate = "" {
            // All predicates
            If direction = "out" {
                If tWarm { Set deg = $ListGet($Get(^KG("degc", pGraph, node, tAll)), 2, 0) } Else { Set deg = ##class(Graph.KG.Adj).Degree(pGraph, node) }
            } ElseIf direction = "in" {
                // Walk ^KG("in", pGraph, node, p, s) and count
                Set p = ""
                For {
                    Set p = $Order(^KG("in", pGraph, node, p))
                    Quit:p=""
                    Set s = ""
                    For {
                        Set s = $Order(^KG("in", pGraph, node, p, s))
                        Quit:s=""
                        Set deg = deg + 1
                    }
                }
            } Else {
                // both = out + in
                If tWarm { Set deg = $ListGet($Get(^KG("degc", pGraph, node, tAll)), 2, 0) } Else { Set deg = ##class(Graph.KG.Adj).Degree(pGraph, node) }
                Set p = ""
                For {
                    Set p = $Order(^KG("in", pGraph, node, p))
                    Quit:p=""
                    Set s = ""
                    For {
                        Set s = $Order(^KG("in", pGraph, node, p, s))
                        Quit:s=""
                        Set deg = deg + 1
                    }
                }
            }
        } Else {
            // Specific predicate
            If direction = "out" {
                If tWarm { Set deg = $ListGet($Get(^KG("degc", pGraph, node, predicate)), 2, 0) } Else { Set deg = ##class(Graph.KG.Adj).Degree(pGraph, node, predicate) }
            } ElseIf direction = "in" {
                // Walk ^KG("in", pGraph, node, predicate, *)
                Set s = ""
                For {
                    Set s = $Order(^KG("in", pGraph, node, predicate, s))
                    Quit:s=""
                    Set deg = deg + 1
                }
            } Else {
                If tWarm { Set deg = $ListGet($Get(^KG("degc", pGraph, node, predicate)), 2, 0) } Else { Set deg = ##class(Graph.KG.Adj).Degree(pGraph, node, predicate) }
                Set s = ""
                For {
                    Set s = $Order(^KG("in", pGraph, node, predicate, s))
                    Quit:s=""
                    Set deg = deg + 1
                }
            }
        }
        
        Set score = deg * norm
        // Use negative score for descending $Order
        Set ^||CENT(-score, node) = deg
    }
    
    // Build JSON result array (top-K)
    Set result = ##class(%DynamicArray).%New()
    Set negScore = ""
    Set count = 0
    Set cap = $Select(topK > 0: topK, 1: 999999999)
    For {
        Set negScore = $Order(^||CENT(negScore))
        Quit:negScore=""
        Set n = ""
        For {
            Set n = $Order(^||CENT(negScore, n), 1, deg)
            Quit:n=""
            Set obj = ##class(%DynamicObject).%New()
            Do obj.%Set("id", n)
            Do obj.%Set("score", -negScore, "number")
            Do obj.%Set("degree", deg, "number")
            Do result.%Push(obj)
            Set count = count + 1
            Quit:count>=cap
        }
        Quit:count>=cap
    }
    
    Kill ^||CENT
    Return result.%ToJSON()
}

}
