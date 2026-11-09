/// Graph.KG.TemporalPolicy - which predicates of a graph are temporal-only (ADR-0015).
///
/// A temporal edge normally implies a structural edge (ADR-0009): a marked Graph_KG.rdf_edges row, its two
/// endpoint nodes, and the ^KG("out"/"in") entries the row's functional index writes. For a predicate that is
/// never traversed (one observation per span, per sample) those rows are pure cost: measured at 6 to 8 times
/// the write, and one node per target, for ever.
///
///   ^IVG.TemporalStructural(gKey, predicate) = 0     gKey from GraphKey.ForIndex (default 0)
///
/// Absent means structural: the default is ADR-0009 as before. Present means the predicate is TEMPORAL-ONLY in
/// that graph: InsertEdge and BulkInsert write the temporal globals and nothing else, the upgrade backfill
/// skips it, the purge does not look for its rows, and retire_bare_nodes has no endpoint of it to consider.
///
/// This class is the only writer of the registry (Eraser.EraseAll empties it wholesale; EraseGraph keeps it,
/// because it is configuration and not content: a re-ingest after the erase must stay temporal-only). Every
/// reader goes through IsStructural / IsStructuralKey.
///
/// The rows are a projection of the temporal globals, never the reverse, so both directions are reversible:
/// DropRows removes the rows an earlier release or an earlier setting wrote, BackfillRows writes them again
/// from the temporal globals.
Class Graph.KG.TemporalPolicy Extends %RegisteredObject
{

/// 1 when a temporal edge of pPredicate in graph pGraph still implies a structural row (the default).
ClassMethod IsStructural(pGraph As %String = "", pPredicate As %String = "") As %Boolean
{
	Quit ..IsStructuralKey(##class(Graph.KG.GraphKey).ForIndex(pGraph), pPredicate)
}

/// IsStructural for a caller that already holds the graph key: the write path, once per edge.
ClassMethod IsStructuralKey(pKey, pPredicate As %String) As %Boolean
{
	If pPredicate = "" {
		Quit 1
	}
	Quit '$Data(^IVG.TemporalStructural(pKey, pPredicate))
}

/// Register (pEnabled 0) or unregister (pEnabled 1) pPredicate as temporal-only in pGraph. Takes effect for the
/// next write; rows that exist stay until DropRows, rows that are missing stay missing until BackfillRows.
ClassMethod Set(pGraph As %String = "", pPredicate As %String = "", pEnabled As %Boolean = 1) As %Status
{
	Set tSC = $$$OK
	Try {
		If pPredicate = "" {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "a predicate is required"))
		}
		Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
		If pEnabled {
			Kill ^IVG.TemporalStructural(tKey, pPredicate)
		} Else {
			Set ^IVG.TemporalStructural(tKey, pPredicate) = 0
		}
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// Set(), in the shape the Python engine reads: "ok", or "error:<text>".
ClassMethod Apply(pGraph As %String = "", pPredicate As %String = "", pEnabled As %Boolean = 1) As %String
{
	Set tSC = ..Set(pGraph, pPredicate, pEnabled)
	If $$$ISOK(tSC) {
		Quit "ok"
	}
	Quit "error:" _ $System.Status.GetErrorText(tSC)
}

/// The temporal-only predicates of pGraph, as a JSON array, sorted.
ClassMethod List(pGraph As %String = "") As %String
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tOut = []
	Set tP = ""
	For {
		Set tP = $Order(^IVG.TemporalStructural(tKey, tP))
		Quit:tP=""
		Do tOut.%Push(tP)
	}
	Quit tOut.%ToJSON()
}

/// Every graph's temporal-only predicates, as a JSON object {graphName: [predicate, ...]}; the default graph is "".
ClassMethod ListAll() As %String
{
	Set tOut = {}
	Set tKey = ""
	For {
		Set tKey = $Order(^IVG.TemporalStructural(tKey))
		Quit:tKey=""
		Set tArr = []
		Set tP = ""
		For {
			Set tP = $Order(^IVG.TemporalStructural(tKey, tP))
			Quit:tP=""
			Do tArr.%Push(tP)
		}
		Do tOut.%Set($Select(tKey = 0: "", 1: tKey), tArr)
	}
	Quit tOut.%ToJSON()
}

/// Delete the marked structural rows of pPredicate in pGraph, which must be registered temporal-only first, and
/// with pRetireNodes the endpoint nodes that leaves bare (Graph.KG.Eraser.RetireBareNodes). Only rows that still
/// carry the temporal marker (ADR-0009): an edge asserted by create_edge stays, with its endpoints. The row's
/// functional index removes the ^KG("out"/"in") entries and moves the degree generation, so this class writes no
/// adjacency store itself (ADR-0011).
///
/// pBatch      rows per transaction; the list is built first, so a run is resumable and idempotent.
/// pDryRun     1 counts and changes nothing at all (no row, no global, no staging). Two SQL counts, no row read into
///             ObjectScript: the predicate's rows through the indexes on p and graph_id (what could be dropped), and
///             those among them that carry the writers' own marker text (what the run will certainly drop). "rows" is
///             the second, "unchecked" the difference: explicit edges, and rows marked in another JSON layout, which
///             only the real run can tell apart because it reads each of them.
///
/// The real run is two passes. The first selects the ids of the rows that carry the writers' marker text and deletes
/// them by a statement that repeats the test, so an explicit edge that adopts a row between the scan and the delete
/// (create_edge clears the marker) is not deleted. The second reads the remaining rows, which are few, and deletes
/// those whose JSON marks them another way, re-read just before the delete.
///
/// Needs the adjacency functional index (spec 252): without it a temporal edge has a ^KG shadow and no row, and
/// there is nothing here to drop. Returns JSON {"predicate", "rows", "unchecked", "nodes_retired", "batches", "dry_run"}.
ClassMethod DropRows(pGraph As %String = "", pPredicate As %String = "", pBatch As %Integer = 10000, pDryRun As %Boolean = 0, pRetireNodes As %Boolean = 1) As %String
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tName = ##class(Graph.KG.GraphKey).ForName(pGraph)
	Set pBatch = $Select(+pBatch < 1: 10000, 1: +pBatch \ 1)
	If pPredicate = "" {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "a predicate is required"))
	}
	If ..IsStructuralKey(tKey, pPredicate) {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "predicate " _ pPredicate _ " is still structural in this graph: register it temporal-only (set_temporal_structural(predicate, False)) before dropping its rows, or new writes would put them back"))
	}
	If '##class(Graph.KG.Adjacency).Indexed() {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "dropping structural rows needs the adjacency index (idx_edges_adj); without it temporal edges have no rows"))
	}
	Set tM1 = "{""temporal"":true", tM2 = "{""temporal"": true"
	// the default graph is graph_id '' or NULL; a named graph is an equality the indexes answer
	Set tGraphArgs = (tName '= "")
	Set tGraphTest = "graph_id = ?"
	If 'tGraphArgs {
		Set tGraphTest = "COALESCE(graph_id, '') = ''"
	}
	Set tFrom = " FROM Graph_KG.rdf_edges WHERE p = ? AND " _ tGraphTest
	Set tMarker = " AND (qualifiers %STARTSWITH ? OR qualifiers %STARTSWITH ?)"
	Set tRows = 0, tUnchecked = 0, tRetired = 0, tBatches = 0
	Kill ^||IVG.DropRows, ^||IVG.DropNodes
	Try {
		If pDryRun {
			Set tTotal = ..Count("SELECT COUNT(*)" _ tFrom, pPredicate, tName, tGraphArgs)
			Set tRows = ..Count("SELECT COUNT(*)" _ tFrom _ tMarker, pPredicate, tName, tGraphArgs, tM1, tM2)
			Set tUnchecked = tTotal - tRows
		} Else {
			// pass 1: the writers' own marker text, ids only
			Set tRS = ..Open("SELECT edge_id, s, o_id" _ tFrom _ tMarker, pPredicate, tName, tGraphArgs, tM1, tM2)
			While tRS.%Next() {
				Set ^||IVG.DropRows(tRS.%GetData(1)) = $ListBuild(tRS.%GetData(2), tRS.%GetData(3), 1)
			}
			// pass 2: everything else of the predicate, parsed (explicit edges are most of it)
			Set tRS = ..Open("SELECT edge_id, s, o_id, qualifiers" _ tFrom _ " AND NOT (qualifiers %STARTSWITH ? OR qualifiers %STARTSWITH ?)", pPredicate, tName, tGraphArgs, tM1, tM2)
			While tRS.%Next() {
				If ##class(Graph.KG.Adjacency).IsTemporalMarked(tRS.%GetData(4)) {
					Set ^||IVG.DropRows(tRS.%GetData(1)) = $ListBuild(tRS.%GetData(2), tRS.%GetData(3), 0)
				}
			}
			Set tDropped = 0
			If $Data(^||IVG.DropRows) {
				Set tDel = ##class(%SQL.Statement).%New()
				$$$ThrowOnError(tDel.%Prepare("DELETE FROM Graph_KG.rdf_edges WHERE edge_id = ? AND (qualifiers %STARTSWITH ? OR qualifiers %STARTSWITH ?)"))
				Set tId = $Order(^||IVG.DropRows(""))
				Set tN = 0
				Set tTxnLevel = $TLevel
				Try {
					TSTART
					While tId '= "" {
						Set tInfo = ^||IVG.DropRows(tId)
						Set tOk = 1
						If '$List(tInfo, 3) {
							// the marker is laid out otherwise: read the row again, now, and decide on that
							Set tChk = ##class(%SQL.Statement).%ExecDirect(, "SELECT qualifiers FROM Graph_KG.rdf_edges WHERE edge_id = ?", tId)
							Set tOk = tChk.%Next() && ##class(Graph.KG.Adjacency).IsTemporalMarked(tChk.%GetData(1))
							If tOk {
								Set tDel2 = ##class(%SQL.Statement).%ExecDirect(, "DELETE FROM Graph_KG.rdf_edges WHERE edge_id = ?", tId)
								If tDel2.%SQLCODE < 0 {
									$$$ThrowStatus($$$ERROR($$$GeneralError, "DropRows: SQLCODE " _ tDel2.%SQLCODE _ " " _ tDel2.%Message))
								}
							}
						} Else {
							Set tD = tDel.%Execute(tId, tM1, tM2)
							If tD.%SQLCODE < 0 {
								$$$ThrowStatus($$$ERROR($$$GeneralError, "DropRows: SQLCODE " _ tD.%SQLCODE _ " " _ tD.%Message))
							}
							Set tOk = (tD.%ROWCOUNT > 0)
						}
						If tOk {
							Set ^||IVG.DropNodes($List(tInfo, 1)) = "", ^||IVG.DropNodes($List(tInfo, 2)) = ""
							Set tN = tN + 1
						}
						Set tId = $Order(^||IVG.DropRows(tId))
						If (tN >= pBatch) || (tId = "") {
							TCOMMIT
							Set tBatches = tBatches + 1, tDropped = tDropped + tN
							Set tN = 0
							If pRetireNodes {
								Set tRetired = tRetired + ##class(Graph.KG.Eraser).RetireBareNodes(tKey, tName, $Name(^||IVG.DropNodes))
							}
							Kill ^||IVG.DropNodes
							If tId '= "" {
								TSTART
							}
						}
					}
				} Catch tTx {
					While $TLevel > tTxnLevel { TROLLBACK 1 }
					Throw tTx
				}
			}
			// rows that an explicit edge adopted between the scan and the delete were not dropped
			Set tRows = tDropped
		}
	} Catch tEx {
		Kill ^||IVG.DropRows, ^||IVG.DropNodes
		Throw tEx
	}
	Kill ^||IVG.DropRows, ^||IVG.DropNodes
	Quit {"predicate": (pPredicate), "rows": (tRows), "unchecked": (tUnchecked), "nodes_retired": (tRetired), "batches": (tBatches), "dry_run": (+pDryRun)}.%ToJSON()
}

/// A SQL COUNT(*) statement run with the predicate, the graph (when it is a named one) and any further arguments.
ClassMethod Count(pSQL As %String, pPredicate As %String, pName As %String, pNamed As %Boolean, pA1 As %String = "", pA2 As %String = "") As %Integer [ Private ]
{
	Set tRS = ..Open(pSQL, pPredicate, pName, pNamed, pA1, pA2)
	Quit $Select(tRS.%Next(): +tRS.%GetData(1), 1: 0)
}

/// Execute pSQL: arguments are the predicate, the graph name when pNamed, then pA1 and pA2 when given.
ClassMethod Open(pSQL As %String, pPredicate As %String, pName As %String, pNamed As %Boolean, pA1 As %String = "", pA2 As %String = "") As %SQL.StatementResult [ Private ]
{
	Set tStmt = ##class(%SQL.Statement).%New()
	$$$ThrowOnError(tStmt.%Prepare(pSQL))
	If pA1 '= "" {
		Set tRS = $Select(pNamed: tStmt.%Execute(pPredicate, pName, pA1, pA2), 1: tStmt.%Execute(pPredicate, pA1, pA2))
	} Else {
		Set tRS = $Select(pNamed: tStmt.%Execute(pPredicate, pName), 1: tStmt.%Execute(pPredicate))
	}
	If tRS.%SQLCODE < 0 {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "DropRows: SQLCODE " _ tRS.%SQLCODE _ " " _ tRS.%Message))
	}
	Quit tRS
}

/// Write the marked rows, their nodes and (through the row's index) the adjacency entries pPredicate's temporal edges
/// in pGraph lack, from the temporal globals: the way back after a predicate is made structural again, or after an
/// earlier release's rows were dropped. pPredicate must NOT be registered temporal-only. Idempotent.
/// pSeenLimit bounds the memory of triples already asserted (see Graph.KG.TemporalIndex.BackfillRows).
/// Returns the number of triples asserted.
ClassMethod BackfillRows(pGraph As %String = "", pPredicate As %String = "", pSeenLimit As %Integer = 100000) As %Integer
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	If pPredicate = "" {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "a predicate is required"))
	}
	If '..IsStructuralKey(tKey, pPredicate) {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "predicate " _ pPredicate _ " is registered temporal-only (opted out) in this graph: make it structural first (set_temporal_structural(predicate, True)), or its temporal edges would be given rows the registry says they should not have"))
	}
	If '##class(Graph.KG.Adjacency).Indexed() {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "writing structural rows needs the adjacency index (idx_edges_adj)"))
	}
	Set tStats = ##class(%DynamicAbstractObject).%FromJSON(##class(Graph.KG.TemporalIndex).BackfillRows(pGraph, pPredicate, pSeenLimit))
	Quit tStats.asserted
}

}
