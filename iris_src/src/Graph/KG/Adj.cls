/// The adjacency reads: how many edges a node has, which nodes are sources, and whether
/// a node or a predicate has any edges. Every reader of the out-adjacency goes through
/// here, so the rule for a multigraph's parallel edges is written once (ADR-0010).
///
/// Storage (read-only except the cache this class owns):
///   ^KG("out", pGraph, s, p, o)          leaf: one edge, or the weight of the lowest live edge
///   ^KG("out", pGraph, s, p, o, "#")     live edge count, only while the triple has 2+ edges
///   ^KG("gen", pGraph, s)                generation of a source; Graph.KG.Adjacency bumps it
///   ^KG("degc", pGraph, s, key)          $ListBuild(gen, count); key is the predicate, or
///                                        $Char(1) for all predicates. Derived, never authoritative.
///
/// The degree is counted, never stored as a counter: a counter kept with $Increment drifts
/// when a transaction rolls back. The cache is validated instead. A cached count is used
/// only while its generation equals the source's current generation, and it is stored only
/// when no writer was in flight (the lock probe) and the generation did not move.
///
/// The writer lock is ^IVG.AdjW(graph). No data lives there; only the lock name matters.
/// Writers (Graph.KG.Adjacency) hold it in shared mode, so writers never wait for writers;
/// Degree probes it in exclusive mode without waiting. Inside a transaction a writer's
/// unlock is deferred to commit or rollback, which is what makes the probe fail while an
/// uncommitted write is visible to other processes.
///
/// pGraph is the derived graph key (Graph.KG.GraphKey.ForIndex): the default graph is 0.
/// The caller derives it; these methods do not.
Class Graph.KG.Adj Extends %RegisteredObject
{

/// Number of outgoing edges of pSrc in graph pGraph. With pPred "" every predicate counts;
/// otherwise only that predicate. A parallel edge counts once for each live edge, so a
/// triple with three edges adds 3 ("#" holds the count); a single edge adds 1.
/// Returns 0 for an empty pSrc or a node with no outgoing edge.
///
/// The four reader steps, in this order (the order is part of the soundness argument):
///   1. read the generation; a cache entry carrying it is the answer
///   2. probe the writer lock exclusively, no wait, and note whether it was granted
///   3. count the out entries
///   4. store (generation, count) only if the probe was granted and the generation is
///      unchanged
///
/// A lock never conflicts with its own process, so a reader inside the writer's own open
/// transaction gets its probe, counts its own uncommitted edge and stores it. That store
/// is part of the same transaction, so a rollback takes it back with the edge; no guard on
/// $TLevel is needed (checked live), and none is wanted: it would stop every reader inside
/// a transaction from ever caching.
///
/// One addition to the rule: after the store the generation is read once more and the
/// entry is killed if it moved. Graph.KG.Adjacency kills the generation when it erases a
/// source, so a counter can restart at a value an old entry carries.
ClassMethod Degree(pGraph As %String, pSrc As %String, pPred As %String = "") As %Integer
{
	If (pSrc = "") {
		Quit 0
	}
	// The cache key: the predicate, or $Char(1) for "all predicates".
	If (pPred = "") {
		Set tKey = $Char(1)
	} Else {
		Set tKey = pPred
	}
	Set tGen = +$Get(^KG("gen", pGraph, pSrc))
	Set tCached = $Get(^KG("degc", pGraph, pSrc, tKey))
	If (tCached '= "") && ($List(tCached, 1) = tGen) {
		Quit $List(tCached, 2)
	}
	Lock +^IVG.AdjW(pGraph)#"E":0
	Set tGot = $Test
	If tGot {
		Lock -^IVG.AdjW(pGraph)#"E"
	}
	Set tN = ..Count(pGraph, pSrc, pPred)
	If tGot && (+$Get(^KG("gen", pGraph, pSrc)) = tGen) {
		Set ^KG("degc", pGraph, pSrc, tKey) = $ListBuild(tGen, tN)
		If +$Get(^KG("gen", pGraph, pSrc)) '= tGen {
			Kill ^KG("degc", pGraph, pSrc, tKey)
		}
	}
	Quit tN
}

/// Degree's step 1 alone: the cached count where its generation is the source's current one, else "". Reads only, so
/// a reader that must not write (a native Cypher read) can use the cache without filling it.
ClassMethod Cached(pGraph As %String, pSrc As %String, pPred As %String = "") As %String
{
	Quit:pSrc="" 0
	// Degree's cache key: the predicate, or $Char(1) for "all predicates"
	If (pPred = "") {
		Set tKey = $Char(1)
	} Else {
		Set tKey = pPred
	}
	Set tCached = $Get(^KG("degc", pGraph, pSrc, tKey))
	Quit:tCached="" ""
	Quit:$List(tCached,1)'=(+$Get(^KG("gen",pGraph,pSrc))) ""
	Quit $List(tCached, 2)
}

/// The out entries of pSrc counted from the globals, with "#" multiplicity. Step 3 of
/// Degree, and the recount GraphVerify compares the cache with.
ClassMethod Count(pGraph As %String, pSrc As %String, pPred As %String = "") As %Integer
{
	Set tN = 0
	If (pPred '= "") {
		Set tO = ""
		For {
			Set tO = $Order(^KG("out", pGraph, pSrc, pPred, tO))
			Quit:tO=""
			If $Data(^KG("out", pGraph, pSrc, pPred, tO, "#")) {
				Set tN = tN + (+$Get(^KG("out", pGraph, pSrc, pPred, tO, "#")))
			} Else {
				Set tN = tN + 1
			}
		}
		Quit tN
	}
	Set tP = ""
	For {
		Set tP = $Order(^KG("out", pGraph, pSrc, tP))
		Quit:tP=""
		Set tN = tN + ..Count(pGraph, pSrc, tP)
	}
	Quit tN
}

/// Fill the cache for one source in one walk: the all-predicate entry and one per
/// predicate, under the same protocol as Degree (generation first, then the probe, then
/// the count, then a conditional store). Returns the number of entries stored.
ClassMethod WarmFill(pGraph As %String, pSrc As %String) As %Integer
{
	If (pSrc = "") {
		Quit 0
	}
	Set tGen = +$Get(^KG("gen", pGraph, pSrc))
	Lock +^IVG.AdjW(pGraph)#"E":0
	Set tGot = $Test
	If tGot {
		Lock -^IVG.AdjW(pGraph)#"E"
	}
	Kill tCounts
	Set tTotal = 0
	Set tP = ""
	For {
		Set tP = $Order(^KG("out", pGraph, pSrc, tP))
		Quit:tP=""
		Set tCounts(tP) = ..Count(pGraph, pSrc, tP)
		Set tTotal = tTotal + tCounts(tP)
	}
	Set tStored = 0
	If tGot && (+$Get(^KG("gen", pGraph, pSrc)) = tGen) {
		Set ^KG("degc", pGraph, pSrc, $Char(1)) = $ListBuild(tGen, tTotal)
		Set tStored = 1
		Set tP = ""
		For {
			Set tP = $Order(tCounts(tP), 1, tC)
			Quit:tP=""
			Set ^KG("degc", pGraph, pSrc, tP) = $ListBuild(tGen, tC)
			Set tStored = tStored + 1
		}
		If +$Get(^KG("gen", pGraph, pSrc)) '= tGen {
			Kill ^KG("degc", pGraph, pSrc)
			Set tStored = 0
		}
	}
	Quit tStored
}

/// Warm the cache for every source currently in ^KG("out", pGraph): the all-predicate
/// entry and one per predicate, at the source's current generation, each source under
/// the same probe-and-unchanged-generation protocol as Degree (a source whose probe fails
/// or whose generation moves is skipped, never stored dirty). Returns the number of
/// sources filled. This is the one implementation of the warm fill: BuildKG reaches it
/// through Adjacency.EndRebuild, BackfillDegp through WarmCache, and a graph that exists
/// only as globals (a snapshot restore, a generated benchmark graph) calls it directly.
ClassMethod WarmAll(pGraph As %String) As %Integer
{
	Set tVersion = +$Get(^KG("__version"))
	Set tFilled = 0
	Set tSources = 0
	Set tS = ""
	For {
		Set tS = $Order(^KG("out", pGraph, tS))
		Quit:tS=""
		Set tSources = tSources + 1
		If ..WarmFill(pGraph, tS) > 0 {
			Set tFilled = tFilled + 1
		}
	}
	// The whole-graph stamp (see AllWarm): only when every source was filled and no write
	// moved the version while we worked. Anything less leaves the per-source protocol to answer.
	Kill ^KG("degw", pGraph)
	If (tFilled = tSources) && (+$Get(^KG("__version")) = tVersion) {
		Set ^KG("degw", pGraph) = tVersion
	}
	Quit tFilled
}

/// 1 when every source of pGraph has a valid cache entry and nothing has written to the
/// Adjacency Index since: ^KG("degw", g) holds the ^KG("__version") that was current when
/// WarmAll finished, and every adjacency write bumps that version (a rolled-back write
/// bumps it too, which only turns this off). While it holds, an entry is valid without a
/// generation read and an ABSENT entry means zero, so a reader that visits every source
/// (centrality, PageRank, the NKG and two-hop builds, KHopCount) answers with one global
/// read per node, as the stored counter used to. When it does not hold, readers use Degree.
ClassMethod AllWarm(pGraph As %String) As %Boolean
{
	Quit ($Data(^KG("degw", pGraph)) = 1) && (^KG("degw", pGraph) = +$Get(^KG("__version")))
}

/// WarmAll for every graph. Returns the number of sources filled.
ClassMethod WarmCache() As %Integer
{
	Set tFilled = 0
	Set tG = ""
	For {
		Set tG = $Order(^KG("out", tG))
		Quit:tG=""
		Set tFilled = tFilled + ..WarmAll(tG)
	}
	Quit tFilled
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
