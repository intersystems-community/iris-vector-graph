/// Graph.KG.Adjacency — the one writer of ^KG("out") and ^KG("in") (specs 245, 248).
///
/// Every structural edge is mirrored in two stores under its graph key (ADR-0001,
/// ADR-0003):
///
///     ^KG("out", graph, s, p, o) = weight
///     ^KG("in",  graph, o, p, s) = weight
///
/// Nothing else may Set, Kill, Merge or $Increment those two stores. The scan in
/// tests/unit/test_adjacency_single_writer.py enforces it, with an empty allow-list.
/// Callers that used to write them reach this class instead:
///
///   Graph.KG.EdgeScan       WriteAdjacency / DeleteAdjacency / BulkDeleteAdjacency and the
///                           keyed variants: one-line delegations, names kept for Python
///   Graph.KG.LedgerApply    relationship create, weight refresh and delete
///   Graph.KG.BenchFormat    seed
///   Graph.KG.Eraser         RemoveGraph, RemoveAll, RemoveNode, Remove
///   Graph.KG.TemporalIndex  PutShadow, Remove
///   Graph.KG.TraversalBuild BuildKG: BeginRebuild, FillEdge, FillTriple, EndRebuild
///
/// THE DEGREE CACHE (spec 248, docs/research/alt-impl/BRIEF-PARITY.md). The stored
/// counters ^KG("deg") and ^KG("degp") are gone. Graph.KG.Adj.Degree validates a cached
/// count against a per-source generation, ^KG("gen", graph, src), and that is what this
/// class maintains. The writer rule, for every code path that adds or removes an out entry:
///
///     Lock +^IVG.AdjW(graph)#"S":5        shared: writers never wait for writers
///     change out/in
///     $Increment(^KG("gen", graph, src))  $Increment survives a rollback; a rolled-back
///                                         write over-bumps, which only makes an entry look stale
///     Lock -^IVG.AdjW(graph)#"S"          inside a transaction the unlock waits for commit or
///                                         rollback, which is what makes Degree's exclusive
///                                         no-wait probe fail while an uncommitted write is visible
///
/// Erase and rebuild kill the affected gen and degc subtrees with the out/in kill, under the
/// same lock. A lock that cannot be taken in 5 seconds is an error status and nothing is written.
/// Reweight does not change a degree, so it takes no lock and does not bump.
///
/// graph is a derived index key: integer 0 for the default graph, the graph name
/// otherwise. Derive it with Graph.KG.GraphKey.ForIndex() before calling.
///
/// ^KG("__version") is bumped once per write exactly as before the cache; ^ArnoKG serves a
/// cached snapshot while its stamp equals it.
///
/// Errors: Put, Remove and Reweight do not catch. An exception from ^NKG interning
/// reaches the caller, as it always did. PutKeyed and RemoveKeyed keep the Try/Catch
/// they had, because they are the multigraph writers that report a status.
Class Graph.KG.Adjacency Extends %RegisteredObject
{

/// Write adjacency for one edge. pWeight "" means 1.0.
///
/// The generation is bumped on every call, so this is for an edge that is new to the
/// graph; an overwrite over-bumps, which is harmless. An edge already present is handled
/// by PutKeyed or Reweight.
ClassMethod Put(pGraph, pSrc As %String, pPred As %String, pDst As %String, pWeight As %String = "") As %Status
{
	Set tSC = $$$OK
	If pWeight = "" {
		Set pWeight = "1.0"
	}
	Lock +^IVG.AdjW(pGraph)#"S":5
	If '$Test {
		Quit $$$ERROR($$$GeneralError, "Adjacency.Put: the writer lock for graph "_pGraph_" was not granted in 5 seconds")
	}
	Set ^KG("out", pGraph, pSrc, pPred, pDst) = +pWeight
	Set ^KG("in", pGraph, pDst, pPred, pSrc) = +pWeight
	Set tmp = $Increment(^KG("gen", pGraph, pSrc))
	// ^ArnoKG serves a cached snapshot while its stamp equals ^KG("__version"), so a
	// write that leaves the stamp alone is invisible to the next Arno walk.
	Set tmp = $Increment(^KG("__version"))
	Lock -^IVG.AdjW(pGraph)#"S"
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
/// Idempotent in the generation: several temporal edges at different timestamps are one
/// structural edge, so a repeat only refreshes the weight and the degree does not move.
ClassMethod PutShadow(pGraph, pSrc As %String, pPred As %String, pDst As %String, pWeight As %String = "") As %Status
{
	Set tSC = $$$OK
	If pWeight = "" {
		Set pWeight = "1.0"
	}
	Lock +^IVG.AdjW(pGraph)#"S":5
	If '$Test {
		Quit $$$ERROR($$$GeneralError, "Adjacency.PutShadow: the writer lock for graph "_pGraph_" was not granted in 5 seconds")
	}
	// Same structural edge at a new timestamp: refresh the weight, leave the
	// generation alone. Killing and rewriting would be the same thing more slowly.
	Set tExists = $Data(^KG("out", pGraph, pSrc, pPred, pDst))
	Set ^KG("out", pGraph, pSrc, pPred, pDst) = +pWeight
	Set ^KG("in", pGraph, pDst, pPred, pSrc) = +pWeight
	If 'tExists {
		Set tmp = $Increment(^KG("gen", pGraph, pSrc))
		Set tmp = $Increment(^KG("__version"))
	}
	Lock -^IVG.AdjW(pGraph)#"S"
	Quit tSC
}

/// Remove adjacency for one edge.
///
/// The inverse of Put. The generation is bumped only when the out entry was really there.
/// ^NKG has no graph dimension, so its entry for the triple goes only when no other graph
/// still holds the triple: deleting a named graph's edge used to take the default graph's
/// merged entry (and its degree) with it.
ClassMethod Remove(pGraph, pSrc As %String, pPred As %String, pDst As %String) As %Status
{
	Set tSC = $$$OK
	Lock +^IVG.AdjW(pGraph)#"S":5
	If '$Test {
		Quit $$$ERROR($$$GeneralError, "Adjacency.Remove: the writer lock for graph "_pGraph_" was not granted in 5 seconds")
	}
	Set tHad = $Data(^KG("out", pGraph, pSrc, pPred, pDst))
	Kill ^KG("out", pGraph, pSrc, pPred, pDst)
	Kill ^KG("in", pGraph, pDst, pPred, pSrc)
	If tHad {
		Set tmp = $Increment(^KG("gen", pGraph, pSrc))
	}
	Set tmp = $Increment(^KG("__version"))
	Lock -^IVG.AdjW(pGraph)#"S"
	// Only when the entry was there: a row delete removes it through the index (spec 252), and a
	// caller's own Remove after that must not count ^NKG down a second time.
	If (tHad) && ($Data(^NKG("$meta", "nodeCount"))) && ('..HeldElsewhere(pGraph, pSrc, pPred, pDst)) {
		// The first argument is unused: ^NKG is merged across graphs.
		Do ##class(Graph.KG.GraphIndex).DeleteIndex("", pSrc, pPred, pDst, "")
	}
	Quit tSC
}

/// 1 when a graph other than pGraph holds the triple in ^KG("out").
ClassMethod HeldElsewhere(pGraph, pSrc As %String, pPred As %String, pDst As %String) As %Boolean [ Private ]
{
	Set tG = ""
	For {
		Set tG = $Order(^KG("out", tG))
		Quit:tG=""
		If (tG '= pGraph) && $Data(^KG("out", tG, pSrc, pPred, pDst)) {
			Return 1
		}
	}
	Quit 0
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
///   ^KG("in", g, o, p, s[, ekey])   mirror
///
/// A triple with one edge is byte-identical to one Put wrote. The generation moves when
/// the edge count of the triple does.
ClassMethod PutKeyed(pGraph, pSrc As %String, pPred As %String, pDst As %String, pEkey As %Integer, pWeight As %String = "") As %Status
{
	Set tSC = $$$OK
	Set tLocked = 0
	Try {
		If pWeight = "" {
			Set pWeight = "1.0"
		}
		Set tW = +pWeight
		Set tEkey = +pEkey
		If '$Data(^KG("out", pGraph, pSrc, pPred, pDst)) {
			Set tSC = ..Put(pGraph, pSrc, pPred, pDst, pWeight)
			Quit
		}
		Set tHash = $Data(^KG("out", pGraph, pSrc, pPred, pDst, "#"))
		Set tOther = ""
		If 'tHash {
			// One edge so far, spelled as a bare leaf: which ekey it is lives only
			// in its row.
			Set tOther = ..OtherEdgeKey(pSrc, pPred, pDst, pGraph, tEkey)
		}
		Lock +^IVG.AdjW(pGraph)#"S":5
		If '$Test {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "Adjacency.PutKeyed: the writer lock for graph "_pGraph_" was not granted in 5 seconds"))
		}
		Set tLocked = 1
		If ('tHash) && (tOther = "") {
			// The same edge again: refresh its weight, count nothing.
			Set ^KG("out", pGraph, pSrc, pPred, pDst) = tW
			Set ^KG("in", pGraph, pDst, pPred, pSrc) = tW
		} Else {
			If 'tHash {
				Set tOldW = ^KG("out", pGraph, pSrc, pPred, pDst)
				Set ^KG("out", pGraph, pSrc, pPred, pDst, tOther) = tOldW
				Set ^KG("in", pGraph, pDst, pPred, pSrc, tOther) = tOldW
				Set ^KG("out", pGraph, pSrc, pPred, pDst, tEkey) = tW
				Set ^KG("in", pGraph, pDst, pPred, pSrc, tEkey) = tW
				Set ^KG("out", pGraph, pSrc, pPred, pDst, "#") = 2
				Set tmp = $Increment(^KG("gen", pGraph, pSrc))
			} ElseIf $Data(^KG("out", pGraph, pSrc, pPred, pDst, tEkey)) {
				Set ^KG("out", pGraph, pSrc, pPred, pDst, tEkey) = tW
				Set ^KG("in", pGraph, pDst, pPred, pSrc, tEkey) = tW
			} Else {
				Set ^KG("out", pGraph, pSrc, pPred, pDst, tEkey) = tW
				Set ^KG("in", pGraph, pDst, pPred, pSrc, tEkey) = tW
				// Recounted, not $Increment: a rolled-back row must take its count with it (spec 252)
				Set ^KG("out", pGraph, pSrc, pPred, pDst, "#") = ..KeyCount(pGraph, pSrc, pPred, pDst)
				Set tmp = $Increment(^KG("gen", pGraph, pSrc))
			}
			Do ..SetLeafFromLowest(pSrc, pPred, pDst, pGraph)
			// A count changed, which PageRank divides by (spec 236, audit item 4). The
			// Put path above bumps the version in Put.
			Set tmp = $Increment(^KG("__version"))
		}
		Lock -^IVG.AdjW(pGraph)#"S"
		Set tLocked = 0
	} Catch tEx {
		If tLocked {
			Lock -^IVG.AdjW(pGraph)#"S"
		}
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// Remove one keyed edge of a multigraph (spec 234); the inverse of PutKeyed. A triple
/// whose last edge goes is removed by Remove, which takes ^NKG with it; a triple that
/// drops from two edges to one loses its children and count, and is then byte-identical
/// to one Put wrote with the survivor's weight.
ClassMethod RemoveKeyed(pGraph, pSrc As %String, pPred As %String, pDst As %String, pEkey As %Integer) As %Status
{
	Set tSC = $$$OK
	Set tLocked = 0
	Try {
		Set tEkey = +pEkey
		If '$Data(^KG("out", pGraph, pSrc, pPred, pDst, "#")) {
			Set tSC = ..Remove(pGraph, pSrc, pPred, pDst)
			Quit
		}
		// A repeated delete of a gone edge is a no-op, not a second bump.
		Quit:'$Data(^KG("out", pGraph, pSrc, pPred, pDst, tEkey))
		Lock +^IVG.AdjW(pGraph)#"S":5
		If '$Test {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "Adjacency.RemoveKeyed: the writer lock for graph "_pGraph_" was not granted in 5 seconds"))
		}
		Set tLocked = 1
		Kill ^KG("out", pGraph, pSrc, pPred, pDst, tEkey)
		Kill ^KG("in", pGraph, pDst, pPred, pSrc, tEkey)
		Set tmp = $Increment(^KG("gen", pGraph, pSrc))
		Set tCount = ..KeyCount(pGraph, pSrc, pPred, pDst)
		If tCount > 1 {
			Set ^KG("out", pGraph, pSrc, pPred, pDst, "#") = tCount
			Do ..SetLeafFromLowest(pSrc, pPred, pDst, pGraph)
			Set tmp = $Increment(^KG("__version"))
		} Else {
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
		}
		Lock -^IVG.AdjW(pGraph)#"S"
		Set tLocked = 0
	} Catch tEx {
		If tLocked {
			Lock -^IVG.AdjW(pGraph)#"S"
		}
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// Refresh the weight of an edge that is already in the mirror. Counts nothing.
///
/// Used when a relationship's weight changes (LedgerApply). Each store is set only if
/// it exists, so a mirror that is half present is not completed by a weight change. The
/// set of out entries does not change, so neither does a degree: no lock, no generation.
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

/// Remove every outgoing edge of pSrc in graph pGraph, with the in mirror at each target,
/// and the source's generation and cache entries (a node that is gone leaves no residue,
/// including a stored counter an older version left behind).
/// Inbound edges stay: they belong to other sources. See RemoveNode.
ClassMethod RemoveSource(pGraph, pSrc As %String) As %Status
{
	Set tSC = $$$OK
	If pSrc = "" {
		Quit tSC
	}
	Lock +^IVG.AdjW(pGraph)#"S":5
	If '$Test {
		Quit $$$ERROR($$$GeneralError, "Adjacency.RemoveSource: the writer lock for graph "_pGraph_" was not granted in 5 seconds")
	}
	Set tNkg = $Data(^NKG("$meta", "nodeCount"))
	Kill tEdges
	Set tN = 0
	Set tP = ""
	For {
		Set tP = $Order(^KG("out", pGraph, pSrc, tP))
		Quit:tP=""
		Set tO = ""
		For {
			Set tO = $Order(^KG("out", pGraph, pSrc, tP, tO))
			Quit:tO=""
			Kill ^KG("in", pGraph, tO, tP, pSrc)
			If tNkg {
				Set tN = tN + 1
				Set tEdges(tN) = $ListBuild(tP, tO)
			}
		}
	}
	Kill ^KG("out", pGraph, pSrc)
	// gen before degc: a reader that stores after this point sees the moved generation
	// and removes its own entry (Graph.KG.Adj.Degree).
	Kill ^KG("gen", pGraph, pSrc)
	Kill ^KG("degc", pGraph, pSrc)
	// Residue of the stored counters (before spec 248). Nothing writes them any more; a database
	// upgraded in place may still hold them, and an erase must not leave a node's counter behind.
	Kill ^KG("deg", pGraph, pSrc)
	Kill ^KG("degp", pGraph, pSrc)
	Set tmp = $Increment(^KG("__version"))
	Lock -^IVG.AdjW(pGraph)#"S"
	For tI = 1:1:tN {
		If '..HeldElsewhere(pGraph, pSrc, $List(tEdges(tI), 1), $List(tEdges(tI), 2)) {
			Do ##class(Graph.KG.GraphIndex).DeleteIndex("", pSrc, $List(tEdges(tI), 1), $List(tEdges(tI), 2), "")
		}
	}
	Quit tSC
}

/// Remove a node from graph pGraph as a source and as a target: RemoveSource, then every
/// edge that points at it, through Remove. Edges are collected before any is removed,
/// because the walk must not mutate the tree it reads.
ClassMethod RemoveNode(pGraph, pNode As %String) As %Status
{
	Set tSC = ..RemoveSource(pGraph, pNode)
	If $$$ISERR(tSC) {
		Quit tSC
	}
	Kill tIn
	Set tN = 0
	Set tP = ""
	For {
		Set tP = $Order(^KG("in", pGraph, pNode, tP))
		Quit:tP=""
		Set tS = ""
		For {
			Set tS = $Order(^KG("in", pGraph, pNode, tP, tS))
			Quit:tS=""
			Set tN = tN + 1
			Set tIn(tN) = $ListBuild(tS, tP)
		}
	}
	For tI = 1:1:tN {
		Set tOne = ..Remove(pGraph, $List(tIn(tI), 1), $List(tIn(tI), 2), pNode)
		If $$$ISERR(tOne) {
			Set tSC = tOne
		}
	}
	Quit tSC
}

/// Remove one graph's adjacency and its degree cache: out, in, gen and degc, each with one
/// Kill because the graph key is the first subscript (ADR-0001). The caller owns the
/// transaction (Graph.KG.Eraser), so an erase that rolls back restores them together.
ClassMethod RemoveGraph(pGraph) As %Status
{
	Set tSC = $$$OK
	Lock +^IVG.AdjW(pGraph)#"S":5
	If '$Test {
		Quit $$$ERROR($$$GeneralError, "Adjacency.RemoveGraph: the writer lock for graph "_pGraph_" was not granted in 5 seconds")
	}
	Kill ^KG("out", pGraph)
	Kill ^KG("in", pGraph)
	Kill ^KG("gen", pGraph)
	Kill ^KG("degc", pGraph)
	Kill ^KG("degw", pGraph)
	// Residue of the stored counters, as in RemoveSource.
	Kill ^KG("deg", pGraph)
	Kill ^KG("degp", pGraph)
	Set tmp = $Increment(^KG("__version"))
	Lock -^IVG.AdjW(pGraph)#"S"
	Quit tSC
}

/// Remove every graph's adjacency and degree cache. The lock is taken on the parent name,
/// which covers every graph's ^IVG.AdjW(graph) (a reader's exclusive probe on a child
/// conflicts with a shared lock on the parent).
ClassMethod RemoveAll() As %Status
{
	Set tSC = $$$OK
	Lock +^IVG.AdjW#"S":5
	If '$Test {
		Quit $$$ERROR($$$GeneralError, "Adjacency.RemoveAll: the writer lock was not granted in 5 seconds")
	}
	Kill ^KG("out")
	Kill ^KG("in")
	Kill ^KG("gen")
	Kill ^KG("degc")
	Kill ^KG("degw")
	// Residue of the stored counters, as in RemoveSource.
	Kill ^KG("deg")
	Kill ^KG("degp")
	Set tmp = $Increment(^KG("__version"))
	Lock -^IVG.AdjW#"S"
	Quit tSC
}

/// The Adjacency Index entry of one rdf_edges row (spec 252, ADR-0008): called by the functional
/// index Graph.KG.AdjIndex in the row's transaction. pGraphId is the row's graph_id ("" or $Char(0)
/// for the default graph), pQuals its qualifiers (the weight), pEkey its multigraph key.
ClassMethod RowPut(pGraphId As %String, pSrc As %String, pPred As %String, pDst As %String, pQuals As %String = "", pEkey As %String = "") As %Status
{
	If (pSrc = "") || (pPred = "") || (pDst = "") {
		Quit $$$OK
	}
	Set tG = ##class(Graph.KG.GraphKey).ForIndex(pGraphId)
	Set tW = ..WeightOf(pQuals)
	If $Get(^IVG.GraphMode(tG)) = "multi" {
		Quit ..PutKeyed(tG, pSrc, pPred, pDst, +pEkey, tW)
	}
	Quit ..Put(tG, pSrc, pPred, pDst, tW)
}

/// The inverse of RowPut, for a deleted row.
ClassMethod RowRemove(pGraphId As %String, pSrc As %String, pPred As %String, pDst As %String, pEkey As %String = "") As %Status
{
	If (pSrc = "") || (pPred = "") || (pDst = "") {
		Quit $$$OK
	}
	Set tG = ##class(Graph.KG.GraphKey).ForIndex(pGraphId)
	If $Get(^IVG.GraphMode(tG)) = "multi" {
		Quit ..RemoveKeyed(tG, pSrc, pPred, pDst, +pEkey)
	}
	Quit ..Remove(tG, pSrc, pPred, pDst)
}

/// An updated row: new values first, old values after, as %Library.FunctionalIndex passes them.
/// The same edge with new qualifiers is a weight change; anything else is a delete and an insert.
ClassMethod RowUpdate(pGraphId As %String, pSrc As %String, pPred As %String, pDst As %String, pQuals As %String, pEkey As %String, pOldGraphId As %String, pOldSrc As %String, pOldPred As %String, pOldDst As %String, pOldQuals As %String, pOldEkey As %String) As %Status
{
	Set tG = ##class(Graph.KG.GraphKey).ForIndex(pGraphId)
	Set tEkey = +pEkey
	Set tSame = (tG = ##class(Graph.KG.GraphKey).ForIndex(pOldGraphId)) && (pSrc = pOldSrc) && (pPred = pOldPred) && (pDst = pOldDst) && (tEkey = +pOldEkey)
	If 'tSame {
		Set tSC = ..RowRemove(pOldGraphId, pOldSrc, pOldPred, pOldDst, pOldEkey)
		If $$$ISERR(tSC) {
			Quit tSC
		}
		Quit ..RowPut(pGraphId, pSrc, pPred, pDst, pQuals, pEkey)
	}
	If pQuals = pOldQuals {
		Quit $$$OK
	}
	Set tW = ..WeightOf(pQuals)
	If $Data(^KG("out", tG, pSrc, pPred, pDst, tEkey)) {
		// A keyed edge of a multigraph triple: its child, then the leaf from the lowest key.
		Lock +^IVG.AdjW(tG)#"S":5
		If '$Test {
			Quit $$$ERROR($$$GeneralError, "Adjacency.RowUpdate: the writer lock for graph "_tG_" was not granted in 5 seconds")
		}
		Set ^KG("out", tG, pSrc, pPred, pDst, tEkey) = +tW
		Set ^KG("in", tG, pDst, pPred, pSrc, tEkey) = +tW
		Do ..SetLeafFromLowest(pSrc, pPred, pDst, tG)
		// The degree is unchanged, but the weighted degree cache (degw) rides on the same generation.
		Set tmp = $Increment(^KG("gen", tG, pSrc))
		Set tmp = $Increment(^KG("__version"))
		Lock -^IVG.AdjW(tG)#"S"
		Quit $$$OK
	}
	Quit ..Reweight(tG, pSrc, pPred, pDst, tW)
}

/// 1 when Graph_KG.rdf_edges carries the adjacency functional index (spec 252): its row writes then
/// maintain ^KG("out"/"in"), and a writer that also calls Put would do the work twice.
ClassMethod Indexed() As %Boolean
{
	Quit ##class(%Dictionary.CompiledIndex).%ExistsId("Graph.KG.rdfedges||idxedgesadj")
}

/// ADR-0009: the structural row a temporal edge implies, marked {"temporal": true}, with its two endpoint
/// nodes in the edge's graph. The row's functional index then writes ^KG("out"/"in"). pGKey is the graph
/// key (0 for the default graph). With pTrustIndex the SQL is skipped when ^KG already has the triple,
/// which the index keeps in step with the rows: further timestamps of one triple cost a $Data. The
/// upgrade backfill passes 0, because there ^KG may hold a pre-ADR-0009 shadow with no row behind it.
ClassMethod AssertTemporalRow(pGKey, pSrc As %String, pPred As %String, pDst As %String, pWeight As %String = "1.0", pTrustIndex As %Boolean = 1) As %Status
{
	If (pTrustIndex) && ($Data(^KG("out", pGKey, pSrc, pPred, pDst))) {
		Quit $$$OK
	}
	Set tSC = $$$OK
	Try {
		// $Char(0) is SQL's '': a bound "" arrives as NULL, which graph_id refuses (the default graph is '')
		Set tGraphId = $Select(pGKey = 0: $Char(0), 1: pGKey)
		Set tNodeSQL = "INSERT INTO Graph_KG.nodes (node_id, graph_id) SELECT ?, ? WHERE NOT EXISTS "
		Set tNodeSQL = tNodeSQL_"(SELECT 1 FROM Graph_KG.nodes WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, ''))"
		For tNode = pSrc, pDst {
			Set tRS = ##class(%SQL.Statement).%ExecDirect(, tNodeSQL, tNode, tGraphId, tNode, tGraphId)
			If tRS.%SQLCODE < 0 {
				$$$ThrowStatus($$$ERROR($$$GeneralError, "AssertTemporalRow node "_tNode_": SQLCODE "_tRS.%SQLCODE_" "_tRS.%Message))
			}
		}
		Set tQuals = ##class(%DynamicObject).%New()
		Do tQuals.%Set("temporal", 1, "boolean")
		If (pWeight '= "") && (+pWeight '= 1) {
			Do tQuals.%Set("weight", +pWeight, "number")
		}
		Set tEdgeSQL = "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, qualifiers) SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS "
		Set tEdgeSQL = tEdgeSQL_"(SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND COALESCE(graph_id, '') = COALESCE(?, ''))"
		Set tRS = ##class(%SQL.Statement).%ExecDirect(, tEdgeSQL, pSrc, pPred, pDst, tGraphId, tQuals.%ToJSON(), pSrc, pPred, pDst, tGraphId)
		If tRS.%SQLCODE < 0 {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "AssertTemporalRow edge: SQLCODE "_tRS.%SQLCODE_" "_tRS.%Message))
		}
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// ADR-0009: the inverse, once the triple's last timestamp is gone: the row is deleted only while it still
/// carries the temporal marker, so an edge created explicitly outlives its temporal edges. The row's index
/// removes the ^KG entries.
ClassMethod RetractTemporalRow(pGKey, pSrc As %String, pPred As %String, pDst As %String) As %Status
{
	Set tSC = $$$OK
	Try {
		Set tGraphId = $Select(pGKey = 0: $Char(0), 1: pGKey)
		Set tRS = ##class(%SQL.Statement).%ExecDirect(, "SELECT %ID, qualifiers FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND COALESCE(graph_id, '') = COALESCE(?, '')", pSrc, pPred, pDst, tGraphId)
		Set tN = 0
		While tRS.%Next() {
			Set tQ = tRS.%GetData(2), tMarked = 0
			If (tQ '= "") && (tQ '= $Char(0)) {
				Try {
					Set tObj = ##class(%DynamicObject).%FromJSON(tQ)
					Set tMarked = ($IsObject(tObj)) && (tObj.%Get("temporal") = 1)
				} Catch {
				}
			}
			If tMarked {
				Set tN = tN + 1, tIds(tN) = tRS.%GetData(1)
			}
		}
		For tI = 1:1:tN {
			Set tDel = ##class(%SQL.Statement).%ExecDirect(, "DELETE FROM Graph_KG.rdf_edges WHERE %ID = ?", tIds(tI))
			If tDel.%SQLCODE < 0 {
				$$$ThrowStatus($$$ERROR($$$GeneralError, "RetractTemporalRow: SQLCODE "_tDel.%SQLCODE_" "_tDel.%Message))
			}
		}
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// The weight a row's qualifiers give its edge: qualifiers.weight, else 1.0 (TraversalBuild.BuildKG's rule).
ClassMethod WeightOf(pQuals As %String) As %String
{
	Set tW = "1.0"
	If (pQuals = "") || (pQuals = $Char(0)) {
		Quit tW
	}
	Try {
		Set tObj = ##class(%DynamicObject).%FromJSON(pQuals)
		If ($IsObject(tObj)) && (tObj.%IsDefined("weight")) && (tObj.%Get("weight") '= "") {
			Set tW = tObj.%Get("weight")
		}
	} Catch {
	}
	Quit tW
}

/// The ^KG("label") entry of one rdf_labels row (spec 252), from Graph.KG.LabelIndex.
ClassMethod PutLabel(pGraphId As %String, pSrc As %String, pLabel As %String) As %Status
{
	If (pSrc = "") || (pLabel = "") {
		Quit $$$OK
	}
	Set ^KG("label", ##class(Graph.KG.GraphKey).ForIndex(pGraphId), pLabel, pSrc) = ""
	Set tmp = $Increment(^KG("__version"))
	Quit $$$OK
}

/// The inverse of PutLabel.
ClassMethod RemoveLabel(pGraphId As %String, pSrc As %String, pLabel As %String) As %Status
{
	If (pSrc = "") || (pLabel = "") {
		Quit $$$OK
	}
	Kill ^KG("label", ##class(Graph.KG.GraphKey).ForIndex(pGraphId), pLabel, pSrc)
	Set tmp = $Increment(^KG("__version"))
	Quit $$$OK
}

/// Every graph's label store, for Graph.KG.LabelIndex's PurgeIndex.
ClassMethod RemoveAllLabels() As %Status
{
	Kill ^KG("label")
	Set tmp = $Increment(^KG("__version"))
	Quit $$$OK
}

/// The live edge count of a multigraph triple: its integer children (the "#" count is derived from them).
ClassMethod KeyCount(pGraph, pSrc As %String, pPred As %String, pDst As %String) As %Integer [ Private ]
{
	Set tN = 0
	Set tK = ""
	For {
		Set tK = $Order(^KG("out", pGraph, pSrc, pPred, pDst, tK))
		Quit:(tK="")||(tK="#")
		Set tN = tN + 1
	}
	Quit tN
}

/// Start a wholesale rebuild of the adjacency (Graph.KG.TraversalBuild.BuildKG): take the
/// shared lock on the parent name and hold it until EndRebuild, so no reader stores a
/// count taken from the half-built tree. Call before the transaction opens; the lock is
/// not tied to it.
ClassMethod BeginRebuild() As %Status
{
	Set tSC = $$$OK
	Lock +^IVG.AdjW#"S":5
	If '$Test {
		Quit $$$ERROR($$$GeneralError, "Adjacency.BeginRebuild: the writer lock was not granted in 5 seconds")
	}
	Quit tSC
}

/// One edge of a rebuild: out and in, nothing else. BeginRebuild holds the lock, and the
/// generation was killed with the rest by RemoveAll, so no bump is needed here.
ClassMethod FillEdge(pGraph, pSrc As %String, pPred As %String, pDst As %String, pWeight As %String = "1.0")
{
	Set ^KG("out", pGraph, pSrc, pPred, pDst) = pWeight
	Set ^KG("in", pGraph, pDst, pPred, pSrc) = pWeight
}

/// One triple of a rebuild when some graph is a multigraph (spec 234): pN rows
/// (pEdges(i) = $ListBuild(ekey, weight), lowest ekey first). The leaf takes the lowest
/// ekey's weight, and a multigraph triple with two or more edges also gets its children
/// and "#" count, the layout PutKeyed keeps incrementally. A graph not in multigraph
/// mode gets the leaf only, with the last row's weight, whatever its rows.
ClassMethod FillTriple(pGraph, pSrc As %String, pPred As %String, pDst As %String, ByRef pEdges, pN As %Integer)
{
	Set tMulti = (pN > 1) && ($Get(^IVG.GraphMode(pGraph)) = "multi")
	Set tLeaf = $List(pEdges(1), 2)
	If 'tMulti {
		// The single-edge loop's answer: the last row's weight on a bare leaf.
		Set tLeaf = $List(pEdges(pN), 2)
	}
	Set ^KG("out", pGraph, pSrc, pPred, pDst) = tLeaf
	Set ^KG("in", pGraph, pDst, pPred, pSrc) = tLeaf
	If tMulti {
		For tI = 1:1:pN {
			Set tEkey = $List(pEdges(tI), 1), tW = $List(pEdges(tI), 2)
			Set ^KG("out", pGraph, pSrc, pPred, pDst, tEkey) = tW
			Set ^KG("in", pGraph, pDst, pPred, pSrc, tEkey) = tW
		}
		Set ^KG("out", pGraph, pSrc, pPred, pDst, "#") = pN
	}
}

/// End a rebuild: release the lock BeginRebuild took, then (pWarm = 1) leave the degree
/// cache warm. The fill runs after the unlock under the ordinary reader protocol, one
/// probe per source, so a writer that arrives meanwhile is never read dirty. A failed
/// rebuild calls this with pWarm = 0.
ClassMethod EndRebuild(pWarm As %Boolean = 1) As %Status
{
	Set tSC = $$$OK
	Lock -^IVG.AdjW#"S"
	If pWarm {
		Do ##class(Graph.KG.Adj).WarmCache()
	}
	Quit tSC
}

/// Point the leaf (out and in) at the weight of the lowest live ekey. Integer
/// subscripts collate before "#", so the first $Order is that ekey. Runs inside the lock
/// window of PutKeyed and RemoveKeyed.
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
