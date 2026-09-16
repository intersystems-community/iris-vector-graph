/// Graph.KG.GraphIndex - ^NKG interning and structural HLL helpers.
///
/// NOT a functional index. It extended %Library.FunctionalIndex and was attached
/// to Graph.KG.Edge as "Index GraphIdx", where InsertIndex wrote
/// ^KG("out", 0, s, p, o) for edges in EVERY graph and ^KG("deg", s) with no
/// graph key at all — routing named-graph edges into the default graph's
/// adjacency and putting the degree counters at the wrong subscript depth. Both
/// violate ADR-0001. PurgeIndex did a bare `Kill ^KG`, destroying the temporal
/// subtrees (the spec 206 class of data loss; see Graph.KG.Ledger).
///
/// The index role is removed. What survives is the ^NKG interning and HLL
/// helpers that Graph.KG.EdgeScan, Graph.KG.TraversalBuild and
/// Graph.KG.NKGAccelTraversal call. The class name is now misleading and is
/// worth renaming, but that is a separate change across three callers.
Class Graph.KG.GraphIndex Extends %RegisteredObject
{

ClassMethod InitStructuralLabels()
{
	If $Data(^NKG("$LS", 0)) Quit
	Set ^NKG("$LS", 0) = "out"
	Set ^NKG("$LI", "out") = 0
	Set ^NKG("$LS", 1) = "in"
	Set ^NKG("$LI", "in") = 1
	Set ^NKG("$LS", 2) = "deg"
	Set ^NKG("$LI", "deg") = 2
	If $Get(^NKG("$meta", "labelCount")) < 3 {
		Set ^NKG("$meta", "labelCount") = 3
	}
}

ClassMethod InternNode(id As %String) As %Integer
{
	Lock +^NKG("$NI", id):0
	If '$Test {
		Lock +^NKG("$NI", id):5
		Lock -^NKG("$NI", id)
		Return $Get(^NKG("$NI", id))
	}
	Set idx = $Get(^NKG("$NI", id))
	If idx '= "" {
		Lock -^NKG("$NI", id)
		Return idx
	}
	Set idx = $Increment(^NKG("$meta", "nodeCount")) - 1
	Set ^NKG("$ND", idx) = id
	Set ^NKG("$NI", id) = idx
	Lock -^NKG("$NI", id)
	Return idx
}

ClassMethod InternLabel(label As %String) As %Integer
{
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	Lock +^NKG("$LI", label):0
	If '$Test {
		Lock +^NKG("$LI", label):5
		Lock -^NKG("$LI", label)
		Return $Get(^NKG("$LI", label))
	}
	Set idx = $Get(^NKG("$LI", label))
	If idx '= "" {
		Lock -^NKG("$LI", label)
		Return idx
	}
	Set idx = $Increment(^NKG("$meta", "labelCount")) - 1
	Set ^NKG("$LS", idx) = label
	Set ^NKG("$LI", label) = idx
	Lock -^NKG("$LI", label)
	Return idx
}

ClassMethod UpdateStructuralHLL(sIdx As %Integer, pIdx As %Integer, oIdx As %Integer)
{
	Set hashBytes = $SYSTEM.Encryption.SHA1Hash(oIdx _ "")
	Set regIdx = ($ASCII(hashBytes, 1) # 256) + 1
	Set b1 = $ASCII(hashBytes, 2)
	Set lz = 1
	If b1 = 0 {
		Set lz = 9
	} Else {
		While (b1 # 2) = 0 { Set lz = lz + 1, b1 = b1 \ 2 }
	}
	Set hll = $Get(^NKG("$agg", sIdx, pIdx, "hll"), ##class(Graph.KG.GraphIndex).EmptyHLL())
	If lz > $List(hll, regIdx) {
		Set $List(hll, regIdx) = lz
		Set ^NKG("$agg", sIdx, pIdx, "hll") = hll
	}
}

ClassMethod EmptyHLL() As %List
{
	Return $ListBuild(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
}

ClassMethod MergeHLL(ByRef merged As %List, other As %List)
{
	For i = 1:1:256 {
		If $List(other, i) > $List(merged, i) {
			Set $List(merged, i) = $List(other, i)
		}
	}
}

ClassMethod EstimateHLL(hll As %List) As %Integer
{
	Set Z = 0
	Set zeroCount = 0
	For i = 1:1:256 {
		Set reg = $List(hll, i)
		If reg = 0 { Set zeroCount = zeroCount + 1 }
		Set Z = Z + (1 / (2 ** reg))
	}
	If Z = 0 Return 0
	Set rawEstimate = 0.7182 * 256 * 256 / Z
	If zeroCount > 0 && (rawEstimate < (2.5 * 256)) {
		Return $Number(256 * $ZLN(256 / zeroCount), 0)
	}
	Return $Number(rawEstimate, 0)
}

ClassMethod GetNodeIdx(id As %String) As %String
{
	Return $Get(^NKG("$NI", id), "")
}

ClassMethod GetLabelIdx(label As %String) As %String
{
	Return $Get(^NKG("$LI", label), "")
}

/// Remove one edge's ^NKG entries. Called by Graph.KG.EdgeScan:DeleteAdjacency.
///
/// ^NKG only. This used to remove ^KG adjacency too, and that second owner is
/// where the deletion bugs lived: the graph key was hardcoded to 0, so deleting a
/// named-graph edge reached into the default graph and removed its entry for the
/// same triple, while the counters were spelled ^KG("deg", s) — one subscript
/// short, putting the node id where the graph key belongs. EdgeScan owns ^KG
/// adjacency and its counters; this owns ^NKG interning. One global, one owner.
///
/// ^NKG carries no graph dimension by design: interning is append-only and
/// ignores graph_id, which is why its ids cannot be attributed to one graph
/// (see Graph.KG.GraphStores).
ClassMethod DeleteIndex(pID As %String, s As %Binary, p As %Binary, o As %Binary, qualifiers As %Binary = "") As %Status
{
	Set sIdx = $Get(^NKG("$NI", s))
	Set oIdx = $Get(^NKG("$NI", o))
	Set pIdx = $Get(^NKG("$LI", p))
	If (sIdx = "") || (oIdx = "") || (pIdx = "") Quit $$$OK
	Kill ^NKG(-1, sIdx, -(pIdx+1), oIdx)
	Kill ^NKG(-2, oIdx, -(pIdx+1), sIdx)
	Set tmp = $Increment(^NKG(-3, sIdx), -1)
	Set tmp = $Increment(^NKG("$meta", "version"))

	Quit $$$OK
}

}
