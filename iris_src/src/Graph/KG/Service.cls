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

/// Create the web application `path` for this namespace, dispatched to this class.
///
/// Every user the application authenticates gets one role, WebAppRole() (IVG_KG_<namespace>), and nothing
/// else (4.6.0 R131): it was %All, so anyone who could log in ran the dispatch with every privilege on the
/// instance. The role holds what the three routes read, and no write privilege of any kind:
///   - read on the resource of each database holding the namespace's globals and routines, or the globals
///     KG, NKG or IVG.* where those are mapped elsewhere (EnsureWebAppRole): the dispatch code, the adjacency
///     index /metaPath walks, the rows the procedures read;
///   - in the IVG Schema (GrantWebAppSql), EXECUTE on kg_KNN_VEC (/vectorSearch), kg_RRF_FUSE (/hybridSearch)
///     and kg_TXT (its text leg), and SELECT on what those read and on what PyOps reads to find the vector
///     table and its width: kg_NodeEmbeddings, rdf_labels, docs, embedding_registry. The procedures' reads
///     run with the caller's privileges, and a read the caller may not make answers no rows, not an error;
///     INFORMATION_SCHEMA shows a table's columns only to a user with a privilege on the table.
ClassMethod CreateWebApp(path As %String = "/kg") As %Status
{
	Set tSC = $$$OK
	Try {
		Set tNs = $Namespace
		Set tRole = ..WebAppRole(tNs)
		Set tSC = ..EnsureWebAppRole(tNs, tRole)
		Quit:$$$ISERR(tSC)
		Set tSC = ..GrantWebAppSql(tRole)
		Quit:$$$ISERR(tSC)
		Set props("Name") = path
		Set props("NameSpace") = tNs
		Set props("DispatchClass") = "Graph.KG.Service"
		Set props("Enabled") = 1
		// ":role" adds the role for every user the application authenticates.
		Set props("MatchRoles") = ":"_tRole
		Set props("AutheEnabled") = 32
		// Security.Applications exists only in %SYS, and the status of Create was ignored,
		// so from any application namespace this threw or reported success for nothing.
		New $Namespace
		Set $Namespace = "%SYS"
		Set tSC = ##class(Security.Applications).Create(path, .props)
	} Catch ex {
		Set tSC = ex.AsStatus()
	}
	Quit tSC
}

/// The role CreateWebApp's application adds in pNamespace: one per namespace, since what it may read (the
/// databases, the IVG Schema's objects) is the namespace's.
ClassMethod WebAppRole(pNamespace As %String = {$Namespace}) As %String
{
	Quit "IVG_KG_"_$ZConvert($Translate(pNamespace, "%-", "__"), "U")
}

/// Create pRole, or reset its resources: read on the resource of every database the routes read in pNamespace.
ClassMethod EnsureWebAppRole(pNamespace As %String, pRole As %String) As %Status [ Internal ]
{
	Set tSC = $$$OK
	Try {
		Set tDescription = "IVG /kg web application in "_pNamespace_" (Graph.KG.Service.CreateWebApp)"
		New $Namespace
		Set $Namespace = "%SYS"
		Set tSC = ##class(Config.Namespaces).Get(pNamespace, .tNsProps)
		Quit:$$$ISERR(tSC)
		For tKey = "Globals", "Routines" {
			Set tDb = $Get(tNsProps(tKey))
			Continue:tDb=""
			Kill tDbProps
			Set tSC = ##class(Config.Databases).Get(tDb, .tDbProps)
			Quit:$$$ISERR(tSC)
			Set tDir = $Get(tDbProps("Directory"))
			If tDir '= "" Set tDirs(tDir) = ""
		}
		Quit:$$$ISERR(tSC)
		// The globals KG, NKG and IVG.* may be mapped to a database of their own: "^^dir" is a local one.
		For tGlobal = "KG", "NKG", "IVG" {
			Set tDest = ##class(%SYS.Namespace).GetGlobalDest(pNamespace, tGlobal)
			Set tDir = $Piece(tDest, "^", 3)
			If ($Piece(tDest, "^", 2) = "") && (tDir '= "") Set tDirs(tDir) = ""
		}
		Set tResources = ""
		Set tDir = ""
		For {
			Set tDir = $Order(tDirs(tDir))
			Quit:tDir=""
			Set tDatabase = ##class(SYS.Database).%OpenId(tDir)
			If '$IsObject(tDatabase) {
				Set tSC = $$$ERROR($$$GeneralError, "no database at "_tDir_" for namespace "_pNamespace)
				Quit
			}
			Set tGrant = tDatabase.ResourceName_":R"
			If (","_tResources_",") '[ (","_tGrant_",") {
				Set tResources = tResources_$Select(tResources = "": "", 1: ",")_tGrant
			}
		}
		Quit:$$$ISERR(tSC)
		If ##class(Security.Roles).Exists(pRole) {
			Set tProps("Resources") = tResources
			Set tProps("Description") = tDescription
			Set tSC = ##class(Security.Roles).Modify(pRole, .tProps)
		} Else {
			Set tSC = ##class(Security.Roles).Create(pRole, tDescription, tResources)
		}
	} Catch ex {
		Set tSC = ex.AsStatus()
	}
	Quit tSC
}

/// Grant pRole, in this namespace, the SQL privileges the routes use. Graph_KG is the deploy's token for the
/// namespace's IVG Schema.
ClassMethod GrantWebAppSql(pRole As %String) As %Status [ Internal ]
{
	Set tSC = $$$OK
	Try {
		Set tGrants(1) = "GRANT EXECUTE ON Graph_KG.kg_KNN_VEC TO "_pRole
		Set tGrants(2) = "GRANT EXECUTE ON Graph_KG.kg_RRF_FUSE TO "_pRole
		Set tGrants(3) = "GRANT EXECUTE ON Graph_KG.kg_TXT TO "_pRole
		Set tGrants(4) = "GRANT SELECT ON Graph_KG.kg_NodeEmbeddings TO "_pRole
		Set tGrants(5) = "GRANT SELECT ON Graph_KG.rdf_labels TO "_pRole
		Set tGrants(6) = "GRANT SELECT ON Graph_KG.docs TO "_pRole
		Set tGrants(7) = "GRANT SELECT ON Graph_KG.embedding_registry TO "_pRole
		For i = 1:1:7 {
			Set tRS = ##class(%SQL.Statement).%ExecDirect(, tGrants(i))
			If tRS.%SQLCODE < 0 {
				Set tSC = $$$ERROR($$$GeneralError, tGrants(i)_": SQLCODE "_tRS.%SQLCODE_" "_tRS.%Message)
				Quit
			}
		}
	} Catch ex {
		Set tSC = ex.AsStatus()
	}
	Quit tSC
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
