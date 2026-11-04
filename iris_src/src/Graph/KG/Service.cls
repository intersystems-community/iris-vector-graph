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

/// Create the web application `path` for this namespace, dispatched to this class, or reset the one there.
///
/// Every user the application authenticates gets one role, WebAppRole() (IVG_KG_<namespace>), and nothing
/// else (4.6.0 R131): it was %All, so anyone who could log in ran the dispatch with every privilege on the
/// instance. The role holds what the three routes read, and no write privilege of any kind:
///   - read on the resource of each database holding the namespace's globals and routines, or the globals
///     KG, NKG or IVG where those are mapped elsewhere (EnsureWebAppRole): the dispatch code, the adjacency
///     index /metaPath walks, the rows the procedures read. No route reads a global ^IVG.* (4.6.0 R147,
///     tests/unit/test_460_kg_routes_read_no_ivg_globals.py), which GetGlobalDest(ns, "IVG") does not resolve;
///   - in the IVG Schema (GrantWebAppSql), EXECUTE on kg_KNN_VEC (/vectorSearch), kg_RRF_FUSE (/hybridSearch)
///     and kg_TXT (its text leg), and SELECT on what those read and on what PyOps reads to find the vector
///     table and its width: kg_NodeEmbeddings, rdf_labels, docs, embedding_registry. The procedures' reads
///     run with the caller's privileges; INFORMATION_SCHEMA shows a table's columns only to a user with a
///     privilege on the table.
///
/// Running it again resets both to exactly that (4.6.0 R147): the role's resources, granted roles and SQL
/// privileges in the IVG Schema, and the application's namespace, dispatch class, MatchRoles, authentication
/// and enabled flag. Re-running it is how an application an earlier release made (MatchRoles ":%All") is
/// fixed. An application at `path` that is not IVG's (another dispatch class) is refused, and nothing changes.
ClassMethod CreateWebApp(path As %String = "/kg") As %Status
{
	Set tSC = $$$OK
	Try {
		Set tNs = $Namespace
		Set tRole = ..WebAppRole(tNs)
		// Refuse another application before touching the role: CreateWebApp("/csp/sys") must change nothing.
		Set tSC = ..CheckWebApp(path)
		Quit:$$$ISERR(tSC)
		Set tSC = ..EnsureWebAppRole(tNs, tRole)
		Quit:$$$ISERR(tSC)
		Set tSC = ..GrantWebAppSql(tRole)
		Quit:$$$ISERR(tSC)
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
		If ##class(Security.Applications).Exists(path) {
			// Create failed on an existing application, after resetting the role, so an application a 4.5.x
			// release made kept ":%All" (4.6.0 R147).
			Set tSC = ##class(Security.Applications).Modify(path, .props)
		} Else {
			Set props("Name") = path
			Set tSC = ##class(Security.Applications).Create(path, .props)
		}
	} Catch ex {
		Set tSC = ex.AsStatus()
	}
	Quit tSC
}

/// An error unless the web application `path` is absent or IVG's own (its dispatch class is this class).
ClassMethod CheckWebApp(path As %String) As %Status [ Internal ]
{
	Set tSC = $$$OK
	Try {
		New $Namespace
		Set $Namespace = "%SYS"
		Quit:'##class(Security.Applications).Exists(path)
		Set tSC = ##class(Security.Applications).Get(path, .tProps)
		Quit:$$$ISERR(tSC)
		Set tDispatch = $Get(tProps("DispatchClass"))
		If tDispatch '= "Graph.KG.Service" {
			Set tSC = $$$ERROR($$$GeneralError, "the web application "_path_" exists and is not IVG's (its dispatch class is """_tDispatch_""", not Graph.KG.Service): CreateWebApp changed nothing; choose another path")
		}
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

/// Create pRole, or reset its resources and granted roles: read on the resource of every database the routes read
/// in pNamespace, and no other role (a role granted to it gives it that role's privileges, 4.6.0 R147).
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
		// The globals KG, NKG and IVG may be mapped to a database of their own: "^^dir" is a local one. This
		// resolves ^IVG only, not ^IVG.Ledger or ^IVG.AlgoCache, which no route reads (4.6.0 R147).
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
			Set tProps("GrantedRoles") = ""
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

/// Set pRole's SQL privileges in this namespace's IVG Schema to exactly the ones the routes use. Graph_KG is the
/// deploy's token for the namespace's IVG Schema.
///
/// Until 4.6.0 R147 it only granted, so a privilege the role held beyond the list (one a later release drops, or
/// one granted by hand) stayed. Every other privilege the role holds in the IVG Schema is now revoked, column
/// privileges included, and a listed one held WITH GRANT OPTION is revoked and granted again without it. Each
/// REVOKE names the privilege's grantor (AS): without it a REVOKE removes only the current user's own grants, and
/// answers SQLCODE 100, not an error, for one another user made. Privileges outside the IVG Schema are left alone.
ClassMethod GrantWebAppSql(pRole As %String) As %Status [ Internal ]
{
	Set tSC = $$$OK
	Try {
		Set tSchema = "Graph_KG"
		Set tGrants(1) = $ListBuild("EXECUTE", tSchema_".kg_KNN_VEC")
		Set tGrants(2) = $ListBuild("EXECUTE", tSchema_".kg_RRF_FUSE")
		Set tGrants(3) = $ListBuild("EXECUTE", tSchema_".kg_TXT")
		Set tGrants(4) = $ListBuild("SELECT", tSchema_".kg_NodeEmbeddings")
		Set tGrants(5) = $ListBuild("SELECT", tSchema_".rdf_labels")
		Set tGrants(6) = $ListBuild("SELECT", tSchema_".docs")
		Set tGrants(7) = $ListBuild("SELECT", tSchema_".embedding_registry")
		For i = 1:1:7 {
			Set tListed($ZConvert($List(tGrants(i), 1)_" "_$List(tGrants(i), 2), "U")) = ""
		}
		// Columns: TYPE, NAME, PRIVILEGE, GRANTED_BY, GRANT_OPTION. A column privilege's NAME is SCHEMA.TABLE.COLUMN.
		Set tRS = ##class(%ResultSet).%New("%Library.SQLCatalogPriv:SQLRolePrivileges")
		Set tSC = tRS.Execute(pRole)
		Quit:$$$ISERR(tSC)
		Set tRevokes = 0
		While tRS.Next() {
			Set tName = tRS.GetData(2)
			Set tPrivilege = tRS.GetData(3)
			Continue:$ZConvert($Piece(tName, "."), "U")'=$ZConvert(tSchema, "U")
			If $Length(tName, ".") = 3 {
				Set tPrivilege = tPrivilege_"("_$Piece(tName, ".", 3)_")"
				Set tName = $Piece(tName, ".", 1, 2)
			} ElseIf $Data(tListed($ZConvert(tPrivilege_" "_tName, "U"))) && (tRS.GetData(5) '= "Yes") {
				Continue
			}
			Set tGrantor = $Replace(tRS.GetData(4), """", """""")
			Set tRevokes($Increment(tRevokes)) = "REVOKE "_tPrivilege_" ON "_tName_" FROM "_pRole_" CASCADE AS """_tGrantor_""""
		}
		Do tRS.Close()
		For i = 1:1:tRevokes {
			Set tResult = ##class(%SQL.Statement).%ExecDirect(, tRevokes(i))
			// 100 is a REVOKE that removed nothing: the privilege was read just above, so here it is a failure.
			If tResult.%SQLCODE '= 0 {
				Set tSC = $$$ERROR($$$GeneralError, tRevokes(i)_": SQLCODE "_tResult.%SQLCODE_" "_tResult.%Message)
				Quit
			}
		}
		Quit:$$$ISERR(tSC)
		For i = 1:1:7 {
			Set tGrant = "GRANT "_$List(tGrants(i), 1)_" ON "_$List(tGrants(i), 2)_" TO "_pRole
			Set tResult = ##class(%SQL.Statement).%ExecDirect(, tGrant)
			If tResult.%SQLCODE < 0 {
				Set tSC = $$$ERROR($$$GeneralError, tGrant_": SQLCODE "_tResult.%SQLCODE_" "_tResult.%Message)
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
