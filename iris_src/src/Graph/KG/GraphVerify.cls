/// Graph.KG.GraphVerify - does one graph's adjacency still agree with its rows?
///
/// Graph_KG.rdf_edges is authoritative; ^KG("out"/"in"/"deg"/"degp") is a derived
/// acceleration index. Only some write paths maintain both. The rest leave the two
/// disagreeing, and until now nothing could see it:
///
///   Engine.verify_sync compares one SQL count against ^NKG's edgeCount. It is
///   graph-blind, and one-directional by design, because ^NKG interning is
///   append-only and ignores graph_id, so its counter over-counts. It therefore
///   flags sql_edges > global_edges and stays silent on the reverse.
///
///   The reverse is what every deletion path produces. Engine.drop_graph deletes
///   the rows and says so in its own comment ("BYPASS: SQL rows deleted without
///   touching ^KG/^NKG"), leaving adjacency that still answers traversals for a
///   graph that no longer exists. After it runs sql_edges is BELOW global_edges,
///   so verify_sync reports in-sync.
///
/// VerifyGraph is scoped to one graph, bidirectional, and reads ^KG directly
/// rather than ^NKG's counters. It is additive: verify_sync keeps its contract.
///
/// It does not invent its own list of stores — Graph.KG.GraphStores owns that —
/// and it does not re-derive the graph key — Graph.KG.GraphKey owns that
/// (ADR-0003). Stores it cannot check are reported, not skipped: "unscoped" for
/// those with no graph column at all, "unverified" for graph-scoped stores this
/// oracle does not yet compare.
Class Graph.KG.GraphVerify Extends %RegisteredObject
{

/// Compare one graph's ^KG adjacency against its rows. Returns a JSON report.
///
/// "ok" answers only "does this graph's adjacency agree with its rows?". The
/// standing "unscoped" list does not affect it: those stores are a schema gap
/// rather than per-graph drift, and folding them in would make every report fail
/// forever and the signal worthless.
ClassMethod VerifyGraph(pGraph As %String = "") As %String
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tDrift = []

	// --- the rows, indexed so the reverse walk can look them up -------------
	//
	// COALESCE on BOTH sides, for two separate reasons. Neither is optional: a
	// plain "graph_id = :pGraph" reports the default graph as completely empty.
	//
	// Left side: the default graph has two spellings in this table.
	// Graph_KG.rdf_edges.graph_id is nullable. Engine.create_edge writes the
	// empty string explicitly, but any INSERT that omits the column — the bulk
	// loaders, the map_sql_table bridge, and most raw INSERTs in the test suite —
	// leaves NULL. A predicate matching only '' reports those rows as absent.
	//
	// Right side: in IRIS embedded SQL an empty host variable binds as NULL, not
	// as the empty string. So "graph_id = :pGraph" for the default graph compiles
	// to "graph_id = NULL", which is never true of any row. This is the trap that
	// makes the mistake invisible — the named-graph case works perfectly and only
	// the default graph silently reports zero.
	//
	// The cost is the graph_id index: verification full-scans the table.
	// Correctness first — an oracle that misses rows is worse than a slow one.
	Kill tRow, tExpDeg
	Set tSqlEdges = 0
	&sql(DECLARE ivgEdges CURSOR FOR
		SELECT s, p, o_id INTO :tS, :tP, :tO
		FROM Graph_KG.rdf_edges
		WHERE COALESCE(graph_id, '') = COALESCE(:pGraph, ''))
	&sql(OPEN ivgEdges)
	For {
		&sql(FETCH ivgEdges)
		Quit:SQLCODE'=0
		Set tRow(tS, tP, tO) = ""
		Set tExpDeg(tS) = $Get(tExpDeg(tS), 0) + 1
		Set tSqlEdges = tSqlEdges + 1
	}
	&sql(CLOSE ivgEdges)

	// --- ^KG("out"): orphans, and what the rows found ----------------------
	Set tOutEdges = 0, tOutOrphan = 0, tOutMatched = 0
	Set tS = ""
	For {
		Set tS = $Order(^KG("out", tKey, tS))
		Quit:tS=""
		Set tP = ""
		For {
			Set tP = $Order(^KG("out", tKey, tS, tP))
			Quit:tP=""
			Set tO = ""
			For {
				Set tO = $Order(^KG("out", tKey, tS, tP, tO))
				Quit:tO=""
				Set tOutEdges = tOutEdges + 1
				If $Data(tRow(tS, tP, tO)) {
					Set tOutMatched = tOutMatched + 1
				} Else {
					Set tOutOrphan = tOutOrphan + 1
				}
			}
		}
	}
	Set tOutMissing = tSqlEdges - tOutMatched

	// --- ^KG("in"): the mirror, subscripted (o, p, s) ----------------------
	Set tInEdges = 0, tInOrphan = 0, tInMatched = 0
	Set tO = ""
	For {
		Set tO = $Order(^KG("in", tKey, tO))
		Quit:tO=""
		Set tP = ""
		For {
			Set tP = $Order(^KG("in", tKey, tO, tP))
			Quit:tP=""
			Set tS = ""
			For {
				Set tS = $Order(^KG("in", tKey, tO, tP, tS))
				Quit:tS=""
				Set tInEdges = tInEdges + 1
				If $Data(tRow(tS, tP, tO)) {
					Set tInMatched = tInMatched + 1
				} Else {
					Set tInOrphan = tInOrphan + 1
				}
			}
		}
	}
	Set tInMissing = tSqlEdges - tInMatched

	// --- ^KG("deg"): the counters ------------------------------------------
	// Graph.KG.EdgeScan:DeleteAdjacency kills ^KG("out") and ^KG("in") but not
	// ^KG("deg") or ^KG("degp"), so the counters outlive the edges they counted.
	Set tDegNodes = 0, tDegWrong = 0
	Set tS = ""
	For {
		Set tS = $Order(^KG("deg", tKey, tS))
		Quit:tS=""
		Set tDegNodes = tDegNodes + 1
		If $Get(^KG("deg", tKey, tS), 0) '= $Get(tExpDeg(tS), 0) {
			Set tDegWrong = tDegWrong + 1
		}
	}
	// A node with rows but no counter is just as wrong as a stale counter.
	Set tS = ""
	For {
		Set tS = $Order(tExpDeg(tS))
		Quit:tS=""
		If '$Data(^KG("deg", tKey, tS)) {
			Set tDegWrong = tDegWrong + 1
		}
	}

	// --- assemble ----------------------------------------------------------
	Set tSC = ..Note(tDrift, "^KG(""out"")", "orphan", tOutOrphan, "adjacency entries with no backing row in Graph_KG.rdf_edges for this graph")
	Set tSC = ..Note(tDrift, "^KG(""out"")", "missing", tOutMissing, "rows in Graph_KG.rdf_edges with no ^KG(""out"") entry, so traversal cannot see them")
	Set tSC = ..Note(tDrift, "^KG(""in"")", "orphan", tInOrphan, "inbound entries with no backing row in Graph_KG.rdf_edges for this graph")
	Set tSC = ..Note(tDrift, "^KG(""in"")", "missing", tInMissing, "rows with no ^KG(""in"") entry, so inbound traversal cannot see them")
	Set tSC = ..Note(tDrift, "^KG(""deg"")", "count", tDegWrong, "nodes whose stored out-degree disagrees with the rows")

	Set tReport = {}
	Set tReport.graph = pGraph
	Set tReport.graphKey = tKey
	Set tReport.ok = $Select(tDrift.%Size() = 0: 1, 1: 0)
	Set tReport.counts = {"sqlEdges": (tSqlEdges), "outEdges": (tOutEdges), "inEdges": (tInEdges), "degNodes": (tDegNodes)}
	Set tReport.drift = tDrift
	Set tReport.checked = ..Checked()
	Set tReport.unverified = ..Unverified()
	Set tReport.unscoped = ##class(Graph.KG.GraphStores).Unscoped()

	Quit tReport.%ToJSON()
}

/// Record a finding, but only when there is one.
ClassMethod Note(pDrift As %DynamicArray, pStore As %String, pKind As %String, pCount As %Integer, pDetail As %String) As %Status [ Private ]
{
	Set tSC = $$$OK
	If pCount > 0 {
		Do pDrift.%Push({"store": (pStore), "kind": (pKind), "count": (pCount), "detail": (pDetail)})
	}
	Quit tSC
}

/// The stores this oracle actually compares.
///
/// Built with %Push rather than a [...] literal: the literal parser rejects a
/// string containing doubled quotes, which every ^KG subscript name needs.
ClassMethod Checked() As %DynamicArray [ Private ]
{
	Set tOut = []
	Do tOut.%Push("Graph_KG.rdf_edges")
	Do tOut.%Push("^KG(""out"")")
	Do tOut.%Push("^KG(""in"")")
	Do tOut.%Push("^KG(""deg"")")
	Quit tOut
}

/// Graph-scoped stores this oracle does not compare yet, derived from the
/// inventory rather than listed again, so a store added there shows up here
/// instead of being silently dropped.
ClassMethod Unverified() As %DynamicArray [ Private ]
{
	Kill tChecked
	Set tIter = ..Checked().%GetIterator()
	While tIter.%GetNext(.tIdx, .tName) {
		Set tChecked(tName) = ""
	}

	Set tOut = []
	Set tIter = ##class(Graph.KG.GraphStores).Scoped().%GetIterator()
	While tIter.%GetNext(.tIdx, .tEntry) {
		Continue:$Data(tChecked(tEntry.name))
		Do tOut.%Push({"store": (tEntry.name), "reason": "graph-scoped, but this oracle compares structural adjacency only"})
	}
	Quit tOut
}

}
