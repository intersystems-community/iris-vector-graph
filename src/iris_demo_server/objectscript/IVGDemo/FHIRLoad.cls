/// FHIR graph demo helper: dispatch FHIR interactions in-process, with no HTTP.
///
/// The enterprise image is a NoPWS build, so the FHIR endpoint has no web server in
/// front of it. HS.FHIRServer.Service.DispatchRequest is the entry point the REST
/// handler calls, so a resource written here lands in Rsrc, RsrcVer and the search
/// tables exactly as one PUT over HTTP would, and the FHIR graph sync sees it the
/// same way.
Class IVGDemo.FHIRLoad Extends %RegisteredObject
{

/// Dispatch one interaction. Returns {"status": "...", "body": {...}} as JSON, or
/// {"status": "error", "error": "..."} when the service throws.
ClassMethod Dispatch(pEndpoint As %String, pMethod As %String, pPath As %String, pBody As %String = "") As %String
{
	Set tOut = ##class(%DynamicObject).%New()
	Try {
		Set tStrategy = ##class(HS.FHIRServer.API.InteractionsStrategy).GetStrategyForEndpoint(pEndpoint)
		If '$IsObject(tStrategy) {
			Do tOut.%Set("status", "error")
			Do tOut.%Set("error", "no FHIR endpoint "_pEndpoint)
			Return tOut.%ToJSON()
		}
		Set tService = ##class(HS.FHIRServer.Service).EnsureInstance(tStrategy.InstanceKey)
		Set tReq = ##class(HS.FHIRServer.API.Data.Request).%New()
		Set tReq.RequestPath = pPath
		Set tReq.RequestMethod = pMethod
		Set tReq.RequestFormatCode = "JSON"
		Set tReq.ResponseFormatCode = "JSON"
		If pBody'="" Set tReq.Json = ##class(%DynamicObject).%FromJSON(pBody)
		Set tResp = ""
		Do tService.DispatchRequest(tReq, .tResp)
		If '$IsObject(tResp) {
			Do tOut.%Set("status", "error")
			Do tOut.%Set("error", "no response from DispatchRequest")
			Return tOut.%ToJSON()
		}
		Do tOut.%Set("status", tResp.Status)
		If $IsObject(tResp.Json) Do tOut.%Set("body", tResp.Json)
	}
	Catch tEx {
		Set tOut = ##class(%DynamicObject).%New()
		Do tOut.%Set("status", "error")
		Do tOut.%Set("error", tEx.DisplayString())
	}
	Quit tOut.%ToJSON()
}

}
