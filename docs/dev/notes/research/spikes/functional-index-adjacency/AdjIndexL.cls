/// SPIKE variant: deg/degp kept with Set under a lock, so a rollback undoes them (a $Increment is not rolled back).
Class IVGSpike.AdjIndexL Extends %Library.FunctionalIndex
{

Parameter SUPPORTSSHARDING = 1;

ClassMethod InsertIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Set g = pArg(1), s = pArg(2), p = pArg(3), o = pArg(4)
	Set k = $Select((g = "") || (g = $Char(0)): 0, 1: g)
	Set ^IVGSpikeL("out", k, s, p, o) = 1
	Set ^IVGSpikeL("in", k, o, p, s) = 1
	Lock +^IVGSpikeL("deg", k, s):30
	Set ^IVGSpikeL("deg", k, s) = $Get(^IVGSpikeL("deg", k, s)) + 1
	Set ^IVGSpikeL("degp", k, s, p) = $Get(^IVGSpikeL("degp", k, s, p)) + 1
	Lock -^IVGSpikeL("deg", k, s)
	Set tmp = $Increment(^IVGSpikeL("__version"))
	Quit $$$OK
}

ClassMethod UpdateIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Do ##class(IVGSpike.AdjIndexL).DeleteIndex(pID, pArg(5), pArg(6), pArg(7), pArg(8))
	Quit ##class(IVGSpike.AdjIndexL).InsertIndex(pID, pArg(1), pArg(2), pArg(3), pArg(4))
}

ClassMethod DeleteIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Set g = pArg(1), s = pArg(2), p = pArg(3), o = pArg(4)
	Set k = $Select((g = "") || (g = $Char(0)): 0, 1: g)
	Kill ^IVGSpikeL("out", k, s, p, o)
	Kill ^IVGSpikeL("in", k, o, p, s)
	Lock +^IVGSpikeL("deg", k, s):30
	Set d = $Get(^IVGSpikeL("deg", k, s)) - 1
	If d <= 0 { Kill ^IVGSpikeL("deg", k, s) } Else { Set ^IVGSpikeL("deg", k, s) = d }
	Set dp = $Get(^IVGSpikeL("degp", k, s, p)) - 1
	If dp <= 0 { Kill ^IVGSpikeL("degp", k, s, p) } Else { Set ^IVGSpikeL("degp", k, s, p) = dp }
	Lock -^IVGSpikeL("deg", k, s)
	Set tmp = $Increment(^IVGSpikeL("__version"))
	Quit $$$OK
}

ClassMethod PurgeIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
	Kill ^IVGSpikeL("out"), ^IVGSpikeL("in"), ^IVGSpikeL("deg"), ^IVGSpikeL("degp")
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
