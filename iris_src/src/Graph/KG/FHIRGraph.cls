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

/// Cols(ResourceType, ParamName) = $lb(table, column, isMulti, definition).
Property Cols As %String [ MultiDimensional ];

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

// ===================================================================== registry

/// Register the namespace's FHIR repository as a graph, or update its settings.
///
/// pEndpoint picks the repository when the namespace has more than one; it is the
/// endpoint path, e.g. /csp/healthshare/ivgfhir/fhir/r4. pDenylistJson is a JSON
/// array of "Type.param" strings. Re-registering keeps the watermarks, so a changed
/// denylist takes effect at the next Rebuild.
ClassMethod Register(pEndpoint As %String = "", pDenylistJson As %String = "[]", pIntervalS As %Integer = 60) As %String
{
	Set tOut = ""
	Try {
		Set tCtx = ..%New()
		If 'tCtx.TableExists("HS_FHIRServer", "Repo") {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "namespace " _ $NAMESPACE _ " has no FHIR repository (HS_FHIRServer.Repo not found)"))
		}
		If 'tCtx.TableExists("Graph_KG", "fhir_graphs") {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "Graph_KG.fhir_graphs not found; run initialize_schema() in " _ $NAMESPACE))
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

		Set tRes = {"status": "registered", "graph_id": (tGraph), "repo_id": (tRepo), "endpoints": (tEndpoints), "denylist": (tDeny), "interval_s": (+pIntervalS)}
		Do tRes.%Set("created", tCreated, "boolean")
		Set tOut = tRes.%ToJSON()
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
		Do tRes.%Set("edges", tCtx.Scalar("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ?", pGraph), "number")
		Do tRes.%Set("unresolved", tCtx.UnresolvedCounts())
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

		// Every current key. Deleted ones go through the same ResyncKey, which drops them.
		Set tKeys = 0
		Kill tBatch
		Set tN = 0
		Set tRs = tCtx.Query("SELECT Key FROM " _ tCtx.RTable() _ " WHERE ID <= ?", tMaxR)
		While tRs.%Next() {
			Set tBatch($Increment(tN)) = tRs.%GetData(1)
			If tN >= ..#BATCHSIZE {
				Set tKeys = tKeys + tCtx.RunBatch(.tBatch, 0, "", "")
				Kill tBatch
				Set tN = 0
			}
		}
		If tN > 0 {
			Set tKeys = tKeys + tCtx.RunBatch(.tBatch, 0, "", "")
			Kill tBatch
		}

		// Nodes the repository no longer has at all (a purge, or a graph built against
		// another state). Collected first: DropNode deletes from the table being read.
		Kill tStale
		Set tRs = tCtx.Query("SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?", pGraph)
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

		Set tRes = {"status": "ok", "graph_id": (pGraph)}
		Do tRes.%Set("keys", tKeys, "number")
		Do tRes.%Set("stale_dropped", tDropped, "number")
		Do tRes.%Set("wm_rsrc", tMaxR, "number")
		Do tRes.%Set("wm_ver", tMaxV, "number")
		Do tRes.%Set("elapsed_ms", $Justify(($ZHorolog - tStart) * 1000, 0, 0), "number")
		Do tRes.%Set("counts", tCounts)
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

		Set tRes = {"status": "ok", "graph_id": (pGraph), "keys": (tKeys)}
		Do tRes.%Set("codes", tNCodes, "number")
		Do tRes.%Set("elapsed_ms", $Justify(($ZHorolog - tStart) * 1000, 0, 1), "number")
		Set tOut = tRes.%ToJSON()
	} Catch tEx {
		Set tOut = ..Err(tEx)
	}
	Return tOut
}

/// The concepts reachable from pIdsJson in at most pHops over ^KG("out") of
/// pConceptGraph, seeds included. pPredicatesJson restricts the predicates followed;
/// empty or [] follows all.
ClassMethod ExpandConcepts(pConceptGraph As %String = "", pIdsJson As %String = "[]", pPredicatesJson As %String = "", pHops As %Integer = 1) As %String
{
	Set tOut = ""
	Try {
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
				Set tP = ""
				For {
					Set tP = $Order(^KG("out", tGk, tS, tP))
					Quit:tP=""
					Continue:('tAllPreds)&&('$Data(tPredOk(tP)))
					Set tO = ""
					For {
						Set tO = $Order(^KG("out", tGk, tS, tP, tO))
						Quit:tO=""
						Continue:$Data(tSeen(tO))
						Set tSeen(tO) = ""
						Set tNext(tO) = ""
						Do tOrder.%Push(tO)
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
	Set tRs = tCtx.Query("SELECT repo_id, rsrc_schema, search_schema, ver_schema, endpoints, denylist, interval_s, wm_rsrc, wm_ver, task_id FROM Graph_KG.fhir_graphs WHERE graph_id = ?", tCtx.GraphId)
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

/// The reference search columns of the repository, minus the denylist (FR-004).
/// CANONICAL and URI columns point at definitions, not resources, and are excluded.
Method LoadColumns()
{
	Kill ..Cols
	Set tRs = ..Query("SELECT ResourceType, ParamName, TableName, ColumnName, isMultiValue, Definition FROM " _ ..#SEARCHCOLUMN _ " WHERE RepoKey = ? AND %UPPER(Type) = 'REFERENCE' AND %UPPER(DataType) = 'REFERENCE'", ..RepoId)
	While tRs.%Next() {
		Set tType = tRs.%GetData(1)
		Set tParam = tRs.%GetData(2)
		Continue:$Data(..Deny(tType _ "." _ tParam))
		Set ..Cols(tType, tParam) = $ListBuild(tRs.%GetData(3), tRs.%GetData(4), +tRs.%GetData(5), tRs.%GetData(6))
	}
}

/// Resync each key of a batch in one transaction, optionally advancing a watermark.
///
/// pFollow also resyncs the sources of unresolved references that now point at a
/// live key, which is how a re-created or late-created target gets its incoming
/// edges back. Rebuild does not need it: it resyncs every source anyway.
///
/// ^IVG.FHIRGraphFault(graph) = "beforecommit" throws just before TCOMMIT. It exists
/// so the E2E test can show the batch and its watermark roll back together.
Method RunBatch(ByRef pBatch, pFollow As %Boolean, pFeed As %String, pWatermark As %Integer) As %Integer
{
	Kill ..Done
	Set tCount = 0
	TSTART
	Set tI = ""
	For {
		Set tI = $Order(pBatch(tI), 1, tKey)
		Quit:tI=""
		Continue:$Data(..Done(tKey))
		Do ..ResyncKey(tKey, pFollow)
		Set tCount = tCount + 1
	}
	If pFeed '= "" {
		Do ..Exec("UPDATE Graph_KG.fhir_graphs SET " _ pFeed _ " = ? WHERE graph_id = ?", pWatermark, ..GraphId)
	}
	If $Get(^IVG.FHIRGraphFault(..GraphId)) = "beforecommit" {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "injected fault before commit (^IVG.FHIRGraphFault)"))
	}
	TCOMMIT
	If pFeed = "wm_rsrc" { Set ..WmRsrc = pWatermark }
	If pFeed = "wm_ver" { Set ..WmVer = pWatermark }
	Return tCount
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

	// --- what the repository says this key references ------------------------
	Kill tWant, tUnres
	Set tParam = ""
	For {
		Set tParam = $Order(..Cols(tType, tParam), 1, tCol)
		Quit:tParam=""
		Set tTable = $ListGet(tCol, 1)
		Set tColumn = $ListGet(tCol, 2)
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
				Set tWant(tParam, tTarget) = $ListGet(tCol, 4)
			} Else {
				Set tUnres(tParam, tTarget) = $Select(tTLive = -1: "deleted", 1: "missing")
			}
		}
	}

	// --- diff against the edges the graph holds ------------------------------
	Kill tHave
	Set tRs = ..Query("SELECT edge_id, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?", ..GraphId, pKey)
	Kill tRows
	While tRs.%Next() {
		Set tRows($Increment(tRows)) = $ListBuild(tRs.%GetData(1), tRs.%GetData(2), tRs.%GetData(3))
	}
	For tI = 1:1:$Get(tRows) {
		Set tEid = $ListGet(tRows(tI), 1)
		Set tP = $ListGet(tRows(tI), 2)
		Set tO = $ListGet(tRows(tI), 3)
		If $Data(tWant(tP, tO)) {
			Set tHave(tP, tO) = ""
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
			Set tQual = {"searchParam": (tDef)}.%ToJSON()
			Do ..Exec("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)", pKey, tP, tO, tQual, ..GraphId)
			Do ##class(Graph.KG.EdgeScan).WriteAdjacency(pKey, tP, tO, "1.0", ..GKey)
		}
	}

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

	// --- references that were waiting for this key ----------------------------
	If pFollow {
		Kill tWaiting
		Set tRs = ..Query("SELECT DISTINCT source FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND target = ? AND reason <> 'external'", ..GraphId, pKey)
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
		If tS '= pKey {
			Do ..Exec("DELETE FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND source = ? AND param = ? AND target = ?", ..GraphId, tS, tP, pKey)
			Do ..Exec("INSERT INTO Graph_KG.fhir_unresolved (graph_id, source, param, target, reason) VALUES (?, ?, ?, ?, ?)", ..GraphId, tS, tP, pKey, pReason)
		}
		Do ..DeleteEdge($ListGet(tRows(tI), 1), tS, tP, pKey)
	}
	Do ..Exec("UPDATE Graph_KG.fhir_unresolved SET reason = ? WHERE graph_id = ? AND target = ? AND reason <> 'external'", pReason, ..GraphId, pKey)

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
	Set tRs = ..Query("SELECT s, p FROM Graph_KG.rdf_edges WHERE graph_id = ?", ..GraphId)
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
	Set tRes = {"external": 0, "missing": 0, "deleted": 0}
	Set tRs = ..Query("SELECT reason, COUNT(*) FROM Graph_KG.fhir_unresolved WHERE graph_id = ? GROUP BY reason", ..GraphId)
	While tRs.%Next() {
		Do tRes.%Set($ZConvert(tRs.%GetData(1), "L"), +tRs.%GetData(2), "number")
	}
	Return tRes
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
