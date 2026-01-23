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
		Set sc = $$$OK
		Set webName = path
		Set props("Name") = webName
		Set props("DispatchClass") = "Graph.KG.Service"
		Set props("Enabled") = 1
		Set props("MatchRoles") = ":/%All"
		Set props("AutheEnabled") = 32
		Do ##class(Security.Applications).Create(webName, .props)
		Return sc
	} Catch ex {
		Return ex.AsStatus()
	}
}

ClassMethod VectorSearch() As %Status
{
	Try {
		Set in = ..ReadJSON()
		Set k = $Get(in.k, 50)
		Set label = $Get(in.label, "")
		Set vec = in.vector
		Set res = ##class(Graph.KG.PyOps).VectorSearch(vec, k, label)
		Return ..WriteJSON(res)
	} Catch ex {
		Return ..WriteError(ex.AsStatus())
	}
}

ClassMethod HybridSearch() As %Status
{
	Try {
		Set in = ..ReadJSON()
		Set k = $Get(in.k, 50)
		Set c = $Get(in.c, 60)
		Set text = $Get(in.text, "")
		Set vec = in.vector
		Set res = ##class(Graph.KG.PyOps).HybridSearch(vec, text, k, c)
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
		Set preds = $Get(in.predicates, [])
		Set maxHops = $Get(in.maxHops, 2)
		Set dstLabel = $Get(in.dstLabel, "")
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
	Do %response.Write(obj.%ToJSON())
	Return $$$OK
}

ClassMethod WriteError(sc As %Status) As %Status
{
	Set errText = $System.Status.GetErrorText(sc)
	Set err = {"error": (errText)}
	Do %response.SetHeader("Content-Type","application/json")
	Set %response.Status = "500 Internal Server Error"
	Do %response.Write(err.%ToJSON())
	Return $$$OK
}

}
