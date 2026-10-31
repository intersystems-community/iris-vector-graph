/// Spec 260 D-NKG-SWAP scale measurement, lab-only (a throwaway container; never deployed to a
/// product namespace). The lab harness of layout-lab (run_nkg_swap.py, LayoutLab.NkgEquiv) with
/// the pre-fix builder kept beside the rewrite, so both run on the same ^KG in one container.
Class IVGLab.NkgScale Extends %RegisteredObject
{

/// Fill ^KG("out"/"in", 0, ...) with pNodes nodes of about pDeg out-edges over 3 predicates,
/// directly (a bypass: the lab needs the same ^KG for both builders, not the write path), as
/// LayoutLab.NkgEquiv.GenKG did. Returns the edge count.
ClassMethod GenKG(pNodes As %Integer, pDeg As %Integer) As %Integer
{
	Kill ^KG, ^NKG
	Set tN = 0
	For tS = 1:1:pNodes {
		For tE = 1:1:pDeg {
			Set tO = $Random(pNodes) + 1, tP = "P" _ ($Random(3) + 1)
			If '$Data(^KG("out", 0, "n" _ tS, tP, "n" _ tO)) {
				Set tW = ($Random(2900) + 100) / 1000
				Set ^KG("out", 0, "n" _ tS, tP, "n" _ tO) = tW, ^KG("in", 0, "n" _ tO, tP, "n" _ tS) = tW
				Set tN = tN + 1
			}
		}
	}
	Quit tN
}

/// Drift ^KG away from ^NKG by about pPct percent of the edges: half added to ^KG only (as a
/// temporal shadow is), half removed from ^KG only (as a %NOINDEX delete leaves ^NKG). Bypass
/// writes, lab only. Returns the number of changes.
ClassMethod Drift(pNodes As %Integer, pPct As %Integer) As %Integer
{
	Set tChanges = (pNodes * 10 * pPct) \ 100, tN = 0
	For tI = 1:1:tChanges {
		Set tS = "n" _ ($Random(pNodes) + 1)
		If tI # 2 {
			Set tO = "n" _ ($Random(pNodes) + 1)
			Set ^KG("out", 0, tS, "P4", tO) = 1, ^KG("in", 0, tO, "P4", tS) = 1
		} Else {
			Set tP = $Order(^KG("out", 0, tS, ""))
			Continue:tP=""
			Set tO = $Order(^KG("out", 0, tS, tP, ""))
			Continue:tO=""
			Kill ^KG("out", 0, tS, tP, tO), ^KG("in", 0, tO, tP, tS)
		}
		Set tN = tN + 1
	}
	Set tmp = $Increment(^KG("__version"))
	Quit tN
}

/// The pre-fix TraversalBuild.BuildNKG, verbatim apart from the class references: DropNKG, then
/// intern and write every row, sketch and degree.
ClassMethod OldBuildNKG() As %Status
{
	Try { Do ##class(Graph.KG.NKGAccel).InvalidateAdjCache() } Catch invEx {}
	Do ##class(Graph.KG.GraphIndex).DropNKG()
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	Set edgeCount = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set tWarm = ##class(Graph.KG.Adj).AllWarm(g)
		Set src = ""
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:src=""
			Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(src)
			Set pred = ""
			For {
				Set pred = $Order(^KG("out", g, src, pred))
				Quit:pred=""
				Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(pred)
				Set dst = ""
				For {
					Set dst = $Order(^KG("out", g, src, pred, dst), 1, weight)
					Quit:dst=""
					Set oIdx = ##class(Graph.KG.GraphIndex).InternNode(dst)
					If '$Data(^NKG(-1, sIdx, -(pIdx+1), oIdx)) {
						Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight
						Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight
						Set edgeCount = edgeCount + 1
					}
					Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(sIdx, pIdx, oIdx)
				}
			}
			If tWarm {
				Set tDeg = $ListGet($Get(^KG("degc", g, src, $Char(1))), 2, 0)
			} Else {
				Set tDeg = ##class(Graph.KG.Adj).Degree(g, src)
			}
			Set ^NKG(-3, sIdx) = $Get(^NKG(-3, sIdx), 0) + tDeg
		}
	}
	Set ^NKG("$meta", "edgeCount") = edgeCount
	Set tmp = $Increment(^NKG("$meta", "version"))
	Do ##class(Graph.KG.TraversalBuild).Build2HopStats()
	Do ##class(Graph.KG.TraversalBuild).Build2HopExactStats()
	Return $$$OK
}

/// The lab reader (LayoutLab.NkgEquiv.Reader): until ^LLSW("stop"), look up pNode and its
/// out-rows in ^NKG and count "no such node", "node with no row" and good reads, with the
/// $ZH of the first and last bad read. ^LLSW("r") = $ListBuild(ok, noNode, noRow, first, last).
ClassMethod Reader(pNode As %String) As %Status
{
	Set tOk = 0, tNoNode = 0, tNoRow = 0, tFirst = "", tLast = ""
	Set ^LLSW("pid") = $Job
	While '$Get(^LLSW("stop")) {
		Set tIdx = $Get(^NKG("$NI", pNode))
		If tIdx = "" {
			Set tNoNode = tNoNode + 1
			Set tLast = $ZHorolog
			Set:tFirst="" tFirst = tLast
		} ElseIf '$Data(^NKG(-1, tIdx)) {
			Set tNoRow = tNoRow + 1
			Set tLast = $ZHorolog
			Set:tFirst="" tFirst = tLast
		} Else {
			Set tOk = tOk + 1
		}
	}
	Set ^LLSW("r") = $ListBuild(tOk, tNoNode, tNoRow, tFirst, tLast)
	Quit $$$OK
}

/// One timed build, pWhich "old" or "new", with a reader job on pNode when pReader = 1. Returns JSON.
ClassMethod Run(pWhich As %String, pNode As %String = "n99999", pReader As %Boolean = 1) As %String
{
	Kill ^LLSW
	If pReader {
		Job ##class(IVGLab.NkgScale).Reader(pNode)
		For tW = 1:1:200 { Quit:$Data(^LLSW("pid"))  Hang 0.01 }
		Hang 0.2
	}
	Set t0 = $ZHorolog
	If pWhich = "old" {
		Set tSC = ..OldBuildNKG()
	} Else {
		Set tSC = ##class(Graph.KG.Traversal).BuildNKG()
	}
	Set t1 = $ZHorolog
	Set tOut = {"which": (pWhich), "seconds": (t1 - t0), "ok": ($$$ISOK(tSC)), "edgeCount": (+$Get(^NKG("$meta", "edgeCount"))), "version": (+$Get(^NKG("$meta", "version")))}
	If pReader {
		Set ^LLSW("stop") = 1
		For tW = 1:1:500 { Quit:$Data(^LLSW("r"))  Hang 0.01 }
		Set r = $Get(^LLSW("r"))
		Do tOut.%Set("reader_ok", $ListGet(r, 1))
		Do tOut.%Set("reader_no_node", $ListGet(r, 2))
		Do tOut.%Set("reader_no_row", $ListGet(r, 3))
		Do tOut.%Set("first_bad_rel_s", $Select($ListGet(r, 4) = "": "", 1: $ListGet(r, 4) - t0))
		Do tOut.%Set("last_bad_rel_s", $Select($ListGet(r, 5) = "": "", 1: $ListGet(r, 5) - t0))
	}
	Quit tOut.%ToJSON()
}

}
