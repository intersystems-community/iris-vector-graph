Class Graph.KG.Service Extends (%RegisteredObject, %CSP.REST)
{

XData UrlMap [ XMLNamespace = "http://www.intersystems.com/urlmap" ]
{
<Routes>
  <Route Url="/vectorSearch" Method="POST" Call="VectorSearch"/>
  <Route Url="/hybridSearch" Method="POST" Call="HybridSearch"/>
  <Route Url="/metaPath" Method="POST" Call="MetaPath"/>
</Routes>
}

ClassMethod CreateWebApp(path As %String = "/kg") As %Status
{
	Try {
		Set webName = path
		Set tNs = $Namespace
		Set props("Name") = webName
		Set props("NameSpace") = tNs
		Set props("DispatchClass") = "Graph.KG.Service"
		Set props("Enabled") = 1
		Set props("MatchRoles") = ":%All"
		Set props("AutheEnabled") = 32
		// Security.Applications exists only in %SYS, and the status of Create was ignored,
		// so from any application namespace this threw or reported success for nothing.
		New $Namespace
		Set $Namespace = "%SYS"
		Set sc = ##class(Security.Applications).Create(webName, .props)
		Return sc
	} Catch ex {
		Return ex.AsStatus()
	}
}

ClassMethod VectorSearch() As %Status
{
	Try {
		Set in = ..ReadJSON()
		// $Get does not read dynamic-object properties (it throws <INVALID CLASS>), so every
		// default below goes through %Get.
		Set k = in.%Get("k")
		If k = "" Set k = 50
		Set label = in.%Get("label")
		Set vec = in.vector
		// Spec 230 FR-014: the request names its graph, or gets the default graph.
		// %Get rather than $Get — a missing key answers "", and $Get does not read
		// dynamic-object keys.
		Set graph = in.%Get("graph")
		Set res = ##class(Graph.KG.PyOps).VectorSearch(vec, k, label, graph)
		Return ..WriteJSON(res)
	} Catch ex {
		Return ..WriteError(ex.AsStatus())
	}
}

ClassMethod HybridSearch() As %Status
{
	Try {
		Set in = ..ReadJSON()
		Set k = in.%Get("k")
		If k = "" Set k = 50
		Set c = in.%Get("c")
		If c = "" Set c = 60
		Set text = in.%Get("text")
		Set vec = in.vector
		Set graph = in.%Get("graph")
		Set res = ##class(Graph.KG.PyOps).HybridSearch(vec, text, k, c, graph)
		Return ..WriteJSON(res)
	} Catch ex {
		Return ..WriteError(ex.AsStatus())
	}
}

ClassMethod MetaPath() As %Status
{
	Try {
		Set in = ..ReadJSON()
		Set srcId = in.srcId
		Set preds = in.predicates
		If '$IsObject(preds) Set preds = []
		Set maxHops = in.%Get("maxHops")
		If maxHops = "" Set maxHops = 2
		Set dstLabel = in.%Get("dstLabel")
		Set res = ##class(Graph.KG.PyOps).MetaPath(srcId, preds, maxHops, dstLabel)
		Return ..WriteJSON(res)
	} Catch ex {
		Return ..WriteError(ex.AsStatus())
	}
}

ClassMethod ReadJSON() As %DynamicObject
{
	If '$IsObject(%request.Content) Return {}
	Set body = %request.Content.Read()
	If body="" Return {}
	Return {}.%FromJSON(body)
}

ClassMethod WriteJSON(obj As %DynamicAbstractObject) As %Status
{
	Do %response.SetHeader("Content-Type","application/json")
	// %CSP.Response has no Write method: a REST handler writes to the current device.
	Write obj.%ToJSON()
	Return $$$OK
}

ClassMethod WriteError(sc As %Status) As %Status
{
	Set errText = $System.Status.GetErrorText(sc)
	Set err = {"error": (errText)}
	Do %response.SetHeader("Content-Type","application/json")
	Set %response.Status = "500 Internal Server Error"
	Write err.%ToJSON()
	Return $$$OK
}

}
