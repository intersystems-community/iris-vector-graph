Class Graph.KG.Service Extends %CSP.REST
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
    Set props("AutheEnabled") = 32  // unauthenticated dev
    Do ##class(%SYS.REST).CreateApplication(.props)
    Quit sc
  } Catch ex {
    Quit ex.AsStatus()
  }
}

ClassMethod ReadJSON() As %DynamicObject
{
  Set body = %request.Content.Read()
  Quit {}.%FromJSON(body)
}

ClassMethod WriteJSON(obj As %DynamicAbstractObject) As %Status
{
  Do %response.SetHeader("Content-Type","application/json")
  Do %response.Write(obj.%ToJSON())
  Quit $$$OK
}

ClassMethod VectorSearch() As %Status
{
  Set in = ..ReadJSON()
  Set k = $Get(in.k, 50)
  Set label = $Get(in.label, "")
  Set vec = in.vector
  Set out = []
  Set sc = ##class(Graph.KG.PyOps).VectorSearch(vec, k, label, .out)
  If $$$ISOK(sc) Quit ..WriteJSON(out) Else  Quit sc
}

ClassMethod HybridSearch() As %Status
{
  Set in = ..ReadJSON()
  Set k = $Get(in.k, 50)
  Set c = $Get(in.c, 60)
  Set text = $Get(in.text, "")
  Set vec = in.vector
  Set out = []
  Set sc = ##class(Graph.KG.PyOps).HybridSearch(vec, text, k, c, .out)
  If $$$ISOK(sc) Quit ..WriteJSON(out) Else  Quit sc
}

ClassMethod MetaPath() As %Status
{
  Set in = ..ReadJSON()
  Set srcId = in.srcId
  Set preds = $Get(in.predicates, [])
  Set maxHops = $Get(in.maxHops, 2)
  Set dstLabel = $Get(in.dstLabel, "")
  Set out = []
  Set sc = ##class(Graph.KG.PyOps).MetaPath(srcId, preds, maxHops, dstLabel, .out)
  If $$$ISOK(sc) Quit ..WriteJSON(out) Else  Quit sc
}

}
