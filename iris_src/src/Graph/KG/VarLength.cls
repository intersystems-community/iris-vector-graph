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
/// The walk's inputs as EndsInto and LastLevelPart read them: pType(type) and their count pNT ("" types: every type),
/// pGraphs(graph key) (every graph's when pAll), and the entry kinds 1 (out) .. 2 (in) walked, pD1 to pD2. Out edges
/// are read from a node's out entries, in edges from its in entries; "both" reads both, and skips a self-loop's in
/// entry, which is the same edge as its out entry.
ClassMethod Setup(ByRef pTypes As %String, pDir As %String, pAll As %Integer, ByRef pGraph As %String, Output pType, Output pNT As %Integer, Output pGraphs, Output pD1 As %Integer, Output pD2 As %Integer) [ Private ]
{
	Set:pGraph=$Char(0) pGraph = ""
	Set:pTypes=$Char(0) pTypes = ""
	Kill pType, pGraphs
	Set pNT = 0
	If pTypes '= "" {
		Set tTA = ##class(%Library.DynamicArray).%FromJSON(pTypes)
		Set pNT = tTA.%Size()
		For tI = 1:1:pNT {
			Set pType(tTA.%Get(tI - 1)) = ""
		}
	}
	If pAll {
		Set tG = ""
		For {
			Set tG = $Order(^KG("out", tG))
			Quit:tG=""
			Set pGraphs(tG) = ""
		}
	} Else {
		Set pGraphs(##class(Graph.KG.GraphKey).ForIndex(pGraph)) = ""
	}
	Set pD1 = $Select(pDir = "in": 2, 1: 1)
	Set pD2 = $Select(pDir = "both": 2, 1: pD1)
}

/// EndsInto's ends among the candidates pCands(i) (nodes holding the far node's equality, IC1's `f.firstName = $name`),
/// for pMinH <= 1 and no candidate pSrc: an end is a candidate at distance 1..pMaxH, its depth that distance (a
/// shortest path is a trail, so the trail walk finds every such end). The ball of radius (pMaxH + 1) \ 2 around pSrc
/// is read once, then each candidate's own, backwards, for the rest: a few thousand edges where the walk reads every
/// node in reach (IC1 at SF1: 9,900 of 10,600 people, for the few named).
ClassMethod NamedEnds(pSrc As %String, pTypes As %String, pDir As %String, pMinH As %Integer, pMaxH As %Integer, pGraph As %String, ByRef pCands, Output pEnds)
{
	Kill pEnds
	Set pEnds = 0
	Do ..Setup(.pTypes, pDir, 0, .pGraph, .tType, .tNT, .tGraphs, .tD1, .tD2)
	Set tR1 = (pMaxH + 1) \ 2, tR2 = pMaxH - tR1
	Set tDist(pSrc) = 0, tFr(pSrc) = ""
	For tD = 1:1:tR1 {
		Kill tNext
		Set tCur = ""
		For {
			Set tCur = $Order(tFr(tCur))
			Quit:tCur=""
			Do ..Adjacent(tCur, tD1, tD2, tNT, .tType, .tGraphs, .tNb)
			Set tM = ""
			For {
				Set tM = $Order(tNb(tM))
				Quit:tM=""
				Continue:$Data(tDist(tM))
				Set tDist(tM) = tD, tNext(tM) = ""
			}
		}
		Kill tFr
		Merge tFr = tNext
	}
	// the candidate side walks the edges backwards: an incoming walk's ends are reached over outgoing edges from them
	Set tB1 = $Select(tD1 = tD2: 3 - tD1, 1: tD1), tB2 = $Select(tD1 = tD2: 3 - tD2, 1: tD2)
	Set tI = ""
	For {
		Set tI = $Order(pCands(tI), 1, tC)
		Quit:tI=""
		Set tBest = $Get(tDist(tC), pMaxH + 1)
		Kill tSeen, tFr
		Set tSeen(tC) = "", tFr(tC) = ""
		For tK = 1:1:tR2 {
			Quit:tK>=tBest
			Kill tNext
			Set tCur = ""
			For {
				Set tCur = $Order(tFr(tCur))
				Quit:tCur=""
				Do ..Adjacent(tCur, tB1, tB2, tNT, .tType, .tGraphs, .tNb)
				Set tM = ""
				For {
					Set tM = $Order(tNb(tM))
					Quit:tM=""
					Continue:$Data(tSeen(tM))
					Set tSeen(tM) = "", tNext(tM) = ""
					Set:$Data(tDist(tM),tDM)&&((tDM+tK)<tBest) tBest = tDM + tK
				}
			}
			Kill tFr
			Merge tFr = tNext
		}
		Continue:(tBest>pMaxH)||(tBest<pMinH)
		Set pEnds($Increment(pEnds)) = $ListBuild(tC, tBest)
	}
}

/// pCur's neighbours over directions pD1..pD2 (1 out, 2 in), of the types pType (all when pNT = 0), in every graph of
/// pGraphs, as pNb(node) = "".
ClassMethod Adjacent(pCur As %String, pD1 As %Integer, pD2 As %Integer, pNT As %Integer, ByRef pType, ByRef pGraphs, Output pNb) [ Private ]
{
	Kill pNb
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
					Set pNb(tM) = ""
				}
			}
		}
	}
}

/// The number of EndsInto's ends: what `count(DISTINCT m)` over `(s)-[*pMinH..pMaxH]->(m)` answers, counted here rather
/// than read out (the SQL path's BFS route streamed every reached edge to count them).
ClassMethod EndsCount(pSrc As %String, pTypes As %String, pDir As %String, pMinH As %Integer, pMaxH As %Integer, pAll As %Integer, pGraph As %String) As %Integer
{
	Do ..EndsInto(pSrc, pTypes, pDir, pMinH, pMaxH, pAll, pGraph, .tEnds)
	Quit tEnds
}

ClassMethod EndsInto(pSrc As %String, pTypes As %String, pDir As %String, pMinH As %Integer, pMaxH As %Integer, pAll As %Integer, pGraph As %String, Output pEnds)
{
	Kill pEnds
	Set pEnds = 0
	If (pSrc = "") || (pMaxH < pMinH) {
		Quit
	}
	Do ..Setup(.pTypes, pDir, pAll, .pGraph, .tType, .tNT, .tGraphs, .tD1, .tD2)
	If pMinH <= 1 {
		Do ..EndsByDistance(pSrc, pDir, pMinH, pMaxH, tD1, tD2, tNT, .tType, .tGraphs, .pEnds, $ListBuild(pTypes, pAll, pGraph))
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

/// LastLevel's workers read the walk so far from a shared global, rather than walk it again, past this many edge reads
/// (^||IVG.VLSharedReads overrides it; 0 always shares).
Parameter SHAREDREADS = 200000;

/// Whether one of EndsByDistance's levels past the first (pD) is worth splitting across workers: a frontier of at least
/// ^||IVG.VLParallelMin nodes (512; 0 always, negative never), more than one CPU, not inside a worker, and pSrc's own
/// end settled already or settled by the workers: pMinH = 0; a directed walk, where an edge of this level back into
/// pSrc makes it an end at pD and the workers report one; or a cycle short enough that no edge of this level can
/// shorten it ("both", where a cycle is made of two branches the workers do not see).
ClassMethod SplitLast(pFrN As %Integer, pCyc As %String, pBoth As %Boolean, pD As %Integer, pMinH As %Integer) As %Boolean [ Private ]
{
	Set tMin = $Get(^||IVG.VLParallelMin, 512)
	Quit:(tMin<0)||(pFrN<tMin)||(pFrN=0) 0
	Quit:$Get(^||IVG.VLWorker) 0
	Quit:$System.Util.NumberOfCPUs()<2 0
	Quit (pMinH = 0) || 'pBoth || ((pCyc '= "") && (pCyc <= $Select(pBoth: 2 * pD - 1, 1: pD)))
}

/// One of EndsByDistance's levels (pMaxH, the depth it reaches) in workers (LastLevelPart): each expands its share of
/// the frontier (every pParts-th node in key order) and files the nodes no earlier level reached; they are added here
/// at depth pMaxH, in the order the serial walk would meet them, and returned in pNew (the next frontier). pReads
/// gains the workers' edge reads. pSrcHit: whether a frontier node has an edge into pSrc. A worker walks the levels
/// before again where that took fewer than ..#SHAREDREADS edge reads (pReads); past that, which made a deep split
/// slower than none (DRKG, three hops from a hub: 2.3 s against 1.3), it reads the nodes reached (pDist) and the
/// frontier (pFr) from ^IRIS.Temp.IVG.VL(share), a global read an edge (SNB IC1 at SF1: 89 ms walking again, 101 shared).
/// pArgs = $ListBuild(types JSON, all graphs, graph), EndsInto's.
ClassMethod LastLevel(pSrc As %String, pDir As %String, pMaxH As %Integer, pArgs As %List, ByRef pDist, ByRef pFr, ByRef pOrd, ByRef pN As %Integer, Output pSrcHit As %Boolean, ByRef pReads As %Integer = 0, Output pNew) [ Private ]
{
	Set tN = $System.Util.NumberOfCPUs()
	Set:tN>8 tN = 8
	// Output is a declaration only: pNew arrives holding the caller's array, which would make old nodes the next frontier
	Kill pNew
	Set tShare = $Increment(^IRIS.Temp.IVG.VL), pSrcHit = 0, tShared = (pReads >= $Get(^||IVG.VLSharedReads, ..#SHAREDREADS))
	Kill ^IRIS.Temp.IVG.VL(tShare)
	Merge:tShared ^IRIS.Temp.IVG.VL(tShare, "seen") = pDist, ^IRIS.Temp.IVG.VL(tShare, "fr") = pFr
	Set tWQ = $System.WorkMgr.%New(, tN)
	Try {
		For tI = 0:1:(tN - 1) {
			$$$ThrowOnError(tWQ.Queue("##class(Graph.KG.VarLength).LastLevelPart", pSrc, $List(pArgs, 1), pDir, pMaxH, $List(pArgs, 2), $List(pArgs, 3), tShare, tI, tN, tShared))
		}
		$$$ThrowOnError(tWQ.WaitForComplete())
		// frontier position, then the order each was met: a node two frontier nodes reach is the earlier one's
		Set tP = ""
		For {
			Set tP = $Order(^IRIS.Temp.IVG.VL(tShare, "new", tP))
			Quit:tP=""
			Set tS = ""
			For {
				Set tS = $Order(^IRIS.Temp.IVG.VL(tShare, "new", tP, tS), 1, tM)
				Quit:tS=""
				Continue:$Data(pDist(tM))
				Set pDist(tM) = pMaxH, pN = pN + 1, pOrd(pN) = tM, pNew(tM) = ""
			}
		}
		Set pSrcHit = ''$Data(^IRIS.Temp.IVG.VL(tShare, "src")), tP = ""
		For {
			Set tP = $Order(^IRIS.Temp.IVG.VL(tShare, "reads", tP), 1, tR)
			Quit:tP=""
			Set pReads = pReads + tR
		}
	} Catch tEx {
		Set tErr = tEx
	}
	Kill ^IRIS.Temp.IVG.VL(tShare)
	Throw:$Data(tErr) tErr
	Set ^||IVG.VLParallelRuns = $Get(^||IVG.VLParallelRuns) + 1
}

/// A worker's share of LastLevel: pPart's share of the frontier expanded, its new nodes filed under
/// ^IRIS.Temp.IVG.VL(pShare, "new", frontier position, n), and ("src") set where an edge leads into pSrc. pShared: the
/// nodes reached and the frontier are ("seen") and ("fr") there; otherwise the worker walks to depth pMaxH - 1 itself.
ClassMethod LastLevelPart(pSrc As %String, pTypes As %String, pDir As %String, pMaxH As %Integer, pAll As %Integer, pGraph As %String, pShare As %Integer, pPart As %Integer, pParts As %Integer, pShared As %Boolean = 0) As %Status
{
	Set tSC = $$$OK
	Try {
		Set ^||IVG.VLWorker = 1
		If 'pShared {
			Do ..EndsInto(pSrc, pTypes, pDir, 0, pMaxH - 1, pAll, pGraph, .tEnds)
			For tI = 1:1:tEnds {
				Set tM = $List(tEnds(tI), 1), tSeen(tM) = ""
				Set:$List(tEnds(tI), 2)=(pMaxH-1) tFr(tM) = ""
			}
		}
		Do ..Setup(.pTypes, pDir, pAll, .pGraph, .tType, .tNT, .tGraphs, .tD1, .tD2)
		// the frontier in key order, as EndsByDistance expands it; a new node is filed under (frontier position, n), so
		// LastLevel adds them in the order the serial walk meets them
		Set tCur = "", tPos = -1, tSrc = 0, tReads = 0
		For {
			Set tCur = $Select(pShared: $Order(^IRIS.Temp.IVG.VL(pShare, "fr", tCur)), 1: $Order(tFr(tCur)))
			Quit:tCur=""
			Set tPos = tPos + 1, tSeq = 0
			Continue:(tPos#pParts)'=pPart
			Set tG = ""
			For {
				Set tG = $Order(tGraphs(tG))
				Quit:tG=""
				For tDD = tD1:1:tD2 {
					Set tSub = $Select(tDD = 1: "out", 1: "in"), tP = ""
					For {
						Set tP = $Select(tNT > 0: $Order(tType(tP)), 1: $Order(^KG(tSub, tG, tCur, tP)))
						Quit:tP=""
						Continue:'$Data(^KG(tSub, tG, tCur, tP))
						Set tM = ""
						For {
							Set tM = $Order(^KG(tSub, tG, tCur, tP, tM))
							Quit:tM=""
							Set tReads = tReads + 1
							Continue:(tDD=2)&&(tD1=1)&&(tM=tCur)
							Set:tM=pSrc tSrc = 1
							Continue:$Data(tSeen(tM))
							Continue:pShared&&$Data(^IRIS.Temp.IVG.VL(pShare,"seen",tM))
							Set tSeen(tM) = "", tNew(tPos, $Increment(tSeq)) = tM
						}
					}
				}
			}
		}
		Merge ^IRIS.Temp.IVG.VL(pShare, "new") = tNew
		Set:tSrc ^IRIS.Temp.IVG.VL(pShare, "src") = 1
		Set ^IRIS.Temp.IVG.VL(pShare, "reads", pPart) = tReads
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Kill ^||IVG.VLWorker
	Quit tSC
}

/// Ends for pMinH <= 1, without walking every trail. A node other than pSrc is an end at its BFS distance: a
/// shortest path is a trail and no trail is shorter. pSrc is an end at depth 0 when pMinH = 0, else at the length
/// of its shortest closed trail, which is its shortest cycle (a closed trail through a node holds a cycle through
/// it). The BFS reads ^KG directly and numbers no edges; the cycle needs only:
///   a self-loop at pSrc (length 1); two distinct edges between pSrc and one neighbour (2, "both" only: directed, a
///   return edge is found by the walk); directed, an edge u -> pSrc (d(u) + 1); "both", an edge u - v other than the
///   tree edge to a BFS parent that joins two first-hop branches (d(u) + d(v) + 1; pSrc is a branch of its own).
ClassMethod EndsByDistance(pSrc As %String, pDir As %String, pMinH As %Integer, pMaxH As %Integer, pD1 As %Integer, pD2 As %Integer, pNT As %Integer, ByRef pType, ByRef pGraphs, ByRef pEnds, pArgs As %List = "") [ Private ]
{
	Set tDist(pSrc) = 0, tPar(pSrc) = "", tBr(pSrc) = pSrc
	Set tFr(pSrc) = "", tN = 0, tOrd(0) = pSrc, tFrN = 1
	Set tCyc = "", tBoth = (pD1 '= pD2), tReads = 0
	For tD = 1:1:pMaxH {
		Set tFrN0 = tN
		// a level past the first, where a deep walk's edges are, split across workers when its frontier is large and
		// pSrc's cycle can no longer change (see LastLevel): its new nodes are ends at depth tD, and the next frontier
		If (tD > 1) && (pArgs '= "") && ..SplitLast(tFrN, tCyc, tBoth, tD, pMinH) {
			Do ..LastLevel(pSrc, pDir, tD, pArgs, .tDist, .tFr, .tOrd, .tN, .tSrcHit, .tReads, .tNx)
			// directed, an edge into pSrc at this depth closes its first cycle here ("both" splits only when settled)
			Set:tSrcHit&&'tBoth&&(tCyc="") tCyc = tD
			Quit:tD=pMaxH
			// a later level reads parents and branches only for a "both" cycle, which is settled by now (or, pMinH = 0,
			// never read: pSrc is an end at depth 0 whatever its cycle)
			Set tM = ""
			For {
				Set tM = $Order(tNx(tM))
				Quit:tM=""
				Set tPar(tM) = "", tBr(tM) = ""
			}
			Kill tFr
			Merge tFr = tNx
			Set tFrN = tN - tFrN0
			Continue
		}
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
							Set tReads = tReads + 1
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
		Set tFrN = tN - tFrN0
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
