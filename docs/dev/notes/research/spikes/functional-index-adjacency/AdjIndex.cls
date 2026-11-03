/// SPIKE (throwaway): the ^KG adjacency stores as a functional index on an edge table, so every SQL write
/// maintains them in the same transaction. Same layout as ^KG("out"/"in"/"deg"/"degp"/"__version"), under ^IVGSpike.
Class IVGSpike.AdjIndex Extends %Library.FunctionalIndex
{

Parameter SUPPORTSSHARDING = 1;

ClassMethod InsertIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Set g = pArg(1), s = pArg(2), p = pArg(3), o = pArg(4)
	Set k = $Select((g = "") || (g = $Char(0)): 0, 1: g)
	Set ^IVGSpike("out", k, s, p, o) = 1
	Set ^IVGSpike("in", k, o, p, s) = 1
	Set tmp = $Increment(^IVGSpike("deg", k, s))
	Set tmp = $Increment(^IVGSpike("degp", k, s, p))
	Set tmp = $Increment(^IVGSpike("__version"))
	Quit $$$OK
}

ClassMethod UpdateIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Do ##class(IVGSpike.AdjIndex).DeleteIndex(pID, pArg(5), pArg(6), pArg(7), pArg(8))
	Quit ##class(IVGSpike.AdjIndex).InsertIndex(pID, pArg(1), pArg(2), pArg(3), pArg(4))
}

ClassMethod DeleteIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Set g = pArg(1), s = pArg(2), p = pArg(3), o = pArg(4)
	Set k = $Select((g = "") || (g = $Char(0)): 0, 1: g)
	Kill ^IVGSpike("out", k, s, p, o)
	Kill ^IVGSpike("in", k, o, p, s)
	If $Increment(^IVGSpike("deg", k, s), -1) <= 0 { Kill ^IVGSpike("deg", k, s) }
	If $Increment(^IVGSpike("degp", k, s, p), -1) <= 0 { Kill ^IVGSpike("degp", k, s, p) }
	Set tmp = $Increment(^IVGSpike("__version"))
	Quit $$$OK
}

/// Only the derived stores are purged, never a shared root.
ClassMethod PurgeIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
	Kill ^IVGSpike("out"), ^IVGSpike("in"), ^IVGSpike("deg"), ^IVGSpike("degp")
	Quit $$$OK
}

ClassMethod SortBeginIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
	Quit $$$OK
}

ClassMethod SortEndIndex(pCommit As %Integer = 1) As %Status [ ServerOnly = 1 ]
{
	Quit $$$OK
}

}
