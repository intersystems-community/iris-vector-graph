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
	Do ..InitStructuralLabels()
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

	Set sIdx = ..InternNode(s)
	Set oIdx = ..InternNode(o)
	Set pIdx = ..InternLabel(p)
	Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight
	Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight
	Set tmp = $Increment(^NKG(-3, sIdx))
	Set tmp = $Increment(^NKG("$meta", "version"))

	Quit $$$OK
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
