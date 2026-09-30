/// Graph.KG.FHIRGraph - an ISC FHIR repository projected as one named graph (spec 231).
///
/// One node per live resource, keyed by its FHIR Key (Patient/p1) and labelled with
/// its resource type; one edge per indexed reference to a live local resource, named
/// by the search param (subject) and qualified with the SearchParameter URL. Nothing
/// else is copied: properties and codes stay in the repository and are read live.
///
/// Every table this class reads on the FHIR side is reached with dynamic SQL, and the
/// schema names come from Graph_KG.fhir_graphs. The HS_FHIRServer classes exist only
/// in a FHIR namespace, so the class compiles everywhere and Register refuses where
/// there is no repository.
///
/// CHANGE FEED. Rsrc holds the current version of every resource and RsrcVer the
/// superseded ones. A first create writes only Rsrc (new Rsrc.ID); every later write,
/// delete included, archives into RsrcVer (new RsrcVer.ID) and updates Rsrc in place.
/// So a sync reads both: Rsrc.ID > wm_rsrc catches creates, RsrcVer.ID > wm_ver catches
/// the rest. Each batch of keys is resynced and its watermark advanced inside one
/// transaction; a failure rolls both back, and the next sync repeats the batch.
///
/// RESYNC. Resyncing a key is idempotent and reads only the repository's current
/// state, so a rebuild and any sequence of syncs converge on the same graph (SC-002).
///
/// Every public entry point returns a JSON string. A failure is
/// {"status":"error","error":...}; a sync that finds another holding the graph's lock
/// is {"status":"busy"}.
Class Graph.KG.FHIRGraph Extends %RegisteredObject
{

/// Keys per transaction. A batch is also the unit of repeat after a failure.
Parameter BATCHSIZE = 200;

/// The one table of search-column metadata, shared by every repository.
Parameter SEARCHCOLUMN = "HS_FHIRServer_Storage_Json.SearchColumn";

/// The derived resource -> Patient edge (spec 235, research R2).
Parameter COMPARTMENTPRED = "in_patient_compartment";

/// The interpretation rules' version; fhir_graphs.interp_version below it is stale.
Parameter INTERPVERSION = 1;

/// How many patient-less keys a coverage report lists per type.
Parameter SAMPLEMAX = 20;

/// The category systems whose codes also become node labels (spec 235, research R8).
Parameter CATEGORYSYSTEMS = "[""http://terminology.hl7.org/CodeSystem/observation-category"",""http://terminology.hl7.org/CodeSystem/condition-category"",""http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category"",""http://hl7.org/fhir/us/core/CodeSystem/careplan-category"",""http://hl7.org/fhir/us/core/CodeSystem/condition-category"",""http://hl7.org/fhir/us/core/CodeSystem/us-core-category""]";

/// Registry row, and the index subscript derived from it (ADR-0003).
Property GraphId As %String(MAXLEN = 256);

Property GKey As %String(MAXLEN = 256);

Property RepoId As %String;

Property RSchema As %String;

Property SSchema As %String;

Property VSchema As %String;

Property WmRsrc As %Integer;

Property WmVer As %Integer;

Property IntervalS As %Integer;

Property TaskId As %String;

/// Endpoints(path) for every endpoint of the repository: a reference containing one
/// of them followed by "/" is local even when it is absolute.
Property Endpoints As %String [ MultiDimensional ];

/// Deny("Type.param") for the params the graph leaves out.
Property Deny As %String [ MultiDimensional ];

/// Cols(ResourceType, ParamName) = $lb(table, column, isMulti, definition, dataType,
/// isSplit). dataType is "reference" or "canonical"; isSplit marks a column whose
/// expression unions in <Type>.library (research R4).
Property Cols As %String [ MultiDimensional ];

/// DefTypes(ResourceType) = $lb(table, urlColumn, versionColumn) for the types that
/// declare a canonical url and version: the definitions a canonical resolves to.
Property DefTypes As %String [ MultiDimensional ];

/// Prepared statements, keyed by SQL text, for the life of one call.
Property Stmts As %String [ MultiDimensional ];

/// Live(key) = 1 live, -1 deleted, 0 absent. For the life of one call: Rsrc is the
/// authority, and a resource changing mid-call is the next sync's business.
Property Live As %String [ MultiDimensional ];

/// Nodes known to exist in this graph during this call, to skip the existence probe.
Property Known As %String [ MultiDimensional ];

/// Keys resynced in the current batch.
Property Done As %String [ MultiDimensional ];

/// Routed embedding tables (kg_emb_ / kg_eemb_), collected once per call.
Property Routes As %String [ MultiDimensional ];

Property RoutesLoaded As %Boolean [ InitialExpression = 0 ];

/// json_links as stored; "" (NULL) means the default list (research R5).
Property LinksRaw As %String(MAXLEN = "");

/// The graph's json links in ParseLinks form: Links = count, Links(n) = $lb(entry,
/// kind, typeOrUrl, predicate). Loaded by LoadColumns.
Property Links As %String [ MultiDimensional ];

/// LinkTypes(ResourceType) for the types an element entry names.
Property LinkTypes As %String [ MultiDimensional ];

/// LibLink(ResourceType) = entry while <Type>.library is a json link and not denied:
/// the library split applies to that type's split columns (research R4).
Property LibLink As %String [ MultiDimensional ];

/// Some json link is an extension: entry, so every resource body is read.
Property HasExt As %Boolean [ InitialExpression = 0 ];

/// SplitTypes(ResourceType) for the types with a split column, denied or not: the
/// default list is data-driven from them.
Property SplitTypes As %String [ MultiDimensional ];

/// Sources that lost a json-link edge when its target was dropped. RunBatch resyncs
/// them, so the source records the reason the rule gives (a canonical reason or
/// `deleted`), not DropNode.
Property Pending As %String [ MultiDimensional ];

/// Comp(ResourceType, param) for the patient-compartment params of each type, from
/// the repository's own metadata set (spec 235, research R1). R4Types(type) for
/// every resource type the schema knows.
Property Comp As %String [ MultiDimensional ];

Property R4Types As %String [ MultiDimensional ];

/// Props(ResourceType, prop) = $lb(table, column, multi) for the node properties read
/// from the search index: "category" (a token) and "meta_profile" (Resource._profile).
Property Props As %String [ MultiDimensional ];

/// CatSys(system) for the CATEGORYSYSTEMS allowlist.
Property CatSys As %String [ MultiDimensional ];

/// Interp("added") and Interp("removed"): in_patient_compartment edges inserted and
/// deleted by this context's resyncs; Interp("category") and Interp("meta_profile"):
/// property rows written, changed or deleted (FR-025).
Property Interp As %Integer [ MultiDimensional ];

/// The Rederive batch being run, from 1; 0 outside Rederive. The fault hook reads it.
Property RederiveN As %Integer [ InitialExpression = 0 ];

// ===================================================================== registry

/// Register the namespace's FHIR repository as a graph, or update its settings.
///
/// pEndpoint picks the repository when the namespace has more than one; it is the
/// endpoint path, e.g. /csp/healthshare/ivgfhir/fhir/r4. pDenylistJson is a JSON
/// array of "Type.param" strings. Re-registering keeps the watermarks, so a changed
/// denylist takes effect at the next Rebuild.
///
/// pJsonLinksJson is a JSON array of json link entries, or "" for the default list
/// (research R5). A bad list is an error that changes nothing. A changed list on a
/// graph that already exists rebuilds it here; if that rebuild is busy or fails, the
/// list is still stored and last_error says a rebuild is pending.
ClassMethod Register(pEndpoint As %String = "", pDenylistJson As %String = "[]", pIntervalS As %Integer = 60, pJsonLinksJson As %String = "") As %String
{
	Set tOut = ""
	Try {
		If pJsonLinksJson '= "" {
			Set tErr = ..ParseLinks(pJsonLinksJson, .tNewLinks)
			If tErr '= "" {
				Set tOut = {"status": "error", "error": (tErr)}.%ToJSON()
				Quit
			}
		}
		Set tCtx = ..%New()
		If 'tCtx.TableExists("HS_FHIRServer", "Repo") {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "namespace " _ $NAMESPACE _ " has no FHIR repository (HS_FHIRServer.Repo not found)"))
		}
		If 'tCtx.TableExists("Graph_KG", "fhir_graphs") {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "Graph_KG.fhir_graphs not found; register through engine.fhir_graph_register(), which creates it, in " _ $NAMESPACE))
		}
		If (+pIntervalS < 60) {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "interval must be at least 60 seconds (Task Manager granularity is one minute)"))
		}
		Set tDeny = ..ParseDenylist(pDenylistJson)

		// --- which repository ------------------------------------------------
		Set tRepo = ""
		If pEndpoint '= "" {
			Set tPath = pEndpoint
			If $Extract(tPath, *) = "/" { Set tPath = $Extract(tPath, 1, *-1) }
			Set tRs = tCtx.Query("SELECT repo FROM HS_FHIRServer.RepoInstance WHERE cspUrl = ? OR cspUrl = ?", tPath, tPath _ "/")
			If tRs.%Next() { Set tRepo = tRs.%GetData(1) }
			If tRepo = "" {
				$$$ThrowStatus($$$ERROR($$$GeneralError, "no FHIR endpoint " _ pEndpoint _ " in " _ $NAMESPACE))
			}
		} Else {
			Set tRs = tCtx.Query("SELECT ID FROM HS_FHIRServer.Repo")
			Set tN = 0
			While tRs.%Next() {
				Set tN = tN + 1
				Set tRepo = tRs.%GetData(1)
			}
			If tN = 0 {
				$$$ThrowStatus($$$ERROR($$$GeneralError, "namespace " _ $NAMESPACE _ " has no FHIR repository"))
			}
			If tN > 1 {
				$$$ThrowStatus($$$ERROR($$$GeneralError, "namespace " _ $NAMESPACE _ " has " _ tN _ " FHIR repositories; pass endpoint= to choose one"))
			}
		}

		Set tRs = tCtx.Query("SELECT resourceClassesPackage, searchClassesPackage, versionClassesPackage, strategyKey FROM HS_FHIRServer.Repo WHERE ID = ?", tRepo)
		If 'tRs.%Next() {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "FHIR repository " _ tRepo _ " not found"))
		}
		Set tRPkg = tRs.%GetData(1)
		Set tSPkg = tRs.%GetData(2)
		Set tVPkg = tRs.%GetData(3)
		Set tStrategy = tRs.%GetData(4)
		If tStrategy '= "JsonAdvSQL" {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "FHIR repository " _ tRepo _ " uses storage strategy '" _ tStrategy _ "'; only JsonAdvSQL is supported"))
		}

		Set tGraph = "fhir:" _ $NAMESPACE _ ":" _ $Piece(tRPkg, ".", 2)
		$$$ThrowOnError(##class(Graph.KG.GraphKey).Validate(tGraph))

		Set tEndpoints = []
		Set tRs = tCtx.Query("SELECT cspUrl FROM HS_FHIRServer.RepoInstance WHERE repo = ?", tRepo)
		While tRs.%Next() {
			Do tEndpoints.%Push(tRs.%GetData(1))
		}

		// --- upsert, keeping the watermarks ------------------------------------
		Set tRs = tCtx.Query("SELECT COUNT(*) FROM Graph_KG.fhir_graphs WHERE graph_id = ?", tGraph)
		Do tRs.%Next()
		Set tCreated = (tRs.%GetData(1) = 0)
		If tCreated {
			Do tCtx.Exec("INSERT INTO Graph_KG.fhir_graphs (graph_id, repo_id, rsrc_schema, search_schema, ver_schema, endpoints, denylist, interval_s, wm_rsrc, wm_ver) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)", tGraph, tRepo, $Translate(tRPkg, ".", "_"), $Translate(tSPkg, ".", "_"), $Translate(tVPkg, ".", "_"), tEndpoints.%ToJSON(), tDeny.%ToJSON(), +pIntervalS)
		} Else {
			Do tCtx.Exec("UPDATE Graph_KG.fhir_graphs SET repo_id = ?, rsrc_schema = ?, search_schema = ?, ver_schema = ?, endpoints = ?, denylist = ?, interval_s = ? WHERE graph_id = ?", tRepo, $Translate(tRPkg, ".", "_"), $Translate(tSPkg, ".", "_"), $Translate(tVPkg, ".", "_"), tEndpoints.%ToJSON(), tDeny.%ToJSON(), +pIntervalS, tGraph)
		}

		// --- json links: resolve, store, and rebuild on a change ---------------
		Set tLinkCtx = ..Open(tGraph)
		Do tLinkCtx.LoadColumns()
		Set tOldJson = tLinkCtx.LinksJson()
		If pJsonLinksJson = "" {
			Set tNewJson = tLinkCtx.DefaultLinksJson()
		} Else {
			Set tNew = []
			For tI = 1:1:tNewLinks {
				Do tNew.%Push($ListGet(tNewLinks(tI), 1))
			}
			Set tNewJson = tNew.%ToJSON()
		}
		Do tCtx.Exec("UPDATE Graph_KG.fhir_graphs SET json_links = ? WHERE graph_id = ?", tNewJson, tGraph)

		Set tRes = {"status": "registered", "graph_id": (tGraph), "repo_id": (tRepo), "endpoints": (tEndpoints), "denylist": (tDeny), "interval_s": (+pIntervalS)}
		Do tRes.%Set("created", tCreated, "boolean")
		Do tRes.%Set("json_links", ##class(%DynamicArray).%FromJSON(tNewJson))
		If 'tCreated && (tNewJson '= tOldJson) {
			Set tRebuild = ##class(%DynamicObject).%FromJSON(..Rebuild(tGraph))
			Set tOk = (tRebuild.status = "ok")
			Do tRes.%Set("rebuilt", tOk, "boolean")
			Do tRes.%Set("rebuild", tRebuild)
			If 'tOk {
				Do tCtx.Exec("UPDATE Graph_KG.fhir_graphs SET last_error = ? WHERE graph_id = ?", "json_links changed; rebuild pending", tGraph)
			}
		}
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// {"type": pType, "params": [...]}: pType's params in pCompartment, sorted; [] for
/// a type or compartment with none.
ClassMethod CompartmentParams(pGraph As %String, pType As %String, pCompartment As %String = "patient") As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Do tCtx.LoadCompartment(, pCompartment)
		Set tParams = []
		Set tParam = ""
		For {
			Set tParam = $Order(tCtx.Comp(pType, tParam))
			Quit:tParam=""
			Do tParams.%Push(tParam)
		}
		Set tOut = {"status": "ok", "type": (pType), "params": (tParams)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// The registry row and live counts for one graph.
ClassMethod Status(pGraph As %String) As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Set tRs = tCtx.Query("SELECT interval_s, task_id, last_sync, last_rebuild, last_error, last_counts, endpoints, denylist FROM Graph_KG.fhir_graphs WHERE graph_id = ?", pGraph)
		Do tRs.%Next()
		Set tRes = {"status": "ok", "graph_id": (pGraph), "repo_id": (tCtx.RepoId)}
		Do tRes.%Set("interval_s", +tRs.%GetData(1), "number")
		Do tRes.%Set("task_id", tRs.%GetData(2))
		Do tRes.%Set("last_sync", tRs.%GetData(3))
		Do tRes.%Set("last_rebuild", tRs.%GetData(4))
		Do tRes.%Set("last_error", tRs.%GetData(5))
		Set tCounts = tRs.%GetData(6)
		Do tRes.%Set("last_counts", $Select(tCounts = "": "", 1: ##class(%DynamicAbstractObject).%FromJSON(tCounts)))
		Do tRes.%Set("endpoints", ##class(%DynamicAbstractObject).%FromJSON(tRs.%GetData(7)))
		Do tRes.%Set("denylist", ##class(%DynamicAbstractObject).%FromJSON(tRs.%GetData(8)))
		Do tRes.%Set("wm_rsrc", tCtx.WmRsrc, "number")
		Do tRes.%Set("wm_ver", tCtx.WmVer, "number")
		Do tRes.%Set("nodes", tCtx.Scalar("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", pGraph), "number")
		// Reference edges only; the derived compartment edges are counted apart (FR-026).
		Do tRes.%Set("edges", tCtx.Scalar("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p <> ?", pGraph, ..#COMPARTMENTPRED), "number")
		Do tRes.%Set("compartment_edges", tCtx.Scalar("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = ?", pGraph, ..#COMPARTMENTPRED), "number")
		Set tVer = tCtx.InterpVersion()
		Do tRes.%Set("interpretation_version", $Select(tVer = "": "", 1: +tVer), $Select(tVer = "": "null", 1: "number"))
		Do tRes.%Set("interpretation_current", ..#INTERPVERSION, "number")
		Do tRes.%Set("interpretation_stale", tCtx.InterpStale(), "boolean")
		Do tRes.%Set("unresolved", tCtx.UnresolvedCounts())
		Do tRes.%Set("canonical", tCtx.Scalar("SELECT COUNT(*) FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ? AND kind = 'canonical'", pGraph), "number")
		// Matches per json link, every configured entry present (FR-016).
		Do tCtx.LoadColumns()
		Kill tByLink
		Set tRs = tCtx.Query("SELECT origin, COUNT(*) FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ? AND origin <> 'index' GROUP BY origin", pGraph)
		While tRs.%Next() { Set tByLink(tRs.%GetData(1)) = +tRs.%GetData(2) }
		Set tLinks = {}
		For tI = 1:1:tCtx.Links {
			Set tEntry = $ListGet(tCtx.Links(tI), 1)
			Do tLinks.%Set(tEntry, +$Get(tByLink(tEntry)), "number")
		}
		Do tRes.%Set("json_links", tLinks)
		// What a sync would pick up now: rows past each watermark.
		Set tPending = +tCtx.Scalar("SELECT COUNT(*) FROM " _ tCtx.RTable() _ " WHERE ID > ?", tCtx.WmRsrc)
		Set tPending = tPending + tCtx.Scalar("SELECT COUNT(*) FROM " _ tCtx.VTable() _ " WHERE ID > ?", tCtx.WmVer)
		Do tRes.%Set("pending", tPending, "number")
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// Every canonical and json-link reference the graph's sources carry, or pSource's,
/// with its status: the target key when it resolves, otherwise the reason (FR-018).
/// Read-only and unlocked; each status is recomputed by the rules the sync uses, so
/// the report describes the graph as of the last sync.
ClassMethod LinkReport(pGraph As %String, pSource As %String = "") As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Do tCtx.LoadColumns()
		Set tSql = "SELECT source, param, url, version, origin, kind FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ?"
		If pSource = "" {
			Set tRs = tCtx.Query(tSql _ " ORDER BY source, param, url, version", tCtx.GraphId)
		} Else {
			Set tRs = tCtx.Query(tSql _ " AND source = ? ORDER BY param, url, version", tCtx.GraphId, pSource)
		}
		Set tRows = []
		Kill tCands, tByParam, tByLink
		While tRs.%Next() {
			Set tUrl = tRs.%GetData(3)
			Set tVer = tRs.%GetData(4)
			Set tOrigin = tRs.%GetData(5)
			Set tKind = tRs.%GetData(6)
			If tKind = "reference" {
				Set tOutcome = tCtx.Classify(tUrl, tCtx.BodyRel(tUrl), .tTarget)
				If tOutcome = "local" {
					Set tLive = tCtx.Liveness(tTarget)
					Set tOutcome = $Select(tLive = 1: "resolved", tLive = -1: "deleted", 1: "missing")
				}
			} Else {
				If '$Data(tCands(tUrl)) {
					Set tCands(tUrl) = ""
					Set tRs2 = tCtx.Query("SELECT rsrc_key, version FROM Graph_KG.fhir_definitions WHERE graph_id = ? AND url = ?", tCtx.GraphId, tUrl)
					While tRs2.%Next() { Set tCands(tUrl, tRs2.%GetData(1)) = tRs2.%GetData(2) }
				}
				Kill tCand
				Merge tCand = tCands(tUrl)
				Set tOutcome = ..ResolveCands(tVer, .tCand, .tTarget)
			}
			Set tRow = {"source": (tRs.%GetData(1)), "param": (tRs.%GetData(2)), "url": (tUrl)}
			If tVer = "" {
				Do tRow.%Set("version", "", "null")
			} Else {
				Do tRow.%Set("version", tVer)
			}
			Do tRow.%Set("origin", tOrigin)
			Do tRow.%Set("kind", tKind)
			Do tRow.%Set("status", $Select(tOutcome = "resolved": tTarget, 1: tOutcome))
			Do tRows.%Push(tRow)
			Set tByParam(tRs.%GetData(2), tOutcome) = $Get(tByParam(tRs.%GetData(2), tOutcome)) + 1
			Set:tOrigin'="index" tByLink(tOrigin) = $Get(tByLink(tOrigin)) + 1
		}
		Set tParams = {}
		Set tP = ""
		For {
			Set tP = $Order(tByParam(tP))
			Quit:tP=""
			Set tOne = {}
			Set tO = ""
			For {
				Set tO = $Order(tByParam(tP, tO), 1, tN)
				Quit:tO=""
				Do tOne.%Set(tO, tN, "number")
			}
			Do tParams.%Set(tP, tOne)
		}
		// Every configured entry, 0 included, as Status.json_links.
		Set tLinks = {}
		For tI = 1:1:tCtx.Links {
			Set tEntry = $ListGet(tCtx.Links(tI), 1)
			Do tLinks.%Set(tEntry, +$Get(tByLink(tEntry)), "number")
		}
		Set tRes = {"graph": (pGraph)}
		If pSource = "" {
			Do tRes.%Set("source", "", "null")
		} Else {
			Do tRes.%Set("source", pSource)
		}
		Do tRes.%Set("rows", tRows)
		Do tRes.%Set("totals", {"by_param": (tParams), "by_link": (tLinks)})
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

// ===================================================================== build/sync

/// Reconcile the graph to the repository's current state.
///
/// Diffs rather than erases, so a surviving node keeps its vectors. Both watermarks
/// are set to the maxima read BEFORE the scan: a change landing during the scan is
/// then picked up by the next sync, never lost.
ClassMethod Rebuild(pGraph As %String) As %String
{
	Set tOut = ""
	Set tLocked = 0
	Set tLevel = $TLevel
	Try {
		Set tCtx = ..Open(pGraph)
		Lock +^IVG.FHIRGraph(pGraph):0
		If '$Test {
			Set tOut = {"status": "busy", "graph_id": (pGraph)}.%ToJSON()
			Quit
		}
		Set tLocked = 1
		Set tStart = $ZHorolog
		Do tCtx.LoadColumns()
		Set tMaxR = +tCtx.Scalar("SELECT MAX(ID) FROM " _ tCtx.RTable())
		Set tMaxV = +tCtx.Scalar("SELECT MAX(ID) FROM " _ tCtx.VTable())

		// Pass 1 over everything first, so every source resolves against the whole set.
		Do tCtx.SyncAllDefinitions(tMaxR)

		// Every live key. A deleted one is skipped: FHIR deletes are soft, so the table
		// keeps every one ever made, and resyncing them cost the history, not the data
		// (282k of 306k rows on the 4.1.1 test repo). One the graph still holds is
		// dropped by the stale pass below.
		Set tKeys = 0
		Kill tBatch
		Set tN = 0
		Set tRs = tCtx.Query("SELECT Key, Deleted FROM " _ tCtx.RTable() _ " WHERE ID <= ?", tMaxR)
		While tRs.%Next() {
			If +tRs.%GetData(2) {
				Set tCtx.Live(tRs.%GetData(1)) = -1
				Continue
			}
			Set tBatch($Increment(tN)) = tRs.%GetData(1)
			If tN >= ..#BATCHSIZE {
				Set tKeys = tKeys + tCtx.RunBatch(.tBatch, 0, "", "", 0)
				Kill tBatch
				Set tN = 0
			}
		}
		If tN > 0 {
			Set tKeys = tKeys + tCtx.RunBatch(.tBatch, 0, "", "", 0)
			Kill tBatch
		}

		// Nodes of keys deleted or gone (a purge, or a graph built against another
		// state), and unresolved rows whose source is. Collected first: DropNode deletes
		// from the tables being read.
		Kill tStale
		Set tRs = tCtx.Query("SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?", pGraph)
		While tRs.%Next() {
			Set tK = tRs.%GetData(1)
			If tCtx.Liveness(tK) '= 1 { Set tStale(tK) = "" }
		}
		Set tRs = tCtx.Query("SELECT source FROM Graph_KG.fhir_unresolved WHERE graph_id = ?", pGraph)
		While tRs.%Next() {
			Set tK = tRs.%GetData(1)
			If tCtx.Liveness(tK) '= 1 { Set tStale(tK) = "" }
		}
		Set tDropped = 0
		Set tK = ""
		For {
			Set tK = $Order(tStale(tK))
			Quit:tK=""
			TSTART
			Do tCtx.DropNode(tK, $Select(tCtx.Liveness(tK) = -1: "deleted", 1: "missing"))
			TCOMMIT
			Set tDropped = tDropped + 1
		}

		Set tCounts = tCtx.Counts()
		Do tCtx.Exec("UPDATE Graph_KG.fhir_graphs SET wm_rsrc = ?, wm_ver = ?, last_rebuild = CURRENT_TIMESTAMP, last_error = NULL, last_counts = ? WHERE graph_id = ?", tMaxR, tMaxV, tCounts.%ToJSON(), pGraph)
		// Every key was just resynced under this version's rules.
		Do tCtx.SetInterpVersion()

		Set tRes = {"status": "ok", "graph_id": (pGraph)}
		Do tRes.%Set("keys", tKeys, "number")
		Do tRes.%Set("stale_dropped", tDropped, "number")
		Do tRes.%Set("wm_rsrc", tMaxR, "number")
		Do tRes.%Set("wm_ver", tMaxV, "number")
		Do tRes.%Set("elapsed_ms", $Justify(($ZHorolog - tStart) * 1000, 0, 0), "number")
		Do tRes.%Set("counts", tCounts)
		Do tRes.%Set("interpretation", tCtx.InterpBlock(1))
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		While $TLevel > tLevel { TROLLBACK 1 }
		Set tOut = ..Err(tEx)
		Do ..RecordError(pGraph, tOut)
	}
	If tLocked { Lock -^IVG.FHIRGraph(pGraph) }
	Return tOut
}

/// Apply every change since the watermarks, one transaction per batch.
ClassMethod SyncOnce(pGraph As %String) As %String
{
	Set tOut = ""
	Set tLocked = 0
	Set tLevel = $TLevel
	Try {
		Set tCtx = ..Open(pGraph)
		Lock +^IVG.FHIRGraph(pGraph):0
		If '$Test {
			Set tOut = {"status": "busy", "graph_id": (pGraph)}.%ToJSON()
			Quit
		}
		Set tLocked = 1
		Set tStart = $ZHorolog
		Do tCtx.LoadColumns()
		Set tMaxR = +tCtx.Scalar("SELECT MAX(ID) FROM " _ tCtx.RTable())
		Set tMaxV = +tCtx.Scalar("SELECT MAX(ID) FROM " _ tCtx.VTable())

		// A repository behind the watermarks (emptied by Installer.Reset, or restored
		// from an older backup) has no rows above them, so the feeds would apply
		// nothing and the graph would keep what is gone. Rebuild instead; it takes
		// the same lock.
		If (tMaxR < tCtx.WmRsrc) || (tMaxV < tCtx.WmVer) {
			Lock -^IVG.FHIRGraph(pGraph)
			Set tLocked = 0
			Set tRes = {}.%FromJSON(..Rebuild(pGraph))
			Do:tRes.status="ok" tRes.%Set("rebuilt", "repository_behind_watermark")
			Set tOut = tRes.%ToJSON()
			Quit
		}

		// A graph last interpreted by older rules (NULL: synced before spec 235, or erased) is re-derived
		// in full first, then marked. An interrupted pass leaves the marker, so the next
		// sync starts over (spec 235, FR-024).
		Set tFull = tCtx.InterpStale()
		If tFull {
			Do tCtx.Rederive()
			Do tCtx.SetInterpVersion()
		}

		Set tKeys = 0
		Set tBatches = 0
		For tFeed = "wm_rsrc", "wm_ver" {
			Set tTable = $Select(tFeed = "wm_rsrc": tCtx.RTable(), 1: tCtx.VTable())
			Set tFrom = $Select(tFeed = "wm_rsrc": tCtx.WmRsrc, 1: tCtx.WmVer)
			Set tTo = $Select(tFeed = "wm_rsrc": tMaxR, 1: tMaxV)
			Continue:tTo<=tFrom
			Kill tBatch
			Set tN = 0
			Set tLast = tFrom
			Set tRs = tCtx.Query("SELECT ID, Key FROM " _ tTable _ " WHERE ID > ? AND ID <= ? ORDER BY ID", tFrom, tTo)
			While tRs.%Next() {
				Set tBatch($Increment(tN)) = tRs.%GetData(2)
				Set tLast = tRs.%GetData(1)
				If tN >= ..#BATCHSIZE {
					Set tKeys = tKeys + tCtx.RunBatch(.tBatch, 1, tFeed, tLast)
					Set tBatches = tBatches + 1
					Kill tBatch
					Set tN = 0
				}
			}
			// The tail batch also carries the watermark to tTo, past any IDs the scan
			// saw no row for.
			Set tKeys = tKeys + tCtx.RunBatch(.tBatch, 1, tFeed, tTo)
			Set tBatches = tBatches + 1
			Kill tBatch
		}

		Do tCtx.Exec("UPDATE Graph_KG.fhir_graphs SET last_sync = CURRENT_TIMESTAMP, last_error = NULL WHERE graph_id = ?", pGraph)
		Set tRes = {"status": "ok", "graph_id": (pGraph)}
		Do tRes.%Set("keys", tKeys, "number")
		Do tRes.%Set("batches", tBatches, "number")
		Do tRes.%Set("wm_rsrc", tMaxR, "number")
		Do tRes.%Set("wm_ver", tMaxV, "number")
		Do tRes.%Set("elapsed_ms", $Justify(($ZHorolog - tStart) * 1000, 0, 0), "number")
		Do tRes.%Set("interpretation", tCtx.InterpBlock(tFull))
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		While $TLevel > tLevel { TROLLBACK 1 }
		Set tOut = ..Err(tEx)
		Do ..RecordError(pGraph, tOut)
	}
	If tLocked { Lock -^IVG.FHIRGraph(pGraph) }
	Return tOut
}

/// Re-derive the interpretation of every live resource now and set interp_version
/// (spec 235). Watermarks are untouched: changes past them wait for the next sync.
ClassMethod Reinterpret(pGraph As %String) As %String
{
	Set tOut = ""
	Set tLocked = 0
	Set tLevel = $TLevel
	Try {
		Set tCtx = ..Open(pGraph)
		Lock +^IVG.FHIRGraph(pGraph):0
		If '$Test {
			Set tOut = {"status": "busy", "graph_id": (pGraph)}.%ToJSON()
			Quit
		}
		Set tLocked = 1
		Set tStart = $ZHorolog
		Do tCtx.LoadColumns()
		Set tKeys = tCtx.Rederive()
		Do tCtx.SetInterpVersion()
		Set tRes = {"status": "ok", "graph": (pGraph)}
		Do tRes.%Set("resources", tKeys, "number")
		Do tRes.%Set("elapsed_ms", $Justify(($ZHorolog - tStart) * 1000, 0, 0), "number")
		Do tRes.%Set("interpretation", tCtx.InterpBlock(1))
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		While $TLevel > tLevel { TROLLBACK 1 }
		Set tOut = ..Err(tEx)
		Do ..RecordError(pGraph, tOut)
	}
	If tLocked { Lock -^IVG.FHIRGraph(pGraph) }
	Return tOut
}

// ===================================================================== scheduling

/// Create or update the graph's Task Manager task. pIntervalS = 0 keeps the
/// registered interval. Task Manager counts in minutes, so the interval is rounded
/// up to whole minutes.
ClassMethod Schedule(pGraph As %String, pIntervalS As %Integer = 0) As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Set tInterval = $Select(+pIntervalS > 0: +pIntervalS, 1: tCtx.IntervalS)
		If tInterval < 60 {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "interval must be at least 60 seconds (Task Manager granularity is one minute)"))
		}
		Set tMinutes = tInterval \ 60 + (tInterval # 60 > 0)
		If tMinutes < 1 { Set tMinutes = 1 }

		Set tTask = ""
		If (tCtx.TaskId '= "") && ##class(%SYS.Task).%ExistsId(tCtx.TaskId) {
			Set tTask = ##class(%SYS.Task).%OpenId(tCtx.TaskId)
		}
		If '$IsObject(tTask) {
			Set tTask = ##class(%SYS.Task).%New()
		}
		Set tTask.Name = $Extract("IVG FHIR sync " _ pGraph, 1, 50)
		Set tTask.Description = "Spec 231: Graph.KG.FHIRGraph.SyncOnce(""" _ pGraph _ """)"
		Set tTask.NameSpace = $NAMESPACE
		Set tTask.TaskClass = "Graph.KG.FHIRGraphSyncTask"
		Set tTask.TimePeriod = 0
		Set tTask.DailyFrequency = 1
		Set tTask.DailyFrequencyTime = 0
		Set tTask.DailyIncrement = tMinutes
		Set tTask.DailyStartTime = 0
		Set tTask.DailyEndTime = 86399
		Set tDef = ##class(Graph.KG.FHIRGraphSyncTask).%New()
		Set tDef.GraphId = pGraph
		$$$ThrowOnError(tTask.AssignSettings(tDef))
		$$$ThrowOnError(tTask.%Save())
		Set tTaskId = tTask.%Id()
		Do tCtx.Exec("UPDATE Graph_KG.fhir_graphs SET task_id = ?, interval_s = ? WHERE graph_id = ?", tTaskId, tInterval, pGraph)

		Set tRes = {"status": "scheduled", "graph_id": (pGraph), "task_id": (tTaskId)}
		Do tRes.%Set("interval_s", tInterval, "number")
		Do tRes.%Set("minutes", tMinutes, "number")
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// Delete the graph's Task Manager task, if it has one.
ClassMethod Unschedule(pGraph As %String) As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Set tRemoved = 0
		If (tCtx.TaskId '= "") && ##class(%SYS.Task).%ExistsId(tCtx.TaskId) {
			$$$ThrowOnError(##class(%SYS.Task).%DeleteId(tCtx.TaskId))
			Set tRemoved = 1
		}
		Do tCtx.Exec("UPDATE Graph_KG.fhir_graphs SET task_id = NULL WHERE graph_id = ?", pGraph)
		Set tRes = {"status": "unscheduled", "graph_id": (pGraph), "task_id": (tCtx.TaskId)}
		Do tRes.%Set("removed", tRemoved, "boolean")
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

// ===================================================================== concepts

/// The keys of live resources whose token param matches a code crosswalked to one of
/// the given concept nodes.
///
/// pIdsJson: concept node ids in pConceptGraph. pParamsJson: token params to match
/// (default ["code"]). pRelationsJson: crosswalk relations to follow (default all four).
/// Codes are read live from the repository's token tables; nothing is copied.
ClassMethod ResolveConcepts(pGraph As %String, pConceptGraph As %String = "", pIdsJson As %String = "[]", pParamsJson As %String = "", pRelationsJson As %String = "") As %String
{
	Set tOut = ""
	Try {
		Set tStart = $ZHorolog
		Set tCtx = ..Open(pGraph)
		Set tConceptName = ##class(Graph.KG.GraphKey).ForName(pConceptGraph)
		Set tIds = ##class(%DynamicAbstractObject).%FromJSON(pIdsJson)
		Set tParams = $Select(pParamsJson = "": ["code"], 1: ##class(%DynamicAbstractObject).%FromJSON(pParamsJson))
		Set tRels = $Select(pRelationsJson = "": ["exact", "broader", "narrower", "related"], 1: ##class(%DynamicAbstractObject).%FromJSON(pRelationsJson))
		Kill tRelOk
		Set tIt = tRels.%GetIterator()
		While tIt.%GetNext(, .tR) { Set tRelOk(tR) = "" }

		// (system, code) pairs for the concepts.
		Kill tCodes
		Set tNCodes = 0
		// A "" bind arrives as NULL and matches nothing, so the default graph is a literal.
		Set tXwSql = "SELECT code_system_uri, code, relation FROM Graph_KG.code_crosswalk WHERE target_node_id = ? AND target_graph = " _ $Select(tConceptName = "": "''", 1: "?")
		Set tIt = tIds.%GetIterator()
		While tIt.%GetNext(, .tId) {
			If tConceptName = "" {
				Set tRs = tCtx.Query(tXwSql, tId)
			} Else {
				Set tRs = tCtx.Query(tXwSql, tId, tConceptName)
			}
			While tRs.%Next() {
				Continue:'$Data(tRelOk(tRs.%GetData(3)))
				Set tSys = tRs.%GetData(1)
				Set tCode = tRs.%GetData(2)
				If '$Data(tCodes(tSys, tCode)) {
					Set tCodes(tSys, tCode) = ""
					Set tNCodes = tNCodes + 1
				}
			}
		}

		// Token columns for the params, restricted to the resource types present.
		Kill tTypes
		Set tRs = tCtx.Query("SELECT DISTINCT ResourceType FROM " _ tCtx.RTable() _ " WHERE Deleted = 0")
		While tRs.%Next() { Set tTypes(tRs.%GetData(1)) = "" }
		Kill tTok
		Set tIt = tParams.%GetIterator()
		While tIt.%GetNext(, .tParam) {
			Set tRs = tCtx.Query("SELECT ResourceType, TableName, ColumnName, isMultiValue FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND %UPPER(Type) = 'TOKEN' AND ParamName = ?", tCtx.RepoId, tParam)
			While tRs.%Next() {
				Set tType = tRs.%GetData(1)
				Continue:'$Data(tTypes(tType))
				Set tTok($Increment(tTok)) = $ListBuild(tRs.%GetData(2), tRs.%GetData(3), +tRs.%GetData(4))
			}
		}

		Set tKeys = []
		Kill tSeen
		For tI = 1:1:$Get(tTok) {
			Set tTable = $ListGet(tTok(tI), 1)
			Set tCol = $ListGet(tTok(tI), 2)
			If $ListGet(tTok(tI), 3) {
				Set tSql = "SELECT Key FROM " _ ..Q(tCtx.SSchema _ "_" _ tTable) _ "." _ ..Q(tCol) _ " WHERE value_System = ? AND value_Value = ?"
			} Else {
				Set tSql = "SELECT Key FROM " _ ..Q(tCtx.SSchema) _ "." _ ..Q(tTable) _ " WHERE " _ ..Q(tCol _ "_System") _ " = ? AND " _ ..Q(tCol _ "_Value") _ " = ?"
			}
			Set tSys = ""
			For {
				Set tSys = $Order(tCodes(tSys))
				Quit:tSys=""
				Set tCode = ""
				For {
					Set tCode = $Order(tCodes(tSys, tCode))
					Quit:tCode=""
					Set tRs = tCtx.Query(tSql, tSys, tCode)
					While tRs.%Next() {
						Set tK = tRs.%GetData(1)
						Continue:$Data(tSeen(tK))
						Set tSeen(tK) = ""
						// A delete removes the search rows (spec fact 5); checking Rsrc as
						// well costs one indexed probe and does not depend on that.
						Continue:tCtx.Liveness(tK)'=1
						Do tKeys.%Push(tK)
					}
				}
			}
		}

		// Medication hop (spec 235, R10): a resolved Medication adds the four
		// medication-use types that reference it through `medication`.
		Set tVia = {}
		Set tNMed = 0
		Set tAdded = 0
		Set tN = tKeys.%Size()
		For tI = 0:1:tN-1 {
			Set tM = tKeys.%Get(tI)
			Continue:$Piece(tM, "/")'="Medication"
			Set tNMed = tNMed + 1
			Set tSrc = ""
			For {
				Set tSrc = $Order(^KG("in", tCtx.GKey, tM, "medication", tSrc))
				Quit:tSrc=""
				Continue:'$ListFind($ListBuild("MedicationRequest", "MedicationStatement", "MedicationAdministration", "MedicationDispense"), $Piece(tSrc, "/"))
				Continue:$Data(tSeen(tSrc))
				Set tSeen(tSrc) = ""
				Continue:tCtx.Liveness(tSrc)'=1
				Do tKeys.%Push(tSrc)
				Do tVia.%Set(tSrc, "medication")
				Set tAdded = tAdded + 1
			}
		}
		Set tHop = {}
		Do tHop.%Set("medications", tNMed, "number")
		Do tHop.%Set("added", tAdded, "number")

		Set tRes = {"status": "ok", "graph_id": (pGraph), "keys": (tKeys), "via": (tVia), "medication_hop": (tHop)}
		Do tRes.%Set("codes", tNCodes, "number")
		Do tRes.%Set("elapsed_ms", $Justify(($ZHorolog - tStart) * 1000, 0, 1), "number")
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// Which of the token params in pParamsJson the repository indexes for a type the
/// graph holds: {"used": [...], "dropped": [...]}, each in request order, each param
/// once. A param counts as used when a TOKEN SearchColumn row exists for it on a
/// resource type with a live resource (spec 235, R9).
ClassMethod IndexedTokenParams(pGraph As %String, pParamsJson As %String = "[]") As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Set tParams = ##class(%DynamicAbstractObject).%FromJSON(pParamsJson)
		If 'tParams.%IsA("%DynamicArray") {
			Set tOut = {"status": "error", "error": "params must be a JSON array"}.%ToJSON()
			Return tOut
		}
		Kill tTypes
		Set tRs = tCtx.Query("SELECT DISTINCT ResourceType FROM " _ tCtx.RTable() _ " WHERE Deleted = 0")
		While tRs.%Next() { Set tTypes(tRs.%GetData(1)) = "" }
		Set tUsed = []
		Set tDropped = []
		Kill tDone
		Set tIt = tParams.%GetIterator()
		While tIt.%GetNext(, .tParam) {
			Continue:$Data(tDone(tParam))
			Set tDone(tParam) = ""
			Set tHit = 0
			Set tRs = tCtx.Query("SELECT ResourceType FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND %UPPER(Type) = 'TOKEN' AND ParamName = ?", tCtx.RepoId, tParam)
			While 'tHit && tRs.%Next() { Set tHit = ''$Data(tTypes(tRs.%GetData(1))) }
			If tHit { Do tUsed.%Push(tParam) } Else { Do tDropped.%Push(tParam) }
		}
		Set tOut = {"status": "ok", "graph_id": (pGraph), "used": (tUsed), "dropped": (tDropped)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// The per-type coverage, resolution and linked-patient figures of one graph (spec
/// 235, US4), without link_report, which Python adds from LinkReport. Computed live,
/// never stored. pParamsJson are the token params for by_system (Python passes the
/// indexed part of the clinical preset); pPrefix, when set, keeps only the resources
/// whose id starts with it. Patient is never a compartment type.
ClassMethod CoverageReport(pGraph As %String, pParamsJson As %String = "[""code""]", pPrefix As %String = "") As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Do tCtx.LoadCompartment()
		Do tCtx.NodeSet(pPrefix, .tNodes, .tByType)
		Kill tLess, tWith, tHist, tCat
		Set tKey = ""
		For {
			Set tKey = $Order(tNodes(tKey))
			Quit:tKey=""
			Set tType = $Piece(tKey, "/")
			Continue:(tType="Patient")||'$Data(tCtx.Comp(tType))
			If $Data(^KG("out", tCtx.GKey, tKey, ..#COMPARTMENTPRED)) {
				Set tWith(tType) = $Get(tWith(tType)) + 1
			} Else {
				Set tLess(tType, " " _ tKey) = ""
			}
		}
		// category and meta_profile as the sync stored them.
		Set tCatSys = {}
		Set tIt = ##class(%DynamicArray).%FromJSON(..#CATEGORYSYSTEMS).%GetIterator()
		While tIt.%GetNext(, .tSys) { Do tCatSys.%Set(tSys, 1) }
		Set tRs = tCtx.Query("SELECT s, ""key"", val FROM Graph_KG.rdf_props WHERE graph_id = ? AND ""key"" IN ('category', 'meta_profile')", tCtx.GraphId)
		While tRs.%Next() {
			Set tKey = tRs.%GetData(1)
			Continue:'$Data(tNodes(tKey))
			Set tType = $Piece(tKey, "/")
			Set tVals = ##class(%DynamicArray).%FromJSON(tRs.%GetData(3))
			If tRs.%GetData(2) = "meta_profile" {
				Set tIt = tVals.%GetIterator()
				While tIt.%GetNext(, .tV) { Set tHist(tType, tV) = $Get(tHist(tType, tV)) + 1 }
				Continue
			}
			Set tLabel = 0
			Set tIt = tVals.%GetIterator()
			While tIt.%GetNext(, .tV) { Set:tCatSys.%Get($Piece(tV, "|", 1)) tLabel = 1 }
			Set tCat(tType, $Select(tLabel: "label", 1: "local")) = $Get(tCat(tType, $Select(tLabel: "label", 1: "local"))) + 1
		}
		Set tTypes = {}
		Set tType = ""
		For {
			Set tType = $Order(tByType(tType), 1, tN)
			Quit:tType=""
			Set tT = {}
			Do tT.%Set("count", tN, "number")
			Set tIsComp = (tType '= "Patient") && ($Data(tCtx.Comp(tType)) > 0)
			Do tT.%Set("compartment_type", tIsComp, "boolean")
			If tIsComp {
				Set tW = +$Get(tWith(tType))
				Do tT.%Set("with_compartment", tW, "number")
				Do tT.%Set("compartment_share", $Justify(tW / tN, 0, 4), "number")
				Do tT.%Set("patientless", tN - tW, "number")
				Set tSample = []
				Set tK = ""
				For {
					Set tK = $Order(tLess(tType, tK))
					Quit:(tK="")||(tSample.%Size()>=..#SAMPLEMAX)
					Do tSample.%Push($Extract(tK, 2, *))
				}
				Do tT.%Set("patientless_sample", tSample)
			}
			Set tH = {}
			Set tP = ""
			For {
				Set tP = $Order(tHist(tType, tP), 1, tC)
				Quit:tP=""
				Do tH.%Set(tP, tC, "number")
			}
			Do tT.%Set("meta_profile", tH)
			Set tL = +$Get(tCat(tType, "label"))
			Set tLo = +$Get(tCat(tType, "local"))
			Set tC = {}
			Do tC.%Set("label", tL, "number")
			Do tC.%Set("local", tLo, "number")
			Do tC.%Set("none", tN - tL - tLo, "number")
			Do tT.%Set("category", tC)
			Do tTypes.%Set(tType, tT)
		}

		// by_system: codes and resources per system over every param's tokens.
		Kill tCodes, tRes, tRCodes, tRRes
		Set tParams = ##class(%DynamicAbstractObject).%FromJSON(pParamsJson)
		Set tIt = tParams.%GetIterator()
		While tIt.%GetNext(, .tParam) {
			Do tCtx.TokenRows(tParam, .tByType, .tTok)
			For tI = 1:1:$Get(tTok) {
				Set tRs = tCtx.Query(tTok(tI))
				While tRs.%Next() {
					Set tKey = tRs.%GetData(1)
					Set tCode = tRs.%GetData(3)
					Continue:(tCode="")||'$Data(tNodes(tKey))
					Set tSys = " " _ tRs.%GetData(2)
					Set tCodes(tSys, " " _ tCode) = ""
					Set tRes(tSys, tKey, " " _ tCode) = ""
				}
			}
		}
		Set tBySys = {}
		Set tSys = ""
		For {
			Set tSys = $Order(tCodes(tSys))
			Quit:tSys=""
			Set (tNC, tNR, tNRC) = 0
			Kill tResolved
			Set tCode = ""
			For {
				Set tCode = $Order(tCodes(tSys, tCode))
				Quit:tCode=""
				Set tNC = tNC + 1
				If tCtx.Crosswalked($Extract(tSys, 2, *), $Extract(tCode, 2, *)) {
					Set tNRC = tNRC + 1
					Set tResolved(tCode) = ""
				}
			}
			// A resource is resolved when one of its codes of the system is.
			Set tNRR = 0
			Set tKey = ""
			For {
				Set tKey = $Order(tRes(tSys, tKey))
				Quit:tKey=""
				Set tNR = tNR + 1
				Set tCode = ""
				For {
					Set tCode = $Order(tRes(tSys, tKey, tCode))
					Quit:tCode=""
					If $Data(tResolved(tCode)) {
						Set tNRR = tNRR + 1
						Quit
					}
				}
			}
			Set tS = {}
			Do tS.%Set("codes", tNC, "number")
			Do tS.%Set("resources", tNR, "number")
			Do tS.%Set("resolved_codes", tNRC, "number")
			Do tS.%Set("resolved_resources", tNRR, "number")
			Do tBySys.%Set($Extract(tSys, 2, *), tS)
		}

		// Patients at either end of a link to another Patient of the set.
		Kill tLinked
		Set tKey = ""
		For {
			Set tKey = $Order(tNodes(tKey))
			Quit:tKey=""
			Continue:$Piece(tKey, "/")'="Patient"
			Set tO = ""
			For {
				Set tO = $Order(^KG("out", tCtx.GKey, tKey, "link", tO))
				Quit:tO=""
				Continue:($Piece(tO, "/")'="Patient")||'$Data(tNodes(tO))
				Set tLinked(tKey) = "", tLinked(tO) = ""
			}
		}
		Set tNL = 0
		Set tKey = ""
		For {
			Set tKey = $Order(tLinked(tKey))
			Quit:tKey=""
			Set tNL = tNL + 1
		}

		Set tRep = {"status": "ok", "graph": (pGraph)}
		Set tVer = tCtx.InterpVersion()
		If tVer = "" {
			Do tRep.%Set("interpretation_version", "", "null")
		} Else {
			Do tRep.%Set("interpretation_version", tVer, "number")
		}
		Do tRep.%Set("interpretation_current", ..#INTERPVERSION, "number")
		Do tRep.%Set("stale", (tVer '= ..#INTERPVERSION), "boolean")
		Do tRep.%Set("types", tTypes)
		Do tRep.%Set("resolution", {"by_system": (tBySys)})
		Do tRep.%Set("linked_patients", tNL, "number")
		Set tOut = tRep.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// The (system, code) pairs of the params with no code_crosswalk row, most resources
/// first, then system, then code; their totals; and per param the resources with no
/// token that carry only text there (spec 235, US4, R11). pFieldsJson is
/// {Type: {param: [[element, ...], ...]}}, the element paths Python derives from
/// TokenPaths; only resources with no token row for a param are read.
ClassMethod ConceptGaps(pGraph As %String, pParamsJson As %String = "[""code""]", pFieldsJson As %String = "{}", pTop As %Integer = 20, pPrefix As %String = "") As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Do tCtx.LoadCompartment()
		Do tCtx.NodeSet(pPrefix, .tNodes, .tByType)
		Set tParams = ##class(%DynamicAbstractObject).%FromJSON(pParamsJson)
		Set tFields = ##class(%DynamicAbstractObject).%FromJSON(pFieldsJson)
		Kill tHas, tPair
		Set tIt = tParams.%GetIterator()
		While tIt.%GetNext(, .tParam) {
			Do tCtx.TokenRows(tParam, .tByType, .tTok)
			For tI = 1:1:$Get(tTok) {
				Set tRs = tCtx.Query(tTok(tI))
				While tRs.%Next() {
					Set tKey = tRs.%GetData(1)
					Set tCode = tRs.%GetData(3)
					Continue:(tCode="")||'$Data(tNodes(tKey))
					Set tHas(tParam, tKey) = ""
					Set tPair(" " _ tRs.%GetData(2), " " _ tCode, tKey) = ""
				}
			}
		}
		Kill tRank, tUnKeys
		Set tNU = 0
		Set tSys = ""
		For {
			Set tSys = $Order(tPair(tSys))
			Quit:tSys=""
			Set tCode = ""
			For {
				Set tCode = $Order(tPair(tSys, tCode))
				Quit:tCode=""
				Continue:tCtx.Crosswalked($Extract(tSys, 2, *), $Extract(tCode, 2, *))
				Set tNU = tNU + 1
				Set tN = 0
				Set tKey = ""
				For {
					Set tKey = $Order(tPair(tSys, tCode, tKey))
					Quit:tKey=""
					Set tN = tN + 1
					Set tUnKeys(tKey) = ""
				}
				Set tRank(-tN, tSys, tCode) = tN
			}
		}
		Set tRows = []
		Set tNeg = ""
		For {
			Set tNeg = $Order(tRank(tNeg))
			Quit:(tNeg="")||(tRows.%Size()>=pTop)
			Set tSys = ""
			For {
				Set tSys = $Order(tRank(tNeg, tSys))
				Quit:(tSys="")||(tRows.%Size()>=pTop)
				Set tCode = ""
				For {
					Set tCode = $Order(tRank(tNeg, tSys, tCode), 1, tN)
					Quit:(tCode="")||(tRows.%Size()>=pTop)
					Set tRow = {"system": ($Extract(tSys, 2, *)), "code": ($Extract(tCode, 2, *))}
					Do tRow.%Set("resources", tN, "number")
					Do tRows.%Push(tRow)
				}
			}
		}
		Set tNR = 0
		Set tKey = ""
		For {
			Set tKey = $Order(tUnKeys(tKey))
			Quit:tKey=""
			Set tNR = tNR + 1
		}

		// text_only: one read per resource missing a token for some param.
		Kill tText
		Set tIt = tParams.%GetIterator()
		While tIt.%GetNext(, .tParam) { Set tText(tParam) = 0 }
		Set tKey = ""
		For {
			Set tKey = $Order(tNodes(tKey))
			Quit:tKey=""
			Set tType = $Piece(tKey, "/")
			Set tByParam = tFields.%Get(tType)
			Continue:'$IsObject(tByParam)
			Set tBody = ""
			Set tIt = tParams.%GetIterator()
			While tIt.%GetNext(, .tParam) {
				Continue:$Data(tHas(tParam, tKey))
				Set tPaths = tByParam.%Get(tParam)
				Continue:'$IsObject(tPaths)
				Set:'$IsObject(tBody) tBody = tCtx.ReadResource(tKey)
				Continue:'$IsObject(tBody)
				Set tHit = 0
				Set tPI = tPaths.%GetIterator()
				While 'tHit && tPI.%GetNext(, .tPath) {
					Set tL = ""
					Set tEI = tPath.%GetIterator()
					While tEI.%GetNext(, .tE) { Set tL = tL _ $ListBuild(tE) }
					Set tHit = ..HasText(tBody, tL, 1)
				}
				Set:tHit tText(tParam) = tText(tParam) + 1
			}
		}
		Set tTO = {}
		Set tIt = tParams.%GetIterator()
		While tIt.%GetNext(, .tParam) { Do tTO.%Set(tParam, tText(tParam), "number") }

		Set tTotal = {}
		Do tTotal.%Set("codes", tNU, "number")
		Do tTotal.%Set("resources", tNR, "number")
		Set tOut = {"status": "ok", "graph": (pGraph), "unmatched": (tRows), "unmatched_total": (tTotal), "text_only": (tTO)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// {"present", "concepts"}: whether the graph holds node pPatientKey and, if it does,
/// the sorted concept ids code_crosswalk maps the pParamsJson tokens of the resources
/// in its compartment to (spec 235, R15). Any relation counts.
ClassMethod PatientConcepts(pGraph As %String, pPatientKey As %String, pParamsJson As %String = "[""code""]") As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Set tPresent = ''tCtx.Scalar("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ? AND node_id = ?", tCtx.GraphId, pPatientKey)
		Kill tRes, tByType, tPair, tIds
		If tPresent {
			Set tSrc = ""
			For {
				Set tSrc = $Order(^KG("in", tCtx.GKey, pPatientKey, ..#COMPARTMENTPRED, tSrc))
				Quit:tSrc=""
				Set tType = $Piece(tSrc, "/")
				Set tRes(tType, tSrc) = ""
				Set tByType(tType) = ""
			}
			Set tIt = ##class(%DynamicAbstractObject).%FromJSON(pParamsJson).%GetIterator()
			While tIt.%GetNext(, .tParam) {
				Do tCtx.TokenRows(tParam, .tByType, .tTok)
				For tI = 1:1:$Get(tTok) {
					Set tType = tTok(tI, "type")
					Set tSrc = ""
					For {
						Set tSrc = $Order(tRes(tType, tSrc))
						Quit:tSrc=""
						Set tRs = tCtx.Query(tTok(tI) _ " WHERE Key = ?", tSrc)
						While tRs.%Next() {
							Set tSys = tRs.%GetData(2)
							Set tCode = tRs.%GetData(3)
							Continue:(tSys="")||(tCode="")
							Set tPair(tSys, tCode) = ""
						}
					}
				}
			}
			Set tSys = ""
			For {
				Set tSys = $Order(tPair(tSys))
				Quit:tSys=""
				Set tCode = ""
				For {
					Set tCode = $Order(tPair(tSys, tCode))
					Quit:tCode=""
					Set tRs = tCtx.Query("SELECT target_node_id FROM Graph_KG.code_crosswalk WHERE code_system_uri = ? AND code = ?", tSys, tCode)
					While tRs.%Next() { Set tIds(" " _ tRs.%GetData(1)) = "" }
				}
			}
		}
		Set tConcepts = []
		Set tId = ""
		For {
			Set tId = $Order(tIds(tId))
			Quit:tId=""
			Do tConcepts.%Push($Extract(tId, 2, *))
		}
		Set tOut = {"status": "ok", "graph": (pGraph), "present": (tPresent), "concepts": (tConcepts)}
		Do tOut.%Set("present", tPresent, "boolean")
		Set tOut = tOut.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// {"paths": {Type: {param: FHIRPath}}}: the TOKEN SearchColumn FHIRPath of each
/// param in pParamsJson, for the types with a live resource. Python turns them into
/// the element paths ConceptGaps reads.
ClassMethod TokenPaths(pGraph As %String, pParamsJson As %String = "[""code""]") As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..Open(pGraph)
		Kill tTypes
		Set tRs = tCtx.Query("SELECT DISTINCT ResourceType FROM " _ tCtx.RTable() _ " WHERE Deleted = 0")
		While tRs.%Next() { Set tTypes(tRs.%GetData(1)) = "" }
		Set tPaths = {}
		Set tIt = ##class(%DynamicAbstractObject).%FromJSON(pParamsJson).%GetIterator()
		While tIt.%GetNext(, .tParam) {
			Set tRs = tCtx.Query("SELECT ResourceType, FHIRPath FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND %UPPER(Type) = 'TOKEN' AND ParamName = ?", tCtx.RepoId, tParam)
			While tRs.%Next() {
				Set tType = tRs.%GetData(1)
				Continue:'$Data(tTypes(tType))
				Do:'$IsObject(tPaths.%Get(tType)) tPaths.%Set(tType, {})
				Do tPaths.%Get(tType).%Set(tParam, tRs.%GetData(2))
			}
		}
		Set tOut = {"status": "ok", "graph": (pGraph), "paths": (tPaths)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// Whether a leaf at element path pPath (a $List) from pNode, through arrays at any
/// level, is an object with a non-empty text.
ClassMethod HasText(pNode As %DynamicAbstractObject, pPath As %List, pI As %Integer) As %Boolean [ Private ]
{
	If pNode.%IsA("%DynamicArray") {
		For tJ = 0:1:(pNode.%Size() - 1) {
			Continue:'$IsObject(pNode.%Get(tJ))
			Return:..HasText(pNode.%Get(tJ), pPath, pI) 1
		}
		Return 0
	}
	If pI > $ListLength(pPath) { Return (pNode.%Get("text") '= "") }
	Set tE = $ListGet(pPath, pI)
	Set tT = pNode.%GetTypeOf(tE)
	Return:(tT'="object")&&(tT'="array") 0
	Return ..HasText(pNode.%Get(tE), pPath, pI + 1)
}

/// pNodes(key) for the graph's nodes of an R4 type (LoadCompartment first) whose id
/// starts with pPrefix, and pByType(type) = count.
Method NodeSet(pPrefix As %String, Output pNodes, Output pByType) [ Private ]
{
	Kill pNodes, pByType
	Set tRs = ..Query("SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?", ..GraphId)
	While tRs.%Next() {
		Set tKey = tRs.%GetData(1)
		Set tType = $Piece(tKey, "/")
		Continue:(tType="")||'$Data(..R4Types(tType))
		Continue:(pPrefix'="")&&($Extract($Piece(tKey, "/", 2), 1, $Length(pPrefix))'=pPrefix)
		Set pNodes(tKey) = ""
		Set pByType(tType) = $Get(pByType(tType)) + 1
	}
}

/// pTok(i) = a SELECT of (Key, system, code) over one TOKEN index of pParam, for
/// each type in pByType the repository indexes it on; pTok(i, "type") is the type.
Method TokenRows(pParam As %String, ByRef pByType, Output pTok) [ Private ]
{
	Kill pTok
	Set tRs = ..Query("SELECT ResourceType, TableName, ColumnName, isMultiValue FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND %UPPER(Type) = 'TOKEN' AND ParamName = ?", ..RepoId, pParam)
	While tRs.%Next() {
		Set tType = tRs.%GetData(1)
		Continue:'$Data(pByType(tType))
		Set tTable = tRs.%GetData(2)
		Set tCol = tRs.%GetData(3)
		Set tI = $Increment(pTok)
		Set pTok(tI, "type") = tType
		If +tRs.%GetData(4) {
			Set pTok(tI) = "SELECT Key, value_System, value_Value FROM " _ ..Q(..SSchema _ "_" _ tTable) _ "." _ ..Q(tCol)
		} Else {
			Set pTok(tI) = "SELECT Key, " _ ..Q(tCol _ "_System") _ ", " _ ..Q(tCol _ "_Value") _ " FROM " _ ..Q(..SSchema) _ "." _ ..Q(tTable)
		}
	}
}

/// Whether code_crosswalk maps (pSystem, pCode) to any concept.
Method Crosswalked(pSystem As %String, pCode As %String) As %Boolean [ Private ]
{
	Return ''..Scalar("SELECT COUNT(*) FROM Graph_KG.code_crosswalk WHERE code_system_uri = ? AND code = ?", pSystem, pCode)
}

/// fhir_graphs.interp_version for the graph; "" when NULL or the column is absent.
Method InterpVersion() As %String [ Private ]
{
	Set tV = ""
	Try {
		Set tV = ..Scalar("SELECT interp_version FROM Graph_KG.fhir_graphs WHERE graph_id = ?", ..GraphId)
	} Catch {
		Set tV = ""
	}
	Return tV
}

/// Whether the graph needs a full re-derivation: no marker, or an older one. A newer
/// marker is left alone, since older rules would downgrade it.
Method InterpStale() As %Boolean [ Private ]
{
	Set tV = ..InterpVersion()
	Return (tV = "") || (+tV < ..#INTERPVERSION)
}

/// Record INTERPVERSION in fhir_graphs.interp_version. A pre-235 table without the
/// column gets it here, by the ALTER initialize_schema issues, so a namespace upgraded
/// by deploying classes alone does not re-derive on every sync.
Method SetInterpVersion() [ Private ]
{
	Set tSql = "UPDATE Graph_KG.fhir_graphs SET interp_version = ? WHERE graph_id = ?"
	If $$$ISERR(##class(%SQL.Statement).%New().%Prepare(tSql)) {
		Do ..Exec("ALTER TABLE Graph_KG.fhir_graphs ADD COLUMN interp_version INTEGER")
	}
	Do ..Exec(tSql, ..#INTERPVERSION, ..GraphId)
}

/// The interpretation block of a sync, rebuild or reinterpret result (FR-025).
Method InterpBlock(pFull As %Boolean) As %DynamicObject [ Private ]
{
	Set tB = {}
	Do tB.%Set("compartment_edges_added", +$Get(..Interp("added")), "number")
	Do tB.%Set("compartment_edges_removed", +$Get(..Interp("removed")), "number")
	Do tB.%Set("category_set", +$Get(..Interp("category")), "number")
	Do tB.%Set("meta_profile_set", +$Get(..Interp("meta_profile")), "number")
	Do tB.%Set("full_rederivation", ''pFull, "boolean")
	Set tV = ..InterpVersion()
	Do tB.%Set("interpretation_version", $Select(tV = "": "", 1: +tV), $Select(tV = "": "null", 1: "number"))
	Return tB
}

/// Resync every live key synced so far, BATCHSIZE per transaction, watermarks
/// untouched. The caller holds the graph's lock. Returns the number of keys.
Method Rederive() As %Integer
{
	Set tKeys = 0
	Set ..RederiveN = 0
	Kill tBatch
	Set tN = 0
	Set tRs = ..Query("SELECT Key FROM " _ ..RTable() _ " WHERE ID <= ? AND (Deleted IS NULL OR Deleted = 0)", ..WmRsrc)
	While tRs.%Next() {
		Set tBatch($Increment(tN)) = tRs.%GetData(1)
		If tN >= ..#BATCHSIZE {
			Set ..RederiveN = ..RederiveN + 1
			Set tKeys = tKeys + ..RunBatch(.tBatch, 0, "", "", 0)
			Kill tBatch
			Set tN = 0
		}
	}
	If tN > 0 {
		Set ..RederiveN = ..RederiveN + 1
		Set tKeys = tKeys + ..RunBatch(.tBatch, 0, "", "", 0)
	}
	Set ..RederiveN = 0
	Return tKeys
}

/// The concepts reachable from pIdsJson in at most pHops of pConceptGraph, seeds
/// included. pDirection picks the tree walked at every hop: "in" (the default)
/// follows ^KG("in", g, o, p, s) from object to subject, which under child-to-parent
/// edges (subClassOf, broader, is_a) reaches subclasses and a disease's genes; "out"
/// follows ^KG("out", g, s, p, o) from subject to object; "both" does both. pPredicatesJson restricts the predicates followed; empty or [] follows all.
ClassMethod ExpandConcepts(pConceptGraph As %String = "", pIdsJson As %String = "[]", pPredicatesJson As %String = "", pHops As %Integer = 1, pDirection As %String = "in") As %String
{
	Set tOut = ""
	Try {
		If (pDirection '= "out") && (pDirection '= "in") && (pDirection '= "both") {
			Set tOut = {"status": "error", "error": ("direction must be out, in or both, got '"_pDirection_"'")}.%ToJSON()
			Return tOut
		}
		Set tTrees = $Select(pDirection = "both": $ListBuild("out", "in"), 1: $ListBuild(pDirection))
		Set tGk = ##class(Graph.KG.GraphKey).ForIndex(pConceptGraph)
		Set tIds = ##class(%DynamicAbstractObject).%FromJSON(pIdsJson)
		Kill tPredOk
		Set tAllPreds = 1
		If pPredicatesJson '= "" {
			Set tPreds = ##class(%DynamicAbstractObject).%FromJSON(pPredicatesJson)
			Set tIt = tPreds.%GetIterator()
			While tIt.%GetNext(, .tP) {
				Set tPredOk(tP) = ""
				Set tAllPreds = 0
			}
		}
		Kill tSeen, tFrontier
		Set tOrder = []
		Set tIt = tIds.%GetIterator()
		While tIt.%GetNext(, .tId) {
			Continue:$Data(tSeen(tId))
			Set tSeen(tId) = ""
			Set tFrontier(tId) = ""
			Do tOrder.%Push(tId)
		}
		For tHop = 1:1:+pHops {
			Kill tNext
			Set tS = ""
			For {
				Set tS = $Order(tFrontier(tS))
				Quit:tS=""
				For tT = 1:1:$ListLength(tTrees) {
					Set tTree = $List(tTrees, tT)
					Set tP = ""
					For {
						Set tP = $Order(^KG(tTree, tGk, tS, tP))
						Quit:tP=""
						Continue:('tAllPreds)&&('$Data(tPredOk(tP)))
						Set tO = ""
						For {
							Set tO = $Order(^KG(tTree, tGk, tS, tP, tO))
							Quit:tO=""
							Continue:$Data(tSeen(tO))
							Set tSeen(tO) = ""
							Set tNext(tO) = ""
							Do tOrder.%Push(tO)
						}
					}
				}
			}
			Kill tFrontier
			Merge tFrontier = tNext
			Quit:$Data(tFrontier)=0
		}
		Set tOut = {"status": "ok", "ids": (tOrder)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// Group PPR scores by patient (spec 235): a patient's score is the sum of the scores
/// of the resources in its compartment, read from the in_patient_compartment edges.
///
/// pScoresJson: {key: score} as PPR returned it. pViaJson: a JSON array of params; a
/// (resource, patient) pair counts only when its edge's `via` shares one ("" keeps
/// every pair). pExplainTop: contributors listed per patient; pTopK: patients
/// returned, 0 for all. A resource in two compartments gives its full score to each.
/// Patients and resources with no kept pair are unattributed.
ClassMethod GroupByPatient(pGraph As %String, pScoresJson As %String, pViaJson As %String = "", pExplainTop As %Integer = 5, pTopK As %Integer = 50) As %String
{
	Set tOut = ""
	Try {
		Set tScores = ##class(%DynamicAbstractObject).%FromJSON(pScoresJson)
		If tScores.%ClassName(1) '= "%Library.DynamicObject" {
			Return {"status": "error", "error": "scores must be a JSON object"}.%ToJSON()
		}
		Kill tS
		Set tIt = tScores.%GetIterator()
		While tIt.%GetNext(.tKey, .tScore) { Set tS(tKey) = tScore }
		Set tOut = ..Group(pGraph, .tS, pViaJson, pExplainTop, pTopK)
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// Personalized PageRank from pSeedJson, walked both ways over pGraph less pExcludeJson
/// (Graph.KG.PageRank.Run), then grouped by patient as GroupByPatient does. One call, so
/// no score list crosses the wire: every score as JSON passed the string limit at ~100
/// Synthea patients (spec 235, R17). Only scores above zero are grouped.
ClassMethod GroupPPR(pGraph As %String, pSeedJson As %String, pAlpha As %Double = 0.85, pMaxIter As %Integer = 20, pExcludeJson As %String = "", pViaJson As %String = "", pExplainTop As %Integer = 5, pTopK As %Integer = 50) As %String
{
	Set tOut = ""
	Try {
		Kill tRanks, tS
		Do ##class(Graph.KG.PageRank).Run(pSeedJson, pAlpha, pMaxIter, 1, 1.0, pGraph, pExcludeJson, .tRanks)
		Set tKey = ""
		For {
			Set tKey = $Order(tRanks(tKey), 1, tScore)
			Quit:tKey=""
			Set:tScore>0 tS(tKey) = tScore
		}
		Kill tRanks
		Set tOut = ..Group(pGraph, .tS, pViaJson, pExplainTop, pTopK)
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// GroupByPatient's grouping over pScores(key) = score. Throws on a bad pViaJson.
ClassMethod Group(pGraph As %String, ByRef pScores, pViaJson As %String, pExplainTop As %Integer, pTopK As %Integer) As %String [ Internal ]
{
	Set tPred = ..#COMPARTMENTPRED
	Set tGk = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tName = ##class(Graph.KG.GraphKey).ForName(pGraph)
	Kill tViaOk
	Set tAllVia = 1
	If pViaJson '= "" {
		Set tIt = ##class(%DynamicAbstractObject).%FromJSON(pViaJson).%GetIterator()
		While tIt.%GetNext(, .tV) {
			Set tViaOk(tV) = ""
			Set tAllVia = 0
		}
	}
	// A "" bind arrives as NULL and matches nothing, so the default graph is a literal.
	Set tStmt = ##class(%SQL.Statement).%New()
	$$$ThrowOnError(tStmt.%Prepare("SELECT qualifiers FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = " _ $Select(tName = "": "''", 1: "?")))

	Kill tSum, tN, tBy
	Set tUnN = 0
	Set tUnScore = 0
	Set tKey = ""
	For {
		Set tKey = $Order(pScores(tKey), 1, tScore)
		Quit:tKey=""
		Set tKept = 0
		If $Extract(tKey, 1, 8) '= "Patient/" {
			Set tPat = ""
			For {
				Set tPat = $Order(^KG("out", tGk, tKey, tPred, tPat))
				Quit:tPat=""
				If tName = "" {
					Set tRs = tStmt.%Execute(tKey, tPred, tPat)
				} Else {
					Set tRs = tStmt.%Execute(tKey, tPred, tPat, tName)
				}
				Set tVia = []
				If tRs.%Next() {
					Set tQ = tRs.%GetData(1)
					If tQ '= "" {
						Set tQv = ##class(%DynamicAbstractObject).%FromJSON(tQ).%Get("via")
						Set:$IsObject(tQv) tVia = tQv
					}
				}
				If 'tAllVia {
					Set tHit = 0
					Set tVi = tVia.%GetIterator()
					While 'tHit && tVi.%GetNext(, .tV) { Set tHit = $Data(tViaOk(tV)) > 0 }
					Continue:'tHit
				}
				Set tKept = 1
				Set tSum(tPat) = $Get(tSum(tPat)) + tScore
				Set tN(tPat) = $Get(tN(tPat)) + 1
				// Contributors by score descending, key ascending on a tie.
				Set tBy(tPat, -tScore, tKey) = tVia.%ToJSON()
			}
		}
		If 'tKept {
			Set tUnN = tUnN + 1
			Set tUnScore = tUnScore + tScore
		}
	}

	Kill tRank
	Set tPat = ""
	For {
		Set tPat = $Order(tSum(tPat))
		Quit:tPat=""
		Set tRank(-tSum(tPat), tPat) = ""
	}
	Set tTopK = +pTopK
	Set tExplain = +pExplainTop
	Set tPatients = []
	Set tNeg = ""
	For {
		Set tNeg = $Order(tRank(tNeg))
		Quit:tNeg=""
		Quit:(tTopK>0)&&(tPatients.%Size()>=tTopK)
		Set tPat = ""
		For {
			Set tPat = $Order(tRank(tNeg, tPat))
			Quit:tPat=""
			Quit:(tTopK>0)&&(tPatients.%Size()>=tTopK)
			Set tContrib = []
			Set tCs = ""
			For {
				Quit:tContrib.%Size()>=tExplain
				Set tCs = $Order(tBy(tPat, tCs))
				Quit:tCs=""
				Set tCk = ""
				For {
					Quit:tContrib.%Size()>=tExplain
					Set tCk = $Order(tBy(tPat, tCs, tCk), 1, tViaJson)
					Quit:tCk=""
					Set tC = {"key": (tCk)}
					Do tC.%Set("score", -tCs, "number")
					Do tC.%Set("via", ##class(%DynamicAbstractObject).%FromJSON(tViaJson))
					Do tContrib.%Push(tC)
				}
			}
			Set tP = {"patient": (tPat)}
			Do tP.%Set("score", tSum(tPat), "number")
			Do tP.%Set("contributors_total", tN(tPat), "number")
			Do tP.%Set("contributors", tContrib)
			Do tPatients.%Push(tP)
		}
	}
	Set tUn = {}
	Do tUn.%Set("count", tUnN, "number")
	Do tUn.%Set("score", tUnScore, "number")
	Return {"status": "ok", "patients": (tPatients), "unattributed": (tUn)}.%ToJSON()
}

// ===================================================================== internals

/// Load the registry row for pGraph into a new context. Throws when unregistered.
ClassMethod Open(pGraph As %String) As Graph.KG.FHIRGraph
{
	If pGraph = "" {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "a FHIR graph id is required"))
	}
	Set tCtx = ..%New()
	Set tCtx.GraphId = ##class(Graph.KG.GraphKey).ForName(pGraph)
	Set tCtx.GKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	// The first registration creates the table; without it nothing is registered.
	If 'tCtx.TableExists("Graph_KG", "fhir_graphs") {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "FHIR graph " _ pGraph _ " is not registered; call Register() first"))
	}
	Set tRs = tCtx.Query("SELECT repo_id, rsrc_schema, search_schema, ver_schema, endpoints, denylist, interval_s, wm_rsrc, wm_ver, task_id, json_links FROM Graph_KG.fhir_graphs WHERE graph_id = ?", tCtx.GraphId)
	If 'tRs.%Next() {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "FHIR graph " _ pGraph _ " is not registered; call Register() first"))
	}
	Set tCtx.RepoId = tRs.%GetData(1)
	Set tCtx.RSchema = tRs.%GetData(2)
	Set tCtx.SSchema = tRs.%GetData(3)
	Set tCtx.VSchema = tRs.%GetData(4)
	Set tEndpoints = ##class(%DynamicAbstractObject).%FromJSON($Select(tRs.%GetData(5) = "": "[]", 1: tRs.%GetData(5)))
	Set tDeny = ##class(%DynamicAbstractObject).%FromJSON($Select(tRs.%GetData(6) = "": "[]", 1: tRs.%GetData(6)))
	Set tCtx.IntervalS = +tRs.%GetData(7)
	Set tCtx.WmRsrc = +tRs.%GetData(8)
	Set tCtx.WmVer = +tRs.%GetData(9)
	Set tCtx.TaskId = tRs.%GetData(10)
	Set tCtx.LinksRaw = tRs.%GetData(11)
	Set tIt = tEndpoints.%GetIterator()
	While tIt.%GetNext(, .tE) {
		If $Extract(tE, *) = "/" { Set tE = $Extract(tE, 1, *-1) }
		Set:tE'="" tCtx.Endpoints(tE) = ""
	}
	Set tIt = tDeny.%GetIterator()
	While tIt.%GetNext(, .tD) { Set tCtx.Deny(tD) = "" }
	Return tCtx
}

/// Validate a denylist: a JSON array of "Type.param" strings.
ClassMethod ParseDenylist(pJson As %String) As %DynamicArray [ Private ]
{
	Set tDeny = ##class(%DynamicAbstractObject).%FromJSON($Select(pJson = "": "[]", 1: pJson))
	If 'tDeny.%IsA("%DynamicArray") {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "denylist must be a JSON array of ""Type.param"" strings"))
	}
	Set tIt = tDeny.%GetIterator()
	While tIt.%GetNext(, .tD, .tType) {
		If (tType '= "string") || ($Length(tD, ".") '= 2) || ($Piece(tD, ".", 1) = "") || ($Piece(tD, ".", 2) = "") {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "denylist entry " _ tD _ " is not ""Type.param"""))
		}
	}
	Return tDeny
}

/// The reference and canonical search columns of the repository, minus the denylist
/// (FR-004), and the definitional types. uri params (Resource._profile) stay out: a
/// profile is not a link between resources in the repository.
Method LoadColumns()
{
	Kill ..Cols, ..DefTypes, ..SplitTypes
	Set tRs = ..Query("SELECT ResourceType, ParamName, TableName, ColumnName, isMultiValue, Definition, DataType, FHIRPath FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND %UPPER(Type) = 'REFERENCE' AND %UPPER(DataType) IN ('REFERENCE', 'CANONICAL')", ..RepoId)
	While tRs.%Next() {
		Set tType = tRs.%GetData(1)
		Set tParam = tRs.%GetData(2)
		Set tDataType = $ZConvert(tRs.%GetData(7), "L")
		Set tSplit = 0
		Set tPath = tRs.%GetData(8)
		For tI = 1:1:$Length(tPath, "|") {
			If $ZStrip($Piece(tPath, "|", tI), "<>W") = (tType _ ".library") {
				Set tSplit = 1
				Quit
			}
		}
		Set:tSplit ..SplitTypes(tType) = ""
		Continue:$Data(..Deny(tType _ "." _ tParam))
		Set ..Cols(tType, tParam) = $ListBuild(tRs.%GetData(3), tRs.%GetData(4), +tRs.%GetData(5), tRs.%GetData(6), tDataType, tSplit)
	}
	// Definitional types: a single-value url (uri) param and a version token. Device
	// and Subscription carry a url that is a network address and have no version.
	Kill tUrl
	Set tRs = ..Query("SELECT ResourceType, ParamName, TableName, ColumnName FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND isMultiValue = 0 AND ((ParamName = 'url' AND %UPPER(Type) = 'URI' AND %UPPER(DataType) = 'URI') OR (ParamName = 'version' AND %UPPER(Type) = 'TOKEN'))", ..RepoId)
	While tRs.%Next() {
		Set tUrl(tRs.%GetData(1), tRs.%GetData(2)) = $ListBuild(tRs.%GetData(3), tRs.%GetData(4))
	}
	Set tType = ""
	For {
		Set tType = $Order(tUrl(tType))
		Quit:tType=""
		Continue:'$Data(tUrl(tType, "url"), tU)
		Continue:'$Data(tUrl(tType, "version"), tV)
		Continue:$ListGet(tU, 1)'=$ListGet(tV, 1)
		Set ..DefTypes(tType) = $ListBuild($ListGet(tU, 1), $ListGet(tU, 2), $ListGet(tV, 2) _ "_Value")
	}
	Do ..LoadLinks(..LinksRaw)
	Do ..LoadCompartment()
	Do ..LoadProps()
}

/// Fill Props and CatSys (research R8). A token category is multi-value on most
/// types and a main-table column on the rest; _profile is multi-value on every type
/// that has its table.
Method LoadProps()
{
	Kill ..Props, ..CatSys
	Set tIt = ##class(%DynamicArray).%FromJSON(..#CATEGORYSYSTEMS).%GetIterator()
	While tIt.%GetNext(, .tSys) { Set ..CatSys(tSys) = "" }
	Set tRs = ..Query("SELECT ResourceType, TableName, ColumnName, isMultiValue FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND ParamName = 'category' AND %UPPER(Type) = 'TOKEN'", ..RepoId)
	While tRs.%Next() {
		Set ..Props(tRs.%GetData(1), "category") = $ListBuild(tRs.%GetData(2), tRs.%GetData(3), +tRs.%GetData(4))
	}
	Quit:'..Scalar("SELECT COUNT(*) FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND ResourceType = 'Resource' AND ParamName = '_profile'", ..RepoId)
	Set tPre = ..SSchema _ "_"
	Set tRs = ..Query("SELECT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA %STARTSWITH ? AND TABLE_NAME = '_profile'", tPre)
	While tRs.%Next() {
		Set tType = $Extract(tRs.%GetData(1), $Length(tPre) + 1, *)
		Set:tType'="" ..Props(tType, "meta_profile") = $ListBuild(tType, "_profile", 1)
	}
}

/// Fill Comp and R4Types for pCompartment ("patient", "encounter", ...: the schema's
/// lower camel case names). pSchema is an HS.FHIRServer.Schema; "" loads the
/// repository's metadata set. An
/// unknown compartment leaves Comp empty.
Method LoadCompartment(pSchema As %RegisteredObject = "", pCompartment As %String = "patient")
{
	Kill ..Comp, ..R4Types
	If '$IsObject(pSchema) {
		Set tKey = ..Scalar("SELECT metadataSetKey FROM HS_FHIRServer.RepoInstance WHERE repo = ?", ..RepoId)
		Set:$ListValid(tKey) tKey = $ListToString(tKey)
		// By name: the class exists only in a namespace with a FHIR server.
		Set pSchema = $ClassMethod("HS.FHIRServer.Schema", "LoadSchema", tKey)
	}
	Set tType = ""
	For {
		Set tType = pSchema.NextSearchParamResourceType(tType)
		Quit:tType=""
		Set ..R4Types(tType) = ""
		Kill tP
		Do pSchema.GetCompartmentParamsForType(tType, .tP)
		Set tParam = ""
		For {
			Set tParam = $Order(tP(pCompartment, tParam))
			Quit:tParam=""
			Set ..Comp(tType, tParam) = ""
		}
	}
}

/// Load a json_links array into Links, LinkTypes, LibLink and HasExt. pJson = ""
/// means the default list. Needs SplitTypes (LoadColumns) for the default.
Method LoadLinks(pJson As %String)
{
	Kill ..Links, ..LinkTypes, ..LibLink
	Set ..HasExt = 0
	Set tErr = ..ParseLinks($Select(pJson = "": ..DefaultLinksJson(), 1: pJson), .tLinks)
	If tErr '= "" {
		$$$ThrowStatus($$$ERROR($$$GeneralError, tErr))
	}
	Merge ..Links = tLinks
	For tI = 1:1:tLinks {
		Set $ListBuild(tEntry, tKind, tWhat, tPred) = tLinks(tI)
		If tKind = "extension" {
			Set ..HasExt = 1
			Continue
		}
		Set ..LinkTypes(tWhat) = ""
		Set:(tPred="library")&&'$Data(..Deny(tWhat _ ".library")) ..LibLink(tWhat) = tEntry
	}
}

/// The default json links: <Type>.library for each type with a split column, in
/// order, then cqf-library (research R5).
Method DefaultLinksJson() As %String
{
	Set tOut = []
	Set tType = ""
	For {
		Set tType = $Order(..SplitTypes(tType))
		Quit:tType=""
		Do tOut.%Push(tType _ ".library")
	}
	Do tOut.%Push("extension:" _ ..#CQFLIBRARY)
	Return tOut.%ToJSON()
}

/// The loaded json links as a JSON array of entries.
Method LinksJson() As %String
{
	Set tOut = []
	For tI = 1:1:..Links {
		Do tOut.%Push($ListGet(..Links(tI), 1))
	}
	Return tOut.%ToJSON()
}

/// Resync each key of a batch in one transaction, optionally advancing a watermark.
///
/// pFollow also resyncs the sources of unresolved references that now point at a
/// live key, which is how a re-created or late-created target gets its incoming
/// edges back. Rebuild does not need it: it resyncs every source anyway.
///
/// Two passes (research R8). Pass 1 brings the batch's definitions up to date and
/// collects the urls they declared before and after; pass 2 resyncs the batch, then
/// every source whose canonical refs name one of those urls, so a referrer resolves
/// against this batch's definitions. pDefs = 0 skips pass 1: Rebuild runs it over
/// every key first.
///
/// ^IVG.FHIRGraphFault(graph) = "beforecommit" throws just before TCOMMIT. It exists
/// so the E2E test can show the batch and its watermark roll back together.
/// "rederive:<n>" throws in Rederive's nth batch only (spec 235).
Method RunBatch(ByRef pBatch, pFollow As %Boolean, pFeed As %String, pWatermark As %Integer, pDefs As %Boolean = 1) As %Integer
{
	Kill ..Done, ..Pending
	Set tCount = 0
	TSTART
	Kill tAffected
	If pDefs {
		Set tI = ""
		For {
			Set tI = $Order(pBatch(tI), 1, tKey)
			Quit:tI=""
			Do ..SyncDefinition(tKey, .tAffected)
		}
	}
	Set tI = ""
	For {
		Set tI = $Order(pBatch(tI), 1, tKey)
		Quit:tI=""
		Continue:$Data(..Done(tKey))
		Do ..ResyncKey(tKey, pFollow)
		Set tCount = tCount + 1
	}
	Kill tDependants
	Set tUrl = ""
	For {
		Set tUrl = $Order(tAffected(tUrl))
		Quit:tUrl=""
		Set tRs = ..Query("SELECT DISTINCT source FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ? AND url = ? AND kind = 'canonical'", ..GraphId, tUrl)
		While tRs.%Next() { Set tDependants(tRs.%GetData(1)) = "" }
	}
	Set tS = ""
	For {
		Set tS = $Order(tDependants(tS))
		Quit:tS=""
		Continue:$Data(..Done(tS))
		Do ..ResyncKey(tS, 0)
	}
	Set tS = ""
	For {
		Set tS = $Order(..Pending(tS))
		Quit:tS=""
		Continue:$Data(..Done(tS))
		Do ..ResyncKey(tS, 0)
	}
	If pFeed '= "" {
		Do ..Exec("UPDATE Graph_KG.fhir_graphs SET " _ pFeed _ " = ? WHERE graph_id = ?", pWatermark, ..GraphId)
	}
	Set tFault = $Get(^IVG.FHIRGraphFault(..GraphId))
	If (tFault = "beforecommit") || (..RederiveN && (tFault = ("rederive:" _ ..RederiveN))) {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "injected fault before commit (^IVG.FHIRGraphFault)"))
	}
	TCOMMIT
	If pFeed = "wm_rsrc" { Set ..WmRsrc = pWatermark }
	If pFeed = "wm_ver" { Set ..WmVer = pWatermark }
	Return tCount
}

/// Pass 1 over the whole repository, for Rebuild: both bookkeeping tables start empty,
/// then every definition goes in, batch by batch. Pass 2 refills the refs.
Method SyncAllDefinitions(pMaxR As %Integer)
{
	Set tN = 0
	TSTART
	Do ..Exec("DELETE FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ?", ..GraphId)
	Do ..Exec("DELETE FROM Graph_KG.fhir_definitions WHERE graph_id = ?", ..GraphId)
	// Live keys only: a deleted key's definition row is none, and both tables are empty.
	Set tRs = ..Query("SELECT Key FROM " _ ..RTable() _ " WHERE ID <= ? AND (Deleted IS NULL OR Deleted = 0)", pMaxR)
	While tRs.%Next() {
		Set tKey = tRs.%GetData(1)
		Continue:'$Data(..DefTypes($Piece(tKey, "/", 1)))
		Do ..SyncDefinition(tKey, .tAffected)
		Set tN = tN + 1
		If tN >= ..#BATCHSIZE {
			TCOMMIT
			TSTART
			Set tN = 0
		}
	}
	TCOMMIT
}

/// Bring a key's fhir_definitions row to what the repository declares: a row while
/// the key is live and declares a url, none otherwise. pAffected(url) collects the
/// url before and after when either the url or the version changed.
Method SyncDefinition(pKey As %String, ByRef pAffected) [ Private ]
{
	Set tType = $Piece(pKey, "/", 1)
	Quit:'$Data(..DefTypes(tType), tDef)
	Set tOldUrl = "", tOldVer = "", tHad = 0
	Set tRs = ..Query("SELECT url, version FROM Graph_KG.fhir_definitions WHERE graph_id = ? AND rsrc_key = ?", ..GraphId, pKey)
	If tRs.%Next() {
		Set tHad = 1
		Set tOldUrl = tRs.%GetData(1)
		Set tOldVer = tRs.%GetData(2)
	}
	Set tUrl = "", tVer = ""
	If ..Liveness(pKey) = 1 {
		Set tRs = ..Query("SELECT " _ ..Q($ListGet(tDef, 2)) _ ", " _ ..Q($ListGet(tDef, 3)) _ " FROM " _ ..Q(..SSchema) _ "." _ ..Q($ListGet(tDef, 1)) _ " WHERE Key = ?", pKey)
		If tRs.%Next() {
			Set tUrl = $Extract(tRs.%GetData(1), 1, ..#CANONICALMAX)
			Set tVer = $Extract(tRs.%GetData(2), 1, ..#CANONICALMAX)
		}
	}
	Quit:(tUrl=tOldUrl)&&(tVer=tOldVer)&&(tHad=(tUrl'=""))
	If tUrl = "" {
		Do ..Exec("DELETE FROM Graph_KG.fhir_definitions WHERE graph_id = ? AND rsrc_key = ?", ..GraphId, pKey)
	} ElseIf tHad {
		Do ..Exec("UPDATE Graph_KG.fhir_definitions SET url = ?, version = ? WHERE graph_id = ? AND rsrc_key = ?", tUrl, tVer, ..GraphId, pKey)
	} Else {
		Do ..Exec("INSERT INTO Graph_KG.fhir_definitions (graph_id, rsrc_key, url, version) VALUES (?, ?, ?, ?)", ..GraphId, pKey, tUrl, tVer)
	}
	Set:tOldUrl'="" pAffected(tOldUrl) = ""
	Set:tUrl'="" pAffected(tUrl) = ""
}

/// Bring one key's node, out-edges and unresolved rows to the repository's state.
Method ResyncKey(pKey As %String, pFollow As %Boolean = 0)
{
	Set ..Done(pKey) = ""
	Set tLive = ..Liveness(pKey)
	If tLive '= 1 {
		Do ..DropNode(pKey, $Select(tLive = -1: "deleted", 1: "missing"))
		Return
	}
	Set tType = $Piece(pKey, "/", 1)
	Do ..EnsureNode(pKey)

	// --- what the body says, when a json link or the split needs it (R6) -------
	// tWant(p, target) = $lb(qualifier key, value); tRefs(p, url, version or $C(0))
	// = origin; tXRefs(p, reference) = entry, for extension References.
	Kill tWant, tUnres, tRefs, tXRefs, tFound, tInLib, tInRa
	Set tSplitOn = ($Data(..LibLink(tType)) > 0)
	If $Data(..LinkTypes(tType)) || ..HasExt {
		Set tBody = ..ReadResource(pKey)
		If $IsObject(tBody) {
			Kill tLinks
			Merge tLinks = ..Links
			Do ..Extract(tBody, .tLinks, .tFound)
			If tSplitOn {
				If tBody.%GetTypeOf("library") = "array" {
					Set tArr = tBody.library
					For tI = 0:1:(tArr.%Size() - 1) {
						Continue:tArr.%GetTypeOf(tI)'="string"
						Set tC = ..CanonKey(tArr.%Get(tI))
						Set:tC'="" tInLib(tC) = ""
					}
				}
				If tBody.%GetTypeOf("relatedArtifact") = "array" {
					Set tArr = tBody.relatedArtifact
					For tI = 0:1:(tArr.%Size() - 1) {
						Continue:tArr.%GetTypeOf(tI)'="object"
						Set tRa = tArr.%Get(tI)
						Continue:(tRa.type'="depends-on")||(tRa.%GetTypeOf("resource")'="string")
						Set tC = ..CanonKey(tRa.resource)
						Set:tC'="" tInRa(tC) = ""
					}
				}
			}
		}
	}

	// --- what the repository index says this key references --------------------
	Set tParam = ""
	For {
		Set tParam = $Order(..Cols(tType, tParam), 1, tCol)
		Quit:tParam=""
		Set tTable = $ListGet(tCol, 1)
		Set tColumn = $ListGet(tCol, 2)
		If $ListGet(tCol, 5) = "canonical" {
			// The index has split each value into url and version already.
			If $ListGet(tCol, 3) {
				Set tSql = "SELECT value_Reference, value_Version FROM " _ ..Q(..SSchema _ "_" _ tTable) _ "." _ ..Q(tColumn) _ " WHERE Key = ?"
			} Else {
				Set tSql = "SELECT " _ ..Q(tColumn _ "_Reference") _ ", " _ ..Q(tColumn _ "_Version") _ " FROM " _ ..Q(..SSchema) _ "." _ ..Q(tTable) _ " WHERE Key = ?"
			}
			Set tRs = ..Query(tSql, pKey)
			While tRs.%Next() {
				Set tUrl = $Extract(tRs.%GetData(1), 1, ..#CANONICALMAX)
				Continue:(tUrl="")||($Extract(tUrl)="#")
				Set tVer = $Extract(tRs.%GetData(2), 1, ..#CANONICALMAX)
				// The split: a value the body has only in library is a library edge.
				Continue:tSplitOn&&$ListGet(tCol,6)&&'..KeepIndex(tUrl_$Select(tVer="":"",1:"|"_tVer),.tInLib,.tInRa)
				Set tRefs(tParam, tUrl, $Select(tVer = "": $Char(0), 1: tVer)) = "index"
			}
			Continue
		}
		If $ListGet(tCol, 3) {
			Set tSql = "SELECT value_Reference, value_RelativeRef FROM " _ ..Q(..SSchema _ "_" _ tTable) _ "." _ ..Q(tColumn) _ " WHERE Key = ?"
		} Else {
			Set tSql = "SELECT " _ ..Q(tColumn _ "_Reference") _ ", " _ ..Q(tColumn _ "_RelativeRef") _ " FROM " _ ..Q(..SSchema) _ "." _ ..Q(tTable) _ " WHERE Key = ?"
		}
		Set tRs = ..Query(tSql, pKey)
		While tRs.%Next() {
			Set tKind = ..Classify(tRs.%GetData(1), tRs.%GetData(2), .tTarget)
			Continue:tKind="skip"
			If tKind = "external" {
				Set tUnres(tParam, tTarget) = "external"
				Continue
			}
			Set tTLive = ..Liveness(tTarget)
			If tTLive = 1 {
				Set tWant(tParam, tTarget) = $ListBuild("searchParam", $ListGet(tCol, 4))
			} Else {
				Set tUnres(tParam, tTarget) = $Select(tTLive = -1: "deleted", 1: "missing")
			}
		}
	}
	// Body links. A canonical joins the index's; the index's origin wins a tie.
	For tI = 1:1:$Get(tFound) {
		Set $ListBuild(tEntry, tPred, tValue, tKind) = tFound(tI)
		Continue:$Data(..Deny(tType _ "." _ tPred))
		If tKind = "canonical" {
			Continue:'..SplitValue(tValue,.tUrl,.tVer)
			Set tV = $Select(tVer = "": $Char(0), 1: tVer)
			Set:'$Data(tRefs(tPred, tUrl, tV)) tRefs(tPred, tUrl, tV) = tEntry
			Continue
		}
		Set tKindOf = ..Classify(tValue, ..BodyRel(tValue), .tTarget)
		Continue:tKindOf="skip"
		Set tRef = $Extract(tValue, 1, 512)
		Continue:$Data(tXRefs(tPred, tRef))
		Set tXRefs(tPred, tRef) = tEntry
		If tKindOf = "external" {
			Set tUnres(tPred, tTarget) = "external"
			Continue
		}
		Set tTLive = ..Liveness(tTarget)
		If tTLive = 1 {
			Set:'$Data(tWant(tPred, tTarget)) tWant(tPred, tTarget) = $ListBuild("jsonLink", tEntry)
		} Else {
			Set tUnres(tPred, tTarget) = $Select(tTLive = -1: "deleted", 1: "missing")
		}
	}
	// Canonicals resolve against the definitions table by the rule (research R7).
	Kill tCands
	Set tParam = ""
	For {
		Set tParam = $Order(tRefs(tParam))
		Quit:tParam=""
		Set tUrl = ""
		For {
			Set tUrl = $Order(tRefs(tParam, tUrl))
			Quit:tUrl=""
			If '$Data(tCands(tUrl)) {
				Set tCands(tUrl) = ""
				Set tRs = ..Query("SELECT rsrc_key, version FROM Graph_KG.fhir_definitions WHERE graph_id = ? AND url = ?", ..GraphId, tUrl)
				While tRs.%Next() { Set tCands(tUrl, tRs.%GetData(1)) = tRs.%GetData(2) }
			}
			Kill tCand
			Merge tCand = tCands(tUrl)
			Set tV = ""
			For {
				Set tV = $Order(tRefs(tParam, tUrl, tV), 1, tOrigin)
				Quit:tV=""
				Set tVer = $Select(tV = $Char(0): "", 1: tV)
				Set tOutcome = ..ResolveCands(tVer, .tCand, .tTarget)
				If tOutcome = "resolved" {
					If tOrigin = "index" {
						Set tWant(tParam, tTarget) = $ListBuild("searchParam", $ListGet(..Cols(tType, tParam), 4))
					} ElseIf '$Data(tWant(tParam, tTarget)) {
						Set tWant(tParam, tTarget) = $ListBuild("jsonLink", tOrigin)
					}
				} Else {
					Set tUnres(tParam, tUrl _ $Select(tVer = "": "", 1: "|" _ tVer)) = tOutcome
				}
			}
		}
	}

	// --- the patient compartment, from the reference edges just wanted (R2) ----
	// One edge per Patient; via lists every compartment param that reaches it.
	If tType '= "Patient" {
		Kill tVia
		Set tP = ""
		For {
			Set tP = $Order(tWant(tP))
			Quit:tP=""
			Continue:'$Data(..Comp(tType, tP))
			Set tO = "Patient/"
			For {
				Set tO = $Order(tWant(tP, tO))
				Quit:$Extract(tO, 1, 8)'="Patient/"
				Set tVia(tO) = $Get(tVia(tO)) _ $Select($Get(tVia(tO)) = "": "", 1: ",") _ tP
			}
		}
		Set tO = ""
		For {
			Set tO = $Order(tVia(tO), 1, tList)
			Quit:tO=""
			Set tWant(..#COMPARTMENTPRED, tO) = $ListBuild("via", tList)
		}
	}

	// --- diff against the edges the graph holds ------------------------------
	Kill tHave
	Set tRs = ..Query("SELECT edge_id, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?", ..GraphId, pKey)
	Kill tRows
	While tRs.%Next() {
		Set tRows($Increment(tRows)) = $ListBuild(tRs.%GetData(1), tRs.%GetData(2), tRs.%GetData(3), tRs.%GetData(4))
	}
	For tI = 1:1:$Get(tRows) {
		Set tEid = $ListGet(tRows(tI), 1)
		Set tP = $ListGet(tRows(tI), 2)
		Set tO = $ListGet(tRows(tI), 3)
		If $Data(tWant(tP, tO), tDef) {
			Set tHave(tP, tO) = ""
			// Same edge, new provenance (a json link became an index edge, or back).
			Set tQual = ..Qualifier(tDef)
			If $ListGet(tRows(tI), 4) '= tQual {
				Do ..Exec("UPDATE Graph_KG.rdf_edges SET qualifiers = ? WHERE edge_id = ?", tQual, tEid)
			}
			// The row survived; make sure the adjacency did too.
			If '$Data(^KG("out", ..GKey, pKey, tP, tO)) {
				Do ##class(Graph.KG.EdgeScan).WriteAdjacency(pKey, tP, tO, "1.0", ..GKey)
			}
		} Else {
			Do ..DeleteEdge(tEid, pKey, tP, tO)
		}
	}
	// Adjacency with no row behind it is drift; remove what the repository does not want.
	Kill tDrift
	Set tP = ""
	For {
		Set tP = $Order(^KG("out", ..GKey, pKey, tP))
		Quit:tP=""
		Set tO = ""
		For {
			Set tO = $Order(^KG("out", ..GKey, pKey, tP, tO))
			Quit:tO=""
			Set:'$Data(tWant(tP, tO)) tDrift(tP, tO) = ""
		}
	}
	Set tP = ""
	For {
		Set tP = $Order(tDrift(tP))
		Quit:tP=""
		Set tO = ""
		For {
			Set tO = $Order(tDrift(tP, tO))
			Quit:tO=""
			Do ##class(Graph.KG.EdgeScan).DeleteAdjacency(pKey, tP, tO, ..GKey)
		}
	}
	Set tP = ""
	For {
		Set tP = $Order(tWant(tP))
		Quit:tP=""
		Set tO = ""
		For {
			Set tO = $Order(tWant(tP, tO), 1, tDef)
			Quit:tO=""
			Continue:$Data(tHave(tP, tO))
			Do ..EnsureNode(tO)
			Set tQual = ..Qualifier(tDef)
			Do ..Exec("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)", pKey, tP, tO, tQual, ..GraphId)
			Do ##class(Graph.KG.EdgeScan).WriteAdjacency(pKey, tP, tO, "1.0", ..GKey)
			Set:tP=..#COMPARTMENTPRED ..Interp("added") = $Get(..Interp("added")) + 1
		}
	}

	// --- category and meta_profile, and the labels category implies (R8) -------
	Do ..SyncProps(pKey, tType)

	// --- the unresolved rows this key owns ------------------------------------
	Do ..Exec("DELETE FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND source = ?", ..GraphId, pKey)
	Set tP = ""
	For {
		Set tP = $Order(tUnres(tP))
		Quit:tP=""
		Set tT = ""
		For {
			Set tT = $Order(tUnres(tP, tT), 1, tReason)
			Quit:tT=""
			Do ..Exec("INSERT INTO Graph_KG.fhir_unresolved (graph_id, source, param, target, reason) VALUES (?, ?, ?, ?, ?)", ..GraphId, pKey, tP, tT, tReason)
		}
	}

	// --- the canonical refs this key carries ----------------------------------
	Do ..Exec("DELETE FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ? AND source = ?", ..GraphId, pKey)
	Set tP = ""
	For {
		Set tP = $Order(tRefs(tP))
		Quit:tP=""
		Set tUrl = ""
		For {
			Set tUrl = $Order(tRefs(tP, tUrl))
			Quit:tUrl=""
			Set tV = ""
			For {
				Set tV = $Order(tRefs(tP, tUrl, tV), 1, tOrigin)
				Quit:tV=""
				Do ..Exec("INSERT INTO Graph_KG.fhir_canonical_refs (graph_id, source, param, url, version, origin, kind) VALUES (?, ?, ?, ?, ?, ?, 'canonical')", ..GraphId, pKey, tP, tUrl, $Select(tV = $Char(0): "", 1: tV), tOrigin)
			}
		}
	}
	Set tP = ""
	For {
		Set tP = $Order(tXRefs(tP))
		Quit:tP=""
		Set tRef = ""
		For {
			Set tRef = $Order(tXRefs(tP, tRef), 1, tOrigin)
			Quit:tRef=""
			Do ..Exec("INSERT INTO Graph_KG.fhir_canonical_refs (graph_id, source, param, url, version, origin, kind) VALUES (?, ?, ?, ?, NULL, ?, 'reference')", ..GraphId, pKey, tP, tRef, tOrigin)
		}
	}

	// --- references that were waiting for this key ----------------------------
	// Only key references wait on a key; canonical reasons wait on a url (RunBatch).
	If pFollow {
		Kill tWaiting
		Set tRs = ..Query("SELECT DISTINCT source FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND target = ? AND reason IN ('missing', 'deleted')", ..GraphId, pKey)
		While tRs.%Next() { Set tWaiting(tRs.%GetData(1)) = "" }
		Set tS = ""
		For {
			Set tS = $Order(tWaiting(tS))
			Quit:tS=""
			Continue:$Data(..Done(tS))
			Do ..ResyncKey(tS, 0)
		}
	}
}

/// An edge's qualifiers from its tWant value, $lb(key, value). A `via` value is a
/// comma list, written as a JSON array.
ClassMethod Qualifier(pDef As %List) As %String [ Private ]
{
	Set tQual = {}
	If $ListGet(pDef, 1) = "via" {
		Set tArr = []
		Set tList = $ListFromString($ListGet(pDef, 2), ",")
		For tI = 1:1:$ListLength(tList) { Do tArr.%Push($ListGet(tList, tI)) }
		Do tQual.%Set("via", tArr)
	} Else {
		Do tQual.%Set($ListGet(pDef, 1), $ListGet(pDef, 2))
	}
	Return tQual.%ToJSON()
}

/// The relative reference of a body Reference, as the index would store it: an
/// absolute url on one of the repository's endpoints loses the endpoint.
Method BodyRel(pRef As %String) As %String
{
	Quit:pRef'["://" pRef
	Set tE = ""
	For {
		Set tE = $Order(..Endpoints(tE))
		Quit:tE=""
		If pRef [ (tE _ "/") {
			Return $Piece(pRef, tE _ "/", 2, *)
		}
	}
	Return ""
}

/// The resource body as a dynamic object, from ResourceString or, for a large one,
/// ResourceStream. "" when the key has no current row.
Method ReadResource(pKey As %String) As %DynamicObject
{
	Set tRs = ..Query("SELECT ResourceString, ResourceStream FROM " _ ..RTable() _ " WHERE Key = ?", pKey)
	Return:'tRs.%Next() ""
	Try {
		If tRs.%GetData(1) '= "" {
			Set tBody = ##class(%DynamicAbstractObject).%FromJSON(tRs.%GetData(1))
		} Else {
			Set tStream = ##class(%Stream.Object).%Open(tRs.%GetData(2))
			Set tBody = $Select($IsObject(tStream): ##class(%DynamicAbstractObject).%FromJSON(tStream), 1: "")
		}
	} Catch {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "resource " _ pKey _ ": body is not JSON"))
	}
	Return tBody
}

/// Remove a node that is deleted or absent in the repository (FR-009).
///
/// Each incoming edge becomes an unresolved row with pReason, so the edge comes back
/// when the target does. The node's vectors go from every table that holds them:
/// an embedding row requires its node (4.0.0 FK).
Method DropNode(pKey As %String, pReason As %String)
{
	// Incoming edges become unresolved references with the reason.
	Kill tRows
	Set tRs = ..Query("SELECT edge_id, s, p FROM Graph_KG.rdf_edges WHERE graph_id = ? AND o_id = ?", ..GraphId, pKey)
	While tRs.%Next() {
		Set tRows($Increment(tRows)) = $ListBuild(tRs.%GetData(1), tRs.%GetData(2), tRs.%GetData(3))
	}
	For tI = 1:1:$Get(tRows) {
		Set tS = $ListGet(tRows(tI), 2)
		Set tP = $ListGet(tRows(tI), 3)
		// A compartment edge just goes: the reference edge beside it records the reason.
		If tP = ..#COMPARTMENTPRED {
			Do ..DeleteEdge($ListGet(tRows(tI), 1), tS, tP, pKey)
			Continue
		}
		// A canonical edge's source resyncs in the batch's second pass, which records
		// the canonical reason; `deleted` is for references to keys.
		// A json-link edge's source resyncs too (Pending): only its own rule knows
		// whether the link was a canonical or a Reference.
		Set tCol = $Get(..Cols($Piece(tS, "/", 1), tP))
		Set tCanon = (tCol = "") || ($ListGet(tCol, 5) = "canonical")
		Set:(tCol="")&&(tS'=pKey) ..Pending(tS) = ""
		If (tS '= pKey) && 'tCanon {
			Do ..Exec("DELETE FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND source = ? AND param = ? AND target = ?", ..GraphId, tS, tP, pKey)
			Do ..Exec("INSERT INTO Graph_KG.fhir_unresolved (graph_id, source, param, target, reason) VALUES (?, ?, ?, ?, ?)", ..GraphId, tS, tP, pKey, pReason)
		}
		Do ..DeleteEdge($ListGet(tRows(tI), 1), tS, tP, pKey)
	}
	Do ..Exec("UPDATE Graph_KG.fhir_unresolved SET reason = ? WHERE graph_id = ? AND target = ? AND reason IN ('missing', 'deleted')", pReason, ..GraphId, pKey)

	// Outgoing edges.
	Kill tRows
	Set tRs = ..Query("SELECT edge_id, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?", ..GraphId, pKey)
	While tRs.%Next() {
		Set tRows($Increment(tRows)) = $ListBuild(tRs.%GetData(1), tRs.%GetData(2), tRs.%GetData(3))
	}
	For tI = 1:1:$Get(tRows) {
		Do ..DeleteEdge($ListGet(tRows(tI), 1), pKey, $ListGet(tRows(tI), 2), $ListGet(tRows(tI), 3))
	}
	// Adjacency left without rows, in either direction.
	Kill tDrift
	Set tP = ""
	For {
		Set tP = $Order(^KG("out", ..GKey, pKey, tP))
		Quit:tP=""
		Set tO = ""
		For {
			Set tO = $Order(^KG("out", ..GKey, pKey, tP, tO))
			Quit:tO=""
			Set tDrift($Increment(tDrift)) = $ListBuild(pKey, tP, tO)
		}
	}
	Set tP = ""
	For {
		Set tP = $Order(^KG("in", ..GKey, pKey, tP))
		Quit:tP=""
		Set tS = ""
		For {
			Set tS = $Order(^KG("in", ..GKey, pKey, tP, tS))
			Quit:tS=""
			Set tDrift($Increment(tDrift)) = $ListBuild(tS, tP, pKey)
		}
	}
	For tI = 1:1:$Get(tDrift) {
		Do ##class(Graph.KG.EdgeScan).DeleteAdjacency($ListGet(tDrift(tI), 1), $ListGet(tDrift(tI), 2), $ListGet(tDrift(tI), 3), ..GKey)
	}

	Do ..Exec("DELETE FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND source = ?", ..GraphId, pKey)
	Do ..Exec("DELETE FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ? AND source = ?", ..GraphId, pKey)
	Do ..Exec("DELETE FROM Graph_KG.fhir_definitions WHERE graph_id = ? AND rsrc_key = ?", ..GraphId, pKey)
	// Category labels too, not only the type label. SyncProps writes them only beside
	// the type label, so a key without one (a Rebuild's deleted history) has none.
	If $Data(^KG("label", ..GKey, $Piece(pKey, "/", 1), pKey)) {
		Set tRs = ..Query("SELECT label FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ?", ..GraphId, pKey)
		While tRs.%Next() { Kill ^KG("label", ..GKey, tRs.%GetData(1), pKey) }
	}
	Do ..Exec("DELETE FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ?", ..GraphId, pKey)
	Do ..Exec("DELETE FROM Graph_KG.rdf_props WHERE graph_id = ? AND s = ?", ..GraphId, pKey)
	Do ..Exec("DELETE FROM Graph_KG.docs WHERE graph_id = ? AND id = ?", ..GraphId, pKey)
	Do ..Exec("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE graph_id = ? AND node_id = ?", ..GraphId, pKey)
	Do ..Exec("DELETE FROM Graph_KG.kg_NodeEmbeddings_optimized WHERE graph_id = ? AND node_id = ?", ..GraphId, pKey)
	Do ..LoadRoutes()
	Set tT = ""
	For {
		Set tT = $Order(..Routes("node", tT))
		Quit:tT=""
		Do ..ExecTolerant("DELETE FROM Graph_KG." _ tT _ " WHERE graph_id = ? AND node_id = ?", ..GraphId, pKey)
	}
	Kill ^KG("label", ..GKey, $Piece(pKey, "/", 1), pKey)
	Kill ^KG("prop", ..GKey, pKey)
	Do ..Exec("DELETE FROM Graph_KG.nodes WHERE graph_id = ? AND node_id = ?", ..GraphId, pKey)
	Kill ..Known(pKey)
}

/// Write pKey's category and meta_profile rows from the search index, and make its
/// labels {type} + labels(category), less R4 type names. No resource read.
Method SyncProps(pKey As %String, pType As %String) [ Private ]
{
	Kill tWant
	Set tWant(pType) = ""
	For tProp = "category", "meta_profile" {
		Kill tVals
		If $Data(..Props(pType, tProp), tDef) {
			Set tTable = $ListGet(tDef, 1)
			Set tCol = $ListGet(tDef, 2)
			If tProp = "meta_profile" {
				// No DISTINCT: on an SQLUPPER column it returns the upper-cased form.
				Set tRs = ..Query("SELECT value FROM " _ ..Q(..SSchema _ "_" _ tTable) _ "." _ ..Q(tCol) _ " WHERE Key = ?", pKey)
				While tRs.%Next() { Set:tRs.%GetData(1)'="" tVals(tRs.%GetData(1)) = "" }
			} Else {
				If $ListGet(tDef, 3) {
					Set tSql = "SELECT value_System, value_Value FROM " _ ..Q(..SSchema _ "_" _ tTable) _ "." _ ..Q(tCol) _ " WHERE Key = ?"
				} Else {
					Set tSql = "SELECT " _ ..Q(tCol _ "_System") _ ", " _ ..Q(tCol _ "_Value") _ " FROM " _ ..Q(..SSchema) _ "." _ ..Q(tTable) _ " WHERE Key = ?"
				}
				Set tRs = ..Query(tSql, pKey)
				While tRs.%Next() {
					Set tSys = tRs.%GetData(1)
					Set tCode = tRs.%GetData(2)
					Continue:tCode=""
					Set tVals(tSys _ "|" _ tCode) = ""
					Continue:(tSys="")||'$Data(..CatSys(tSys))
					Set tLab = ..Pascal(tCode)
					Set:(tLab'="")&&'$Data(..R4Types(tLab)) tWant(tLab) = ""
				}
			}
		}
		Set tArr = []
		Set tV = ""
		For {
			Set tV = $Order(tVals(tV))
			Quit:tV=""
			Do tArr.%Push(tV)
		}
		Set tJson = $Select(tArr.%Size(): tArr.%ToJSON(), 1: "")
		Set tHave = ..Scalar("SELECT val FROM Graph_KG.rdf_props WHERE graph_id = ? AND s = ? AND ""key"" = ?", ..GraphId, pKey, tProp)
		Continue:tHave=tJson
		If tJson = "" {
			Do ..Exec("DELETE FROM Graph_KG.rdf_props WHERE graph_id = ? AND s = ? AND ""key"" = ?", ..GraphId, pKey, tProp)
			Kill ^KG("prop", ..GKey, pKey, tProp)
		} Else {
			If tHave = "" {
				Do ..Exec("INSERT INTO Graph_KG.rdf_props (graph_id, s, ""key"", val) VALUES (?, ?, ?, ?)", ..GraphId, pKey, tProp, tJson)
			} Else {
				Do ..Exec("UPDATE Graph_KG.rdf_props SET val = ? WHERE graph_id = ? AND s = ? AND ""key"" = ?", tJson, ..GraphId, pKey, tProp)
			}
			Set ^KG("prop", ..GKey, pKey, tProp) = tJson
		}
		Set ..Interp(tProp) = $Get(..Interp(tProp)) + 1
	}
	// Labels: the type label is in tWant, so it is never removed.
	Kill tHave
	Set tRs = ..Query("SELECT label FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ?", ..GraphId, pKey)
	While tRs.%Next() { Set tHave(tRs.%GetData(1)) = "" }
	Set tL = ""
	For {
		Set tL = $Order(tHave(tL))
		Quit:tL=""
		Continue:$Data(tWant(tL))
		Do ..Exec("DELETE FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ? AND label = ?", ..GraphId, pKey, tL)
		Kill ^KG("label", ..GKey, tL, pKey)
	}
	Set tL = ""
	For {
		Set tL = $Order(tWant(tL))
		Quit:tL=""
		Set ^KG("label", ..GKey, tL, pKey) = ""
		Continue:$Data(tHave(tL))
		Do ..Exec("INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)", ..GraphId, pKey, tL)
	}
}

/// A category code as a label: vital-signs -> VitalSigns, split on - and _.
ClassMethod Pascal(pCode As %String) As %String
{
	Set tOut = ""
	Set tCode = $Translate(pCode, "_", "-")
	For tI = 1:1:$Length(tCode, "-") {
		Set tP = $Piece(tCode, "-", tI)
		Set tOut = tOut _ $ZConvert($Extract(tP), "U") _ $Extract(tP, 2, *)
	}
	Return tOut
}

/// Delete one edge row and everything that hangs off it.
Method DeleteEdge(pEdgeId As %Integer, pS As %String, pP As %String, pO As %String) [ Private ]
{
	Do ..Exec("DELETE FROM Graph_KG.rdf_reifications WHERE edge_id = ?", pEdgeId)
	Do ..Exec("DELETE FROM Graph_KG.kg_EdgeEmbeddings WHERE graph_id = ? AND s = ? AND p = ? AND o_id = ?", ..GraphId, pS, pP, pO)
	Do ..LoadRoutes()
	Set tT = ""
	For {
		Set tT = $Order(..Routes("edge", tT))
		Quit:tT=""
		Do ..ExecTolerant("DELETE FROM Graph_KG." _ tT _ " WHERE graph_id = ? AND s = ? AND p = ? AND o_id = ?", ..GraphId, pS, pP, pO)
	}
	Do ..Exec("DELETE FROM Graph_KG.rdf_edges WHERE edge_id = ?", pEdgeId)
	Do ##class(Graph.KG.EdgeScan).DeleteAdjacency(pS, pP, pO, ..GKey)
	Set:pP=..#COMPARTMENTPRED ..Interp("removed") = $Get(..Interp("removed")) + 1
}

/// Ensure a node row and its resource-type label exist in this graph.
Method EnsureNode(pKey As %String) [ Private ]
{
	Quit:$Data(..Known(pKey))
	Set tType = $Piece(pKey, "/", 1)
	If '..Scalar("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ? AND node_id = ?", ..GraphId, pKey) {
		Do ..Exec("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", pKey, ..GraphId)
	}
	If '..Scalar("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ? AND label = ?", ..GraphId, pKey, tType) {
		Do ..Exec("INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)", ..GraphId, pKey, tType)
	}
	// Cypher reads n.id from an rdf_props row, as create_node writes one for every node.
	If '..Scalar("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE graph_id = ? AND s = ? AND ""key"" = 'id'", ..GraphId, pKey) {
		Do ..Exec("INSERT INTO Graph_KG.rdf_props (graph_id, s, ""key"", val) VALUES (?, ?, 'id', ?)", ..GraphId, pKey, pKey)
	}
	Set ^KG("label", ..GKey, tType, pKey) = ""
	Set ..Known(pKey) = ""
}

/// Classify one indexed reference (FR-005, spec fact 8).
///
/// Returns "skip" (contained, urn, or nothing to resolve), "external" with pTarget the
/// original reference, or "local" with pTarget the key it names.
Method Classify(pRef As %String, pRel As %String, Output pTarget As %String) As %String
{
	Set pTarget = ""
	If $Extract(pRef) = "#" { Return "skip" }
	If $ZConvert($Extract(pRef, 1, 4), "L") = "urn:" { Return "skip" }
	If pRef [ "://" {
		Set tLocal = 0
		Set tE = ""
		For {
			Set tE = $Order(..Endpoints(tE))
			Quit:tE=""
			If pRef [ (tE _ "/") {
				Set tLocal = 1
				Quit
			}
		}
		If 'tLocal {
			Set pTarget = $Extract(pRef, 1, 512)
			Return "external"
		}
	}
	Set tKey = pRel
	If tKey [ "/_history/" { Set tKey = $Piece(tKey, "/_history/", 1) }
	If ($Length(tKey, "/") '= 2) || ($Piece(tKey, "/", 1) = "") || ($Piece(tKey, "/", 2) = "") { Return "skip" }
	Set pTarget = tKey
	Return "local"
}

/// 1 live, -1 deleted, 0 absent, per Rsrc.
Method Liveness(pKey As %String) As %Integer
{
	If $Data(..Live(pKey), tV) { Return tV }
	Set tRs = ..Query("SELECT Deleted FROM " _ ..RTable() _ " WHERE Key = ?", pKey)
	Set tV = $Select('tRs.%Next(): 0, +tRs.%GetData(1): -1, 1: 1)
	Set ..Live(pKey) = tV
	Return tV
}

/// The routed embedding tables in the catalog. Every one is visited with a
/// graph_id predicate, so a route of another graph loses nothing.
Method LoadRoutes() [ Private ]
{
	Quit:..RoutesLoaded
	Set tRs = ..Query("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' AND ((TABLE_NAME %STARTSWITH 'kg_emb_') OR (TABLE_NAME %STARTSWITH 'kg_eemb_'))")
	While tRs.%Next() {
		Set tT = tRs.%GetData(1)
		Set ..Routes($Select($Extract(tT, 1, 8) = "kg_eemb_": "edge", 1: "node"), tT) = ""
	}
	Set ..RoutesLoaded = 1
}

/// Edge counts per Type.param, with every denylisted param reported at 0 (US1-5/6).
Method Counts() As %DynamicObject
{
	Set tParams = {}
	Set tRs = ..Query("SELECT s, p FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p <> ?", ..GraphId, ..#COMPARTMENTPRED)
	Kill tN
	While tRs.%Next() {
		Set tK = $Piece(tRs.%GetData(1), "/", 1) _ "." _ tRs.%GetData(2)
		Set tN(tK) = $Get(tN(tK)) + 1
	}
	Set tD = ""
	For {
		Set tD = $Order(..Deny(tD))
		Quit:tD=""
		Set tN(tD) = 0
	}
	Set tEdges = 0
	Set tK = ""
	For {
		Set tK = $Order(tN(tK), 1, tV)
		Quit:tK=""
		Do tParams.%Set(tK, tV, "number")
		Set tEdges = tEdges + tV
	}
	Set tRes = {"params": (tParams)}
	Do tRes.%Set("nodes", ..Scalar("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", ..GraphId), "number")
	Do tRes.%Set("edges", tEdges, "number")
	Do tRes.%Set("unresolved", ..UnresolvedCounts())
	Return tRes
}

Method UnresolvedCounts() As %DynamicObject
{
	Set tRes = {"external": 0, "missing": 0, "deleted": 0, "version-not-found": 0, "ambiguous": 0, "no-definition": 0}
	Set tRs = ..Query("SELECT reason, COUNT(*) FROM Graph_KG.fhir_unresolved WHERE graph_id = ? GROUP BY reason", ..GraphId)
	While tRs.%Next() {
		Do tRes.%Set($ZConvert(tRs.%GetData(1), "L"), +tRs.%GetData(2), "number")
	}
	Return tRes
}

// ================================================================= link rules
//
// Spec 232. These methods run no SQL and change no state. Each public one is a JSON
// wrapper over a private core that the sync calls with local arrays, so the tests
// over TCP exercise the code the sync runs.

/// The repository index keeps this many characters of a canonical url or version
/// (value_Reference, value_Version and url are MAXLEN 220 with TRUNCATE=1).
Parameter CANONICALMAX = 220;

/// The extension every default json_links list ends with.
Parameter CQFLIBRARY = "http://hl7.org/fhir/StructureDefinition/cqf-library";

/// Bounds on the walk for extension: links, per resource (research R6).
Parameter WALKDEPTH = 64;

Parameter WALKMATCHES = 10000;

/// Validate a json_links array (research R5).
/// Returns {"status":"ok","links":[...]} or the error reply.
ClassMethod ParseJsonLinks(pJson As %String) As %String
{
	Set tErr = ..ParseLinks(pJson, .tLinks)
	If tErr '= "" {
		Return {"status": "error", "error": (tErr)}.%ToJSON()
	}
	Set tOut = []
	For tI = 1:1:tLinks {
		Do tOut.%Push($List(tLinks(tI), 1))
	}
	Return {"status": "ok", "links": (tOut)}.%ToJSON()
}

/// pLinks = count; pLinks(n) = $lb(entry, kind, typeOrUrl, predicate), kind being
/// "element" or "extension". Returns "" or the error message.
ClassMethod ParseLinks(pJson As %String, Output pLinks) As %String [ Private ]
{
	Kill pLinks
	Set pLinks = 0
	Set tNotArray = "json links must be a JSON array of strings"
	Try {
		Set tArr = ##class(%DynamicAbstractObject).%FromJSON(pJson)
	} Catch {
		Set tArr = ""
	}
	If '$IsObject(tArr) || ($ClassName(tArr) '= "%Library.DynamicArray") {
		Return tNotArray
	}
	For tI = 0:1:(tArr.%Size() - 1) {
		If tArr.%GetTypeOf(tI) '= "string" {
			Return tNotArray
		}
	}
	For tI = 0:1:(tArr.%Size() - 1) {
		Set tEntry = tArr.%Get(tI)
		Set tBad = "json link '" _ tEntry _ "' is not 'Type.element' or 'extension:<url>'"
		If $Extract(tEntry, 1, 10) = "extension:" {
			Set tUrl = $Extract(tEntry, 11, *)
			If '$Match(tUrl, "(https?://[^\s/]\S*|urn:\S+)") {
				Return tBad
			}
			Set tPred = $Piece($Piece(tUrl, "/", *), ":", *)
			Set tKind = "extension", tWhat = tUrl
		} Else {
			If '$Match(tEntry, "[A-Z][A-Za-z]+\.[a-z][A-Za-z0-9]*") {
				Return tBad
			}
			Set tPred = $Piece(tEntry, ".", 2)
			Set tKind = "element", tWhat = $Piece(tEntry, ".", 1)
		}
		If tPred = "" {
			Return tBad
		}
		If $Data(tSeen(tEntry)) {
			Return "json link '" _ tEntry _ "' is listed twice"
		}
		Set tSeen(tEntry) = ""
		// Element entries on different types may share a predicate; an extension
		// applies to every type, so it may share one with nothing.
		Set tOther = ""
		For {
			Set tOther = $Order(tByPred(tPred, tOther), 1, tOtherKind)
			Quit:tOther=""
			If (tKind = "extension") || (tOtherKind = "extension") {
				Return "json links '" _ tOther _ "' and '" _ tEntry _ "' both give predicate '" _ tPred _ "'"
			}
		}
		Set tByPred(tPred, tEntry) = tKind
		Set pLinks = pLinks + 1
		Set pLinks(pLinks) = $ListBuild(tEntry, tKind, tWhat, tPred)
	}
	Return ""
}

/// The resolution rule (research R7) over candidates given as JSON,
/// {"<key>": "<version>" or null}. Returns {"outcome": ..., "target": ...}.
ClassMethod Resolve(pUrl As %String, pVersion As %String, pCandJson As %String) As %String
{
	Try {
		Set tCands = ##class(%DynamicObject).%FromJSON(pCandJson)
		Set tIter = tCands.%GetIterator()
		While tIter.%GetNext(.tKey, .tVer) {
			Set tCand(tKey) = $Select(tCands.%GetTypeOf(tKey) = "null": "", 1: tVer)
		}
		Set tOutcome = ..ResolveCands(pVersion, .tCand, .tTarget)
		Set tOut = {"outcome": (tOutcome), "target": (tTarget)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// pCand(key) = version declared, "" for none. A versioned reference needs exactly
/// one candidate at that exact version; a versionless one needs exactly one
/// candidate. Returns resolved, version-not-found, ambiguous or no-definition.
ClassMethod ResolveCands(pVersion As %String, ByRef pCand, Output pTarget As %String) As %String [ Private ]
{
	Set pTarget = ""
	If $Order(pCand("")) = "" {
		Return "no-definition"
	}
	Set tHits = 0, tKey = ""
	For {
		Set tKey = $Order(pCand(tKey), 1, tVer)
		Quit:tKey=""
		Continue:(pVersion'="")&&(tVer'=pVersion)
		Set tHits = tHits + 1, tHit = tKey
	}
	If tHits = 1 {
		Set pTarget = tHit
		Return "resolved"
	}
	Return $Select(tHits > 1: "ambiguous", 1: "version-not-found")
}

/// A canonical value from the body split as the indexer's IndexCanonical splits it:
/// on the first "|", each part cut to 220 characters. Returns {"url", "version"}, or
/// "" for a value the index skips (empty, or a contained "#" reference).
ClassMethod SplitCanonical(pValue As %String) As %String
{
	If '..SplitValue(pValue, .tUrl, .tVer) {
		Return ""
	}
	Set tOut = {"url": (tUrl)}
	If tVer = "" {
		Do tOut.%Set("version", "", "null")
	} Else {
		Do tOut.%Set("version", tVer)
	}
	Return tOut.%ToJSON()
}

ClassMethod SplitValue(pValue As %String, Output pUrl As %String, Output pVersion As %String) As %Boolean [ Private ]
{
	Set pUrl = $Extract($Piece(pValue, "|", 1), 1, ..#CANONICALMAX)
	Set pVersion = $Extract($Piece(pValue, "|", 2, *), 1, ..#CANONICALMAX)
	Return (pUrl '= "") && ($Extract(pValue) '= "#")
}

/// One canonical as a comparable string, url or url|version, after SplitValue.
/// "" when the value is skipped.
ClassMethod CanonKey(pValue As %String) As %String [ Private ]
{
	If '..SplitValue(pValue, .tUrl, .tVer) {
		Return ""
	}
	Return tUrl _ $Select(tVer = "": "", 1: "|" _ tVer)
}

/// The library split (research R4): which index values of a split column stay
/// depends-on edges. Each argument is a JSON array of canonical strings; the index
/// values are url or url|version. Returns {"keep": [...]}.
ClassMethod SplitKeep(pIndexUrlsJson As %String, pLibraryJson As %String, pRelatedJson As %String) As %String
{
	Try {
		Set tIndex = ##class(%DynamicArray).%FromJSON(pIndexUrlsJson)
		Set tLib = ##class(%DynamicArray).%FromJSON(pLibraryJson)
		Set tRa = ##class(%DynamicArray).%FromJSON(pRelatedJson)
		For tI = 0:1:(tLib.%Size() - 1) {
			Set tC = ..CanonKey(tLib.%Get(tI))
			Set:tC'="" tInLib(tC) = ""
		}
		For tI = 0:1:(tRa.%Size() - 1) {
			Set tC = ..CanonKey(tRa.%Get(tI))
			Set:tC'="" tInRa(tC) = ""
		}
		Set tKeep = []
		For tI = 0:1:(tIndex.%Size() - 1) {
			Set tV = tIndex.%Get(tI)
			Do:..KeepIndex(..CanonKey(tV), .tInLib, .tInRa) tKeep.%Push(tV)
		}
		Set tOut = {"keep": (tKeep)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// An index value stays a depends-on edge when relatedArtifact names it, or when
/// library does not: a url in both gets both edges, and a url in neither came
/// through a branch this class does not read, so it is not taken away.
ClassMethod KeepIndex(pCanon As %String, ByRef pInLib, ByRef pInRa) As %Boolean [ Private ]
{
	Return ($Data(pInRa(pCanon)) > 0) || ('$Data(pInLib(pCanon)))
}

/// Apply json links to one resource (research R6). pLinksJson is a validated
/// json_links array. Returns {"links": [{"entry", "param", "value", "kind"}, ...]}.
ClassMethod ExtractLinks(pResourceJson As %String, pLinksJson As %String) As %String
{
	Try {
		Set tErr = ..ParseLinks(pLinksJson, .tLinks)
		If tErr '= "" {
			$$$ThrowStatus($$$ERROR($$$GeneralError, tErr))
		}
		Set tRes = ##class(%DynamicObject).%FromJSON(pResourceJson)
		Do ..Extract(tRes, .tLinks, .tFound)
		Set tOut = []
		For tI = 1:1:tFound {
			Set tL = tFound(tI)
			Do tOut.%Push({"entry": ($List(tL, 1)), "param": ($List(tL, 2)), "value": ($List(tL, 3)), "kind": ($List(tL, 4))})
		}
		Set tOut = {"links": (tOut)}.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// pFound = count; pFound(n) = $lb(entry, predicate, value, kind), kind canonical or
/// reference. Throws when the walk passes the depth or match bound.
ClassMethod Extract(pRes As %DynamicObject, ByRef pLinks, Output pFound) [ Private ]
{
	Kill pFound
	Set pFound = 0
	Set tType = pRes.resourceType
	Set tWho = tType _ "/" _ pRes.id
	For tI = 1:1:$Get(pLinks) {
		Set $ListBuild(tEntry, tKind, tWhat, tPred) = pLinks(tI)
		If tKind = "extension" {
			Set tExt(tWhat) = $ListBuild(tEntry, tPred)
			Continue
		}
		Continue:tWhat'=tType
		Set tKindOf = pRes.%GetTypeOf(tPred)
		If tKindOf = "string" {
			Set pFound = pFound + 1
			Set pFound(pFound) = $ListBuild(tEntry, tPred, pRes.%Get(tPred), "canonical")
		} ElseIf tKindOf = "array" {
			Set tArr = pRes.%Get(tPred)
			For tJ = 0:1:(tArr.%Size() - 1) {
				Continue:tArr.%GetTypeOf(tJ)'="string"
				Set pFound = pFound + 1
				Set pFound(pFound) = $ListBuild(tEntry, tPred, tArr.%Get(tJ), "canonical")
			}
		}
	}
	If $Data(tExt) {
		Do ..Walk(pRes, 0, 0, .tExt, .pFound, tWho)
	}
	If pFound > ..#WALKMATCHES {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "resource " _ tWho _ ": more than " _ ..#WALKMATCHES _ " json link matches"))
	}
}

/// Depth-first over every object and array. pInExt: pNode is a member of an
/// extension or modifierExtension array, so it is a candidate for pExt(url).
ClassMethod Walk(pNode As %DynamicAbstractObject, pDepth As %Integer, pInExt As %Boolean, ByRef pExt, ByRef pFound, pWho As %String) [ Private ]
{
	If pDepth > ..#WALKDEPTH {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "resource " _ pWho _ ": json link walk deeper than " _ ..#WALKDEPTH _ " levels"))
	}
	If pFound > ..#WALKMATCHES {
		Return
	}
	Set tIsArray = ($ClassName(pNode) = "%Library.DynamicArray")
	If 'tIsArray && pInExt && (pNode.%GetTypeOf("url") = "string") && $Data(pExt(pNode.url), tHit) {
		If pNode.%GetTypeOf("valueCanonical") = "string" {
			Set pFound = pFound + 1
			Set pFound(pFound) = $ListBuild($List(tHit, 1), $List(tHit, 2), pNode.valueCanonical, "canonical")
		} ElseIf (pNode.%GetTypeOf("valueReference") = "object") && (pNode.valueReference.%GetTypeOf("reference") = "string") {
			Set pFound = pFound + 1
			Set pFound(pFound) = $ListBuild($List(tHit, 1), $List(tHit, 2), pNode.valueReference.reference, "reference")
		}
	}
	Set tIter = pNode.%GetIterator()
	While tIter.%GetNext(.tKey, .tVal) {
		Set tKindOf = pNode.%GetTypeOf(tKey)
		Continue:(tKindOf'="object")&&(tKindOf'="array")
		// Only the members of an extension array are extensions: the array itself
		// is walked with the flag set for its children.
		Set tChildInExt = tIsArray && pInExt
		If 'tIsArray && (tKindOf = "array") && ((tKey = "extension") || (tKey = "modifierExtension")) {
			Do ..WalkArray(tVal, pDepth + 1, .pExt, .pFound, pWho)
			Continue
		}
		Do ..Walk(tVal, pDepth + 1, tChildInExt, .pExt, .pFound, pWho)
	}
}

/// An extension array: each member object is a candidate extension.
ClassMethod WalkArray(pArr As %DynamicArray, pDepth As %Integer, ByRef pExt, ByRef pFound, pWho As %String) [ Private ]
{
	If pDepth > ..#WALKDEPTH {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "resource " _ pWho _ ": json link walk deeper than " _ ..#WALKDEPTH _ " levels"))
	}
	For tI = 0:1:(pArr.%Size() - 1) {
		Set tKindOf = pArr.%GetTypeOf(tI)
		Continue:(tKindOf'="object")&&(tKindOf'="array")
		Do ..Walk(pArr.%Get(tI), pDepth + 1, (tKindOf = "object"), .pExt, .pFound, pWho)
	}
}

Method RTable() As %String
{
	Return ..Q(..RSchema) _ ".Rsrc"
}

Method VTable() As %String
{
	Return ..Q(..VSchema) _ ".RsrcVer"
}

Method TableExists(pSchema As %String, pTable As %String) As %Boolean
{
	Return ''..Scalar("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", pSchema, pTable)
}

/// Run a query from the statement cache. Throws on any SQL error.
Method Query(pSql As %String, pArgs...) As %SQL.StatementResult
{
	If $Data(..Stmts(pSql)) {
		Set tStmt = ..Stmts(pSql)
	} Else {
		Set tStmt = ##class(%SQL.Statement).%New()
		$$$ThrowOnError(tStmt.%Prepare(pSql))
		Set ..Stmts(pSql) = tStmt
	}
	Set tRs = tStmt.%Execute(pArgs...)
	If tRs.%SQLCODE < 0 {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "SQLCODE " _ tRs.%SQLCODE _ " " _ tRs.%Message _ " in: " _ pSql))
	}
	Return tRs
}

Method Exec(pSql As %String, pArgs...) As %Integer
{
	Set tRs = ..Query(pSql, pArgs...)
	Return tRs.%ROWCOUNT
}

/// Exec that treats "table not found" (-30) as nothing to do: a route can be dropped
/// between LoadRoutes and its use.
Method ExecTolerant(pSql As %String, pArgs...) As %Integer [ Private ]
{
	Set tStmt = ##class(%SQL.Statement).%New()
	Set tSC = tStmt.%Prepare(pSql)
	If $$$ISERR(tSC) {
		If $System.Status.GetErrorText(tSC) [ "-30" { Return 0 }
		$$$ThrowStatus(tSC)
	}
	Set tRs = tStmt.%Execute(pArgs...)
	If (tRs.%SQLCODE < 0) && (tRs.%SQLCODE '= -30) {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "SQLCODE " _ tRs.%SQLCODE _ " " _ tRs.%Message _ " in: " _ pSql))
	}
	Return tRs.%ROWCOUNT
}

Method Scalar(pSql As %String, pArgs...) As %String
{
	Set tRs = ..Query(pSql, pArgs...)
	Return $Select(tRs.%Next(): tRs.%GetData(1), 1: "")
}

/// A delimited identifier.
ClassMethod Q(pName As %String) As %String
{
	Return """" _ $Replace(pName, """", """""") _ """"
}

ClassMethod Err(pEx As %Exception.AbstractException) As %String [ Private ]
{
	Return {"status": "error", "error": ($System.Status.GetErrorText(pEx.AsStatus()))}.%ToJSON()
}

/// Record a failed build or sync on the registry row, outside any transaction.
ClassMethod RecordError(pGraph As %String, pJson As %String) [ Private ]
{
	Try {
		Set tErr = $Extract(##class(%DynamicAbstractObject).%FromJSON(pJson).error, 1, 4000)
		Set tRs = ##class(%SQL.Statement).%ExecDirect(, "UPDATE Graph_KG.fhir_graphs SET last_error = ? WHERE graph_id = ?", tErr, pGraph)
	} Catch {
	}
}

}
