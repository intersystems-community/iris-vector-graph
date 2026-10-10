/// Graph.KG.AlgoCache - the shared result cache for graph-wide algorithms (spec 236).
///
///   ^IVG.AlgoCache(graphKey, algorithm, paramHash) = $ListBuild(token, payload)
///
/// `token` is ^KG("__version") as read BEFORE the answer was computed, so a write that
/// lands during the computation leaves the entry stale and never wrongly fresh. Every
/// read compares it with the current value; a mismatch is a miss. The graph key comes
/// from Graph.KG.GraphKey.ForIndex (ADR-0003), the same subscript ^KG uses, never the
/// ledger's form.
///
/// Kept out of ^KG so that Kill ^KG cannot take it along: the token comparison would
/// still catch the stale entry, but a cache that outlives its graph is a cache that can
/// be read by mistake (ADR-0004). Erase paths kill it too (Graph.KG.Eraser).
Class Graph.KG.AlgoCache Extends %RegisteredObject
{

/// The current graph-version token. 0 when nothing has ever written the graph.
ClassMethod Token() As %Integer
{
	Quit +$Get(^KG("__version"), 0)
}

/// One round trip for both outcomes: "H<token>|<payload>" when the stored token equals
/// the current one, otherwise "M<token>". The token is the one the caller hands back to
/// Put after it has computed the answer (on a miss), or validates a front copy against
/// (on a hit).
ClassMethod Lookup(pGraph As %String = "", pAlgorithm As %String, pParamHash As %String) As %String
{
	Set tToken = ..Token()
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tEntry = $Get(^IVG.AlgoCache(tKey, pAlgorithm, pParamHash))
	If (tEntry '= "") && ($ListGet(tEntry, 1) = tToken) {
		Quit "H" _ tToken _ "|" _ $ListGet(tEntry, 2)
	}
	Quit "M" _ tToken
}

/// The payload when the entry is current, otherwise "".
ClassMethod Get(pGraph As %String = "", pAlgorithm As %String, pParamHash As %String) As %String
{
	Set tOut = ..Lookup(pGraph, pAlgorithm, pParamHash)
	Quit $Select($Extract(tOut) = "H": $Extract(tOut, $Find(tOut, "|"), *), 1: "")
}

/// Store an answer under the token read before it was computed.
ClassMethod Put(pGraph As %String = "", pAlgorithm As %String, pParamHash As %String, pToken As %Integer, pPayload As %String) As %Integer
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set ^IVG.AlgoCache(tKey, pAlgorithm, pParamHash) = $ListBuild(+pToken, pPayload)
	Quit 1
}

/// Drop every entry of one graph.
ClassMethod InvalidateGraph(pGraph As %String = "") As %Integer
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Kill ^IVG.AlgoCache(tKey)
	Quit 1
}

/// Drop every entry of every graph.
ClassMethod InvalidateAll() As %Integer
{
	Kill ^IVG.AlgoCache
	Quit 1
}

}
