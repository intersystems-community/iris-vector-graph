/// Fast bulk seeder for benchmark graphs.
/// SeedFromStaging: Python writes to ^KGSeed, ObjectScript reads server-side.
/// SeedRandom: pure ObjectScript random graph generator — no Python needed.
Class Graph.KG.BenchSeeder Extends %RegisteredObject
{

ClassMethod SeedFromStaging(version As %String = "1") As %Status
{
	Set count = $Get(^KGSeed("count"), 0)
	Set i = 0
	While i < count {
		Set pair = ^KGSeed(i)
		Set src = $Piece(pair, $Char(9), 1)
		Set dst = $Piece(pair, $Char(9), 2)
		Set ^KG("out", src, "REL", dst) = 1
		Set ^KG("in", dst, "REL", src) = 1
		Set ^KG("deg", src) = $Get(^KG("deg", src), 0) + 1
		Set i = i + 1
	}
	Set ^KG("__version") = version
	Kill ^KGSeed
	Return $$$OK
}

/// Generate a random graph with nNodes nodes and nEdges edges directly into ^KG + ^NKG.
/// Bypasses SQL entirely — pure global writes for maximum speed.
/// predCount controls the number of distinct predicates (default 5).
ClassMethod SeedRandom(nNodes As %Integer = 10000, nEdges As %Integer = 50000, predCount As %Integer = 5) As %String
{
	Kill ^KG, ^NKG
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	
	Set t0 = $ZH
	
	For i = 0:1:(predCount-1) {
		Set preds(i) = "REL"_i
		Set tmp = ##class(Graph.KG.GraphIndex).InternLabel("REL"_i)
	}
	
	For i = 0:1:(nNodes-1) {
		Set nid = "N"_i
		Set tmp = ##class(Graph.KG.GraphIndex).InternNode(nid)
	}
	Set nodeTime = $ZH - t0
	
	Set t1 = $ZH
	Set inserted = 0
	For e = 1:1:nEdges {
		Set si = $Random(nNodes)
		Set di = $Random(nNodes)
		If si = di Continue
		Set src = "N"_si
		Set dst = "N"_di
		Set pred = preds($Random(predCount))
		
		If $Data(^KG("out", src, pred, dst)) Continue
		
		Set ^KG("out", src, pred, dst) = 1
		Set ^KG("in", dst, pred, src) = 1
		Set ^KG("deg", src) = $Get(^KG("deg", src), 0) + 1
		Set ^KG("degp", src, pred) = $Get(^KG("degp", src, pred), 0) + 1
		
		Set sIdx = $Get(^NKG("$NI", src))
		Set oIdx = $Get(^NKG("$NI", dst))
		Set pIdx = $Get(^NKG("$LI", pred))
		Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = 1
		Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = 1
		Set tmp = $Increment(^NKG(-3, sIdx))
		
		Set inserted = inserted + 1
	}
	Set edgeTime = $ZH - t1
	
	Set ^NKG("$meta", "edgeCount") = inserted
	Set tmp = $Increment(^NKG("$meta", "version"))
	
	Set totalTime = $ZH - t0
	Set result = ##class(%DynamicObject).%New()
	Do result.%Set("nodes", nNodes)
	Do result.%Set("edges", inserted)
	Do result.%Set("predicates", predCount)
	Do result.%Set("nodeTimeSec", $FN(nodeTime, "", 3))
	Do result.%Set("edgeTimeSec", $FN(edgeTime, "", 3))
	Do result.%Set("totalTimeSec", $FN(totalTime, "", 3))
	Return result.%ToJSON()
}

}
