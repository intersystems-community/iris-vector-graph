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
/// either a projection or one aggregate. Returns the number of rows written under pTag.
///
/// The plan is compiled into local arrays once (pQ), so the walk's inner loops touch no %DynamicObject:
///   pQ("n", i, "labels") = $ListBuild(label, ...)      pQ("n", i, "props") = $ListBuild($ListBuild(key, value), ...)
///   pQ("n", i, "neq") = $ListBuild($ListBuild(key, value), ...), the `<>` filters
///   pQ("h", k) = $ListBuild(from, to, dir, $ListBuild(type, ...))   pQ("hops") = number of hops
///   pQ("h", k, "vl") = $ListBuild(min, max, types JSON), for a variable-length hop
///   pQ("agg") = "" | "count" | "count_distinct"         pQ("aggvar") = the counted node   pQ("aggn") = the count
///   pQ("ret") = $ListBuild($ListBuild(var, key), ...)  pQ("ord") = $ListBuild($ListBuild(var, key, desc), ...)
ClassMethod RunA(pPlan As %DynamicObject, pTag As %Integer) As %Integer [ Private ]
{
	Set tScoped = (pPlan.%GetTypeOf("graph") = "string")
	Set tGraph = $Select(tScoped: pPlan.%Get("graph"), 1: "")
	Do ..Compile(pPlan, .tQ)
	Set tAnchor = pPlan.%Get("anchor")
	Set tQ("limit") = $Select(pPlan.%GetTypeOf("limit") = "number": pPlan.%Get("limit"), 1: -1)
	Set tQ("ordered") = ($ListLength(tQ("ord")) > 0), tQ("distinct") = pPlan.%Get("distinct")
	Set tQ("count") = 0, tQ("seq") = 0, tQ("tag") = pTag
	// ^||IVG.CypherRun: the walk's scratch (candidates, distinct keys, the sort tree), killed before and after
	Kill ^||IVG.CypherRun
	Do ..Anchors(pPlan.%Get("nodes").%Get(tAnchor), tScoped, tGraph)
	Set tC = ""
	For {
		Set tC = $Order(^||IVG.CypherRun("cand", tC), 1, tCand)
		Quit:tC=""
		Quit:..Full(.tQ)
		Set tKey = $List(tCand, 1), tGName = $List(tCand, 2), tS = $List(tCand, 3)
		Continue:'..NodeOK(.tQ, tAnchor, tKey, tGName, tS)
		Kill tB
		Set tB(tAnchor) = tS
		Do ..Walk(.tQ, 1, tKey, tGName, .tB, ",")
	}
	If tQ("ordered") {
		Do ..EmitSorted(.tQ, $Name(^||IVG.CypherRun("sort")), 1)
	}
	If tQ("agg") '= "" {
		Set ^||IVG.CypherOut(pTag, $Increment(tQ("count"))) = "["_tQ("aggn")_"]"
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
		Set pQ("n", tI, "props") = ..Pairs(tNode.%Get("props")), pQ("n", tI, "neq") = ..Pairs(tNode.%Get("neq"))
	}
	Set pQ("hops") = 0, tIter = pPlan.%Get("hops").%GetIterator()
	While tIter.%GetNext(.tI, .tHop) {
		Set tTypes = ..ToList(tHop.%Get("types"))
		Set pQ("h", $Increment(pQ("hops"))) = $ListBuild(tHop.%Get("from"), tHop.%Get("to"), tHop.%Get("dir"), tTypes)
		If tHop.%IsDefined("min") {
			// variable length: its bounds and its types as the JSON Graph.KG.VarLength.EndsInto takes
			Set pQ("h", pQ("hops"), "vl") = $ListBuild(tHop.%Get("min"), tHop.%Get("max"), tHop.%Get("types").%ToJSON())
		}
	}
	Set pQ("agg") = "", pQ("aggvar") = "", pQ("aggn") = 0
	If pPlan.%GetTypeOf("agg") = "object" {
		Set tAgg = pPlan.%Get("agg")
		Set pQ("agg") = tAgg.%Get("kind")
		Set:tAgg.%IsDefined("var") pQ("aggvar") = tAgg.%Get("var")
	}
	Set pQ("ret") = "", tIter = pPlan.%Get("returns").%GetIterator()
	While tIter.%GetNext(.tI, .tR) { Set pQ("ret") = pQ("ret")_$ListBuild($ListBuild(tR.%Get(0), tR.%Get(1))) }
	Set pQ("ord") = "", tIter = pPlan.%Get("order").%GetIterator()
	While tIter.%GetNext(.tI, .tO) {
		Set pQ("ord") = pQ("ord")_$ListBuild($ListBuild(tO.%Get(0), tO.%Get(1), ''tO.%Get(2)))
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

/// The anchor's candidates: ^||IVG.CypherRun("cand", n) = $ListBuild(graph key, graph name, node).
ClassMethod Anchors(pSpec As %DynamicObject, pScoped As %Boolean, pGraph As %String) [ Private ]
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
	Set tLabel = pSpec.%Get("labels").%Get(0)
	If pScoped {
		Set tKeys(##class(Graph.KG.GraphKey).ForIndex(pGraph)) = ""
	} Else {
		Set g = ""
		For {
			Set g = $Order(^KG("label", g))
			Quit:g=""
			Set tKeys(g) = ""
		}
	}
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
	// `<>` on a missing property is null, which filters the row out (the SQL path's three-valued comparison)
	Set tX = pQ("n", pI, "neq")
	For tJ = 1:1:$ListLength(tX) {
		Set tPair = $List(tX, tJ)
		Set tVal = ##class(Graph.KG.Cypher.Props).Get(pGName, pS, $List(tPair, 1), .tFound)
		Return:('tFound)||(tVal = $List(tPair, 2)) 0
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
}

ClassMethod Neighbours(ByRef pQ, pH, pSub, pDir, pKey, pGName, pS, pP, pTo, ByRef pB, pUsed) [ Private ]
{
	Set tM = ""
	For {
		Set tM = $Order(^KG(pSub, pKey, pS, pP, tM))
		Quit:tM=""
		Quit:..Full(.pQ)
		// "both" reads a self-loop through its out entry only: its in entry is the same relationship
		Continue:(pDir = "both")&&(pSub = "in")&&(tM = pS)
		Continue:'..NodeOK(.pQ, pTo, pKey, pGName, tM)
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

/// One complete match: project it (or, when ordering without DISTINCT, file its bindings under their sort keys so only
/// the rows the LIMIT keeps are projected), apply DISTINCT, write it.
ClassMethod Emit(ByRef pQ, pGName As %String, ByRef pB) [ Private ]
{
	If pQ("agg") = "count" {
		Set pQ("aggn") = pQ("aggn") + 1
		Quit
	}
	If pQ("agg") = "count_distinct" {
		// a node is (graph, id): one subscript holding both, which needs no stand-in for the default graph's ""
		Set tNode = $ListBuild(pGName, pB(pQ("aggvar")))
		If '$Data(^||IVG.CypherRun("aggd", tNode)) {
			Set ^||IVG.CypherRun("aggd", tNode) = ""
			Set pQ("aggn") = pQ("aggn") + 1
		}
		Quit
	}
	If pQ("ordered") && 'pQ("distinct") {
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
	If pQ("distinct") {
		Set tHash = $ZCRC(tJSON, 7)
		Quit:$Data(^||IVG.CypherRun("seen", tHash, tJSON))
		Set ^||IVG.CypherRun("seen", tHash, tJSON) = ""
	}
	If 'pQ("ordered") {
		Set ^||IVG.CypherOut(pQ("tag"), $Increment(pQ("count"))) = tJSON
		Quit
	}
	Set @..SortRef(.pQ, pGName, .pB)@($Increment(pQ("seq"))) = $ListBuild("j", tJSON)
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
