/// The native Cypher executor (spec 253): runs a plan from iris_vector_graph.cypher.native_plan in one call, next to
/// the data, and keeps the rows for the caller to read in pages.
///
/// Rows live in ^||IVG.CypherOut(tag, n) as JSON arrays, one per row: a result is never one string, which stops at
/// about 3.6 MB (FR-015). Python reads them with ReadPage and frees them with Drop. Any error is thrown to the caller,
/// which answers the query on the SQL path instead (FR-007).
///
/// Phase 1 knows two operators, both for the plumbing tests: "range" (rows [0] .. [n-1]) and "rows" (literal rows).
Class Graph.KG.Cypher.Exec Extends %RegisteredObject
{

/// Run pPlan (JSON {"columns", "ops"}) with pParams (JSON object); returns {"tag", "count"}.
ClassMethod Run(pPlan As %String, pParams As %String = "{}") As %String
{
	Set tPlan = ##class(%Library.DynamicObject).%FromJSON(pPlan)
	Set tTag = $Increment(^||IVG.CypherOut)
	Kill ^||IVG.CypherOut(tTag)
	Set tCount = 0
	Set tOps = tPlan.%Get("ops")
	Set tIter = tOps.%GetIterator()
	While tIter.%GetNext(.tI, .tOp) {
		Set tOpName = tOp.%Get("op")
		If tOpName = "range" {
			For tJ = 0:1:(tOp.%Get("n") - 1) {
				Set ^||IVG.CypherOut(tTag, $Increment(tCount)) = "["_tJ_"]"
			}
		} ElseIf tOpName = "rows" {
			Set tRowIter = tOp.%Get("rows").%GetIterator()
			While tRowIter.%GetNext(.tK, .tRow) {
				Set ^||IVG.CypherOut(tTag, $Increment(tCount)) = tRow.%ToJSON()
			}
		} Else {
			Kill ^||IVG.CypherOut(tTag)
			$$$ThrowStatus($$$ERROR($$$GeneralError, "Graph.KG.Cypher.Exec: unknown operator "_tOpName))
		}
	}
	Set tOut = ##class(%Library.DynamicObject).%New()
	Do tOut.%Set("tag", tTag)
	Do tOut.%Set("count", tCount)
	Quit tOut.%ToJSON()
}

/// Rows pStart .. pStart + pCount - 1 of result pTag, as a JSON array of row arrays.
ClassMethod ReadPage(pTag As %Integer, pStart As %Integer, pCount As %Integer = 1000) As %String
{
	Set tOut = "["
	For tN = pStart:1:(pStart + pCount - 1) {
		Quit:'$Data(^||IVG.CypherOut(pTag, tN), tRow)
		Set tOut = tOut_$Select(tN = pStart: "", 1: ",")_tRow
	}
	Quit tOut_"]"
}

/// Free result pTag.
ClassMethod Drop(pTag As %Integer)
{
	Kill ^||IVG.CypherOut(pTag)
}

}
