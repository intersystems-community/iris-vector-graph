/// Test-only helper: dispatch FHIR interactions in-process, with no HTTP.
///
/// The enterprise test image is a NoPWS build, so the FHIR endpoint has no web
/// server in front of it. HS.FHIRServer.Service.DispatchRequest is the same entry
/// point the REST handler calls, so a resource loaded here lands in Rsrc, RsrcVer
/// and the search tables exactly as one POSTed over HTTP would.
Class IVGTest.FHIRLoad Extends %RegisteredObject
{

/// Dispatch one interaction. Returns {"status": "...", "body": {...}} as JSON.
ClassMethod Dispatch(pEndpoint As %String, pMethod As %String, pPath As %String, pBody As %String = "") As %String
{
	Set tStrategy = ##class(HS.FHIRServer.API.InteractionsStrategy).GetStrategyForEndpoint(pEndpoint)
	Set tService = ##class(HS.FHIRServer.Service).EnsureInstance(tStrategy.InstanceKey)
	Set tReq = ##class(HS.FHIRServer.API.Data.Request).%New()
	Set tReq.RequestPath = pPath
	Set tReq.RequestMethod = pMethod
	Set tReq.RequestFormatCode = "JSON"
	Set tReq.ResponseFormatCode = "JSON"
	If pBody '= "" Set tReq.Json = ##class(%DynamicObject).%FromJSON(pBody)
	Set tResp = ""
	Do tService.DispatchRequest(tReq, .tResp)
	Set tOut = ##class(%DynamicObject).%New()
	Do tOut.%Set("status", tResp.Status)
	If $IsObject(tResp.Json) Do tOut.%Set("body", tResp.Json)
	Quit tOut.%ToJSON()
}

/// Hold a FHIR graph's sync lock in this process, as a running sync would.
ClassMethod HoldLock(pGraph As %String)
{
	Lock +^IVG.FHIRGraph(pGraph):5
}

/// Release the lock HoldLock took.
ClassMethod ReleaseLock(pGraph As %String)
{
	Lock -^IVG.FHIRGraph(pGraph)
}

}
