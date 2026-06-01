Class Graph.KG.TraversalPaths Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

ClassMethod ShortestPathJson(srcId As %String, dstId As %String, maxHops As %Integer = 5, predsJson As %String = "", direction As %String = "both", findAll As %Integer = 0) As %String
{
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
					Set p = $Order(^KG("out", 0, s, p))
					Quit:p=""
					If $IsObject(preds) {
						Set allowed = 0
						For pi = 0:1:(preds.%Size()-1) { If preds.%Get(pi) = p { Set allowed = 1  Quit } }
						Continue:'allowed
					}
					Set o = ""
					For {
						Set o = $Order(^KG("out", 0, s, p, o))
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
					Set p = $Order(^KG("in", 0, s, p))
					Quit:p=""
					If $IsObject(preds) {
						Set allowed = 0
						For pi = 0:1:(preds.%Size()-1) { If preds.%Get(pi) = p { Set allowed = 1  Quit } }
						Continue:'allowed
					}
					Set o = ""
					For {
						Set o = $Order(^KG("in", 0, s, p, o))
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
ClassMethod DijkstraJson(srcId As %String, dstId As %String, weightProp As %String = "", maxCost As %Double = 9999, maxHops As %Integer = 10, direction As %String = "out") As %String
{
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
				Set p = $Order(^KG("out", 0, curNode, p))
				Quit:p=""
				Set neighbor = ""
				For {
					Set neighbor = $Order(^KG("out", 0, curNode, p, neighbor))
					Quit:neighbor=""
					If $Data(^||Dij.seen(neighbor)) Continue
					Set rawW = $Get(^KG("out", 0, curNode, p, neighbor), 1)
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
				Set p = $Order(^KG("in", 0, curNode, p))
				Quit:p=""
				Set neighbor = ""
				For {
					Set neighbor = $Order(^KG("in", 0, curNode, p, neighbor))
					Quit:neighbor=""
					If $Data(^||Dij.seen(neighbor)) Continue
					Set rawW = $Get(^KG("in", 0, curNode, p, neighbor), 1)
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
