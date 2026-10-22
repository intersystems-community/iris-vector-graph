/// Spec 255: the paged temporal window reader, with edge attributes, and the latest event per source.
///
/// QueryWindow answers a window as one %String, which stops at 3,641,144 characters (about 35 k edges), its source
/// filter still walks every source at every timestamp, and each edge's attributes cost the caller a GetEdgeAttrs round
/// trip. This reader returns the same rows, attributes included, in pages through a cursor:
///
///   Open(graphId, spec) -> {"h", "rows", "done"}   the first page; a window that fits in it is freed at once
///   Next(h, n)          -> {"h", "rows", "done"}   the next page; "done" frees the handle
///   Close(h)                                        frees it at once
///
/// The cursor is a resume key (ts, k1, p, k2) kept with the spec in ^||IVG.TWin(h), never rows (Decision 2): each
/// page walks on from the key, so the server holds the same few nodes after page 1 and page 1,000, and an edge removed
/// after a page was read is not returned by a later one. Process-private, so a dropped connection frees it, and a
/// handle is good only on the connection that opened it.
///
/// spec: {"start", "end"     both inclusive, as QueryWindow's
///        "dir"              "out" (default) walks ^KG("tout", g, ts, s, p, o); "in" walks ^KG("tin", g, ts, o, p, s)
///        "source" | "sources" | "prefix"    at most one; none reads every source
///        "predicates": [..] a set; absent reads every predicate
///        "attrs": 0 | 1     "page": rows per page (1,000)}
/// For "in" the source filter names the target, as QueryWindowInbound's target does, and rows come in (ts, o, p, s)
/// order; edges written with suppressReverseIndex have no ^KG("tin") entry and no inbound read sees them. For "out"
/// the order is (ts, s, p, o) (FR-005), stable across pages because it is the index's own.
///
/// FR-004: a filter costs a lookup per timestamp, not a walk of every source at it. One source is a $Data; a list is a
/// leapfrog of the sorted list against the sorted sources at the timestamp (NextListed), which costs about the smaller
/// of the two; a prefix is a range $Order from the prefix. IRIS collates canonical numbers before every string, so a
/// prefix a number could start with ("4" and "42", "400", "4a") has no contiguous range: it is compared against every
/// source at the timestamp instead, correct and as slow as QueryWindow.
///
/// A row: {"s", "p", "o", "ts", "w"[, "a": {..}]}, the coordinates spelled as QueryWindow spells them and the
/// attributes built as GetEdgeAttrs builds them, so a value comes back with the type it was written with.
Class Graph.KG.TemporalWindow Extends %RegisteredObject
{

/// A page ends at its row count or when its JSON passes this many characters, whichever comes first, so a large page
/// size or large attributes cannot take a page near the string limit.
Parameter PAGECHARS = 2000000;

/// Sources Latest resolves per call when it enumerates them itself (a prefix, or every source).
Parameter LATESTBATCH = 500;

/// Open a reader over one graph's window and return its first page.
ClassMethod Open(graphId As %String = "", specJSON As %String) As %String
{
	Set tSpec = ##class(%DynamicObject).%FromJSON(specJSON)
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tDir = tSpec.%Get("dir")
	If (tDir '= "") && (tDir '= "out") && (tDir '= "in") {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "Graph.KG.TemporalWindow: dir is ""out"" or ""in"", not """_tDir_""""))
	}
	Set tH = $Increment(^||IVG.TWin)
	Kill ^||IVG.TWin(tH)
	// filter mode: 0 every source, 1 one source, 2 a list, 3 a prefix read as a range, 4 a prefix compared
	Set tMode = 0, tArg = ""
	If tSpec.%Get("source") '= "" {
		Set tMode = 1, tArg = tSpec.%Get("source")
	} ElseIf tSpec.%IsDefined("sources") {
		Set tMode = 2
		Set tIter = tSpec.%Get("sources").%GetIterator()
		While tIter.%GetNext(.tI, .tS) {
			Set:tS'="" ^||IVG.TWin(tH, "src", tS) = ""
		}
	} ElseIf tSpec.%Get("prefix") '= "" {
		Set tArg = tSpec.%Get("prefix"), tMode = $Select(..RangeSafe(tArg): 3, 1: 4)
	}
	Set tPM = 0
	If tSpec.%IsDefined("predicates") {
		Set tPM = 1
		Set tIter = tSpec.%Get("predicates").%GetIterator()
		While tIter.%GetNext(.tI, .tP) {
			Set:tP'="" ^||IVG.TWin(tH, "pred", tP) = ""
		}
	}
	Set tAttrs = $Select(tSpec.%IsDefined("attrs"): +tSpec.%Get("attrs"), 1: 1)
	Set tPage = $Select(+tSpec.%Get("page") > 0: +tSpec.%Get("page"), 1: 1000)
	Set ^||IVG.TWin(tH) = $ListBuild($Select(tDir = "in": "tin", 1: "tout"), tGKey, +tSpec.%Get("end"), tMode, tArg, tPM, tAttrs)
	// level 1 = find the next timestamp; the walk starts just before the window
	Set ^||IVG.TWin(tH, "key") = $ListBuild(1, +tSpec.%Get("start") - 1, "", "", "")
	Quit ..Page(tH, tPage)
}

/// The next page of reader pHandle. A handle that is not open here (closed, finished, or opened on another
/// connection) is an error, never an empty page: an empty answer would read as the end of the window.
ClassMethod Next(pHandle As %Integer, pCount As %Integer = 1000) As %String
{
	If '($Data(^||IVG.TWin(pHandle)) # 2) {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "temporal window reader "_pHandle_" is not open in this process (closed, finished, or opened on another connection)"))
	}
	Quit ..Page(pHandle, $Select(+pCount > 0: +pCount, 1: 1000))
}

/// Free reader pHandle. Closing a handle that is not open does nothing.
ClassMethod Close(pHandle As %Integer)
{
	Kill ^||IVG.TWin(pHandle)
}

/// What reader pHandle holds on the server: {"open", "nodes", "key"}. For tests and diagnosis: "nodes" counts the
/// nodes under ^||IVG.TWin(pHandle), which is the spec, the filter lists and the resume key, never rows.
ClassMethod State(pHandle As %Integer) As %String
{
	Set tOut = ##class(%DynamicObject).%New()
	Do tOut.%Set("open", ($Data(^||IVG.TWin(pHandle)) # 2), "number")
	Set tN = 0
	If $Data(^||IVG.TWin(pHandle)) {
		Set tRef = $Name(^||IVG.TWin(pHandle)), tN = 1
		For {
			Set tRef = $Query(@tRef)
			Quit:tRef=""
			Quit:$QSubscript(tRef, 1)'=pHandle
			Set tN = tN + 1
		}
		Set tKey = ##class(%DynamicArray).%New()
		Set tL = ^||IVG.TWin(pHandle, "key")
		For tI = 1:1:$ListLength(tL) { Do tKey.%Push($ListGet(tL, tI)) }
		Do tOut.%Set("key", tKey)
	}
	Do tOut.%Set("nodes", tN, "number")
	Quit tOut.%ToJSON()
}

/// Walk on from reader pH's resume key for at most pCount rows, and save the key where the walk stopped.
///
/// One loop over four levels instead of four nested loops, so the walk can resume at any of them: tLvl names the
/// subscript to advance next (1 the timestamp, 2 the first key under it, 3 the predicate, 4 the last key). A full
/// page stops at level 4 with the coordinates of its last row, and the next page's first $Order is the one after it.
ClassMethod Page(pH As %Integer, pCount As %Integer) As %String [ Private ]
{
	Set tSpec = ^||IVG.TWin(pH)
	Set tStore = $List(tSpec, 1), tG = $List(tSpec, 2), tEnd = $List(tSpec, 3), tMode = $List(tSpec, 4)
	Set tArg = $List(tSpec, 5), tPM = $List(tSpec, 6), tAttrs = $List(tSpec, 7)
	Set tKey = ^||IVG.TWin(pH, "key")
	Set tLvl = $List(tKey, 1), tTs = $List(tKey, 2), tK1 = $List(tKey, 3), tP = $List(tKey, 4), tK2 = $List(tKey, 5)
	// local copies: the inner loops read them once per timestamp
	Kill tSrc, tPred
	Merge:tMode=2 tSrc = ^||IVG.TWin(pH, "src")
	Merge:tPM tPred = ^||IVG.TWin(pH, "pred")
	Set tIn = (tStore = "tin")
	Set tOut = "", tN = 0, tDone = 0
	For {
		If tLvl = 1 {
			Set tTs = $Order(^KG(tStore, tG, tTs))
			If (tTs = "") || (tTs > tEnd) {
				Set tDone = 1
				Quit
			}
			Set tK1 = "", tLvl = 2
		}
		If tLvl = 2 {
			If tMode {
				Set tK1 = ..NextKey(tStore, tG, tTs, tK1, tMode, tArg, .tSrc)
			} Else {
				Set tK1 = $Order(^KG(tStore, tG, tTs, tK1))
			}
			If tK1 = "" {
				Set tLvl = 1
				Continue
			}
			Set tP = "", tLvl = 3
		}
		If tLvl = 3 {
			If tPM {
				Set tP = ..NextPred(tStore, tG, tTs, tK1, tP, .tPred)
			} Else {
				Set tP = $Order(^KG(tStore, tG, tTs, tK1, tP))
			}
			If tP = "" {
				Set tLvl = 2
				Continue
			}
			Set tK2 = "", tLvl = 4
		}
		Set tK2 = $Order(^KG(tStore, tG, tTs, tK1, tP, tK2), 1, tW)
		If tK2 = "" {
			Set tLvl = 3
			Continue
		}
		If tIn {
			Set tRow = ..Row(tG, tTs, tK2, tP, tK1, tW, tAttrs)
		} Else {
			Set tRow = ..Row(tG, tTs, tK1, tP, tK2, tW, tAttrs)
		}
		Set tOut = tOut_$Select(tN: ",", 1: "")_tRow, tN = tN + 1
		Quit:(tN>=pCount)||($Length(tOut)>=..#PAGECHARS)
	}
	If tDone {
		Kill ^||IVG.TWin(pH)
	} Else {
		Set ^||IVG.TWin(pH, "key") = $ListBuild(tLvl, tTs, tK1, tP, tK2)
	}
	Quit "{""h"":"_pH_",""rows"":["_tOut_"],""done"":"_tDone_"}"
}

/// The first key after pAfter under ^KG(pStore, pG, pTs) that the filter admits, or "".
ClassMethod NextKey(pStore As %String, pG, pTs, pAfter As %String, pMode As %Integer, pArg As %String, ByRef pSrc) As %String [ Private ]
{
	If pMode = 1 {
		Return:pAfter'="" ""
		Return:$Data(^KG(pStore, pG, pTs, pArg)) pArg
		Return ""
	}
	If pMode = 2 { Return ..NextListed(pStore, pG, pTs, pAfter, .pSrc) }
	Set tLen = $Length(pArg)
	If pMode = 3 {
		// the prefix holds a character no canonical number has, so every key it starts sits in one string range
		If pAfter = "" {
			Return:$Data(^KG(pStore, pG, pTs, pArg)) pArg
			Set tK = $Order(^KG(pStore, pG, pTs, pArg))
		} Else {
			Set tK = $Order(^KG(pStore, pG, pTs, pAfter))
		}
		Return:$Extract(tK, 1, tLen)'=pArg ""
		Return tK
	}
	Set tK = pAfter
	For {
		Set tK = $Order(^KG(pStore, pG, pTs, tK))
		Return:tK="" ""
		Return:$Extract(tK, 1, tLen)=pArg tK
	}
}

/// The first key after pAfter that is both in pList and under ^KG(pStore, pG, pTs): a leapfrog join of two sorted
/// sets. Each step either finds a key in both or jumps one side to the other's next key, so the cost is about twice
/// the smaller set, not a walk of the larger. Both sides collate the same way (local arrays and ^KG are both IRIS
/// standard collation), which is what makes the jumps safe. TemporalIndex.Relatest uses it too.
ClassMethod NextListed(pStore As %String, pG, pTs, pAfter As %String, ByRef pList) As %String [ Internal ]
{
	Set tL = $Order(pList(pAfter))
	While tL '= "" {
		Return:$Data(^KG(pStore, pG, pTs, tL)) tL
		Set tK = $Order(^KG(pStore, pG, pTs, tL))
		Return:tK="" ""
		Return:$Data(pList(tK)) tK
		Set tL = $Order(pList(tK))
	}
	Return ""
}

/// The first predicate after pAfter in pPred that has an edge under ^KG(pStore, pG, pTs, pK1), or "".
ClassMethod NextPred(pStore As %String, pG, pTs, pK1 As %String, pAfter As %String, ByRef pPred) As %String [ Private ]
{
	Set tP = pAfter
	For {
		Set tP = $Order(pPred(tP))
		Return:tP="" ""
		Return:$Data(^KG(pStore, pG, pTs, pK1, tP)) tP
	}
}

/// Can a key in the range that starts at pPrefix be a canonical number? Not when the prefix holds a character that no
/// canonical number has (anything but digits, "-" and "."): then every key it starts is a string, and strings that
/// share a prefix are contiguous.
ClassMethod RangeSafe(pPrefix As %String) As %Boolean [ Private ]
{
	Quit $Translate(pPrefix, "0123456789-.") '= ""
}

/// One row's JSON. pAttrs adds "a", the edge's attributes as GetEdgeAttrs builds them ({} when it has none).
/// $ZConvert(, "O", "JSON") escapes exactly what TemporalIndex.jsonEsc escapes (checked on 2026.3, including
/// control characters, quotes, backslashes and non-ASCII text) at a fraction of its cost, which was most of a row's.
ClassMethod Row(pG, pTs, pS As %String, pP As %String, pO As %String, pW, pAttrs As %Boolean) As %String [ Private ]
{
	Set tRow = "{""s"":"""_$ZConvert(pS, "O", "JSON")_""",""p"":"""_$ZConvert(pP, "O", "JSON")
	Set tRow = tRow_""",""o"":"""_$ZConvert(pO, "O", "JSON")_""",""ts"":"_pTs_",""w"":"_##class(Graph.KG.TemporalIndex).jsonNum(pW)
	If pAttrs {
		// The object GetEdgeAttrs builds, without building one (a %DynamicObject per row was half a row's cost).
		// %Set with no type makes an ObjectScript number a JSON number and anything else a string, and a value
		// read back from a global keeps the type it was set with; $ListBuild encodes that type, so a value is a
		// number exactly when it encodes as its own numeric form. "237" written as a string stays "237".
		Set tA = "", tK = ""
		For {
			Set tK = $Order(^KG("edgeprop", pG, pTs, pS, pP, pO, tK), 1, tV)
			Quit:tK=""
			If $ListBuild(tV) = $ListBuild(+tV) {
				Set tV = ##class(Graph.KG.TemporalIndex).jsonNum(tV)
			} Else {
				Set tV = """"_$ZConvert(tV, "O", "JSON")_""""
			}
			Set tA = tA_$Select(tA = "": "", 1: ",")_""""_$ZConvert(tK, "O", "JSON")_""":"_tV
		}
		Set tRow = tRow_",""a"":{"_tA_"}"
	}
	Quit tRow_"}"
}

/// The newest event of each source, from ^KG("tlast", g, s, p) = ts (Decision 1 (b), kept by TemporalIndex).
///
/// spec: {"sources": [..] | "prefix": p | neither (every source), "predicate", "before" (exclusive, as
/// PurgeRawBefore's end), "lookback" (with "before": nothing older than before - lookback counts), "attrs",
/// "after" and "limit" (paging when this enumerates the sources itself)}. Returns {"rows": [..], "next"}: rows in
/// source order, a source with no qualifying event absent rather than null; "next" is the source to pass as "after"
/// for the rest, or null. Ties at the newest timestamp go to the last (p, o) in index order, the maximum in the
/// reader's order.
///
/// The index holds each pair's newest timestamp only, so a cutoff at or below it needs a backwards walk of the
/// timestamps (Resolve), bounded by the newest qualifying timestamp the index does know and by lookback. A graph
/// written before the index existed has no completeness flag (^KG("tlast", g) = 1): its entries cannot be trusted,
/// so a named list is answered by the walk alone, and a prefix or every-source read, which enumerates the index's own
/// sources, is refused with the name of the rebuild that makes it complete.
ClassMethod Latest(graphId As %String = "", specJSON As %String) As %String
{
	Set tSpec = ##class(%DynamicObject).%FromJSON(specJSON)
	Set tG = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tComplete = ($Data(^KG("tlast", tG)) # 2)
	Set tPred = tSpec.%Get("predicate")
	Set tBefore = $Select(tSpec.%GetTypeOf("before") = "number": tSpec.%Get("before"), 1: "")
	Set tFloor = ""
	If (tBefore '= "") && (tSpec.%GetTypeOf("lookback") = "number") { Set tFloor = tBefore - tSpec.%Get("lookback") }
	Set tAttrs = $Select(tSpec.%IsDefined("attrs"): +tSpec.%Get("attrs"), 1: 1)
	Set tNext = ""
	Kill tWant
	If tSpec.%IsDefined("sources") {
		Set tIter = tSpec.%Get("sources").%GetIterator()
		While tIter.%GetNext(.tI, .tS) {
			Set:tS'="" tWant(tS) = ""
		}
	} Else {
		If 'tComplete {
			$$$ThrowStatus($$$ERROR($$$GeneralError, "graph "_graphId_" has no complete latest-event index (written before spec 255): name the sources, or rebuild it once with rebuild_temporal_latest (Graph.KG.TemporalIndex.RebuildLatest)"))
		}
		Set tPrefix = tSpec.%Get("prefix"), tLen = $Length(tPrefix)
		Set tRange = (tLen > 0) && ..RangeSafe(tPrefix)
		Set tLimit = $Select(+tSpec.%Get("limit") > 0: +tSpec.%Get("limit"), 1: ..#LATESTBATCH)
		Set tS = tSpec.%Get("after"), tN = 0, tMore = 0
		If tRange && (tS = "") {
			Set:$Data(^KG("tlast", tG, tPrefix)) tWant(tPrefix) = "", tN = 1, tNext = tPrefix
			Set tS = tPrefix
		}
		For {
			Set tS = $Order(^KG("tlast", tG, tS))
			Quit:tS=""
			If tLen && ($Extract(tS, 1, tLen) '= tPrefix) {
				Quit:tRange
				Continue
			}
			If tN >= tLimit {
				Set tMore = 1
				Quit
			}
			Set tWant(tS) = "", tN = tN + 1, tNext = tS
		}
		Set:'tMore tNext = ""
	}
	Do ..Resolve(tG, .tWant, tPred, tBefore, tFloor, tComplete, .tAns)
	Set tOut = "", tFirst = 1, tS = ""
	For {
		Set tS = $Order(tAns(tS), 1, tA)
		Quit:tS=""
		Set tTs = $List(tA, 1), tP = $List(tA, 2), tO = $List(tA, 3)
		Set tOut = tOut_$Select(tFirst: "", 1: ",")_..Row(tG, tTs, tS, tP, tO, $Get(^KG("tout", tG, tTs, tS, tP, tO)), tAttrs)
		Set tFirst = 0
	}
	Quit "{""rows"":["_tOut_"],""next"":"_$Select(tNext = "": "null", 1: """"_$ZConvert(tNext, "O", "JSON")_"""")_"}"
}

/// Answer pWant (sources) into pAns(s) = $ListBuild(ts, p, o).
///
/// With a complete index, a source whose newest timestamps (one per predicate) are all below the cutoff is answered
/// from the index alone. A source with a pair at or after the cutoff goes to one shared backwards walk of the
/// timestamps below the cutoff, which stops for that source at its first qualifying event and never goes below the
/// newest qualifying timestamp the index already gave it. Without a complete index every source walks.
ClassMethod Resolve(pG, ByRef pWant, pPred As %String, pBefore, pFloor, pComplete As %Boolean, Output pAns) [ Private ]
{
	Kill pAns
	Kill tPending
	Set tAllBound = 1, tLow = ""
	Set tS = ""
	For {
		Set tS = $Order(pWant(tS))
		Quit:tS=""
		If 'pComplete {
			Set tPending(tS) = "", tAllBound = 0
			Continue
		}
		// the newest qualifying (ts, p) the index knows, and whether a newer pair needs the walk
		Set tBest = "", tBestP = "", tWalk = 0
		Set tP = pPred
		For {
			If pPred = "" {
				Set tP = $Order(^KG("tlast", pG, tS, tP), 1, tT)
				Quit:tP=""
			} Else {
				Set tT = $Get(^KG("tlast", pG, tS, tP))
			}
			If tT '= "" {
				If (pBefore '= "") && (tT >= pBefore) {
					Set tWalk = 1
				} ElseIf (tBest = "") || (tT > tBest) || ((tT = tBest) && (tP ]] tBestP)) {
					Set tBest = tT, tBestP = tP
				}
			}
			Quit:pPred'=""
		}
		If (tBest '= "") && (pFloor '= "") && (tBest < pFloor) { Set tBest = "", tBestP = "" }
		If tWalk {
			Set tPending(tS) = tBest
			If tBest = "" {
				Set tAllBound = 0
			} ElseIf (tLow = "") || (tBest < tLow) {
				Set tLow = tBest
			}
			Continue
		}
		Continue:tBest=""
		Set tO = $Order(^KG("tout", pG, tBest, tS, tBestP, ""), -1)
		If tO = "" {
			// the index names an event that is gone: walk rather than answer from it
			Set tPending(tS) = "", tAllBound = 0
			Continue
		}
		Set pAns(tS) = $ListBuild(tBest, tBestP, tO)
	}
	Quit:'$Data(tPending)
	// the walk stops below the lookback floor, and below the lowest bound once every pending source has one
	Set tStop = pFloor
	If tAllBound && (tLow '= "") && ((tStop = "") || (tLow > tStop)) { Set tStop = tLow }
	Set tTs = pBefore
	For {
		Set tTs = $Order(^KG("tout", pG, tTs), -1)
		Quit:tTs=""
		Quit:(tStop'="")&&(tTs<tStop)
		Set tS = ""
		For {
			Set tS = ..NextListed("tout", pG, tTs, tS, .tPending)
			Quit:tS=""
			If pPred '= "" {
				Continue:'$Data(^KG("tout", pG, tTs, tS, pPred))
				Set tP = pPred
			} Else {
				Set tP = $Order(^KG("tout", pG, tTs, tS, ""), -1)
			}
			Set pAns(tS) = $ListBuild(tTs, tP, $Order(^KG("tout", pG, tTs, tS, tP, ""), -1))
			Kill tPending(tS)
		}
		Quit:'$Data(tPending)
	}
}

}
