/// The adjacency reads: how many edges a node has, which nodes are sources, and whether
/// a node or a predicate has any edges. Every reader of the out-adjacency goes through
/// here, so the rule for a multigraph's parallel edges is written once (ADR-0010).
///
/// Storage (read-only; the layout EdgeScan writes):
///   ^KG("out", pGraph, s, p, o)          leaf: one edge, or the weight of the lowest live edge
///   ^KG("out", pGraph, s, p, o, "#")     live edge count, only while the triple has 2+ edges
///
/// The stored counters ^KG("deg") and ^KG("degp") are gone from every reader: a counter
/// kept with $Increment drifts when a transaction rolls back, so nothing counts from them.
///
/// pGraph is the derived graph key (Graph.KG.GraphKey.ForIndex): the default graph is 0.
/// The caller derives it; these methods do not.
///
/// UNCOMPILED: written without an IRIS container; compile and run
/// tests/e2e/test_adj_degree_live.py before relying on it.
Class Graph.KG.Adj Extends %RegisteredObject
{

/// Number of outgoing edges of pSrc in graph pGraph. With pPred "" every predicate counts;
/// otherwise only that predicate. A parallel edge counts once for each live edge, so a
/// triple with three edges adds 3 ("#" holds the count); a single edge adds 1.
/// Returns 0 for an empty pSrc or a node with no outgoing edge.
ClassMethod Degree(pGraph As %String, pSrc As %String, pPred As %String = "") As %Integer
{
	Set tN = 0
	If (pSrc = "") {
		Quit tN
	}
	// The predicates to count: all of them under pSrc, or only pPred.
	Kill tPreds
	If (pPred = "") {
		Set tP = ""
		For {
			Set tP = $Order(^KG("out", pGraph, pSrc, tP))
			Quit:tP=""
			Set tPreds(tP) = ""
		}
	} Else {
		Set tPreds(pPred) = ""
	}
	// One count per target o, or its "#" multiplicity when the triple has 2+ edges.
	Set tP = ""
	For {
		Set tP = $Order(tPreds(tP))
		Quit:tP=""
		Set tO = ""
		For {
			Set tO = $Order(^KG("out", pGraph, pSrc, tP, tO))
			Quit:tO=""
			If $Data(^KG("out", pGraph, pSrc, tP, tO, "#")) {
				Set tN = tN + (+$Get(^KG("out", pGraph, pSrc, tP, tO, "#")))
			} Else {
				Set tN = tN + 1
			}
		}
	}
	Quit tN
}

/// The next source after pSrc in graph pGraph, "" at the end. Iterate from "" to list
/// every node that has an outgoing edge. A node that is only a target is not a source.
ClassMethod NextSource(pGraph As %String, pSrc As %String = "") As %String
{
	Quit $Order(^KG("out", pGraph, pSrc))
}

/// 1 when pSrc has an outgoing edge in graph pGraph, or, with pPred, one for that predicate;
/// otherwise 0. An empty pSrc has none.
ClassMethod HasEdges(pGraph As %String, pSrc As %String, pPred As %String = "") As %Boolean
{
	If (pSrc = "") {
		Quit 0
	}
	If (pPred = "") {
		Quit ($Data(^KG("out", pGraph, pSrc)) > 0)
	}
	Quit ($Data(^KG("out", pGraph, pSrc, pPred)) > 0)
}

}
