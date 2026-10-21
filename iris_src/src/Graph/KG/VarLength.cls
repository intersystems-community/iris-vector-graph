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
	If (pMinH = 2) && (pMaxH = 2) {
		Do ..EndsTwo(pSrc, tD1, tD2, tNT, .tType, .tGraphs, .pEnds)
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

/// Ends for exactly two hops (pMinH = pMaxH = 2), as the trail walk lists them, in the same order, without building
/// an adjacency for every first-hop node: y is an end when an edge e1 from pSrc to some x and an edge e2 from x to y are
/// two edges. pSrc's own edges are numbered by Neighbours (identity: graph, source, predicate, target, ekey); the second
/// hop reads ^KG, in Neighbours' order and over every graph pGraphs holds, as Neighbours does. IC3's shape: the walk
/// built and numbered the adjacency of every first-hop node to check that one condition.
ClassMethod EndsTwo(pSrc As %String, pD1 As %Integer, pD2 As %Integer, pNT As %Integer, ByRef pType, ByRef pGraphs, ByRef pEnds) [ Private ]
{
	Set tNE = 0
	Do ..Neighbours(pSrc, pD1, pD2, pNT, .pType, .pGraphs, .tAdj, .tEid, .tNE)
	Set tKey = ""
	For {
		Set tKey = $Order(tEid(tKey), 1, tN)
		Quit:tKey=""
		Set tEdge(tN) = tKey
	}
	For tI = 1:1:$Get(tAdj(pSrc)) {
		Set tA = tAdj(pSrc, tI)
		Do ..SecondHop(pSrc, $List(tA, 2), tEdge($List(tA, 1)), pD1, pD2, pNT, .pType, .pGraphs, .tSeen, .pEnds)
	}
}

/// EndsTwo's second hop from pCur, over every edge but pFirst (the edge from pSrc it arrived by): each new neighbour is
/// an end at depth 2. Only an edge back to pSrc can be pFirst, so any other neighbour needs no comparison.
ClassMethod SecondHop(pSrc As %String, pCur As %String, pFirst As %List, pD1 As %Integer, pD2 As %Integer, pNT As %Integer, ByRef pType, ByRef pGraphs, ByRef pSeen, ByRef pEnds) [ Private ]
{
	Set tG = ""
	For {
		Set tG = $Order(pGraphs(tG))
		Quit:tG=""
		For tD = pD1:1:pD2 {
			Set tSub = $Select(tD = 1: "out", 1: "in"), tP = ""
			For {
				Set tP = $Select(pNT > 0: $Order(pType(tP)), 1: $Order(^KG(tSub, tG, pCur, tP)))
				Quit:tP=""
				Continue:'$Data(^KG(tSub, tG, pCur, tP))
				Set tM = ""
				For {
					Set tM = $Order(^KG(tSub, tG, pCur, tP, tM))
					Quit:tM=""
					Continue:(tD=2)&&(pD1=1)&&(tM=pCur)
					Continue:$Data(pSeen(tM))
					If $Data(^KG(tSub, tG, pCur, tP, tM)) < 10 {
						Continue:(tM=pSrc)&&($ListBuild(tG, $Select(tD = 1: pCur, 1: tM), tP, $Select(tD = 1: tM, 1: pCur), "")=pFirst)
					} Else {
						// an edge of the entry other than the first hop's makes the trail ("#" is no edge)
						Set tE = $ListBuild(tG, $Select(tD = 1: pCur, 1: tM), tP, $Select(tD = 1: tM, 1: pCur)), tK = ""
						For {
							Set tK = $Order(^KG(tSub, tG, pCur, tP, tM, tK))
							Quit:(tK="")||((tK'="#")&&((tM'=pSrc)||(tE_$ListBuild(tK)'=pFirst)))
						}
						Continue:tK=""
					}
					Set pSeen(tM) = "", pEnds($Increment(pEnds)) = $ListBuild(tM, 2)
				}
			}
		}
	}
}

/// Ends for pMinH <= 1, without walking every trail. A node other than pSrc is an end at its BFS distance: a
/// shortest path is a trail and no trail is shorter. pSrc is an end at depth 0 when pMinH = 0, else at the length
/// of its shortest closed trail, which is its shortest cycle (a closed trail through a node holds a cycle through
/// it). The BFS reads ^KG directly and numbers no edges; the cycle needs only:
///   a self-loop at pSrc (length 1); two distinct edges between pSrc and one neighbour (2, "both" only: directed, a
///   return edge is found by the walk); directed, an edge u -> pSrc (d(u) + 1); "both", an edge u - v other than the
///   tree edge to a BFS parent that joins two first-hop branches (d(u) + d(v) + 1; pSrc is a branch of its own).
ClassMethod EndsByDistance(pSrc As %String, pDir As %String, pMinH As %Integer, pMaxH As %Integer, pD1 As %Integer, pD2 As %Integer, pNT As %Integer, ByRef pType, ByRef pGraphs, ByRef pEnds) [ Private ]
{
	Set tDist(pSrc) = 0, tPar(pSrc) = "", tBr(pSrc) = pSrc
	Set tFr(pSrc) = "", tN = 0, tOrd(0) = pSrc
	Set tCyc = "", tBoth = (pD1 '= pD2)
	For tD = 1:1:pMaxH {
		Kill tNx
		Set tCur = ""
		For {
			Set tCur = $Order(tFr(tCur))
			Quit:tCur=""
			Set tG = ""
			For {
				Set tG = $Order(pGraphs(tG))
				Quit:tG=""
				For tDD = pD1:1:pD2 {
					Set tSub = $Select(tDD = 1: "out", 1: "in")
					Set tP = ""
					For {
						Set tP = $Select(pNT > 0: $Order(pType(tP)), 1: $Order(^KG(tSub, tG, tCur, tP)))
						Quit:tP=""
						Continue:'$Data(^KG(tSub, tG, tCur, tP))
						Set tM = ""
						For {
							Set tM = $Order(^KG(tSub, tG, tCur, tP, tM))
							Quit:tM=""
							// "both" reads a self-loop through its out entry only: its in entry is the same edge
							Continue:tBoth&&(tDD=2)&&(tM=tCur)
							If tBoth && (tCur = pSrc) {
								// edges to each neighbour: a parallel edge of a multigraph is one more
								Set tK = $Select($Data(^KG(tSub, tG, tCur, tP, tM)) < 10: 1, 1: ..KeyCount(tSub, tG, tCur, tP, tM))
								Set tMult(tM) = $Get(tMult(tM)) + tK
							}
							If '$Data(tDist(tM)) {
								Set tDist(tM) = tD, tPar(tM) = tCur, tBr(tM) = $Select(tCur = pSrc: tM, 1: tBr(tCur))
								Set tNx(tM) = "", tN = tN + 1, tOrd(tN) = tM
								Continue
							}
							// breadth first, an edge first met at depth tD closes no cycle through pSrc shorter than tD
							// (directed) or 2tD-1 (both): once one that short is known, no later edge can improve it
							Continue:(tCyc'="")&&(tCyc<=$Select(tBoth:2*tD-1,1:tD))
							If (tM = pSrc) && (tCur = pSrc) {
								Set tCyc = 1
								Continue
							}
							If 'tBoth {
								Set:(tM=pSrc)&&((tCyc="")||(tD<tCyc)) tCyc = tD
								Continue
							}
							Continue:(tM=tPar(tCur))||(tCur=tPar(tM))
							Continue:(tBr(tM)=tBr(tCur))&&(tM'=pSrc)&&(tCur'=pSrc)
							Set tLen = tDist(tCur) + tDist(tM) + 1
							Set:(tCyc="")||(tLen<tCyc) tCyc = tLen
						}
					}
				}
			}
		}
		Kill tFr
		Merge tFr = tNx
	}
	// two distinct edges between pSrc and one neighbour close a trail of 2 ("both")
	If tBoth && (pMaxH >= 2) && ((tCyc = "") || (tCyc > 2)) {
		Set tM = ""
		For {
			Set tM = $Order(tMult(tM), 1, tK)
			Quit:tM=""
			If (tM '= pSrc) && (tK >= 2) {
				Set tCyc = 2
				Quit
			}
		}
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

/// The number of parallel edges under one multigraph adjacency entry (its children other than "#").
ClassMethod KeyCount(pSub As %String, pKey, pS As %String, pP As %String, pO As %String) As %Integer [ Private ]
{
	Set tN = 0, tK = ""
	For {
		Set tK = $Order(^KG(pSub, pKey, pS, pP, pO, tK))
		Quit:tK=""
		Set:tK'="#" tN = tN + 1
	}
	Quit tN
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
				Set tP = $Select(pNT > 0: $Order(pType(tP)), 1: $Order(^KG(tSub, tG, pCur, tP)))
				Quit:tP=""
				Continue:'$Data(^KG(tSub, tG, pCur, tP))
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
