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
/// The cheapest path from srcId to dstId in one graph, as JSON {nodes, rels, costs, length, totalCost}; "{}" when there
/// is none within maxCost and maxHops.
///
/// pGraphId is the graph walked ("" or $Char(0) the default graph): its adjacency is ^KG("out"/"in", <its key>, ...).
/// Before 4.6.0 (R154) this walked the default graph's whatever the caller's graph.
///
/// An edge's weight is the value of its ^KG entry: the relationship's "weight" property, the one the adjacency index
/// stores (1 when none, 1 for a weight <= 0). weightProp names it: "" or "weight". Any other name is refused rather
/// than answered with the "weight" property's costs (it was ignored).
///
/// maxHops bounds the path's relationships (<= 0: no bound). With a bound the search is over (node, hops): the
/// cheapest path to a node may use more hops than a dearer one that still reaches dstId in time, so a node settled
/// at h hops is expanded again only at fewer. It was ignored before R154.
///
/// direction: "out" walks the edges forwards, "in" backwards (a path from srcId to dstId against the edges), "both"
/// either way; anything else throws. Before 4.6.0 "in" and any other value walked nothing and answered "{}".
ClassMethod DijkstraJson(srcId As %String, dstId As %String, weightProp As %String = "", maxCost As %Double = 9999, maxHops As %Integer = 10, direction As %String = "out", predsJson As %String = "", pGraphId As %String = "") As %String
{
	Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist

	Set srcId = $Get(srcId, "")
	Set dstId = $Get(dstId, "")
	Do ..CheckWeightProp($Get(weightProp, ""))
	Do ..CheckDirection("ivg.shortestPath.weighted", direction)
	Set pGraph = ##class(Graph.KG.GraphKey).ForIndex($Get(pGraphId, ""))
	Set tBound = (+maxHops > 0)

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
	If (predsJson '= "") && (predsJson '= "[]") && (predsJson '= "null") && (predsJson '= $Char(0)) {
		Set predArr = ##class(%DynamicArray).%FromJSON(predsJson)
		For pi = 0:1:(predArr.%Size() - 1) {
			Set preds(predArr.%Get(pi)) = ""
			Set hasPreds = 1
		}
	}

	// a state is (node, hops); with no bound every state has hops 0, which is plain Dijkstra over nodes
	Set ^||Dij.pq(0, srcId, 0) = ""
	Set ^||Dij.dist(srcId, 0) = 0

	For {
		Set minCost = $Order(^||Dij.pq(""))
		Quit:minCost=""
		Set curNode = $Order(^||Dij.pq(minCost, ""))
		Quit:curNode=""
		Set tHops = $Order(^||Dij.pq(minCost, curNode, ""))
		Kill ^||Dij.pq(minCost, curNode, tHops)

		// settled already at no more hops, and (popped in cost order) at no more cost: nothing new from here
		If $Data(^||Dij.seen(curNode), tSeenHops) && (tSeenHops <= tHops) Continue
		Set ^||Dij.seen(curNode) = tHops

		If curNode = dstId {
			Set result = ..DijkstraBacktrack(srcId, dstId, +minCost, tHops)
			Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist
			Return result
		}

		If minCost >= maxCost Continue
		If tBound && (tHops >= maxHops) Continue
		Set tNext = $Select(tBound: tHops + 1, 1: 0)

		// "out" walks the out entries, "in" the in entries (4.6.0, round 3: it walked none, so a path against the
		// edges was no path), "both" the two
		For tSub = "out", "in" {
			If (direction '= "both") && (tSub '= direction) Continue
			Set p = ""
			For {
				Set p = $Order(^KG(tSub, pGraph, curNode, p))
				Quit:p=""
				If (hasPreds) && ('$Data(preds(p))) Continue
				Set neighbor = ""
				For {
					Set neighbor = $Order(^KG(tSub, pGraph, curNode, p, neighbor))
					Quit:neighbor=""
					If $Data(^||Dij.seen(neighbor), tSeenHops) && (tSeenHops <= tNext) Continue
					Set rawW = $Get(^KG(tSub, pGraph, curNode, p, neighbor), 1)
					Set edgeW = +rawW
					If edgeW <= 0 Set edgeW = 1.0
					Set newCost = minCost + edgeW
					If newCost > maxCost Continue
					If '$Data(^||Dij.dist(neighbor, tNext)) || (newCost < ^||Dij.dist(neighbor, tNext)) {
						Set ^||Dij.dist(neighbor, tNext) = newCost
						Set ^||Dij.pq(newCost, neighbor, tNext) = ""
						Set ^||Dij.parent(neighbor, tNext) = $ListBuild(curNode, tHops, p, edgeW)
					}
				}
			}
		}
	}

	Kill ^||Dij.pq, ^||Dij.parent, ^||Dij.seen, ^||Dij.dist
	Return "{}"
}

/// Throw unless pDirection is "out" (along the edges), "in" (against them) or "both" (4.6.0, round 3: DijkstraJson
/// answered "sideways" with no path, as if there were none; DegreeCentralityJson counted it as "out").
ClassMethod CheckDirection(pCaller As %String, pDirection As %String) [ Internal ]
{
	If '$ListFind($ListBuild("out", "in", "both"), pDirection) {
		$$$ThrowStatus($$$ERROR($$$GeneralError, pCaller_": direction must be 'out', 'in' or 'both', not '"_pDirection_"'"))
	}
}

/// Refuse a weightProp the adjacency index does not hold (4.6.0 R154): ^KG keeps one weight per edge, the
/// relationship's "weight" property, and a search by another property would answer that one's costs.
ClassMethod CheckWeightProp(pWeightProp As %String = "") [ Private ]
{
	Set tProp = $Translate(pWeightProp, $Char(0), "")
	If (tProp '= "") && (tProp '= "weight") {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "ivg.shortestPath.weighted: weightProp '"_tProp_"' is not supported: edge weights are read from the adjacency index, which holds only the 'weight' relationship property"))
	}
}

/// The cost of a cheapest path from srcId to dstId in one graph (pGraphId, as DijkstraJson), "" when there is none
/// within maxCost and maxHops: the totalCost DijkstraJson reports, for a caller that reads nothing else (YIELD
/// totalCost). Dijkstra from both ends at once, each step settling the cheaper side's nearest node, until the two
/// sides' nearest costs add up to the best meeting; local arrays where DijkstraJson keeps its queue in process-private
/// globals. The cost is the two halves' sum, which adds the same weights as DijkstraJson's in another order (equal to
/// about 18 significant digits). Weights as DijkstraJson reads them: the value of the ^KG entry walked (1 when none),
/// 1 for a weight <= 0. direction "out", "in" (against the edges) or "both"; anything else throws.
///
/// maxHops (<= 0: no bound): the search keeps the hop count of each side's best path. A cheapest path within the
/// bound is the cheapest path, so a best meeting of no more than maxHops hops is the answer; one of more hops (a path
/// within the bound may still cost more) is answered by DijkstraJson's search over (node, hops) instead.
ClassMethod DijkstraCost(srcId As %String, dstId As %String, maxCost As %Double = 9999, direction As %String = "out", predsJson As %String = "", pGraphId As %String = "", maxHops As %Integer = 0) As %String
{
	Do ..CheckDirection("ivg.shortestPath.weighted", direction)
	Quit:(srcId="")||(dstId="") ""
	Quit:srcId=dstId 0
	Set tG = ##class(Graph.KG.GraphKey).ForIndex(pGraphId)
	// side 1 walks tFwd entries, side 2 the reverse; "in" walks against the edges (4.6.0, round 3: it walked "out")
	Set tBoth = (direction = "both"), tAny = 1
	Set tFwd = $Select(direction = "in": "in", 1: "out"), tBack = $Select(tFwd = "in": "out", 1: "in")
	If (predsJson '= "") && (predsJson '= "[]") && (predsJson '= "null") && (predsJson '= $Char(0)) {
		Set tA = ##class(%DynamicArray).%FromJSON(predsJson)
		For tI = 0:1:(tA.%Size() - 1) {
			Set tPred(tA.%Get(tI)) = "", tAny = 0
		}
	}
	// side 1 from the source over the edges as walked; side 2 from the target over them backwards, each edge at the
	// weight of walking it forwards
	Set tDist(1, srcId) = 0, tDist(2, dstId) = 0, tPQ(1, 0, srcId) = "", tPQ(2, 0, dstId) = "", tBest = ""
	Set tHop(1, srcId) = 0, tHop(2, dstId) = 0, tBestHops = 0
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
			// side 1 walks tFwd entries (both kinds under "both"); side 2 the reverse
			Set tSub = $Select((tSide = 1) = (tK = 1): tFwd, 1: tBack), tP = ""
			For {
				Set tP = $Order(^KG(tSub, tG, tU, tP))
				Quit:tP=""
				Continue:('tAny)&&'$Data(tPred(tP))
				Set tV = ""
				For {
					Set tV = $Order(^KG(tSub, tG, tU, tP, tV))
					Quit:tV=""
					Continue:$Data(tDone(tSide, tV))
					// Graph.KG.Adjacency writes an edge's out and in leaves with the same weight, so the entry walked holds it
					Set tW = +$Get(^KG(tSub, tG, tU, tP, tV), 1)
					Set:tW<=0 tW = 1.0
					Set tNew = tCost + tW
					Continue:tNew>maxCost
					If '$Data(tDist(tSide, tV), tOld) || (tNew < tOld) {
						Kill:$Data(tDist(tSide, tV)) tPQ(tSide, tOld, tV)
						Set tDist(tSide, tV) = tNew, tPQ(tSide, tNew, tV) = "", tHop(tSide, tV) = tHop(tSide, tU) + 1
					}
					If $Data(tDist(tOther, tV), tThere) {
						Set tMeet = tDist(tSide, tV) + tThere
						If (tBest = "") || (tMeet < tBest) {
							Set tBest = tMeet, tBestHops = tHop(tSide, tV) + tHop(tOther, tV)
						}
					}
				}
			}
		}
	}
	Quit:(tBest="")||(tBest>maxCost) ""
	If (+maxHops > 0) && (tBestHops > maxHops) {
		Set tPath = ..DijkstraJson(srcId, dstId, "", maxCost, maxHops, direction, predsJson, pGraphId)
		Quit:tPath="{}" ""
		Quit ##class(%DynamicObject).%FromJSON(tPath).%Get("totalCost")
	}
	Quit tBest
}

ClassMethod DijkstraBacktrack(srcId As %String, dstId As %String, totalCost As %Double, pHops As %Integer = 0) As %String [ Private ]
{
	Set nodeStack = $ListBuild(dstId)
	Set relStack = ""
	Set costStack = ""
	Set cur = dstId, tHops = pHops

	While cur '= srcId {
		Set lb = $Get(^||Dij.parent(cur, tHops))
		If lb = "" Quit
		Set parentNode = $ListGet(lb, 1)
		Set tHops = $ListGet(lb, 2)
		Set relType = $ListGet(lb, 3)
		Set edgeCost = +$ListGet(lb, 4)
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
