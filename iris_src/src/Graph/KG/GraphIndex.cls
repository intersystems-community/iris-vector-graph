Class Graph.KG.GraphIndex Extends %Library.FunctionalIndex
{

Parameter SUPPORTSSHARDING = 1;

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

ClassMethod InsertIndex(pID As %String, s As %Binary, p As %Binary, o As %Binary, qualifiers As %Binary = "") As %Status [ ServerOnly = 1 ]
{
	Set weight = ##class(Graph.KG.GraphIndex).ExtractWeight(qualifiers)

	Set ^KG("out", s, p, o) = weight
	Set ^KG("in", o, p, s) = weight
	Set tmp = $Increment(^KG("deg", s))
	Set tmp = $Increment(^KG("degp", s, p))

	Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(s)
	Set oIdx = ##class(Graph.KG.GraphIndex).InternNode(o)
	Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(p)
	Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight
	Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight
	Set tmp = $Increment(^NKG(-3, sIdx))
	Set tmp = $Increment(^NKG("$meta", "version"))

	Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(sIdx, pIdx, oIdx)

	Quit $$$OK
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

ClassMethod UpdateIndex(pID As %String, s As %Binary, p As %Binary, o As %Binary, qualifiers As %Binary = "", olds As %Binary, oldp As %Binary, oldo As %Binary, oldqualifiers As %Binary = "") As %Status [ ServerOnly = 1 ]
{
	Do ##class(Graph.KG.GraphIndex).DeleteIndex(pID, olds, oldp, oldo, oldqualifiers)
	Quit ##class(Graph.KG.GraphIndex).InsertIndex(pID, s, p, o, qualifiers)
}

ClassMethod DeleteIndex(pID As %String, s As %Binary, p As %Binary, o As %Binary, qualifiers As %Binary = "") As %Status [ ServerOnly = 1 ]
{
	Kill ^KG("out", s, p, o)
	Kill ^KG("in", o, p, s)
	Set tmp = $Increment(^KG("deg", s), -1)
	Set tmp = $Increment(^KG("degp", s, p), -1)
	If $Get(^KG("deg", s)) <= 0 Kill ^KG("deg", s)
	If $Get(^KG("degp", s, p)) <= 0 Kill ^KG("degp", s, p)

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

ClassMethod ExtractWeight(qualifiers As %String) As %Double
{
	Set result = 1.0
	If qualifiers = "" Return result
	Try {
		Set obj = ##class(%DynamicObject).%FromJSON(qualifiers)
		Set weight = obj.weight
		If weight '= "" Set result = weight
	} Catch ex {
	}
	Return result
}

ClassMethod PurgeIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
	Kill ^KG
	Kill ^NKG
	Quit $$$OK
}

ClassMethod SortBeginIndex() As %Status [ ServerOnly = 1 ]
{
	Quit $$$OK
}

ClassMethod SortEndIndex(pCommit As %Integer = 1) As %Status [ ServerOnly = 1 ]
{
	Quit $$$OK
}

}
