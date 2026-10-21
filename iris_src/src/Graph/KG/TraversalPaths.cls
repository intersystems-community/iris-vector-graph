Class Graph.KG.TraversalPaths Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

ClassMethod ShortestPathJson(srcId As %String, dstId As %String, maxHops As %Integer = 5, predsJson As %String = "", direction As %String = "both", findAll As %Integer = 0) As %String
{
	Set pGraph = 0
	Kill ^||SP.parents, ^||SP.frontier, ^||SP.seen

	If srcId = "" || (dstId = "") {
		Kill ^||SP.parents, ^||SP.frontier, ^||SP.seen
		Return "[]"
	}

	If srcId = dstId {
		Set zeroPath = ##class(%DynamicObject).%New()
		Set nodes = ##class(%DynamicArray).%New()
		Do nodes.%Push(srcId)
		Do zeroPath.%Set("nodes", nodes)
		Do zeroPath.%Set("rels", ##class(%DynamicArray).%New())
		Do zeroPath.%Set("length", 0)
		Set result = ##class(%DynamicArray).%New()
		Do result.%Push(zeroPath)
		Kill ^||SP.parents, ^||SP.frontier, ^||SP.seen
		Return result.%ToJSON()
	}

	Set preds = ""
	If predsJson '= "" && (predsJson '= "[]") && (predsJson '= "null") {
		Set preds = ##class(%DynamicArray).%FromJSON(predsJson)
	}

	Set ^||SP.seen(srcId) = ""
	Set ^||SP.frontier(srcId) = ""

	Set foundDepth = -1

	For hop = 1:1:maxHops {
		Kill ^||SP.nextFrontier
		Set s = ""
		For {
			Set s = $Order(^||SP.frontier(s))
			Quit:s=""

			If direction = "both" || (direction = "out") {
				Set p = ""
				For {
					Set p = $Order(^KG("out", pGraph, s, p))
					Quit:p=""
					If $IsObject(preds) {
						Set allowed = 0
						For pi = 0:1:(preds.%Size()-1) { If preds.%Get(pi) = p { Set allowed = 1  Quit } }
						Continue:'allowed
					}
					Set o = ""
					For {
						Set o = $Order(^KG("out", pGraph, s, p, o))
						Quit:o=""
						Set ^||SP.parents(o, s, p) = ""
						If o = dstId {
							Set foundDepth = hop
						} ElseIf '$Data(^||SP.seen(o)) {
							Set ^||SP.seen(o) = ""
							Set ^||SP.nextFrontier(o) = ""
						}
					}
				}
			}

			If direction = "both" {
				Set p = ""
				For {
					Set p = $Order(^KG("in", pGraph, s, p))
					Quit:p=""
					If $IsObject(preds) {
						Set allowed = 0
						For pi = 0:1:(preds.%Size()-1) { If preds.%Get(pi) = p { Set allowed = 1  Quit } }
						Continue:'allowed
					}
					Set o = ""
					For {
						Set o = $Order(^KG("in", pGraph, s, p, o))
						Quit:o=""
						Set ^||SP.parents(o, s, p) = ""
						If o = dstId {
							Set foundDepth = hop
						} ElseIf '$Data(^||SP.seen(o)) {
							Set ^||SP.seen(o) = ""
							Set ^||SP.nextFrontier(o) = ""
						}
					}
				}
			}
		}

		If foundDepth > 0 {
			Set results = ..BuildPaths(srcId, dstId, foundDepth)
			Kill ^||SP.parents, ^||SP.frontier, ^||SP.seen, ^||SP.nextFrontier
			Return results
		}

		If '$Data(^||SP.nextFrontier) Quit
		Kill ^||SP.frontier
		Merge ^||SP.frontier = ^||SP.nextFrontier
		Kill ^||SP.nextFrontier
	}

	Kill ^||SP.parents, ^||SP.frontier, ^||SP.seen
	Return "[]"
}
/// The length of a shortest path from srcId to dstId over the default graph, -1 when there is none within maxHops:
/// the length ShortestPathJson's paths have, for a caller that reads nothing else (Cypher's length(shortestPath(..))).
/// A breadth-first search from both ends, each step expanding the end with the smaller frontier, reading only the
/// predicates asked for; ShortestPathJson searches from the source alone and keeps a parent per edge to build every
/// shortest path. direction "out" (the source's out entries, the target's in entries) or "both".
ClassMethod ShortestLength(srcId As %String, dstId As %String, maxHops As %Integer = 5, predsJson As %String = "", direction As %String = "both") As %Integer
{
	Quit:(srcId="")||(dstId="") -1
	Quit:srcId=dstId 0
	Set tBoth = (direction = "both"), tNT = 0
	If (predsJson '= "") && (predsJson '= "[]") && (predsJson '= "null") {
		Set tA = ##class(%DynamicArray).%FromJSON(predsJson)
		For tI = 0:1:(tA.%Size() - 1) {
			Set tP = tA.%Get(tI)
			Set:tP'="" tT($Increment(tNT)) = tP
		}
		// only empty names: no predicate matches them (ShortestPathJson finds no path)
		Quit:(tA.%Size()>0)&&(tNT=0) -1
	}
	// side 1 searches from the source, side 2 from the target; tSeen(side, node) = its distance from that end
	Set tSeen(1, srcId) = 0, tSeen(2, dstId) = 0, tFr(1, srcId) = "", tFr(2, dstId) = ""
	Set tN(1) = 1, tN(2) = 1, tL(1) = 0, tL(2) = 0
	// the source's side follows out entries, the target's in entries; "both" reads the other kind too
	Set tSubs(1) = $ListBuild("out"), tSubs(2) = $ListBuild("in")
	Set:tBoth tSubs(1) = $ListBuild("out", "in"), tSubs(2) = $ListBuild("in", "out")
	While (tL(1) + tL(2)) < maxHops {
		Set tSide = $Select(tN(1) <= tN(2): 1, 1: 2), tOther = 3 - tSide, tNext = 0, tU = ""
		Kill tNx
		For {
			Set tU = $Order(tFr(tSide, tU))
			Quit:tU=""
			Set tPtr = 0
			While $ListNext(tSubs(tSide), tPtr, tSub) {
				Set tP = "", tJ = 0
				For {
					If tNT {
						Set tJ = tJ + 1
						Quit:tJ>tNT
						Set tP = tT(tJ)
					} Else {
						Set tP = $Order(^KG(tSub, 0, tU, tP))
						Quit:tP=""
					}
					Set tV = ""
					For {
						Set tV = $Order(^KG(tSub, 0, tU, tP, tV))
						Quit:tV=""
						// the two sides shared no node before this step, so any meeting on it is a shortest path
						Return:$Data(tSeen(tOther, tV), tDo) tL(tSide) + 1 + tDo
						Continue:$Data(tSeen(tSide, tV))
						Set tSeen(tSide, tV) = tL(tSide) + 1, tNx(tV) = "", tNext = tNext + 1
					}
				}
			}
		}
		Quit:tNext=0
		Kill tFr(tSide)
		Merge tFr(tSide) = tNx
		Set tN(tSide) = tNext, tL(tSide) = tL(tSide) + 1
	}
	Quit -1
}

ClassMethod BuildPaths(srcId As %String, dstId As %String, depth As %Integer) As %String [ Private ]
{
	Set results = ##class(%DynamicArray).%New()
	Do ..DFSPaths(srcId, dstId, depth, $ListBuild(dstId), "", results)
	Return results.%ToJSON()
}
ClassMethod DFSPaths(srcId As %String, cur As %String, remaining As %Integer, nodesSoFar As %List, relsSoFar As %List, ByRef results As %DynamicArray) [ Private ]
{
	If cur = srcId && (remaining = 0) {
		Set pathObj = ##class(%DynamicObject).%New()
		Set nodes = ##class(%DynamicArray).%New()
		Set rels = ##class(%DynamicArray).%New()
		Set ptr = 0
		While $ListNext(nodesSoFar, ptr, n) { Do nodes.%Push(n) }
		Set ptr = 0
		While $ListNext(relsSoFar, ptr, r) { Do rels.%Push(r) }
		Do pathObj.%Set("nodes", nodes)
		Do pathObj.%Set("rels", rels)
		Do pathObj.%Set("length", $ListLength(nodesSoFar) - 1)
		Do results.%Push(pathObj)
		Return
	}

	If remaining <= 0 Return

	Set parentNode = ""
	For {
		Set parentNode = $Order(^||SP.parents(cur, parentNode))
		Quit:parentNode=""
		Set relType = $Order(^||SP.parents(cur, parentNode, ""))
		Set newNodes = $ListBuild(parentNode) _ nodesSoFar
		Set newRels = $ListBuild(relType) _ relsSoFar
		Do ..DFSPaths(srcId, parentNode, remaining - 1, newNodes, newRels, .results)
	}
}
ClassMethod BacktrackPath(srcId As %String, dstId As %String, depth As %Integer) As %DynamicObject [ Private ]
{
	Set nodes = ##class(%DynamicArray).%New()
	Set rels = ##class(%DynamicArray).%New()

	Set cur = dstId
	Set nodeStack = $ListBuild(dstId)
	Set relStack = ""

	While cur '= srcId {
		Set parentNode = $Order(^||SP.parents(cur, ""))
		If parentNode = "" Quit
		Set relType = $Order(^||SP.parents(cur, parentNode, ""))
		Set nodeStack = $ListBuild(parentNode) _ nodeStack
		Set relStack = $ListBuild(relType) _ relStack
		Set cur = parentNode
	}

	Set ptr = 0
	While $ListNext(nodeStack, ptr, n) { Do nodes.%Push(n) }
	Set ptr = 0
	While $ListNext(relStack, ptr, r) { Do rels.%Push(r) }

	Set path = ##class(%DynamicObject).%New()
	Do path.%Set("nodes", nodes)
	Do path.%Set("rels", rels)
	Do path.%Set("length", depth)
	Return path
}
ClassMethod DijkstraJson(srcId As %String, dstId As %String, weightProp As %String = "", maxCost As %Double = 9999, maxHops As %Integer = 10, direction As %String = "out", predsJson As %String = "") As %String
{
	Set pGraph = 0
	Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist

	Set srcId = $Get(srcId, "")
	Set dstId = $Get(dstId, "")
	Set weightProp = $Get(weightProp, "")

	If srcId = "" || (dstId = "") {
		Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist
		Return "{}"
	}

	If srcId = dstId {
		Set result = ##class(%DynamicObject).%New()
		Set nodes = ##class(%DynamicArray).%New()
		Do nodes.%Push(srcId)
		Do result.%Set("nodes", nodes)
		Do result.%Set("rels", ##class(%DynamicArray).%New())
		Do result.%Set("costs", ##class(%DynamicArray).%New())
		Do result.%Set("length", 0)
		Do result.%Set("totalCost", 0)
		Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist
		Return result.%ToJSON()
	}

	// Relationship types to walk; none named walks every predicate
	Set hasPreds = 0
	If (predsJson '= "") && (predsJson '= "[]") && (predsJson '= "null") {
		Set predArr = ##class(%DynamicArray).%FromJSON(predsJson)
		For pi = 0:1:(predArr.%Size() - 1) {
			Set preds(predArr.%Get(pi)) = ""
			Set hasPreds = 1
		}
	}

	Set ^||Dij.pq(0, srcId) = ""
	Set ^||Dij.dist(srcId) = 0

	For {
		Set minCost = $Order(^||Dij.pq(""))
		Quit:minCost=""
		Set curNode = $Order(^||Dij.pq(minCost, ""))
		Quit:curNode=""
		Kill ^||Dij.pq(minCost, curNode)

		If $Data(^||Dij.seen(curNode)) Continue
		Set ^||Dij.seen(curNode) = ""

		If curNode = dstId {
			Set result = ..DijkstraBacktrack(srcId, dstId, +minCost)
			Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist
			Return result
		}

		If minCost >= maxCost Continue

		If direction = "out" || (direction = "both") {
			Set p = ""
			For {
				Set p = $Order(^KG("out", pGraph, curNode, p))
				Quit:p=""
				If (hasPreds) && ('$Data(preds(p))) Continue
				Set neighbor = ""
				For {
					Set neighbor = $Order(^KG("out", pGraph, curNode, p, neighbor))
					Quit:neighbor=""
					If $Data(^||Dij.seen(neighbor)) Continue
					Set rawW = $Get(^KG("out", pGraph, curNode, p, neighbor), 1)
					Set edgeW = +rawW
					If edgeW <= 0 Set edgeW = 1.0
					Set newCost = minCost + edgeW
					If newCost > maxCost Continue
					If '$Data(^||Dij.seen(neighbor)) {
						If '$Data(^||Dij.dist(neighbor)) || (newCost < ^||Dij.dist(neighbor)) {
							Set ^||Dij.dist(neighbor) = newCost
							Set ^||Dij.pq(newCost, neighbor) = ""
							Set ^||Dij.parent(neighbor) = $ListBuild(curNode, p, edgeW)
						}
					}
				}
			}
		}

		If direction = "both" {
			Set p = ""
			For {
				Set p = $Order(^KG("in", pGraph, curNode, p))
				Quit:p=""
				If (hasPreds) && ('$Data(preds(p))) Continue
				Set neighbor = ""
				For {
					Set neighbor = $Order(^KG("in", pGraph, curNode, p, neighbor))
					Quit:neighbor=""
					If $Data(^||Dij.seen(neighbor)) Continue
					Set rawW = $Get(^KG("in", pGraph, curNode, p, neighbor), 1)
					Set edgeW = +rawW
					If edgeW <= 0 Set edgeW = 1.0
					Set newCost = minCost + edgeW
					If newCost > maxCost Continue
					If '$Data(^||Dij.dist(neighbor)) || (newCost < ^||Dij.dist(neighbor)) {
						Set ^||Dij.dist(neighbor) = newCost
						Set ^||Dij.pq(newCost, neighbor) = ""
						Set ^||Dij.parent(neighbor) = $ListBuild(curNode, p, edgeW)
					}
				}
			}
		}
	}

	Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist
	Return "{}"
}
/// The cost of a cheapest path from srcId to dstId over the default graph, "" when there is none within maxCost: the
/// totalCost DijkstraJson reports, for a caller that reads nothing else (YIELD totalCost). Dijkstra from both ends at
/// once, each step settling the cheaper side's nearest node, until the two sides' nearest costs add up to the best
/// meeting; local arrays where DijkstraJson keeps its queue in process-private globals. The cost is the two halves'
/// sum, which adds the same weights as DijkstraJson's in another order (equal to about 18 significant digits). Weights as DijkstraJson reads
/// them: the value of the ^KG entry walked (1 when none), 1 for a weight <= 0. direction "out" or "both".
ClassMethod DijkstraCost(srcId As %String, dstId As %String, maxCost As %Double = 9999, direction As %String = "out", predsJson As %String = "") As %String
{
	Quit:(srcId="")||(dstId="") ""
	Quit:srcId=dstId 0
	Set tBoth = (direction = "both"), tAny = 1
	If (predsJson '= "") && (predsJson '= "[]") && (predsJson '= "null") {
		Set tA = ##class(%DynamicArray).%FromJSON(predsJson)
		For tI = 0:1:(tA.%Size() - 1) {
			Set tPred(tA.%Get(tI)) = "", tAny = 0
		}
	}
	// side 1 from the source over the edges as walked; side 2 from the target over them backwards, each edge at the
	// weight of walking it forwards
	Set tDist(1, srcId) = 0, tDist(2, dstId) = 0, tPQ(1, 0, srcId) = "", tPQ(2, 0, dstId) = "", tBest = ""
	For {
		Set tTop1 = $Order(tPQ(1, "")), tTop2 = $Order(tPQ(2, ""))
		// a side with nothing left has reached all it can: the best meeting is final, or there is none
		Quit:(tTop1="")||(tTop2="")
		// no meeting cheaper than the best is left once the two nearest add up to it
		Quit:(tBest'="")&&((tTop1+tTop2)>=tBest)
		Set tSide = $Select(tTop1 <= tTop2: 1, 1: 2), tOther = 3 - tSide
		Set tCost = $Select(tSide = 1: tTop1, 1: tTop2), tU = $Order(tPQ(tSide, tCost, ""))
		// a node is queued once a side (a cheaper cost replaces its entry), so a popped node is new to its side
		Kill tPQ(tSide, tCost, tU)
		Set tDone(tSide, tU) = ""
		Continue:tCost>=maxCost
		For tK = 1, 2 {
			Continue:(tK=2)&&'tBoth
			// side 1 walks out entries (in entries too under "both"); side 2 the reverse
			Set tSub = $Select((tSide = 1) = (tK = 1): "out", 1: "in"), tP = ""
			For {
				Set tP = $Order(^KG(tSub, 0, tU, tP))
				Quit:tP=""
				Continue:('tAny)&&'$Data(tPred(tP))
				Set tV = ""
				For {
					Set tV = $Order(^KG(tSub, 0, tU, tP, tV))
					Quit:tV=""
					Continue:$Data(tDone(tSide, tV))
					// Graph.KG.Adjacency writes an edge's out and in leaves with the same weight, so the entry walked holds it
					Set tW = +$Get(^KG(tSub, 0, tU, tP, tV), 1)
					Set:tW<=0 tW = 1.0
					Set tNew = tCost + tW
					Continue:tNew>maxCost
					If '$Data(tDist(tSide, tV), tOld) || (tNew < tOld) {
						Kill:$Data(tDist(tSide, tV)) tPQ(tSide, tOld, tV)
						Set tDist(tSide, tV) = tNew, tPQ(tSide, tNew, tV) = ""
					}
					If $Data(tDist(tOther, tV), tThere) {
						Set tMeet = tDist(tSide, tV) + tThere
						Set:(tBest="")||(tMeet<tBest) tBest = tMeet
					}
				}
			}
		}
	}
	Quit:(tBest="")||(tBest>maxCost) ""
	Quit tBest
}

ClassMethod DijkstraBacktrack(srcId As %String, dstId As %String, totalCost As %Double) As %String [ Private ]
{
	Set nodeStack = $ListBuild(dstId)
	Set relStack = ""
	Set costStack = ""
	Set cur = dstId

	While cur '= srcId {
		Set lb = $Get(^||Dij.parent(cur))
		If lb = "" Quit
		Set parentNode = $ListGet(lb, 1)
		Set relType = $ListGet(lb, 2)
		Set edgeCost = +$ListGet(lb, 3)
		Set nodeStack = $ListBuild(parentNode) _ nodeStack
		Set relStack = $ListBuild(relType) _ relStack
		Set costStack = $ListBuild(edgeCost) _ costStack
		Set cur = parentNode
	}

	Set nodes = ##class(%DynamicArray).%New()
	Set rels = ##class(%DynamicArray).%New()
	Set costs = ##class(%DynamicArray).%New()

	Set ptr = 0
	While $ListNext(nodeStack, ptr, n) { Do nodes.%Push(n) }
	Set ptr = 0
	While $ListNext(relStack, ptr, r) { Do rels.%Push(r) }
	Set ptr = 0
	While $ListNext(costStack, ptr, c) { Do costs.%Push(+c) }

	Set result = ##class(%DynamicObject).%New()
	Do result.%Set("nodes", nodes)
	Do result.%Set("rels", rels)
	Do result.%Set("costs", costs)
	Do result.%Set("length", nodes.%Size() - 1)
	Do result.%Set("totalCost", totalCost)
	Return result.%ToJSON()
}
}
