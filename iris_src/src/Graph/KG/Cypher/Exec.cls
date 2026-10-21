/// The native Cypher executor (spec 253): runs a plan from iris_vector_graph.cypher.native_plan in one call, next to
/// the data, and keeps the rows for the caller to read in pages.
///
/// Rows live in ^||IVG.CypherOut(tag, n) as JSON arrays, one per row: a result is never one string, which stops at
/// about 3.6 MB (FR-015). Python reads them with ReadPage and frees them with Drop. Any error is thrown to the caller,
/// which answers the query on the SQL path instead (FR-007).
///
/// A plan is either family A ({"family": "A", ...}, see RunA) or a list of plumbing-test operators: "range" (rows
/// [0] .. [n-1]) and "rows" (literal rows).
Class Graph.KG.Cypher.Exec Extends %RegisteredObject
{

/// The plan format this executor reads; iris_vector_graph.cypher.native_plan.PLAN_VERSION must equal it. A family A
/// plan of another version is refused, never run: a field this executor predates would be ignored, and a plan from an
/// older package lacks what this executor expects.
Parameter PLANVERSION = 1;

/// The engine asks this once before it runs any plan here.
ClassMethod PlanVersion() As %Integer
{
	Quit ..#PLANVERSION
}

/// Run pPlan (JSON) with pParams (JSON object); returns Head's {"tag", "count"[, "rows"]}.
ClassMethod Run(pPlan As %String, pParams As %String = "{}") As %String
{
	Set tPlan = ##class(%Library.DynamicObject).%FromJSON(pPlan)
	Set tTag = $Increment(^||IVG.CypherOut)
	Kill ^||IVG.CypherOut(tTag)
	Set tCount = 0
	If tPlan.%Get("family") = "A" {
		If tPlan.%Get("v") '= ..#PLANVERSION {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "plan version "_tPlan.%Get("v")_", executor "_..#PLANVERSION_": redeploy the classes"))
		}
		Do ##class(Graph.KG.Cypher.Props).Begin()
		Try {
			Set tCount = ..RunA(tPlan, tTag, .tWorkers)
		} Catch tEx {
			Do ##class(Graph.KG.Cypher.Props).End()
			Kill ^||IVG.CypherOut(tTag)
			Throw tEx
		}
		Do ##class(Graph.KG.Cypher.Props).End()
		Quit ..Head(tTag, tCount, tWorkers)
	}
	Set tOps = tPlan.%Get("ops")
	Set tIter = tOps.%GetIterator()
	While tIter.%GetNext(.tI, .tOp) {
		Set tOpName = tOp.%Get("op")
		If tOpName = "range" {
			For tJ = 0:1:(tOp.%Get("n") - 1) {
				Set ^||IVG.CypherOut(tTag, $Increment(tCount)) = "["_tJ_"]"
			}
		} ElseIf tOpName = "rows" {
			Set tRowIter = tOp.%Get("rows").%GetIterator()
			While tRowIter.%GetNext(.tK, .tRow) {
				Set ^||IVG.CypherOut(tTag, $Increment(tCount)) = tRow.%ToJSON()
			}
		} Else {
			Kill ^||IVG.CypherOut(tTag)
			$$$ThrowStatus($$$ERROR($$$GeneralError, "Graph.KG.Cypher.Exec: unknown operator "_tOpName))
		}
	}
	Quit ..Head(tTag, tCount)
}

/// Run's answer: {"tag", "count"[, "workers"]}, plus the rows themselves ("rows") when they fit one page, in which case the
/// result is already freed and the caller makes no further call.
ClassMethod Head(pTag As %Integer, pCount As %Integer, pWorkers As %Integer = 0) As %String [ Private ]
{
	Set tHead = "{""tag"":"_pTag_",""count"":"_pCount_$Select(pWorkers: ",""workers"":"_pWorkers, 1: "")
	If pCount <= 1000 {
		Set tHead = tHead_",""rows"":"_..ReadPage(pTag, 1, pCount)
		Kill ^||IVG.CypherOut(pTag)
	}
	Quit tHead_"}"
}

/// Families A and B (spec 253 T021, T031): a chain of hops from an anchor, at most one of them variable-length, and
/// either a projection or grouped aggregates. Returns the number of rows written under pTag.
///
/// The plan is compiled into local arrays once (pQ), so the walk's inner loops touch no %DynamicObject:
///   pQ("n", i, "labels") = $ListBuild(label, ...)      pQ("n", i, "props") = $ListBuild($ListBuild(key, value), ...)
///   pQ("n", i, "cmp") = $ListBuild($ListBuild(key, op, value), ...), the other comparisons (<> < <= > >=)
///   pQ("n", i, "lab") = the labels, defined only when they are node i's only checks
///   pQ("h", k) = $ListBuild(from, to, dir, $ListBuild(type, ...), unique)   pQ("hops") = number of hops
///     (unique: another hop could match the same relationship, so the match keeps its relationships' identity)
///   pQ("mult") = whether parallel relationships each make a match (not under DISTINCT / count(DISTINCT))
///   pQ("acc") = whether an anchor's matches all fall in one group (plain counts keyed by the anchor alone)
///   pQ("h", k, "vl") = $ListBuild(min, max, types JSON), for a variable-length hop
///   pQ("ret") = $ListBuild($ListBuild(var, key), ...)  pQ("ord") = $ListBuild($ListBuild(var, key, desc), ...)
///   pQ("agg") = whether the RETURN aggregates; then pQ("ret") is the grouping keys, pQ("kvars") the nodes they read,
///   pQ("aggs") = $ListBuild($ListBuild("count" | "count_distinct", var), ...)
///   pQ("cols") = $ListBuild($ListBuild("k" | "a", index), ...), the RETURN columns
///   pQ("ord") = $ListBuild($ListBuild(column, "", desc), ...)
/// pWorkers: how many worker processes ran the walk (Parallel), else 0. A worker runs RunA with pParts > 1 over every
/// pParts-th node, from position pPart, of the label scan, of the variable-length hop's ends or of the first hop's
/// neighbours (the plan's "split"), and exports its groups, or writes its rows (Out), to ^IRIS.Temp.IVG.Cypher(pShare).
ClassMethod RunA(pPlan As %DynamicObject, pTag As %Integer, Output pWorkers As %Integer, pPart As %Integer = 0, pParts As %Integer = 1, pShare As %String = "") As %Integer [ Private ]
{
	Set tScoped = (pPlan.%GetTypeOf("graph") = "string")
	Set tGraph = $Select(tScoped: pPlan.%Get("graph"), 1: "")
	Set pWorkers = 0, tLabel = ..Smallest(pPlan, tScoped, tGraph)
	Do ..Compile(pPlan, .tQ)
	Set tAnchor = pPlan.%Get("anchor")
	Set tQ("limit") = $Select(pPlan.%GetTypeOf("limit") = "number": pPlan.%Get("limit"), 1: -1)
	Set tQ("ordered") = ($ListLength(tQ("ord")) > 0), tQ("distinct") = pPlan.%Get("distinct")
	Set tQ("count") = 0, tQ("seq") = 0, tQ("tag") = pTag
	// a LIMIT without ORDER BY can end the walk early (an ordered query collects every row first): only then is the
	// count checked against it, per anchor and per neighbour
	Set (tMayFill, tQ("mayfill")) = 'tQ("ordered") && (tQ("limit") >= 0)
	// with no hop the anchor is the match: Walk would only Emit it, which adds 1 to accw
	Set tAcc0 = tQ("acc") && (tQ("hops") = 0)
	// the direct property reader's globals (Graph.KG.Cypher.Props, checked once by Begin), read inline by NodeOK's
	// comparisons and KeyValues: a call per property was most of a label scan's cost per node
	Set tQ("pd") = ($Get(^||IVG.CypherProps("run")) = 1)
	Set:tQ("pd") tQ("pdata") = ^||IVG.CypherProps("data"), tQ("pidx") = ^||IVG.CypherProps("index"), tQ("pslot") = ^||IVG.CypherProps("slot")
	Do ..CrossFilters(pPlan, .tQ, tAnchor)
	// a worker's share (Part): every pParts-th node of the label scan ("scan"), end of the variable-length hop ("vl")
	// or neighbour on the anchor's first fixed hop ("h1"); a worker's rows and groups go to the shared pShare
	Set tQ("split") = $Select(pParts > 1: pPlan.%Get("split"), 1: ""), tQ("part") = pPart, tQ("parts") = pParts
	Set tQ("share") = pShare
	// a scan's share as key ranges, where one graph key is scanned: Parallel cuts the label into 8 chunks a worker
	// (Bounds), and worker p reads chunks p, p + pParts, ... as (lo, hi], instead of the whole label for every pParts-th
	// member; dealt round robin, a run of busy anchors is shared out as the stride shared it. Otherwise one chunk, all.
	Set tQ("ranged") = (tQ("split") = "scan") && pPlan.%IsDefined("bounds"), tQ("chunks") = 1, tQ("chunk", 1) = $ListBuild("", "")
	If tQ("ranged") {
		Set tBd = pPlan.%Get("bounds"), tLast = tBd.%Size(), tQ("chunks") = 0
		For tC = pPart:pParts:tLast {
			Set tQ("chunk", $Increment(tQ("chunks"))) = $ListBuild($Select(tC = 0: "", 1: tBd.%Get(tC - 1)), $Select(tC = tLast: "", 1: tBd.%Get(tC)))
		}
	}
	// the node the shares are split on: no two workers bind it to the same node
	Set tQ("splitvar") = $Case(tQ("split"), "scan": tAnchor, "vl": $List(tQ("h", 1), 2), : "")
	// ^||IVG.CypherRun: the walk's scratch (candidates, distinct keys, the sort tree), killed before and after
	Kill ^||IVG.CypherRun
	Set tSpec = pPlan.%Get("nodes").%Get(tAnchor)
	If tSpec.%Get("props").%Size() > 0 {
		// a property lookup: its few candidates are collected first (Anchors), then each is walked
		Do ..Anchors(tSpec, tScoped, tGraph)
		// with two anchors, one end may be reached from both, so in two workers' shares: their sets are not disjoint
		Set:$Order(^||IVG.CypherRun("cand", ""), -1)>1 tQ("splitvar") = ""
		If (pParts = 1) && ..ParallelWantedVL(pPlan, .tQ) {
			// the variable-length hop's ends are shared out among workers, each walking on from its share
			Set pWorkers = ..Parallel(pPlan, "", .tQ, "vl")
			Kill ^||IVG.CypherRun("cand")
		} ElseIf (pParts = 1) && ..ParallelWantedH1(pPlan, .tQ) {
			// the first hop's neighbours are shared out
			Set pWorkers = ..Parallel(pPlan, "", .tQ, "h1")
			Kill ^||IVG.CypherRun("cand")
		}
		Set tC = ""
		For {
			Set tC = $Order(^||IVG.CypherRun("cand", tC), 1, tCand)
			Quit:tC=""
			Quit:tMayFill&&(tQ("count")>=tQ("limit"))
			Set tKey = $List(tCand, 1), tGName = $List(tCand, 2), tS = $List(tCand, 3)
			Continue:'..NodeOK(.tQ, tAnchor, tKey, tGName, tS)
			Kill tB
			Set tB(tAnchor) = tS, tQ("accw") = 0
			If tAcc0 { Set tQ("accw") = 1 } Else { Do ..Walk(.tQ, 1, tKey, tGName, .tB, ",") }
			// every match of this anchor falls in one group: Emit summed them into tQ("accw"), grouped once here
			Do:tQ("acc")&&(tQ("accw")>0) ..Group(.tQ, tGName, .tB, tQ("accw"))
		}
	} Else {
		// a label scan reads the label's index in place, and a node with no check but labels is checked inline
		Set:tLabel="" tLabel = pPlan.%Get("scan_label")
		Set:tLabel="" tLabel = tSpec.%Get("labels").%Get(0)
		If (pParts = 1) && ..ParallelWanted(pPlan, .tQ, tScoped, tGraph, tLabel) {
			Set pWorkers = ..Parallel(pPlan, tLabel, .tQ, "scan")
		} Else {
			Set tLabOnly = $Data(tQ("n", tAnchor, "lab"), tLab), tStride = (tQ("split") = "scan") && 'tQ("ranged")
			// grouped counts of the scanned node alone (BI01's shape) are counted by ScanGroups' one loop
			Set tFast = tAcc0 && tQ("agg") && tQ("pd") && ($Get(tQ("n", tAnchor, "x")) = "") && (tQ("n", tAnchor, "props") = "")
			If tFast {
				For tJ = 1:1:$ListLength(tQ("aggs")) { Set:$List($List(tQ("aggs"), tJ), 1)'="count" tFast = 0 }
				// a node key makes every node its own group: nothing to count together
				For tJ = 1:1:$ListLength(tQ("ret")) { Set:($List($List(tQ("ret"), tJ), 1)'=tAnchor)||($List($List(tQ("ret"), tJ), 2)="__node") tFast = 0 }
			}
			Do ..GraphKeys(tScoped, tGraph, .tKeys)
			Set g = "", tPos = -1
			For {
				Set g = $Order(tKeys(g))
				Quit:g=""
				Set tGName = $Select(g = 0: "", 1: g)
				If tFast {
					Do ..ScanGroups(.tQ, tAnchor, g, tGName, tLabel, .tPos, pPart, pParts)
					Continue
				}
				For tCh = 1:1:tQ("chunks") {
					Quit:tMayFill&&(tQ("count")>=tQ("limit"))
					Set $ListBuild(tS, tHi) = tQ("chunk", tCh)
					For {
						Set tS = $Order(^KG("label", g, tLabel, tS))
						Quit:tS=""
						Quit:(tHi'="")&&(tS]]tHi)
						Quit:tMayFill&&(tQ("count")>=tQ("limit"))
						Set tPos = tPos + 1
						Continue:tStride&&((tPos#pParts)'=pPart)
						If tLabOnly {
							Set tOK = 1
							For tJ = 1:1:$ListLength(tLab) {
								If '$Data(^KG("label", g, $List(tLab, tJ), tS)) {
									Set tOK = 0
									Quit
								}
							}
							Continue:'tOK
						} Else {
							Continue:'..NodeOK(.tQ, tAnchor, g, tGName, tS, , tLabel)
						}
						Kill tB
						Set tB(tAnchor) = tS, tQ("accw") = 0
						If tAcc0 { Set tQ("accw") = 1 } Else { Do ..Walk(.tQ, 1, g, tGName, .tB, ",") }
						Do:tQ("acc")&&(tQ("accw")>0) ..Group(.tQ, tGName, .tB, tQ("accw"))
					}
				}
			}
		}
	}
	If (pShare '= "") && tQ("agg") {
		Do ..Export(.tQ, pShare)
	} ElseIf tQ("agg") {
		Do:pPlan.%IsDefined("stage2") ..Stage2(pPlan.%Get("stage2"), .tQ)
		Do ..EmitGroups(.tQ)
	} ElseIf tQ("ordered") {
		Do ..EmitSorted(.tQ, $Name(^||IVG.CypherRun("sort")), 1)
	}
	Kill ^||IVG.CypherRun
	Quit tQ("count")
}

/// Whether a label scan is worth splitting: more than one CPU and an estimated work of at least the plan's
/// parallel_min (0 always, negative never). The estimate is the label's members x the matches of the first two hops
/// from each, sampled from the first 16 members (and, for the second hop, 8 of their neighbours); with no hop, a
/// tenth per member. Counting stops once the estimate is reached.
ClassMethod ParallelWanted(pPlan As %DynamicObject, ByRef pQ, pScoped As %Boolean, pGraph As %String, pLabel As %String) As %Boolean [ Private ]
{
	Set tMin = pPlan.%Get("parallel_min")
	Quit:(tMin="")||(tMin<0) 0
	Quit:$System.Util.NumberOfCPUs()<2 0
	Quit:tMin=0 1
	Do ..GraphKeys(pScoped, pGraph, .tKeys)
	If pQ("hops") = 0 {
		// no hop: an anchor is one match, costing its checks only (a tenth of a hop's walk)
		Set tN = 0, g = ""
		For {
			Set g = $Order(tKeys(g))
			Quit:g=""
			Set tS = ""
			For {
				Set tS = $Order(^KG("label", g, pLabel, tS))
				Quit:tS=""
				Return:$Increment(tN)>=(tMin*10) 1
			}
		}
		Quit 0
	}
	Set tAnchor = $List(pQ("h", 1), 1), tFar = $List(pQ("h", 1), 2)
	Set tFrom2 = $Select(pQ("hops") > 1: $List(pQ("h", 2), 1), 1: "")
	Set tN = 0, tWork = 0, g = ""
	For {
		Set g = $Order(tKeys(g))
		Quit:g=""
		Set tS = ""
		For {
			Set tS = $Order(^KG("label", g, pLabel, tS))
			Quit:tS=""
			Set tN = tN + 1
			If tN <= 16 {
				Set tW = ..Fanout(.pQ, 1, g, tS, .tSome)
				If tFrom2 = tFar {
					// the second hop leaves the first's far node: its fan-out, averaged over a few of them
					Set tD2 = 0
					For tI = 1:1:tSome {
						Set tD2 = tD2 + ..Fanout(.pQ, 2, g, tSome(tI), .tNone)
					}
					Set:tSome tW = tW * $Select(tD2 > tSome: tD2 / tSome, 1: 1)
				} ElseIf tFrom2 = tAnchor {
					Set tD2 = ..Fanout(.pQ, 2, g, tS, .tNone), tW = tW * $Select(tD2 > 1: tD2, 1: 1)
				}
				Set tWork = tWork + $Select(tW > 1: tW, 1: 1)
			}
			// the estimate so far: the members counted x the mean work of the sampled ones
			Return:(tN*tWork/$Select(tN>16:16,1:tN))>=tMin 1
		}
	}
	Quit 0
}

/// Whether an anchored query whose first hop is a fixed one is worth splitting over that hop's neighbours: the plan's
/// parallel_min (0 always, negative never), more than one CPU, and the candidates' first-hop neighbours x the second
/// hop's fan-out (sampled from 8 of them, over the first 16 candidates) of at least parallel_min.
ClassMethod ParallelWantedH1(pPlan As %DynamicObject, ByRef pQ) As %Boolean [ Private ]
{
	Set tMin = pPlan.%Get("parallel_min")
	Quit:(tMin="")||(tMin<0) 0
	Quit:$System.Util.NumberOfCPUs()<2 0
	Quit:pQ("hops")<1 0
	Quit:$Data(pQ("h", 1, "vl")) 0
	Quit:'$Data(^||IVG.CypherRun("cand", 1)) 0
	Quit:tMin=0 1
	Set tNested = (pQ("hops") > 1) && ($List(pQ("h", 2), 1) = $List(pQ("h", 1), 2)), tWork = 0, tC = ""
	For tI = 1:1:16 {
		Set tC = $Order(^||IVG.CypherRun("cand", tC), 1, tCand)
		Quit:tC=""
		Set tW = ..Fanout(.pQ, 1, $List(tCand, 1), $List(tCand, 3), .tSome)
		If tNested && tSome {
			Set tD2 = 0
			For tJ = 1:1:tSome {
				Set tD2 = tD2 + ..Fanout(.pQ, 2, $List(tCand, 1), tSome(tJ), .tNone)
			}
			Set tW = tW * $Select(tD2 > tSome: tD2 / tSome, 1: 1)
		}
		Set tWork = tWork + tW
	}
	Quit tWork >= tMin
}

/// Whether an anchored query whose first hop is variable-length is worth splitting over that hop's ends: the
/// plan's parallel_min (0 always, negative never), more than one CPU, and the first candidate's ends x the second hop's
/// fan-out (sampled from 8 ends) of at least parallel_min.
ClassMethod ParallelWantedVL(pPlan As %DynamicObject, ByRef pQ) As %Boolean [ Private ]
{
	Set tMin = pPlan.%Get("parallel_min")
	Quit:(tMin="")||(tMin<0) 0
	Quit:$System.Util.NumberOfCPUs()<2 0
	Quit:'$Data(pQ("h", 1, "vl"), tVL) 0
	Quit:'$Data(^||IVG.CypherRun("cand", 1), tCand) 0
	Quit:tMin=0 1
	Set tKey = $List(tCand, 1), tGName = $List(tCand, 2), tS = $List(tCand, 3)
	Do ##class(Graph.KG.VarLength).EndsInto(tS, $List(tVL, 3), $List(pQ("h", 1), 3), $List(tVL, 1), $List(tVL, 2), 0, tGName, .tEnds)
	// kept for the walk, which would otherwise compute the same ends again when the query runs here after all
	Merge pQ("vlends", $ListBuild(tGName, tS)) = tEnds
	// with no hop after it, an end costs only its checks, while every worker walks the variable-length hop again
	Set tD = 0.1
	If pQ("hops") > 1 {
		Set tD = 0, tK = $Select(tEnds < 8: tEnds, 1: 8)
		For tI = 1:1:tK {
			Set tD = tD + ..Fanout(.pQ, 2, tKey, $List(tEnds(tI), 1), .tNone)
		}
		Set tD = $Select(tD > tK: tD / tK, 1: 1)
	}
	Quit (tEnds * tD) >= tMin
}

/// The neighbours of pS along fixed hop pH (its direction and types): their number, and the first 8 in pSome(1..8).
/// A variable-length hop counts 1.
ClassMethod Fanout(ByRef pQ, pH As %Integer, pKey, pS As %String, Output pSome) As %Integer [ Private ]
{
	Kill pSome
	Set pSome = 0
	Quit:$Data(pQ("h", pH, "vl")) 1
	Set tHop = pQ("h", pH), tDir = $List(tHop, 3), tTypes = $List(tHop, 4), tN = 0
	For tSub = "out", "in" {
		Continue:(tDir'="both")&&(tDir'=tSub)
		Set tTI = 0, tP = ""
		For {
			If $ListLength(tTypes) > 0 {
				Set tTI = tTI + 1
				Quit:tTI>$ListLength(tTypes)
				Set tP = $List(tTypes, tTI)
			} Else {
				Set tP = $Order(^KG(tSub, pKey, pS, tP))
				Quit:tP=""
			}
			Set tM = ""
			For {
				Set tM = $Order(^KG(tSub, pKey, pS, tP, tM))
				Quit:tM=""
				Set tN = tN + 1
				Set:pSome<8 pSome = pSome + 1, pSome(pSome) = tM
			}
		}
	}
	Quit tN
}

/// Run the walk in worker processes (%SYSTEM.WorkMgr), each over every n-th node (Part) of the label scan (pSplit
/// "scan", of label pLabel), of the variable-length hop's ends ("vl") or of the first hop's neighbours ("h1"), and read
/// their groups back into this process's ^||IVG.CypherRun("g"), so EmitGroups sorts and writes them as if one process
/// had walked; a projection's rows are written here.
/// Counts are summed; a count(DISTINCT) member is counted by the worker that first adds it to the shared set (Export),
/// so either total is read as it stands. Returns the workers.
ClassMethod Parallel(pPlan As %DynamicObject, pLabel As %String, ByRef pQ, pSplit As %String) As %Integer [ Private ]
{
	Set tN = $System.Util.NumberOfCPUs()
	Set:tN>8 tN = 8
	Set tShare = $Increment(^IRIS.Temp.IVG.Cypher)
	Kill ^IRIS.Temp.IVG.Cypher(tShare)
	// the workers run the plan as this process chose it: its anchor and the label it scans
	Do pPlan.%Set("alts", ##class(%Library.DynamicArray).%New()), pPlan.%Set("scan_label", pLabel), pPlan.%Set("split", pSplit)
	If pSplit = "scan" {
		// one graph key scanned: each worker gets key ranges of the label (Bounds, RunA), not every tN-th member of all of it
		Set tScoped = (pPlan.%GetTypeOf("graph") = "string")
		Do ..GraphKeys(tScoped, $Select(tScoped: pPlan.%Get("graph"), 1: ""), .tKeys)
		Set tKey = $Order(tKeys(""))
		If (tKey '= "") && ($Order(tKeys(tKey)) = "") {
			Set tChunks = 8 * tN, tBounds = ..Bounds(tKey, pLabel, tChunks)
			// exactly tChunks - 1 cuts, or two chunks would start at the label's first member
			Do:$IsObject(tBounds)&&(tBounds.%Size()=(tChunks-1)) pPlan.%Set("bounds", tBounds)
		}
	}
	Set tJSON = pPlan.%ToJSON(), tWQ = $System.WorkMgr.%New(, tN)
	Try {
		For tI = 0:1:(tN - 1) {
			$$$ThrowOnError(tWQ.Queue("##class(Graph.KG.Cypher.Exec).Part", tJSON, tShare, tI, tN))
		}
		$$$ThrowOnError(tWQ.WaitForComplete())
	} Catch tEx {
		Kill ^IRIS.Temp.IVG.Cypher(tShare)
		Throw tEx
	}
	If 'pQ("agg") {
		// a projection: each worker kept its own LIMIT; the rows merge in order (or arrival), DISTINCT across workers
		If pQ("ordered") {
			Do ..EmitSorted(.pQ, $Name(^IRIS.Temp.IVG.Cypher(tShare, "sort")), 1)
		} Else {
			Set tK = ""
			For {
				Set tK = $Order(^IRIS.Temp.IVG.Cypher(tShare, "rows", tK), 1, tRow)
				Quit:tK=""
				Quit:..LimitMet(.pQ)
				Continue:..Seen(.pQ, tRow)
				Do ..Out(.pQ, tRow)
			}
		}
		Kill ^IRIS.Temp.IVG.Cypher(tShare)
		Quit tN
	}
	Set tAggs = pQ("aggs"), tK = ""
	For {
		Set tK = $Order(^IRIS.Temp.IVG.Cypher(tShare, "k", tK))
		Quit:tK=""
		Set tG = $Increment(^||IVG.CypherRun("g")), ^||IVG.CypherRun("g", tG) = tK
		For tJ = 1:1:$ListLength(tAggs) {
			If $Data(^IRIS.Temp.IVG.Cypher(tShare, "c", tK, tJ), tC) {
				Set ^||IVG.CypherRun("g", tG, tJ) = tC
			}
		}
	}
	Kill ^IRIS.Temp.IVG.Cypher(tShare)
	Quit tN
}

/// pN - 1 members of label pLabel in graph key pKey that cut it into pN ranges of about equal size, in $Order order,
/// as a %DynamicArray of strings; "" when the label has fewer than pN members. Remembered per process: recomputed
/// (two walks of the label) only when the graph's version token has moved and the last answer is 10 s old. A stale
/// answer can only unbalance the workers: the ranges (lo, hi] still cover every member once.
ClassMethod Bounds(pKey, pLabel As %String, pN As %Integer) As %Library.DynamicArray [ Private ]
{
	Set tSig = $ListBuild($Namespace, pKey, pLabel, pN), tTok = +$Get(^KG("__version"))
	Set tHash = $ZCRC(tSig, 7), tMemo = $Get(^||IVG.CypherBounds(tHash))
	If (tMemo '= "") && ($List(tMemo, 1) = tSig) && (($List(tMemo, 2) = tTok) || (($ZHorolog - $List(tMemo, 3)) < 10)) {
		Quit ##class(%Library.DynamicArray).%FromJSON($List(tMemo, 4))
	}
	Set tC = 0, tS = ""
	For {
		Set tS = $Order(^KG("label", pKey, pLabel, tS))
		Quit:tS=""
		Set tC = tC + 1
	}
	Quit:tC<pN ""
	Set tB = ##class(%Library.DynamicArray).%New(), tI = 0, tK = 1, tAt = tC * tK \ pN, tS = ""
	For {
		Set tS = $Order(^KG("label", pKey, pLabel, tS))
		Quit:tS=""
		Set tI = tI + 1
		Continue:tI<tAt
		Do tB.%Push(tS_"", "string")
		Set tK = tK + 1
		Quit:tK>=pN
		Set tAt = tC * tK \ pN
	}
	Set ^||IVG.CypherBounds(tHash) = $ListBuild(tSig, tTok, $ZHorolog, tB.%ToJSON())
	Quit tB
}

/// A worker's part of Parallel: RunA over its share, exporting its groups or rows.
ClassMethod Part(pPlan As %String, pShare As %Integer, pPart As %Integer, pParts As %Integer) As %Status
{
	Set tSC = $$$OK
	Do ##class(Graph.KG.Cypher.Props).Begin()
	Try {
		Do ..RunA(##class(%Library.DynamicObject).%FromJSON(pPlan), "", , pPart, pParts, pShare)
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Do ##class(Graph.KG.Cypher.Props).End()
	Quit tSC
}

/// A worker's groups, keyed by their keys' JSON: ("k", keys), ("c", keys, j) aggregate j's total (counts added; for
/// count(DISTINCT), members new to the shared set ("d", keys, j, node), or, of the node the shares are split on, the
/// worker's own count, since no other worker binds those nodes).
ClassMethod Export(ByRef pQ, pShare As %Integer) [ Private ]
{
	Set tAggs = pQ("aggs"), tG = ""
	For {
		Set tG = $Order(^||IVG.CypherRun("g", tG), 1, tK)
		Quit:tG=""
		Set ^IRIS.Temp.IVG.Cypher(pShare, "k", tK) = ""
		For tJ = 1:1:$ListLength(tAggs) {
			Set tA = $List(tAggs, tJ)
			// count(DISTINCT) of any node but the split one: two workers may hold the same member
			If ($List(tA, 1) = "count_distinct") && ($List(tA, 2) '= pQ("splitvar")) {
				Set tM = ""
				For {
					Set tM = $Order(^||IVG.CypherRun("gd", tG, tJ, tM))
					Quit:tM=""
					// $Increment is atomic: the one worker that first adds a member counts it
					Do:$Increment(^IRIS.Temp.IVG.Cypher(pShare, "d", tK, tJ, tM))=1 $Increment(^IRIS.Temp.IVG.Cypher(pShare, "c", tK, tJ))
				}
			} ElseIf $Data(^||IVG.CypherRun("g", tG, tJ), tC) {
				// a count, or count(DISTINCT) of the split node, whose workers' sets are disjoint: the totals add up
				Do $Increment(^IRIS.Temp.IVG.Cypher(pShare, "c", tK, tJ), tC)
			}
		}
	}
}

/// Give each cross filter ([i, key, op, j, key2]: node i's key op node j's key2) to whichever of its two nodes this
/// walk binds later, as pQ("n", later, "x") = $ListBuild($ListBuild(key, op, other, other's key), ...), the op turned
/// round where the later node is j; that node is then checked by its labels and properties no longer inline.
ClassMethod CrossFilters(pPlan As %DynamicObject, ByRef pQ, pAnchor As %Integer) [ Private ]
{
	Quit:pPlan.%GetTypeOf("cross")'="array"
	Set tPos(pAnchor) = 0, tP = 0
	For tH = 1:1:pQ("hops") {
		Set tTo = $List(pQ("h", tH), 2)
		Set:'$Data(tPos(tTo)) tPos(tTo) = $Increment(tP)
	}
	Set tIter = pPlan.%Get("cross").%GetIterator()
	While tIter.%GetNext(.tI, .tC) {
		Set tA = tC.%Get(0), tB = tC.%Get(3), tOp = tC.%Get(2)
		If tPos(tA) > tPos(tB) {
			Set tLater = tA, tX = $ListBuild(tC.%Get(1), tOp, tB, tC.%Get(4))
		} Else {
			Set tOp = $Case(tOp, "<": ">", ">": "<", "<=": ">=", ">=": "<=", : tOp)
			Set tLater = tB, tX = $ListBuild(tC.%Get(4), tOp, tA, tC.%Get(1))
		}
		Set pQ("n", tLater, "x") = $Get(pQ("n", tLater, "x"))_$ListBuild(tX)
		Kill pQ("n", tLater, "lab")
	}
}

ClassMethod Compile(pPlan As %DynamicObject, Output pQ) [ Private ]
{
	Kill pQ
	Set tIter = pPlan.%Get("nodes").%GetIterator()
	While tIter.%GetNext(.tI, .tNode) {
		Set pQ("n", tI, "labels") = ..ToList(tNode.%Get("labels"))
		Set pQ("n", tI, "props") = ..Pairs(tNode.%Get("props")), pQ("n", tI, "cmp") = ""
		Set tCmp = tNode.%Get("cmp").%GetIterator()
		While tCmp.%GetNext(.tJ, .tC) {
			Set pQ("n", tI, "cmp") = pQ("n", tI, "cmp")_$ListBuild($ListBuild(tC.%Get(0), tC.%Get(1), tC.%Get(2).%Get("v")))
		}
		// labels only: Neighbours checks them inline, without a call per neighbour
		Set:(pQ("n",tI,"props")="")&&(pQ("n",tI,"cmp")="") pQ("n", tI, "lab") = pQ("n", tI, "labels")
	}
	Set pQ("hops") = 0, tIter = pPlan.%Get("hops").%GetIterator()
	While tIter.%GetNext(.tI, .tHop) {
		Set tTypes = ..ToList(tHop.%Get("types"))
		Set pQ("h", $Increment(pQ("hops"))) = $ListBuild(tHop.%Get("from"), tHop.%Get("to"), tHop.%Get("dir"), tTypes, ''tHop.%Get("unique"))
		If tHop.%IsDefined("min") {
			// variable length: its bounds and its types as the JSON Graph.KG.VarLength.EndsInto takes
			Set pQ("h", pQ("hops"), "vl") = $ListBuild(tHop.%Get("min"), tHop.%Get("max"), tHop.%Get("types").%ToJSON())
		}
		Set:tHop.%Get("tally") pQ("h", pQ("hops"), "tally") = ""
	}
	Set pQ("mult") = ''pPlan.%Get("multiplicity"), pQ("acc") = ''pPlan.%Get("acc")
	Set pQ("agg") = (pPlan.%GetTypeOf("agg") = "object"), pQ("ret") = "", pQ("ord") = "", pQ("kvars") = ""
	If pQ("agg") {
		// grouping: the keys project as a row does; the columns interleave keys and aggregates in RETURN order
		Set tAgg = pPlan.%Get("agg"), tRet = tAgg.%Get("keys")
		Set pQ("aggs") = "", tIter = tAgg.%Get("aggs").%GetIterator()
		While tIter.%GetNext(.tI, .tA) { Set pQ("aggs") = pQ("aggs")_$ListBuild($ListBuild(tA.%Get("kind"), tA.%Get("var"))) }
		Set pQ("cols") = "", tIter = tAgg.%Get("cols").%GetIterator()
		While tIter.%GetNext(.tI, .tC) { Set pQ("cols") = pQ("cols")_$ListBuild($ListBuild(tC.%Get(0), tC.%Get(1))) }
		Set tIter = tAgg.%Get("order").%GetIterator()
		While tIter.%GetNext(.tI, .tO) { Set pQ("ord") = pQ("ord")_$ListBuild($ListBuild(tO.%Get(0), "", ''tO.%Get(1))) }
	} Else {
		Set tRet = pPlan.%Get("returns"), tIter = pPlan.%Get("order").%GetIterator()
		While tIter.%GetNext(.tI, .tO) {
			Set pQ("ord") = pQ("ord")_$ListBuild($ListBuild(tO.%Get(0), tO.%Get(1), ''tO.%Get(2)))
		}
	}
	Set tIter = tRet.%GetIterator()
	While tIter.%GetNext(.tI, .tR) {
		// a key may carry a function of the property: [var, key, ["substring", start, length]]
		Set tFn = ""
		If tR.%GetTypeOf(2) = "array" {
			Set tF = tR.%Get(2), tFn = $ListBuild(tF.%Get(0), tF.%Get(1), tF.%Get(2))
		}
		Set tVar = tR.%Get(0), pQ("ret") = pQ("ret")_$ListBuild($ListBuild(tVar, tR.%Get(1), tFn))
		Set:pQ("agg")&&'$ListFind(pQ("kvars"),tVar) pQ("kvars") = pQ("kvars")_$ListBuild(tVar)
	}
}

/// The plan's [key, {"v": value}] pairs, as $ListBuild($ListBuild(key, value), ...).
ClassMethod Pairs(pArray As %DynamicArray) As %List [ Private ]
{
	Set tList = "", tIter = pArray.%GetIterator()
	While tIter.%GetNext(.tI, .tPair) { Set tList = tList_$ListBuild($ListBuild(tPair.%Get(0), tPair.%Get(1).%Get("v"))) }
	Quit tList
}

/// The strings of a %DynamicArray, as a $List.
ClassMethod ToList(pArray As %DynamicArray) As %List [ Private ]
{
	Set tList = "", tIter = pArray.%GetIterator()
	While tIter.%GetNext(.tI, .tItem) { Set tList = tList_$ListBuild(tItem) }
	Quit tList
}

/// Whether the LIMIT is met by the rows written so far.
ClassMethod LimitMet(ByRef pQ) As %Boolean [ Private ]
{
	Quit (pQ("limit") >= 0) && (pQ("count") >= pQ("limit"))
}

/// Where the plan offers alternative anchors (labelled nodes, no property to look up), make the one whose label has
/// the fewest members the plan's anchor and hops, and answer that label. The labels are counted in lockstep until the
/// first runs out, so the count costs about (alternatives x the smallest label), not the largest. Otherwise "".
ClassMethod Smallest(pPlan As %DynamicObject, pScoped As %Boolean, pGraph As %String) As %String [ Private ]
{
	Set tAlts = pPlan.%Get("alts")
	Quit:'$IsObject(tAlts) ""
	Quit:tAlts.%Size()<2 ""
	Do ..GraphKeys(pScoped, pGraph, .tKeys)
	Set tN = 0, tIter = tAlts.%GetIterator(), tSig = $ListBuild($Namespace, pScoped, pGraph)
	While tIter.%GetNext(.tA, .tAlt) {
		Set tLabels = pPlan.%Get("nodes").%Get(tAlt.%Get("anchor")).%Get("labels"), tLI = tLabels.%GetIterator()
		While tLI.%GetNext(.tJ, .tL) {
			Set tCurAlt($Increment(tN)) = tA, tCurLabel(tN) = tL, tCurKey(tN) = $Order(tKeys("")), tCurS(tN) = ""
			Set tSig = tSig_$ListBuild(tA, tL)
		}
	}
	// the count is remembered for this process until the graph's version token moves (every adjacency write); a
	// label-only write does not move it, which can only cost speed: every alternative gives the same answer
	// (under the alternatives' hash, holding them: any number of long labels fits in a subscript)
	Set tTok = +$Get(^KG("__version")), tHash = $ZCRC(tSig, 7)
	Set tWin = "", tMemo = $Get(^||IVG.CypherSmallest(tHash))
	Set:(tMemo'="")&&($List(tMemo, 1)=tSig)&&($List(tMemo, 2)=tTok) tWin = $List(tMemo, 3)
	While tWin = "" {
		For tC = 1:1:tN {
			Set tL = tCurLabel(tC), g = tCurKey(tC), tS = tCurS(tC)
			Set tS = $Select(g = "": "", 1: $Order(^KG("label", g, tL, tS)))
			While (tS = "") && (g '= "") {
				Set g = $Order(tKeys(g))
				Set:g'="" tS = $Order(^KG("label", g, tL, ""))
			}
			If tS = "" {
				Set tWin = tC
				Quit
			}
			Set tCurKey(tC) = g, tCurS(tC) = tS
		}
	}
	Set ^||IVG.CypherSmallest(tHash) = $ListBuild(tSig, tTok, tWin)
	Set tAlt = tAlts.%Get(tCurAlt(tWin))
	Do pPlan.%Set("anchor", tAlt.%Get("anchor")), pPlan.%Set("hops", tAlt.%Get("hops")), pPlan.%Set("acc", tAlt.%Get("acc"))
	Quit tCurLabel(tWin)
}

/// The graph keys a label scan reads: the plan's graph, or every graph's.
ClassMethod GraphKeys(pScoped As %Boolean, pGraph As %String, Output pKeys) [ Private ]
{
	Kill pKeys
	If pScoped {
		Set pKeys(##class(Graph.KG.GraphKey).ForIndex(pGraph)) = ""
		Quit
	}
	Set g = ""
	For {
		Set g = $Order(^KG("label", g))
		Quit:g=""
		Set pKeys(g) = ""
	}
}

/// A property-lookup anchor's candidates: ^||IVG.CypherRun("cand", n) = $ListBuild(graph key, graph name, node).
ClassMethod Anchors(pSpec As %DynamicObject, pScoped As %Boolean, pGraph As %String) [ Private ]
{
	Set tN = 0
	Set tPair = pSpec.%Get("props").%Get(0), tPropKey = tPair.%Get(0), tVal = tPair.%Get(1).%Get("v")
	Set tSQL = "SELECT graph_id, s FROM Graph_KG.rdf_props WHERE ""key"" = ? AND val = ?"
	Set tArgs = 2, tArgs(1) = tPropKey, tArgs(2) = tVal
	If ##class(%Dictionary.CompiledProperty).%ExistsId("Graph.KG.rdfprops||valkey") {
		// the indexed prefix, so the lookup is an index read rather than a scan of the key's rows
		Set tSQL = tSQL_" AND val_key = ?", tArgs = 3, tArgs(3) = $Extract(tVal, 1, 128)
	}
	If pScoped {
		Set tSQL = tSQL_" AND COALESCE(graph_id, '') = COALESCE(?, '')", tArgs = tArgs + 1
		Set tArgs(tArgs) = $Select(pGraph = "": $Char(0), 1: pGraph)
	}
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, tSQL, tArgs...)
	If tRS.%SQLCODE < 0 {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "Cypher anchor SQLCODE "_tRS.%SQLCODE_": "_tRS.%Message))
	}
	While tRS.%Next() {
		Set tGName = $Translate(tRS.%GetData(1), $Char(0), "")
		Set ^||IVG.CypherRun("cand", $Increment(tN)) = $ListBuild(##class(Graph.KG.GraphKey).ForIndex(tGName), tGName, tRS.%GetData(2))
	}
}

/// Whether node pS of graph pKey carries node pI's labels and properties.
/// pB: the bindings so far, which a cross filter (a comparison with another node's property) reads.
ClassMethod NodeOK(ByRef pQ, pI As %Integer, pKey, pGName As %String, pS As %String, ByRef pB, pScanned As %String = "") As %Boolean [ Private ]
{
	// pScanned: the label a label scan found pS under, which needs no check
	Set tL = pQ("n", pI, "labels")
	For tJ = 1:1:$ListLength(tL) {
		Continue:$List(tL, tJ)=pScanned
		Return:'$Data(^KG("label", pKey, $List(tL, tJ), pS)) 0
	}
	Set tP = pQ("n", pI, "props")
	For tJ = 1:1:$ListLength(tP) {
		Set tPair = $List(tP, tJ)
		Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pS, $List(tPair, 1), .tFound)
		Return:('tFound)||(tVal '= $List(tPair, 2)) 0
	}
	// a comparison with a missing property is null, which filters the row out (the SQL path's three-valued logic);
	// a range orders as SQL compares rdf_props.val, which is sorts-after (]]): canonical numbers first, numerically
	// the value read is remembered for the node (pQ("pv")), so a grouping key on the same property does not read it
	// again (KeyValues)
	Set tX = pQ("n", pI, "cmp")
	For tJ = 1:1:$ListLength(tX) {
		Set tC = $List(tX, tJ), tProp = $List(tC, 1), tOp = $List(tC, 2), tArg = $List(tC, 3)
		If pQ("pd") {
			Set tId = $Order(@pQ("pidx")@($Select(pGName = "": $Char(0), 1: pGName), pS, tProp, ""))
			Return:tId="" 0
			Set tVal = $ListGet($Get(@pQ("pdata")@(tId)), pQ("pslot"))
		} Else {
			Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pS, tProp, .tFound)
			Return:'tFound 0
		}
		Set pQ("pv", pI, tProp) = $ListBuild(pGName, pS, tVal)
		// Holds, inline
		Return:'$Case(tOp, "=": tVal = tArg, "<>": tVal '= tArg, "<": tArg ]] tVal, ">": tVal ]] tArg, "<=": '(tVal ]] tArg), : '(tArg ]] tVal)) 0
	}
	// cross filters: this node's property against a node bound earlier in the walk (RunA assigns each to the later)
	Set tX = $Get(pQ("n", pI, "x"))
	For tJ = 1:1:$ListLength(tX) {
		Set tC = $List(tX, tJ), tProp = $List(tC, 1), tOp = $List(tC, 2), tOtherProp = $List(tC, 4)
		Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pS, tProp, .tFound)
		Return:'tFound 0
		Set tOther = pB($List(tC, 3))
		Set tArg = ##class(Graph.KG.Cypher.Props).Get(pGName, tOther, tOtherProp, .tFound)
		Return:'tFound 0
		If (tOp = "=") || (tOp = "<>") {
			Set tOK = ..Holds(tVal, tOp, tArg)
		} Else {
			// two stored values of either kind: the SQL path's CY_CMP (Cypher orderability; null across kinds), over
			// sort keys kept per node for the run, since one node meets many others
			Set tR = ..CmpKeys(..SortKey(pGName, pS, tProp, tVal), ..SortKey(pGName, tOther, tOtherProp, tArg))
			Set tOK = $Case(tOp, "<": tR = -1, "<=": (tR = -1) || (tR = 0), ">": tR = 1, : (tR = 1) || (tR = 0))
		}
		Return:'tOK 0
	}
	Quit 1
}

/// CYSORTKEY(pVal, "x") of node pS's property pKey, computed once per run (^||IVG.CypherRun("sk")).
ClassMethod SortKey(pGName As %String, pS As %String, pKey As %String, pVal As %String) As %String [ Private ]
{
	Set tNode = $ListBuild(pGName, pS, pKey)
	Quit:$Data(^||IVG.CypherRun("sk", tNode), tK) tK
	Set tK = ##class(User.funcCYSORTKEY).CYSORTKEY(pVal, "x"), ^||IVG.CypherRun("sk", tNode) = tK
	Quit tK
}

/// SQLUser.CY_CMP over two sort keys: -1, 0 or 1, 2 for NaN against a number, "" (null) for a null or different kinds.
ClassMethod CmpKeys(pA As %String, pB As %String) As %String [ Private ]
{
	Quit:(pA="z")||(pB="z") ""
	Set tRa = $Extract(pA), tRb = $Extract(pB)
	Quit:(tRa="o")&&((tRb="o")||(tRb="n")) 2
	Quit:(tRb="o")&&(tRa="n") 2
	Quit:tRa'=tRb ""
	Quit:"abce"[tRa ""
	Quit:pA=pB 0
	Quit $Select(pA ] pB: 1, 1: -1)
}

/// Whether pVal op pArg holds, ordered as SQL compares rdf_props.val: equality as strings, a range by sorts-after (]]).
ClassMethod Holds(pVal As %String, pOp As %String, pArg As %String) As %Boolean [ Private ]
{
	Quit $Case(pOp, "=": pVal = pArg, "<>": pVal '= pArg, "<": pArg ]] pVal, ">": pVal ]] pArg, "<=": '(pVal ]] pArg), : '(pArg ]] pVal))
}

/// Bind hops pH on; at the end of the chain, emit the row. pUsed holds the edges this match has used (",id,id,"):
/// a relationship appears once per match.
ClassMethod Walk(ByRef pQ, pH As %Integer, pKey, pGName As %String, ByRef pB, pUsed As %String) [ Private ]
{
	If pH > pQ("hops") {
		Do ..Emit(.pQ, pGName, .pB)
		Quit
	}
	Set tHop = pQ("h", pH), tFrom = $List(tHop, 1), tTo = $List(tHop, 2), tDir = $List(tHop, 3), tTypes = $List(tHop, 4)
	Set tS = pB(tFrom)
	If $Data(pQ("h", pH, "vl"), tVL) {
		// each end once, in this match's graph (the planner allows this only where paths cannot be told apart)
		If $Data(pQ("vlends", $ListBuild(pGName, tS))) {
			Merge tEnds = pQ("vlends", $ListBuild(pGName, tS))
		} Else {
			Do ##class(Graph.KG.VarLength).EndsInto(tS, $List(tVL, 3), tDir, $List(tVL, 1), $List(tVL, 2), 0, pGName, .tEnds)
		}
		For tI = 1:1:tEnds {
			Quit:pQ("mayfill")&&(pQ("count")>=pQ("limit"))
			// a worker walks its share of the ends; every worker computes them in the same order
			Continue:(pQ("split")="vl")&&(((tI-1)#pQ("parts"))'=pQ("part"))
			Set tM = $List(tEnds(tI), 1)
			Continue:'..NodeOK(.pQ, tTo, pKey, pGName, tM, .pB)
			Set pB(tTo) = tM
			Do ..Walk(.pQ, pH + 1, pKey, pGName, .pB, pUsed)
			Kill pB(tTo)
		}
		Quit
	}
	// a tallied hop (the last, its far node read by nothing but count): its matches are counted, then emitted once
	Set tTally = $Data(pQ("h", pH, "tally"))
	Set:tTally pQ("w") = 0
	For tSub = "out", "in" {
		Continue:(tDir '= "both")&&(tDir '= tSub)
		If $ListLength(tTypes) > 0 {
			For tTI = 1:1:$ListLength(tTypes) {
				// a node with no such relationship costs no call (most of a sparse hop's anchors)
				Continue:'$Data(^KG(tSub, pKey, tS, $List(tTypes, tTI)))
				Do ..Neighbours(.pQ, pH, tSub, tDir, pKey, pGName, tS, $List(tTypes, tTI), tTo, .pB, pUsed)
			}
			Continue
		}
		Set tP = ""
		For {
			Set tP = $Order(^KG(tSub, pKey, tS, tP))
			Quit:tP=""
			Do ..Neighbours(.pQ, pH, tSub, tDir, pKey, pGName, tS, tP, tTo, .pB, pUsed)
		}
	}
	Do:tTally&&(pQ("w")>0) ..Emit(.pQ, pGName, .pB, pQ("w"))
}

ClassMethod Neighbours(ByRef pQ, pH, pSub, pDir, pKey, pGName, pS, pP, pTo, ByRef pB, pUsed) [ Private ]
{
	Set tUnique = $List(pQ("h", pH), 5), tTally = $Data(pQ("h", pH, "tally")), tM = ""
	Set tLabOnly = $Data(pQ("n", pTo, "lab"), tLab)
	// a hop that closes the chain on a node it bound already (a cycle) only checks the edge to it
	Set tBound = $Get(pB(pTo)), tOnce = (tBound '= "")
	For {
		If tOnce {
			Quit:tM'=""
			Set tM = tBound
			Continue:'$Data(^KG(pSub, pKey, pS, pP, tM))
		} Else {
			Set tM = $Order(^KG(pSub, pKey, pS, pP, tM))
			Quit:tM=""
		}
		Quit:pQ("mayfill")&&(pQ("count")>=pQ("limit"))
		// "both" reads a self-loop through its out entry only: its in entry is the same relationship
		Continue:(pDir = "both")&&(pSub = "in")&&(tM = pS)
		If (pH = 1) && (pQ("split") = "h1") {
			// a worker's share of the first hop: every worker meets the neighbours in the same order
			Set tPos = $Increment(pQ("h1pos")) - 1
			Continue:(tPos#pQ("parts"))'=pQ("part")
		}
		If tOnce {
			// checked when it was bound
		} ElseIf tLabOnly {
			Set tOK = 1
			For tJ = 1:1:$ListLength(tLab) {
				If '$Data(^KG("label", pKey, $List(tLab, tJ), tM)) {
					Set tOK = 0
					Quit
				}
			}
			Continue:'tOK
		} Else {
			Continue:'..NodeOK(.pQ, pTo, pKey, pGName, tM, .pB)
		}
		If 'tUnique {
			// no other hop could match this relationship: no identity to keep, and parallel ones are counted only
			// where each makes a match
			Set tN = 1
			If pQ("mult") && ($Data(^KG(pSub, pKey, pS, pP, tM)) >= 10) {
				Set tN = 0, tK = ""
				For {
					Set tK = $Order(^KG(pSub, pKey, pS, pP, tM, tK))
					Quit:tK=""
					Set:tK'="#" tN = tN + 1
				}
			}
			If tTally {
				Set pQ("w") = pQ("w") + tN
				Continue
			}
			Set:'tOnce pB(pTo) = tM
			For tI = 1:1:tN {
				Do ..Walk(.pQ, pH + 1, pKey, pGName, .pB, pUsed)
			}
			Kill:'tOnce pB(pTo)
			Continue
		}
		Set tSrc = $Select(pSub = "out": pS, 1: tM), tDst = $Select(pSub = "out": tM, 1: pS)
		Kill tKeys
		If $Data(^KG(pSub, pKey, pS, pP, tM)) < 10 {
			Set tKeys($Char(1)) = ""  // one edge, no ekey (a local array takes no "" subscript)
		} Else {
			Set tK = ""
			For {
				Set tK = $Order(^KG(pSub, pKey, pS, pP, tM, tK))
				Quit:tK=""
				Set:tK'="#" tKeys(tK) = ""
			}
		}
		Set tK = ""
		For {
			Set tK = $Order(tKeys(tK))
			Quit:tK=""
			Set tEdge = $ZCRC($ListBuild(tSrc, pP, tDst, $Select(tK = $Char(1): "", 1: tK)), 7)
			Continue:pUsed[(","_tEdge_",")
			Set:'tOnce pB(pTo) = tM
			Do ..Walk(.pQ, pH + 1, pKey, pGName, .pB, pUsed_tEdge_",")
			Kill:'tOnce pB(pTo)
		}
	}
}

/// One complete match: project it, apply DISTINCT, write it; or, when ordering, file its bindings under their sort keys,
/// so only the rows the LIMIT keeps are projected. Rows equal under DISTINCT have equal sort keys (DISTINCT orders by
/// returned columns only), so EmitSorted's dedupe in sort order is DISTINCT's.
/// pW: how many matches this is (a tallied hop's, see Walk); only aggregates are tallied.
ClassMethod Emit(ByRef pQ, pGName As %String, ByRef pB, pW As %Integer = 1) [ Private ]
{
	If pQ("acc") {
		Set pQ("accw") = pQ("accw") + pW
		Quit
	}
	If pQ("agg") {
		Do ..Group(.pQ, pGName, .pB, pW)
		Quit
	}
	If pQ("ordered") {
		Set tBind = $ListBuild(pGName), tV = ""
		For {
			Set tV = $Order(pB(tV), 1, tS)
			Quit:tV=""
			Set tBind = tBind_$ListBuild(tV, tS)
		}
		Set @..SortRef(.pQ, pGName, .pB)@($Increment(pQ("seq"))) = $ListBuild("b", tBind)
		Quit
	}
	Set tJSON = ..Project(.pQ, pGName, .pB)
	Quit:..Seen(.pQ, tJSON)
	Quit:..LimitMet(.pQ)  // parallel relationships of one neighbour arrive between the walk's LIMIT checks
	Do ..Out(.pQ, tJSON)
}

/// Under DISTINCT, whether row pJSON was already written (and, if not, remember it); otherwise 0.
ClassMethod Seen(ByRef pQ, pJSON As %String) As %Boolean [ Private ]
{
	Quit:'pQ("distinct") 0
	Set tHash = $ZCRC(pJSON, 7)
	Quit:$Data(^||IVG.CypherRun("seen", tHash, pJSON)) 1
	Set ^||IVG.CypherRun("seen", tHash, pJSON) = ""
	Quit 0
}

/// Add one match to its group: ^||IVG.CypherRun("g", group) = the keys' JSON, ("g", group, j) = aggregate j.
ClassMethod Group(ByRef pQ, pGName As %String, ByRef pB, pW As %Integer = 1) [ Private ]
{
	// the group is a function of the nodes its keys read: projected once per such binding, not once per match;
	// with no key, one group for the whole query, remembered in pQ("g0")
	If pQ("ret") = "" {
		Set tG = $Get(pQ("g0"))
		If tG = "" {
			Set tG = $Increment(^||IVG.CypherRun("g")), ^||IVG.CypherRun("g", tG) = "[]", pQ("g0") = tG
			Set ^||IVG.CypherRun("grp", $ZCRC("[]", 7), "[]") = tG
		}
	} Else {
		// with no hop every match binds another anchor: a binding never recurs, so it is not cached
		Set tG = "", tSig = ""
		If pQ("hops") > 0 {
			Set tSig = $ListBuild(pGName), tVars = pQ("kvars")
			For tJ = 1:1:$ListLength(tVars) {
				Set tSig = tSig_$ListBuild(pB($List(tVars, tJ)))
			}
			Set tG = $Get(^||IVG.CypherRun("gc", tSig))
		}
		If tG = "" {
			// the keys' values as a $List (a number stays a number, a missing value an empty element) find the group
			// without building its JSON, which only a new group needs; a long one goes by its JSON alone
			Set tVals = ..KeyValues(.pQ, pGName, .pB), tShort = ($Length(tVals) < 400)
			Set:tShort tG = $Get(^||IVG.CypherRun("grpv", tVals))
			If tG = "" {
				Set tKeys = ..Project(.pQ, pGName, .pB), tHash = $ZCRC(tKeys, 7)
				Set tG = $Get(^||IVG.CypherRun("grp", tHash, tKeys))
				If tG = "" {
					Set tG = $Increment(^||IVG.CypherRun("g")), ^||IVG.CypherRun("grp", tHash, tKeys) = tG
					Set ^||IVG.CypherRun("g", tG) = tKeys
				}
				Set:tShort ^||IVG.CypherRun("grpv", tVals) = tG
			}
			Set:pQ("hops")>0 ^||IVG.CypherRun("gc", tSig) = tG
		}
	}
	Set tAggs = pQ("aggs")
	For tJ = 1:1:$ListLength(tAggs) {
		Set tA = $List(tAggs, tJ), tW = pW
		If $List(tA, 1) = "count_distinct" {
			// a node is (graph, id): one subscript holding both, which needs no stand-in for the default graph's ""
			Set tNode = $ListBuild(pGName, pB($List(tA, 2))), tW = 1
			Continue:$Data(^||IVG.CypherRun("gd", tG, tJ, tNode))
			Set ^||IVG.CypherRun("gd", tG, tJ, tNode) = ""
		}
		Do $Increment(^||IVG.CypherRun("g", tG, tJ), tW)
	}
}

/// A label scan with no hop whose matches are only counted, grouped by the scanned node's properties (BI01's shape),
/// in one loop: NodeOK's label checks and comparisons and KeyValues' keys, each property read inline (one $Order and
/// one $ListGet) and once per node, the counts kept in a local array by the keys' values, and each group made once, by
/// Group with its count as the weight. A ranged worker reads its chunks (pQ("chunk", i), see RunA); otherwise pPos is
/// the scan position RunA's split counts (a worker takes every pParts-th). The groups are the per-node path's; they are
/// made in key order, not the order first met, which only an unordered LIMIT or a tie at an ORDER BY's LIMIT can show.
ClassMethod ScanGroups(ByRef pQ, pI As %Integer, pKey, pGName As %String, pLabel As %String, ByRef pPos As %Integer, pPart As %Integer, pParts As %Integer) [ Private ]
{
	Set tIdx = pQ("pidx"), tData = pQ("pdata"), tSlot = pQ("pslot"), tGK = $Select(pGName = "": $Char(0), 1: pGName)
	Set tLab = pQ("n", pI, "labels"), tX = pQ("n", pI, "cmp"), tRet = pQ("ret")
	Set tSplit = (pQ("split") = "scan") && 'pQ("ranged")
	For tCh = 1:1:pQ("chunks") {
		Set $ListBuild(tS, tHi) = pQ("chunk", tCh)
		For {
			Set tS = $Order(^KG("label", pKey, pLabel, tS))
			Quit:tS=""
			Quit:(tHi'="")&&(tS]]tHi)
			Set pPos = pPos + 1
			Continue:tSplit&&((pPos#pParts)'=pPart)
			Set tOK = 1
			For tJ = 1:1:$ListLength(tLab) {
				Continue:$List(tLab, tJ)=pLabel
				If '$Data(^KG("label", pKey, $List(tLab, tJ), tS)) { Set tOK = 0 Quit }
			}
			Continue:'tOK
			Kill tV
			For tJ = 1:1:$ListLength(tX) {
				Set tC = $List(tX, tJ), tProp = $List(tC, 1), tOp = $List(tC, 2), tArg = $List(tC, 3)
				Set tId = $Order(@tIdx@(tGK, tS, tProp, ""))
				If tId = "" { Set tOK = 0 Quit }
				Set tVal = $ListGet($Get(@tData@(tId)), tSlot), tV(tProp) = tVal
				If '$Case(tOp, "=": tVal = tArg, "<>": tVal '= tArg, "<": tArg ]] tVal, ">": tVal ]] tArg, "<=": '(tVal ]] tArg), : '(tArg ]] tVal)) { Set tOK = 0 Quit }
			}
			Continue:'tOK
			Set tVals = ""
			For tJ = 1:1:$ListLength(tRet) {
				Set tItem = $List(tRet, tJ), tProp = $List(tItem, 2)
				If tProp = "__node" {
					Set tVals = tVals_$ListBuild($ListBuild(pGName, tS))
					Continue
				}
				If '$Data(tV(tProp), tVal) {
					Set tId = $Order(@tIdx@(tGK, tS, tProp, ""))
					If tId = "" {
						Set tVals = tVals_$ListBuild()
						Continue
					}
					Set tVal = $ListGet($Get(@tData@(tId)), tSlot)
				}
				Set:$ListGet(tItem,3)'="" tVal = "s"_..Substring(tVal, $List(tItem, 3))
				Set tVals = tVals_$ListBuild(tVal)
			}
			If $Length(tVals) >= 400 {
				// too long for a subscript: grouped at once, as the per-node path does
				Kill tB
				Set tB(pI) = tS
				Do ..Group(.pQ, pGName, .tB, 1)
				Continue
			}
			// "k" first: an ungrouped count has no keys, and "" is no subscript
			Set tK = "k"_tVals, tN = $Get(tCount(tK)) + 1, tCount(tK) = tN
			If tN = 1 {
				Set tRep(tK) = tS
				// near-unique keys would hold a local entry per node: past 50,000 the counts go to Group (^||, which spills
				// to IRISTEMP) and start again; Group adds a key's later counts to its group
				If $Increment(tDistinct) >= 50000 {
					Do ..ScanGroupsFlush(.pQ, pI, pGName, .tCount, .tRep)
					Set tDistinct = 0
				}
			}
		}
	}
	Do ..ScanGroupsFlush(.pQ, pI, pGName, .tCount, .tRep)
}

/// ScanGroups' counts so far, each key's made a group (or added to its group) by Group with the count as the weight.
ClassMethod ScanGroupsFlush(ByRef pQ, pI As %Integer, pGName As %String, ByRef pCount, ByRef pRep) [ Private ]
{
	Set tK = ""
	For {
		Set tK = $Order(pCount(tK), 1, tN)
		Quit:tK=""
		Kill tB
		Set tB(pI) = pRep(tK)
		Do ..Group(.pQ, pGName, .tB, tN)
	}
	Kill pCount, pRep
}

/// An aggregate over an aggregate: the first stage's groups (^||IVG.CypherRun("g")) become rows, filtered by the
/// stage's WHERE (a column against a number), and are grouped again by the RETURN's keys, its counts made over the
/// rows; the second stage's groups replace the first's, and its columns and order pQ's, so EmitGroups writes them.
ClassMethod Stage2(pStage As %DynamicObject, ByRef pQ) [ Private ]
{
	Set tKeys = pStage.%Get("keys"), tWhere = pStage.%Get("where"), tAggs = pStage.%Get("aggs")
	Set tG = ""
	For {
		Set tG = $Order(^||IVG.CypherRun("g", tG))
		Quit:tG=""
		// the first-stage row (pQ's columns are still the first stage's)
		Set tRow = ..GroupRow(.pQ, tG)
		Set tOK = 1, tIter = tWhere.%GetIterator()
		While tOK && tIter.%GetNext(.tW, .tCond) {
			Set tV = tRow.%Get(tCond.%Get(0)), tOp = tCond.%Get(1), tN = tCond.%Get(2)
			If '$IsValidNum(tV) {
				Set tOK = 0
			} Else {
				Set tOK = $Case(tOp, "<": tV < tN, "<=": tV <= tN, ">": tV > tN, ">=": tV >= tN, "=": tV = tN, : tV '= tN)
			}
		}
		Continue:'tOK
		Set tK2 = ##class(%Library.DynamicArray).%New(), tIter = tKeys.%GetIterator()
		While tIter.%GetNext(.tW, .tCol) {
			If tRow.%GetTypeOf(tCol) = "null" {
				Do tK2.%Push("", "null")
			} Else {
				Do tK2.%Push(tRow.%Get(tCol))
			}
		}
		Set tKey2 = tK2.%ToJSON(), tHash = $ZCRC(tKey2, 7), tG2 = $Get(^||IVG.CypherRun("grp2", tHash, tKey2))
		If tG2 = "" {
			Set tG2 = $Increment(^||IVG.CypherRun("g2")), ^||IVG.CypherRun("grp2", tHash, tKey2) = tG2
			Set ^||IVG.CypherRun("g2", tG2) = tKey2
		}
		Set tIter = tAggs.%GetIterator()
		While tIter.%GetNext(.tW, .tA) {
			Set tJ = tW + 1
			If tA.%Get("kind") = "count_distinct" {
				// count(DISTINCT) skips nulls; a value is its JSON (a node's identity) or its text
				Set tV = tRow.%Get(tA.%Get("col"))
				Set tV = $Select($IsObject(tV): tV.%ToJSON(), tRow.%GetTypeOf(tA.%Get("col")) = "null": "", 1: "v"_tV)
				Continue:tV=""
				Continue:$Data(^||IVG.CypherRun("g2d", tG2, tJ, tV))
				Set ^||IVG.CypherRun("g2d", tG2, tJ, tV) = ""
			}
			Do $Increment(^||IVG.CypherRun("g2", tG2, tJ))
		}
	}
	Kill ^||IVG.CypherRun("g"), ^||IVG.CypherRun("grp")
	Merge ^||IVG.CypherRun("g") = ^||IVG.CypherRun("g2")
	Kill ^||IVG.CypherRun("g2"), ^||IVG.CypherRun("g2d"), ^||IVG.CypherRun("grp2")
	// the second stage's columns, order and keys: EmitGroups reads these
	Set pQ("cols") = "", tIter = pStage.%Get("cols").%GetIterator()
	While tIter.%GetNext(.tW, .tC) { Set pQ("cols") = pQ("cols")_$ListBuild($ListBuild(tC.%Get(0), tC.%Get(1))) }
	Set pQ("ord") = "", tIter = pStage.%Get("order").%GetIterator()
	While tIter.%GetNext(.tW, .tO) { Set pQ("ord") = pQ("ord")_$ListBuild($ListBuild(tO.%Get(0), "", ''tO.%Get(1))) }
	Set pQ("ret") = $Select(tKeys.%Size() > 0: "keys", 1: "")
}

/// Write one row per group (with no grouping key, one row even when nothing matched), through the sort tree when the
/// query orders: an ORDER BY item names a RETURN column, keyed by the SQL path's sort key as SortRef does.
ClassMethod EmitGroups(ByRef pQ) [ Private ]
{
	If (pQ("ret") = "") && '$Data(^||IVG.CypherRun("g")) {
		Set ^||IVG.CypherRun("g") = 1, ^||IVG.CypherRun("g", 1) = "[]"
	}
	Set tCols = pQ("cols"), tOrd = pQ("ord"), tN = $ListLength(tOrd), tG = ""
	// LIMIT k behind a count that orders first: the k-th best count bounds the groups worth sorting (ties included)
	Set tBound = "", tFirst = $Select(tN > 0: $List(tCols, $List($List(tOrd, 1), 1) + 1), 1: "")
	If (pQ("limit") >= 0) && (tFirst '= "") && ($List(tFirst, 1) = "a") {
		Set tDesc = $List($List(tOrd, 1), 3), tA = $List(tFirst, 2) + 1
		For {
			Set tG = $Order(^||IVG.CypherRun("g", tG))
			Quit:tG=""
			Do $Increment(^||IVG.CypherRun("top", +$Get(^||IVG.CypherRun("g", tG, tA))))
		}
		Set tV = "", tSeen = 0
		For {
			Set tV = $Order(^||IVG.CypherRun("top", tV), $Select(tDesc: -1, 1: 1), tHere)
			Quit:tV=""
			Set tSeen = tSeen + tHere, tBound = tV
			Quit:tSeen>=pQ("limit")
		}
		Kill ^||IVG.CypherRun("top")
	}
	For {
		Set tG = $Order(^||IVG.CypherRun("g", tG), 1, tKeyJSON)
		Quit:tG=""
		If tBound '= "" {
			Set tV = +$Get(^||IVG.CypherRun("g", tG, tA))
			Continue:$Select(tDesc: tV < tBound, 1: tV > tBound)
		}
		If tN = 0 {
			Quit:..LimitMet(.pQ)
			Set ^||IVG.CypherOut(pQ("tag"), $Increment(pQ("count"))) = ..GroupRow(.pQ, tG).%ToJSON()
			Continue
		}
		// the group files under its sort keys, its row built only if the LIMIT keeps it (EmitSorted): a count sorts as
		// the number it is (a column of counts holds nothing else), a key by the SQL path's CYSORTKEY ("" for null,
		// which it files last, as it does a null)
		Set tKeys = ""
		For tJ = 1:1:tN {
			Set tC = $List(tCols, $List($List(tOrd, tJ), 1) + 1), tI = $List(tC, 2)
			If $List(tC, 1) = "a" {
				Set tSub(tJ) = +$Get(^||IVG.CypherRun("g", tG, tI + 1))
				Continue
			}
			Set:tKeys="" tKeys = ##class(%Library.DynamicArray).%FromJSON(tKeyJSON)
			Set tSub(tJ) = ##class(User.funcCYSORTKEY).CYSORTKEY($Select(tKeys.%GetTypeOf(tI) = "null": "", 1: tKeys.%Get(tI)), "x")
		}
		Set tSeq = $Increment(pQ("seq")), tEntry = $ListBuild("g", tG)
		If tN = 1 {
			Set ^||IVG.CypherRun("sort", tSub(1), tSeq) = tEntry
		} ElseIf tN = 2 {
			Set ^||IVG.CypherRun("sort", tSub(1), tSub(2), tSeq) = tEntry
		} ElseIf tN = 3 {
			Set ^||IVG.CypherRun("sort", tSub(1), tSub(2), tSub(3), tSeq) = tEntry
		} Else {
			Set tRef = $Name(^||IVG.CypherRun("sort"))
			For tJ = 1:1:tN {
				Set tRef = $Name(@tRef@(tSub(tJ)))
			}
			Set @tRef@(tSeq) = tEntry
		}
	}
	Do:tN>0 ..EmitSorted(.pQ, $Name(^||IVG.CypherRun("sort")), 1)
}

/// Group pG's row over pQ's columns: its keys in their stored types, its aggregates as counted.
ClassMethod GroupRow(ByRef pQ, pG As %Integer) As %Library.DynamicArray [ Private ]
{
	Set tCols = pQ("cols"), tKeys = ##class(%Library.DynamicArray).%FromJSON(^||IVG.CypherRun("g", pG))
	Set tRow = ##class(%Library.DynamicArray).%New()
	For tJ = 1:1:$ListLength(tCols) {
		Set tC = $List(tCols, tJ), tI = $List(tC, 2)
		If $List(tC, 1) = "a" {
			Do tRow.%Push(+$Get(^||IVG.CypherRun("g", pG, tI + 1)))
		} ElseIf tKeys.%GetTypeOf(tI) = "null" {
			Do tRow.%Push("", "null")
		} Else {
			Do tRow.%Push(tKeys.%Get(tI))
		}
	}
	Quit tRow
}

/// The sort tree node a match files under: one level per ORDER BY item, keyed by the SQL path's own sort key
/// (SQLUser.CY_SORT_KEY), so the order is the SQL path's.
ClassMethod SortRef(ByRef pQ, pGName As %String, ByRef pB) As %String [ Private ]
{
	Set tRef = $Name(^||IVG.CypherRun("sort")), tOrd = pQ("ord")
	For tJ = 1:1:$ListLength(tOrd) {
		Set tItem = $List(tOrd, tJ)
		Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pB($List(tItem, 1)), $List(tItem, 2), .tFound)
		Set tRef = $Name(@tRef@(##class(User.funcCYSORTKEY).CYSORTKEY($Select(tFound: tVal, 1: ""), "x")))
	}
	Quit tRef
}

/// The grouping keys' values for one match, as a $List: what Project writes as JSON, without the JSON. A node key is
/// $ListBuild(graph, node); a missing property an empty element.
ClassMethod KeyValues(ByRef pQ, pGName As %String, ByRef pB) As %List [ Private ]
{
	Set tVals = "", tRet = pQ("ret")
	For tJ = 1:1:$ListLength(tRet) {
		Set tItem = $List(tRet, tJ)
		If $List(tItem, 2) = "__node" {
			Set tVals = tVals_$ListBuild($ListBuild(pGName, pB($List(tItem, 1))))
			Continue
		}
		Set tVar = $List(tItem, 1), tS = pB(tVar), tProp = $List(tItem, 2), tFound = 1
		If $Data(pQ("pv", tVar, tProp), tM) && ($List(tM, 2) = tS) && ($List(tM, 1) = pGName) {
			// read by NodeOK for this very node
			Set tVal = $List(tM, 3)
		} ElseIf pQ("pd") {
			Set tId = $Order(@pQ("pidx")@($Select(pGName = "": $Char(0), 1: pGName), tS, tProp, ""))
			Set tFound = (tId '= "")
			Set:tFound tVal = $ListGet($Get(@pQ("pdata")@(tId)), pQ("pslot"))
		} Else {
			Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, tS, tProp, .tFound)
		}
		If 'tFound {
			Set tVals = tVals_$ListBuild()
			Continue
		}
		// a substring is a string, whatever it looks like
		Set:$ListGet(tItem,3)'="" tVal = "s"_..Substring(tVal, $List(tItem, 3))
		Set tVals = tVals_$ListBuild(tVal)
	}
	Quit tVals
}

/// A key's function pFn, $ListBuild("substring", start, length or -1), of value pVal: the SQL path's
/// SUBSTRING(val, start + 1, length), a string. KeyValues and Project both take it, so they agree on every value.
ClassMethod Substring(pVal As %String, pFn As %List) As %String [ Private ]
{
	Set tStart = $List(pFn, 2) + 1, tLen = $List(pFn, 3)
	Quit:tLen<0 $Extract(pVal, tStart, *)
	Quit $Extract(pVal, tStart, tStart + tLen - 1)
}

/// The RETURN row of one match, as JSON: each value keeps its stored type (the SQL driver reports a numeric `val` as a
/// number); a missing property is null.
ClassMethod Project(ByRef pQ, pGName As %String, ByRef pB) As %String [ Private ]
{
	Set tRow = ##class(%Library.DynamicArray).%New(), tRet = pQ("ret")
	For tJ = 1:1:$ListLength(tRet) {
		Set tItem = $List(tRet, tJ)
		If $List(tItem, 2) = "__node" {
			// a first-stage key that is the node itself: its identity, [graph, id]
			Do tRow.%Push(##class(%Library.DynamicArray).%New().%Push(pGName).%Push(pB($List(tItem, 1))))
			Continue
		}
		Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pB($List(tItem, 1)), $List(tItem, 2), .tFound)
		If 'tFound {
			Do tRow.%Push("", "null")
		} ElseIf $ListGet(tItem, 3) '= "" {
			Do tRow.%Push(..Substring(tVal, $List(tItem, 3)), "string")
		} Else {
			Do tRow.%Push(tVal)  // untyped: a stored number stays a number, a string a string, as the driver reports them
		}
	}
	Quit tRow.%ToJSON()
}

/// Write the sort tree's rows in order: level pLevel is ORDER BY item pLevel (a descending item walks backwards), the
/// last level is arrival order.
ClassMethod EmitSorted(ByRef pQ, pRef As %String, pLevel As %Integer) [ Private ]
{
	Set tN = $ListLength(pQ("ord"))
	Set tStep = 1
	If (pLevel <= tN) && $List($List(pQ("ord"), pLevel), 3) {
		Set tStep = -1
	}
	Set tK = ""
	For {
		Set tK = $Order(@pRef@(tK), tStep, tRow)
		Quit:tK=""
		Quit:..LimitMet(.pQ)
		If pLevel <= tN {
			Do ..EmitSorted(.pQ, $Name(@pRef@(tK)), pLevel + 1)
			Continue
		}
		If $List(tRow, 1) = "j" {
			// a worker's row, merged here (Parallel): two workers may have written the same DISTINCT row
			Set tJSON = $List(tRow, 2)
			Continue:..Seen(.pQ, tJSON)
		} ElseIf $List(tRow, 1) = "g" {
			Set tJSON = ..GroupRow(.pQ, $List(tRow, 2)).%ToJSON()
		} Else {
			Kill tB
			Set tBind = $List(tRow, 2)
			For tI = 2:2:$ListLength(tBind) {
				Set tB($List(tBind, tI)) = $List(tBind, tI + 1)
			}
			Set tJSON = ..Project(.pQ, $List(tBind, 1), .tB)
			Continue:..Seen(.pQ, tJSON)
		}
		Do ..Out(.pQ, tJSON, pRef)
	}
}

/// Write row pJSON: to this run's result, or, in a worker (pQ("share")), to the shared rows: in arrival order, or, for
/// an ordered query, under the sort keys pRef (a node of this worker's sort tree) names, so the coordinator merges the
/// workers' rows in one walk of the shared tree.
ClassMethod Out(ByRef pQ, pJSON As %String, pRef As %String = "") [ Private ]
{
	If pQ("share") = "" {
		Set ^||IVG.CypherOut(pQ("tag"), $Increment(pQ("count"))) = pJSON
		Quit
	}
	Do $Increment(pQ("count"))
	Set tSeq = $Increment(^IRIS.Temp.IVG.Cypher(pQ("share"), "seq"))
	If pRef = "" {
		Set ^IRIS.Temp.IVG.Cypher(pQ("share"), "rows", tSeq) = pJSON
		Quit
	}
	Set tRef = $Name(^IRIS.Temp.IVG.Cypher(pQ("share"), "sort"))
	For tI = 2:1:$QLength(pRef) {
		Set tRef = $Name(@tRef@($QSubscript(pRef, tI)))
	}
	Set @tRef@(tSeq) = $ListBuild("j", pJSON)
}

/// Rows pStart .. pStart + pCount - 1 of result pTag, as a JSON array of row arrays.
ClassMethod ReadPage(pTag As %Integer, pStart As %Integer, pCount As %Integer = 1000) As %String
{
	Set tOut = "["
	For tN = pStart:1:(pStart + pCount - 1) {
		Quit:'$Data(^||IVG.CypherOut(pTag, tN), tRow)
		Set tOut = tOut_$Select(tN = pStart: "", 1: ",")_tRow
	}
	Quit tOut_"]"
}

/// Free result pTag.
ClassMethod Drop(pTag As %Integer)
{
	Kill ^||IVG.CypherOut(pTag)
}

}
