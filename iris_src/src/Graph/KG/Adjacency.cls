/// Graph.KG.Adjacency — the one writer of ^KG("out") and ^KG("in") (spec 245).
///
/// Every structural edge is mirrored in two stores under its graph key (ADR-0001,
/// ADR-0003):
///
///     ^KG("out", graph, s, p, o) = weight
///     ^KG("in",  graph, o, p, s) = weight
///
/// Nothing else may Set, Kill, Merge or $Increment those two stores. The scan in
/// tests/unit/test_adjacency_single_writer.py enforces it. Callers that used to write
/// them reach this class instead:
///
///   Graph.KG.EdgeScan       WriteAdjacency / DeleteAdjacency and the keyed and shadow
///                           variants: one-line delegations, names and signatures kept
///   Graph.KG.LedgerApply    relationship create, weight refresh and delete
///   Graph.KG.Loader         CSV load
///   Graph.KG.BenchFormat    seed
///
/// The counters that sit beside the mirror (^KG("deg"), ^KG("degp"), ^KG("__version"))
/// and the ^NKG interning are written here too, exactly as EdgeScan wrote them before:
/// a caller that writes one edge gets the same stores whichever name it calls.
///
/// graph is a derived index key: integer 0 for the default graph, the graph name
/// otherwise. Derive it with Graph.KG.GraphKey.ForIndex() before calling.
///
/// Errors: Put, Remove and Reweight do not catch. An exception from ^NKG interning
/// reaches the caller, as it always did. PutKeyed and RemoveKeyed keep the Try/Catch
/// they had, because they are the multigraph writers that report a status.
Class Graph.KG.Adjacency Extends %RegisteredObject
{

/// Write adjacency for one edge. pWeight "" means 1.0.
///
/// The counters are incremented on every call, so this is for an edge that is new
/// to the graph. An edge already present is handled by PutKeyed or Reweight.
ClassMethod Put(pGraph, pSrc As %String, pPred As %String, pDst As %String, pWeight As %String = "") As %Status
{
	Set tSC = $$$OK
	If pWeight = "" {
		Set pWeight = "1.0"
	}
	Set ^KG("out", pGraph, pSrc, pPred, pDst) = +pWeight
	Set ^KG("in", pGraph, pDst, pPred, pSrc) = +pWeight
	Set tmp = $Increment(^KG("deg", pGraph, pSrc))
	Set tmp = $Increment(^KG("degp", pGraph, pSrc, pPred))
	// ^ArnoKG serves a cached snapshot while its stamp equals ^KG("__version"), so a
	// write that leaves the stamp alone is invisible to the next Arno walk.
	Set tmp = $Increment(^KG("__version"))
	// ^NKG is per-identifier (no graph dimension); unchanged by spec 214
	If $Data(^NKG("$meta", "nodeCount")) {
		Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(pSrc)
		Set oIdx = ##class(Graph.KG.GraphIndex).InternNode(pDst)
		Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(pPred)
		If '$Data(^NKG(-1, sIdx, -(pIdx+1), oIdx)) {
			Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = +pWeight
			Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = +pWeight
			Set tmp = $Increment(^NKG(-3, sIdx))
			Set tmp = $Increment(^NKG("$meta", "edgeCount"))
		}
		Set tmp = $Increment(^NKG("$meta", "version"))
		Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(sIdx, pIdx, oIdx)
	}
	Quit tSC
}

/// Write the structural mirror for one edge, without interning.
///
/// Put's twin, for callers whose edges must be visible to the ^KG readers but are
/// deliberately absent from ^NKG: a temporal edge keeps a structural shadow so
/// MatchEdges and the centrality readers see it, and is interned only by a BuildNKG.
/// That is the ADJACENCY CONTRACT documented on create_edge_temporal; calling Put
/// instead would quietly break it by interning on every temporal insert.
///
/// Idempotent in the counters: several temporal edges at different timestamps are one
/// structural edge, and ^KG("deg") counts structural edges. A repeat only refreshes the
/// weight.
ClassMethod PutShadow(pGraph, pSrc As %String, pPred As %String, pDst As %String, pWeight As %String = "") As %Status
{
	Set tSC = $$$OK
	If pWeight = "" {
		Set pWeight = "1.0"
	}
	// Same structural edge at a new timestamp: refresh the weight, leave the
	// counters alone. Killing and rewriting would be the same thing more slowly.
	Set tExists = $Data(^KG("out", pGraph, pSrc, pPred, pDst))
	Set ^KG("out", pGraph, pSrc, pPred, pDst) = +pWeight
	Set ^KG("in", pGraph, pDst, pPred, pSrc) = +pWeight
	If 'tExists {
		Set tmp = $Increment(^KG("deg", pGraph, pSrc))
		Set tmp = $Increment(^KG("degp", pGraph, pSrc, pPred))
		Set tmp = $Increment(^KG("__version"))
	}
	Quit tSC
}

/// Remove adjacency for one edge, including its counters.
///
/// The inverse of Put. Decrements only when the entry was really there: a blind
/// decrement makes a repeated delete drive the counter negative, which reads as drift.
ClassMethod Remove(pGraph, pSrc As %String, pPred As %String, pDst As %String) As %Status
{
	Set tSC = $$$OK
	If $Data(^KG("out", pGraph, pSrc, pPred, pDst)) {
		Kill ^KG("out", pGraph, pSrc, pPred, pDst)
		Set tDeg = $Increment(^KG("deg", pGraph, pSrc), -1)
		Set tDegP = $Increment(^KG("degp", pGraph, pSrc, pPred), -1)
		// A zero counter is not neutral: it still claims the node is in the graph.
		If tDeg <= 0 {
			Kill ^KG("deg", pGraph, pSrc)
		}
		If tDegP <= 0 {
			Kill ^KG("degp", pGraph, pSrc, pPred)
		}
	}
	Kill ^KG("in", pGraph, pDst, pPred, pSrc)
	Set tmp = $Increment(^KG("__version"))
	If $Data(^NKG("$meta", "nodeCount")) {
		Do ##class(Graph.KG.GraphIndex).DeleteIndex("", pSrc, pPred, pDst, "")
	}
	Quit tSC
}

/// Write one keyed edge of a multigraph (spec 234). Beside Put, not through it: with the
/// mode off nothing calls this, and the single-edge writer stays what it was.
///
/// pEkey is the row's Graph_KG.rdf_edges.ekey, and the row must already be written. The
/// layout:
///
///   ^KG("out", g, s, p, o)          weight of the lowest live ekey
///   ^KG("out", g, s, p, o, ekey)    that edge's weight   } only while the triple
///   ^KG("out", g, s, p, o, "#")     live edge count      } has 2+ edges
///   ^KG("in", g, o, p, s[, ekey])   mirror, no counter
///
/// A triple with one edge is byte-identical to one Put wrote. deg/degp count edges.
ClassMethod PutKeyed(pGraph, pSrc As %String, pPred As %String, pDst As %String, pEkey As %Integer, pWeight As %String = "") As %Status
{
	Set tSC = $$$OK
	Try {
		If pWeight = "" {
			Set pWeight = "1.0"
		}
		Set tW = +pWeight
		Set tEkey = +pEkey
		If '$Data(^KG("out", pGraph, pSrc, pPred, pDst)) {
			Do ..Put(pGraph, pSrc, pPred, pDst, pWeight)
			Quit
		}
		If '$Data(^KG("out", pGraph, pSrc, pPred, pDst, "#")) {
			// One edge so far, spelled as a bare leaf: which ekey it is lives only
			// in its row.
			Set tOther = ..OtherEdgeKey(pSrc, pPred, pDst, pGraph, tEkey)
			If tOther = "" {
				// The same edge again: refresh its weight, count nothing.
				Set ^KG("out", pGraph, pSrc, pPred, pDst) = tW
				Set ^KG("in", pGraph, pDst, pPred, pSrc) = tW
				Quit
			}
			Set tOldW = ^KG("out", pGraph, pSrc, pPred, pDst)
			Set ^KG("out", pGraph, pSrc, pPred, pDst, tOther) = tOldW
			Set ^KG("in", pGraph, pDst, pPred, pSrc, tOther) = tOldW
			Set ^KG("out", pGraph, pSrc, pPred, pDst, tEkey) = tW
			Set ^KG("in", pGraph, pDst, pPred, pSrc, tEkey) = tW
			Set ^KG("out", pGraph, pSrc, pPred, pDst, "#") = 2
			Set tmp = $Increment(^KG("deg", pGraph, pSrc))
			Set tmp = $Increment(^KG("degp", pGraph, pSrc, pPred))
		} ElseIf $Data(^KG("out", pGraph, pSrc, pPred, pDst, tEkey)) {
			Set ^KG("out", pGraph, pSrc, pPred, pDst, tEkey) = tW
			Set ^KG("in", pGraph, pDst, pPred, pSrc, tEkey) = tW
		} Else {
			Set ^KG("out", pGraph, pSrc, pPred, pDst, tEkey) = tW
			Set ^KG("in", pGraph, pDst, pPred, pSrc, tEkey) = tW
			Set tmp = $Increment(^KG("out", pGraph, pSrc, pPred, pDst, "#"))
			Set tmp = $Increment(^KG("deg", pGraph, pSrc))
			Set tmp = $Increment(^KG("degp", pGraph, pSrc, pPred))
		}
		Do ..SetLeafFromLowest(pSrc, pPred, pDst, pGraph)
		// These branches change ^KG("deg") themselves, which PageRank divides by (spec 236,
		// audit item 4). The Put path above bumps the version in Put.
		Set tmp = $Increment(^KG("__version"))
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// Remove one keyed edge of a multigraph (spec 234); the inverse of PutKeyed. A triple
/// whose last edge goes is removed by Remove, which takes ^NKG with it; a triple that
/// drops from two edges to one loses its children and counter, and is then byte-identical
/// to one Put wrote with the survivor's weight.
ClassMethod RemoveKeyed(pGraph, pSrc As %String, pPred As %String, pDst As %String, pEkey As %Integer) As %Status
{
	Set tSC = $$$OK
	Try {
		Set tEkey = +pEkey
		If '$Data(^KG("out", pGraph, pSrc, pPred, pDst, "#")) {
			Do ..Remove(pGraph, pSrc, pPred, pDst)
			Quit
		}
		// A repeated delete of a gone edge is a no-op, not a second decrement.
		Quit:'$Data(^KG("out", pGraph, pSrc, pPred, pDst, tEkey))
		Kill ^KG("out", pGraph, pSrc, pPred, pDst, tEkey)
		Kill ^KG("in", pGraph, pDst, pPred, pSrc, tEkey)
		Set tDeg = $Increment(^KG("deg", pGraph, pSrc), -1)
		Set tDegP = $Increment(^KG("degp", pGraph, pSrc, pPred), -1)
		If tDeg <= 0 {
			Kill ^KG("deg", pGraph, pSrc)
		}
		If tDegP <= 0 {
			Kill ^KG("degp", pGraph, pSrc, pPred)
		}
		Set tCount = $Increment(^KG("out", pGraph, pSrc, pPred, pDst, "#"), -1)
		If tCount > 1 {
			Do ..SetLeafFromLowest(pSrc, pPred, pDst, pGraph)
			Set tmp = $Increment(^KG("__version"))
			Quit
		}
		// Back to one edge: the leaf takes the survivor's weight and the children go.
		// Each is killed on its own; a Kill of the leaf would take the leaf too.
		Set tLast = $Order(^KG("out", pGraph, pSrc, pPred, pDst, ""))
		If (tLast '= "") && (tLast '= "#") {
			Set tW = ^KG("out", pGraph, pSrc, pPred, pDst, tLast)
			Kill ^KG("out", pGraph, pSrc, pPred, pDst, tLast)
			Kill ^KG("in", pGraph, pDst, pPred, pSrc, tLast)
			Set ^KG("out", pGraph, pSrc, pPred, pDst) = tW
			Set ^KG("in", pGraph, pDst, pPred, pSrc) = tW
		}
		Kill ^KG("out", pGraph, pSrc, pPred, pDst, "#")
		Set tmp = $Increment(^KG("__version"))
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// Refresh the weight of an edge that is already in the mirror. Counts nothing.
///
/// Used when a relationship's weight changes (LedgerApply). Each store is set only if
/// it exists, so a mirror that is half present is not completed by a weight change.
ClassMethod Reweight(pGraph, pSrc As %String, pPred As %String, pDst As %String, pWeight As %String = "") As %Status
{
	Set tSC = $$$OK
	If pWeight = "" {
		Set pWeight = "1.0"
	}
	If $Data(^KG("out", pGraph, pSrc, pPred, pDst)) {
		Set ^KG("out", pGraph, pSrc, pPred, pDst) = +pWeight
	}
	If $Data(^KG("in", pGraph, pDst, pPred, pSrc)) {
		Set ^KG("in", pGraph, pDst, pPred, pSrc) = +pWeight
	}
	Set tmp = $Increment(^KG("__version"))
	Quit tSC
}

/// Point the leaf (out and in) at the weight of the lowest live ekey. Integer
/// subscripts collate before "#", so the first $Order is that ekey.
ClassMethod SetLeafFromLowest(pSrc As %String, pPred As %String, pDst As %String, pGraph) [ Private ]
{
	Set tLow = $Order(^KG("out", pGraph, pSrc, pPred, pDst, ""))
	Quit:(tLow = "")||(tLow = "#")
	Set tW = ^KG("out", pGraph, pSrc, pPred, pDst, tLow)
	Set ^KG("out", pGraph, pSrc, pPred, pDst) = tW
	Set ^KG("in", pGraph, pDst, pPred, pSrc) = tW
}

/// The lowest ekey of an (s, p, o) row in graph other than pEkey, or "" when there is
/// none. Dynamic SQL, so this class still compiles in a namespace whose rdf_edges has no
/// ekey column yet.
ClassMethod OtherEdgeKey(pSrc As %String, pPred As %String, pDst As %String, pGraph, pEkey As %Integer) As %String [ Private ]
{
	Set tName = $Select((pGraph = 0) || (pGraph = ""): "", 1: pGraph)
	Set tSQL = "SELECT MIN(ekey) FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?"
	Set tSQL = tSQL_" AND COALESCE(graph_id, '') = COALESCE(?, '') AND ekey <> ?"
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, tSQL, pSrc, pPred, pDst, tName, pEkey)
	If tRS.%SQLCODE < 0 {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "OtherEdgeKey SQLCODE "_tRS.%SQLCODE_": "_tRS.%Message))
	}
	Set tOther = ""
	If tRS.%Next() { Set tOther = tRS.%GetData(1) }
	Quit $Select(tOther = "": "", 1: +tOther)
}

}
