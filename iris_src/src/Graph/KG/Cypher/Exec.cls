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

/// Run pPlan (JSON) with pParams (JSON object); returns Head's {"tag", "count"[, "rows"]}.
ClassMethod Run(pPlan As %String, pParams As %String = "{}") As %String
{
	Set tPlan = ##class(%Library.DynamicObject).%FromJSON(pPlan)
	Set tTag = $Increment(^||IVG.CypherOut)
	Kill ^||IVG.CypherOut(tTag)
	Set tCount = 0
	If tPlan.%Get("family") = "A" {
		Do ##class(Graph.KG.Cypher.Props).Begin()
		Try {
			Set tCount = ..RunA(tPlan, tTag)
		} Catch tEx {
			Do ##class(Graph.KG.Cypher.Props).End()
			Kill ^||IVG.CypherOut(tTag)
			Throw tEx
		}
		Do ##class(Graph.KG.Cypher.Props).End()
		Quit ..Head(tTag, tCount)
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

/// Run's answer: {"tag", "count"}, plus the rows themselves ("rows") when they fit one page, in which case the
/// result is already freed and the caller makes no further call.
ClassMethod Head(pTag As %Integer, pCount As %Integer) As %String [ Private ]
{
	Set tHead = "{""tag"":"_pTag_",""count"":"_pCount
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
ClassMethod RunA(pPlan As %DynamicObject, pTag As %Integer) As %Integer [ Private ]
{
	Set tScoped = (pPlan.%GetTypeOf("graph") = "string")
	Set tGraph = $Select(tScoped: pPlan.%Get("graph"), 1: "")
	Set tLabel = ..Smallest(pPlan, tScoped, tGraph)
	Do ..Compile(pPlan, .tQ)
	Set tAnchor = pPlan.%Get("anchor")
	Set tQ("limit") = $Select(pPlan.%GetTypeOf("limit") = "number": pPlan.%Get("limit"), 1: -1)
	Set tQ("ordered") = ($ListLength(tQ("ord")) > 0), tQ("distinct") = pPlan.%Get("distinct")
	Set tQ("count") = 0, tQ("seq") = 0, tQ("tag") = pTag
	// ^||IVG.CypherRun: the walk's scratch (candidates, distinct keys, the sort tree), killed before and after
	Kill ^||IVG.CypherRun
	Do ..Anchors(pPlan.%Get("nodes").%Get(tAnchor), tScoped, tGraph, tLabel)
	Set tC = ""
	For {
		Set tC = $Order(^||IVG.CypherRun("cand", tC), 1, tCand)
		Quit:tC=""
		Quit:..Full(.tQ)
		Set tKey = $List(tCand, 1), tGName = $List(tCand, 2), tS = $List(tCand, 3)
		Continue:'..NodeOK(.tQ, tAnchor, tKey, tGName, tS)
		Kill tB
		Set tB(tAnchor) = tS, tQ("accw") = 0
		Do ..Walk(.tQ, 1, tKey, tGName, .tB, ",")
		// every match of this anchor falls in one group: Emit summed them into tQ("accw"), grouped once here
		Do:tQ("acc")&&(tQ("accw")>0) ..Group(.tQ, tGName, .tB, tQ("accw"))
	}
	If tQ("agg") {
		Do ..EmitGroups(.tQ)
	} ElseIf tQ("ordered") {
		Do ..EmitSorted(.tQ, $Name(^||IVG.CypherRun("sort")), 1)
	}
	Kill ^||IVG.CypherRun
	Quit tQ("count")
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
		Set tVar = tR.%Get(0), pQ("ret") = pQ("ret")_$ListBuild($ListBuild(tVar, tR.%Get(1)))
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

/// Whether a LIMIT without ORDER BY is already met (an ordered query collects every row first).
ClassMethod Full(ByRef pQ) As %Boolean [ Private ]
{
	Quit 'pQ("ordered") && ..LimitMet(.pQ)
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
	Set tN = 0, tIter = tAlts.%GetIterator()
	While tIter.%GetNext(.tA, .tAlt) {
		Set tLabels = pPlan.%Get("nodes").%Get(tAlt.%Get("anchor")).%Get("labels"), tLI = tLabels.%GetIterator()
		While tLI.%GetNext(.tJ, .tL) {
			Set tCurAlt($Increment(tN)) = tA, tCurLabel(tN) = tL, tCurKey(tN) = $Order(tKeys("")), tCurS(tN) = ""
		}
	}
	Set tWin = ""
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

/// The anchor's candidates: ^||IVG.CypherRun("cand", n) = $ListBuild(graph key, graph name, node).
/// pLabel: the label to scan when the anchor is a label scan ("" = the node's first).
ClassMethod Anchors(pSpec As %DynamicObject, pScoped As %Boolean, pGraph As %String, pLabel As %String = "") [ Private ]
{
	Set tN = 0
	If pSpec.%Get("props").%Size() > 0 {
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
		Quit
	}
	Set tLabel = $Select(pLabel '= "": pLabel, 1: pSpec.%Get("labels").%Get(0))
	Do ..GraphKeys(pScoped, pGraph, .tKeys)
	Set g = ""
	For {
		Set g = $Order(tKeys(g))
		Quit:g=""
		Set tGName = $Select(g = 0: "", 1: g)
		Set tS = ""
		For {
			Set tS = $Order(^KG("label", g, tLabel, tS))
			Quit:tS=""
			Set ^||IVG.CypherRun("cand", $Increment(tN)) = $ListBuild(g, tGName, tS)
		}
	}
}

/// Whether node pS of graph pKey carries node pI's labels and properties.
ClassMethod NodeOK(ByRef pQ, pI As %Integer, pKey, pGName As %String, pS As %String) As %Boolean [ Private ]
{
	Set tL = pQ("n", pI, "labels")
	For tJ = 1:1:$ListLength(tL) {
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
	Set tX = pQ("n", pI, "cmp")
	For tJ = 1:1:$ListLength(tX) {
		Set tC = $List(tX, tJ), tOp = $List(tC, 2), tArg = $List(tC, 3)
		Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pS, $List(tC, 1), .tFound)
		Return:'tFound 0
		If tOp = "<>" {
			Return:(tVal=tArg) 0
		} ElseIf tOp = "<" {
			Return:'(tArg]]tVal) 0
		} ElseIf tOp = ">" {
			Return:'(tVal]]tArg) 0
		} ElseIf tOp = "<=" {
			Return:(tVal]]tArg) 0
		} Else {  // >=
			Return:(tArg]]tVal) 0
		}
	}
	Quit 1
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
		Do ##class(Graph.KG.VarLength).EndsInto(tS, $List(tVL, 3), tDir, $List(tVL, 1), $List(tVL, 2), 0, pGName, .tEnds)
		For tI = 1:1:tEnds {
			Quit:..Full(.pQ)
			Set tM = $List(tEnds(tI), 1)
			Continue:'..NodeOK(.pQ, tTo, pKey, pGName, tM)
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
	For {
		Set tM = $Order(^KG(pSub, pKey, pS, pP, tM))
		Quit:tM=""
		Quit:..Full(.pQ)
		// "both" reads a self-loop through its out entry only: its in entry is the same relationship
		Continue:(pDir = "both")&&(pSub = "in")&&(tM = pS)
		If tLabOnly {
			Set tOK = 1
			For tJ = 1:1:$ListLength(tLab) {
				If '$Data(^KG("label", pKey, $List(tLab, tJ), tM)) {
					Set tOK = 0
					Quit
				}
			}
			Continue:'tOK
		} Else {
			Continue:'..NodeOK(.pQ, pTo, pKey, pGName, tM)
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
			Set pB(pTo) = tM
			For tI = 1:1:tN {
				Do ..Walk(.pQ, pH + 1, pKey, pGName, .pB, pUsed)
			}
			Kill pB(pTo)
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
			Set pB(pTo) = tM
			Do ..Walk(.pQ, pH + 1, pKey, pGName, .pB, pUsed_tEdge_",")
			Kill pB(pTo)
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
	Set ^||IVG.CypherOut(pQ("tag"), $Increment(pQ("count"))) = tJSON
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
	// the group is a function of the nodes its keys read: projected once per such binding, not once per match
	Set tSig = $ListBuild(pGName), tVars = pQ("kvars")
	For tJ = 1:1:$ListLength(tVars) {
		Set tSig = tSig_$ListBuild(pB($List(tVars, tJ)))
	}
	Set tG = $Get(^||IVG.CypherRun("gc", tSig))
	If tG = "" {
		Set tKeys = $Select(pQ("ret") = "": "[]", 1: ..Project(.pQ, pGName, .pB)), tHash = $ZCRC(tKeys, 7)
		Set tG = $Get(^||IVG.CypherRun("grp", tHash, tKeys))
		If tG = "" {
			Set tG = $Increment(^||IVG.CypherRun("g")), ^||IVG.CypherRun("grp", tHash, tKeys) = tG
			Set ^||IVG.CypherRun("g", tG) = tKeys
		}
		Set ^||IVG.CypherRun("gc", tSig) = tG
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

/// Write one row per group (with no grouping key, one row even when nothing matched), through the sort tree when the
/// query orders: an ORDER BY item names a RETURN column, keyed by the SQL path's sort key as SortRef does.
ClassMethod EmitGroups(ByRef pQ) [ Private ]
{
	If (pQ("ret") = "") && '$Data(^||IVG.CypherRun("g")) {
		Set ^||IVG.CypherRun("g") = 1, ^||IVG.CypherRun("g", 1) = "[]"
	}
	Set tSort = $Name(^||IVG.CypherRun("sort")), tCols = pQ("cols"), tOrd = pQ("ord"), tG = ""
	For {
		Set tG = $Order(^||IVG.CypherRun("g", tG), 1, tKeyJSON)
		Quit:tG=""
		Set tKeys = ##class(%Library.DynamicArray).%FromJSON(tKeyJSON), tRow = ##class(%Library.DynamicArray).%New()
		For tJ = 1:1:$ListLength(tCols) {
			Set tC = $List(tCols, tJ), tI = $List(tC, 2)
			If $List(tC, 1) = "a" {
				Do tRow.%Push(+$Get(^||IVG.CypherRun("g", tG, tI + 1)))
			} ElseIf tKeys.%GetTypeOf(tI) = "null" {
				Do tRow.%Push("", "null")
			} Else {
				Do tRow.%Push(tKeys.%Get(tI))
			}
		}
		If tOrd = "" {
			Quit:..LimitMet(.pQ)
			Set ^||IVG.CypherOut(pQ("tag"), $Increment(pQ("count"))) = tRow.%ToJSON()
			Continue
		}
		Set tRef = tSort
		For tJ = 1:1:$ListLength(tOrd) {
			// a null column reads as "", which CYSORTKEY files last, as it does a null
			Set tCol = $List($List(tOrd, tJ), 1)
			Set tRef = $Name(@tRef@(##class(User.funcCYSORTKEY).CYSORTKEY(tRow.%Get(tCol), "x")))
		}
		Set @tRef@($Increment(pQ("seq"))) = $ListBuild("j", tRow.%ToJSON())
	}
	Do:tOrd'="" ..EmitSorted(.pQ, tSort, 1)
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

/// The RETURN row of one match, as JSON: each value keeps its stored type (the SQL driver reports a numeric `val` as a
/// number); a missing property is null.
ClassMethod Project(ByRef pQ, pGName As %String, ByRef pB) As %String [ Private ]
{
	Set tRow = ##class(%Library.DynamicArray).%New(), tRet = pQ("ret")
	For tJ = 1:1:$ListLength(tRet) {
		Set tItem = $List(tRet, tJ)
		Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pB($List(tItem, 1)), $List(tItem, 2), .tFound)
		If tFound {
			Do tRow.%Push(tVal)  // untyped: a stored number stays a number, a string a string, as the driver reports them
		} Else {
			Do tRow.%Push("", "null")
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
			Set tJSON = $List(tRow, 2)
		} Else {
			Kill tB
			Set tBind = $List(tRow, 2)
			For tI = 2:2:$ListLength(tBind) {
				Set tB($List(tBind, tI)) = $List(tBind, tI + 1)
			}
			Set tJSON = ..Project(.pQ, $List(tBind, 1), .tB)
			Continue:..Seen(.pQ, tJSON)
		}
		Set ^||IVG.CypherOut(pQ("tag"), $Increment(pQ("count"))) = tJSON
	}
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
