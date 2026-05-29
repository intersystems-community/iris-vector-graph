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
///   ^KG("out", 0, s, p, o)   outbound edges (shard-0)
///   ^KG("in",  0, o, p, s)   inbound edges (shard-0)
///   ^KG("deg", node)         total out-degree
///   ^KG("degp", node, pred)  per-predicate out-degree
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
    // Count nodes for normalization
    Set nodeCount = 0
    Set node = ""
    For {
        Set node = $Order(^KG("deg", node))
        Quit:node=""
        Set nodeCount = nodeCount + 1
    }
    If nodeCount = 0 Return "[]"
    Set norm = 1.0 / $Select(nodeCount > 1: nodeCount - 1, 1: 1)
    
    Kill ^||CENT
    
    // Compute degree for each node based on direction + predicate
    Set node = ""
    For {
        Set node = $Order(^KG("deg", node))
        Quit:node=""
        
        Set deg = 0
        If predicate = "" {
            // All predicates
            If direction = "out" {
                Set deg = +$Get(^KG("deg", node))
            } ElseIf direction = "in" {
                // Walk ^KG("in", 0, node, p, s) and count
                Set p = ""
                For {
                    Set p = $Order(^KG("in", 0, node, p))
                    Quit:p=""
                    Set s = ""
                    For {
                        Set s = $Order(^KG("in", 0, node, p, s))
                        Quit:s=""
                        Set deg = deg + 1
                    }
                }
            } Else {
                // both = out + in
                Set deg = +$Get(^KG("deg", node))
                Set p = ""
                For {
                    Set p = $Order(^KG("in", 0, node, p))
                    Quit:p=""
                    Set s = ""
                    For {
                        Set s = $Order(^KG("in", 0, node, p, s))
                        Quit:s=""
                        Set deg = deg + 1
                    }
                }
            }
        } Else {
            // Specific predicate
            If direction = "out" {
                Set deg = +$Get(^KG("degp", node, predicate))
            } ElseIf direction = "in" {
                // Walk ^KG("in", 0, node, predicate, *)
                Set s = ""
                For {
                    Set s = $Order(^KG("in", 0, node, predicate, s))
                    Quit:s=""
                    Set deg = deg + 1
                }
            } Else {
                Set deg = +$Get(^KG("degp", node, predicate))
                Set s = ""
                For {
                    Set s = $Order(^KG("in", 0, node, predicate, s))
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

/// SQL stored procedure wrapper for DegreeCentralityJson.
/// Used by the Cypher translator's ivg.degreeCentrality CTE.
ClassMethod kgDegreeCentrality(direction As %String, predicate As %String, topK As %Integer) As %String [ SqlName = kg_DegreeCentrality, SqlProc ]
{
    Return ..DegreeCentralityJson(direction, predicate, topK)
}

}
