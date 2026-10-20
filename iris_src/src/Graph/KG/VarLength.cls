/// Variable-length expansion over the Adjacency Index (spec 252).
///
/// SQLUser.CY_VLP_ENDS walks every trail (edge-distinct path) from a source and returns each end once with its
/// least depth at or above minh. It read each node's edges with one SQL statement per direction, which made
/// IC1 (KNOWS*1..3) 63x and IC3 (KNOWS*2..2) 33x Neo4j at SNB SF0.1. Where the adjacency functional index is
/// declared, ^KG("out"/"in") equals the rows in the same transaction as every write, so the walk reads it
/// instead; the UDF calls Ends only then. Only the source of the adjacency changes, not the walk.
Class Graph.KG.VarLength Extends %RegisteredObject
{

/// The ends of every trail from pSrc of pMinH..pMaxH hops, as CY_VLP_ENDS returns them: a JSON array of
/// {"t": node, "l": least depth}. pTypes is a JSON array of predicates ("" = any), pDir "out" / "in" / "both",
/// pAll 1 for every graph, else pGraph names the one graph ("" the default).
ClassMethod Ends(pSrc As %String, pTypes As %String = "", pDir As %String = "out", pMinH As %Integer = 1, pMaxH As %Integer = 1, pAll As %Integer = 1, pGraph As %String = "") As %String
{
	Do ..EndsInto(pSrc, pTypes, pDir, pMinH, pMaxH, pAll, pGraph, .tEnds)
	Set tOut = ##class(%Library.DynamicArray).%New()
	For tI = 1:1:tEnds {
		Set tO = ##class(%Library.DynamicObject).%New()
		Do tO.%Set("t", $List(tEnds(tI), 1)_"")
		Do tO.%Set("l", $List(tEnds(tI), 2))
		Do tOut.%Push(tO)
	}
	Quit tOut.%ToJSON()
}

/// Ends without the JSON, for callers in this process (Graph.KG.Cypher.Exec): pEnds = count, pEnds(i) =
/// $ListBuild(node, least depth), in the order Ends lists them.
ClassMethod EndsInto(pSrc As %String, pTypes As %String, pDir As %String, pMinH As %Integer, pMaxH As %Integer, pAll As %Integer, pGraph As %String, Output pEnds)
{
	Kill pEnds
	Set pEnds = 0
	If (pSrc = "") || (pMaxH < pMinH) {
		Quit
	}
	If pGraph = $Char(0) {
		Set pGraph = ""
	}
	If pTypes = $Char(0) {
		Set pTypes = ""
	}
	Set tNT = 0
	If pTypes '= "" {
		Set tTA = ##class(%Library.DynamicArray).%FromJSON(pTypes)
		Set tNT = tTA.%Size()
		For tI = 1:1:tNT {
			Set tType(tTA.%Get(tI - 1)) = ""
		}
	}
	If pAll {
		Set tG = ""
		For {
			Set tG = $Order(^KG("out", tG))
			Quit:tG=""
			Set tGraphs(tG) = ""
		}
	} Else {
		Set tGraphs(##class(Graph.KG.GraphKey).ForIndex(pGraph)) = ""
	}
	// out edges are read from cur's out entries, in edges from its in entries; "both" reads both, and skips a
	// self-loop's in entry, which is the same edge as its out entry
	Set tD1 = $Select(pDir = "in": 2, 1: 1)
	Set tD2 = $Select(pDir = "both": 2, 1: tD1)
	If pMinH <= 1 {
		Do ..EndsByDistance(pSrc, pDir, pMinH, pMaxH, tD1, tD2, tNT, .tType, .tGraphs, .pEnds)
		Quit
	}
	Set tNE = 0
	Set tSP = 1, tSt(1) = pSrc, tSd(1) = 0, tSe(1) = ",", tNo = 0
	While tSP > 0 {
		Set tCur = tSt(tSP), tDep = tSd(tSP), tPE = tSe(tSP), tSP = tSP - 1
		If tDep >= pMinH {
			If '$Data(tBest(tCur)) {
				Set tNo = tNo + 1, tOrd(tNo) = tCur, tBest(tCur) = tDep
			} ElseIf tDep < tBest(tCur) {
				Set tBest(tCur) = tDep
			}
		}
		Continue:tDep>=pMaxH
		If '$Data(tAdj(tCur)) {
			Set tAdj(tCur) = 0
			Do ..Neighbours(tCur, tD1, tD2, tNT, .tType, .tGraphs, .tAdj, .tEid, .tNE)
		}
		For tI = tAdj(tCur):-1:1 {
			Set tA = tAdj(tCur, tI), tE = $List(tA, 1)
			Continue:tPE[(","_tE_",")
			Set tSP = tSP + 1, tSt(tSP) = $List(tA, 2), tSd(tSP) = tDep + 1, tSe(tSP) = tPE_tE_","
		}
	}
	For tI = 1:1:tNo {
		Set pEnds($Increment(pEnds)) = $ListBuild(tOrd(tI), tBest(tOrd(tI)))
	}
}

/// Ends for pMinH <= 1, without walking every trail. A node other than pSrc is an end at its BFS distance: a
/// shortest path is a trail and no trail is shorter. pSrc is an end at depth 0 when pMinH = 0, else at the length
/// of its shortest closed trail, which is its shortest cycle (a closed trail through a node holds a cycle through
/// it): directed, the least d(u) + 1 over edges u -> pSrc; "both", the least d(u) + d(v) + 1 over edges u - v
/// outside the BFS tree that join two first-hop branches (pSrc is a branch of its own).
ClassMethod EndsByDistance(pSrc As %String, pDir As %String, pMinH As %Integer, pMaxH As %Integer, pD1 As %Integer, pD2 As %Integer, pNT As %Integer, ByRef pType, ByRef pGraphs, ByRef pEnds) [ Private ]
{
	Set tNE = 0
	Set tDist(pSrc) = 0, tBr(pSrc) = 0, tTree(pSrc) = 0
	Set tFr(pSrc) = "", tN = 0, tOrd(0) = pSrc
	Set tCyc = ""
	For tD = 1:1:pMaxH {
		Kill tNx
		Set tCur = ""
		For {
			Set tCur = $Order(tFr(tCur))
			Quit:tCur=""
			Kill tAdj
			Set tAdj(tCur) = 0
			Do ..Neighbours(tCur, pD1, pD2, pNT, .pType, .pGraphs, .tAdj, .tEid, .tNE)
			For tI = 1:1:tAdj(tCur) {
				Set tE = $List(tAdj(tCur, tI), 1), tM = $List(tAdj(tCur, tI), 2)
				If '$Data(tDist(tM)) {
					Set tDist(tM) = tD, tTree(tM) = tE, tBr(tM) = $Select(tD = 1: tM, 1: tBr(tCur))
					Set tNx(tM) = "", tN = tN + 1, tOrd(tN) = tM
					Continue
				}
				// tM was reached before: a closed trail through pSrc when this edge closes one
				Continue:tE=tTree(tCur)
				If pDir '= "both" {
					Set:(tM=pSrc)&&((tCyc="")||(tD<tCyc)) tCyc = tD
				} Else {
					Continue:tE=tTree(tM)
					Continue:(tBr(tM)=tBr(tCur))&&(tM'=pSrc)&&(tCur'=pSrc)
					Set tLen = tDist(tCur) + tDist(tM) + 1
					Set:(tCyc="")||(tLen<tCyc) tCyc = tLen
				}
			}
		}
		Kill tFr
		Merge tFr = tNx
	}
	// The last frontier is not expanded: an edge closing a cycle through it makes the cycle longer than pMaxH.
	For tI = 0:1:tN {
		Set tM = tOrd(tI), tL = tDist(tM)
		If (tM = pSrc) && (pMinH > 0) {
			Continue:(tCyc="")||(tCyc>pMaxH)
			Set tL = tCyc
		}
		Set pEnds($Increment(pEnds)) = $ListBuild(tM, tL)
	}
}

/// pAdj(pCur, i) = $ListBuild(edge, neighbour) for each edge at pCur. An edge is numbered once per call by its
/// stored identity (graph, source, predicate, target, ekey), so the out entry at one end and the in entry at the
/// other are the same edge, and each parallel edge of a multigraph is its own.
ClassMethod Neighbours(pCur As %String, pD1 As %Integer, pD2 As %Integer, pNT As %Integer, ByRef pType, ByRef pGraphs, ByRef pAdj, ByRef pEid, ByRef pNE) [ Private ]
{
	Set tG = ""
	For {
		Set tG = $Order(pGraphs(tG))
		Quit:tG=""
		For tD = pD1:1:pD2 {
			Set tSub = $Select(tD = 1: "out", 1: "in")
			Set tP = ""
			For {
				Set tP = $Order(^KG(tSub, tG, pCur, tP))
				Quit:tP=""
				Continue:(pNT>0)&&('$Data(pType(tP)))
				Set tM = ""
				For {
					Set tM = $Order(^KG(tSub, tG, pCur, tP, tM))
					Quit:tM=""
					Continue:(tD=2)&&(pD1=1)&&(tM=pCur)
					Set tS = $Select(tD = 1: pCur, 1: tM), tO = $Select(tD = 1: tM, 1: pCur)
					If $Data(^KG(tSub, tG, pCur, tP, tM)) < 10 {
						Set tKey = $ListBuild(tG, tS, tP, tO, "")
						Set:'$Data(pEid(tKey)) pNE = pNE + 1, pEid(tKey) = pNE
						Set pAdj(pCur, $Increment(pAdj(pCur))) = $ListBuild(pEid(tKey), tM)
						Continue
					}
					Set tK = ""
					For {
						Set tK = $Order(^KG(tSub, tG, pCur, tP, tM, tK))
						Quit:tK=""
						Continue:tK="#"
						Set tKey = $ListBuild(tG, tS, tP, tO, tK)
						Set:'$Data(pEid(tKey)) pNE = pNE + 1, pEid(tKey) = pNE
						Set pAdj(pCur, $Increment(pAdj(pCur))) = $ListBuild(pEid(tKey), tM)
					}
				}
			}
		}
	}
}

}
