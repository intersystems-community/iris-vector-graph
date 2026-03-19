/// Bounded k-hop subgraph extraction over ^KG globals.
/// Pure ObjectScript — no iris module dependency.
Class Graph.KG.Subgraph Extends %RegisteredObject
{

ClassMethod SubgraphJson(seedJson As %String, maxHops As %Integer = 2, edgeTypesJson As %String = "", maxNodes As %Integer = 10000) As %String
{
	If seedJson = "" Return "{""nodes"":[],""edges"":[],""properties"":{},""labels"":{}}"
	
	Try {
		Set seedArr = ##class(%DynamicArray).%FromJSON(seedJson)
	} Catch {
		Return "{""nodes"":[],""edges"":[],""properties"":{},""labels"":{}}"
	}
	If seedArr.%Size() = 0 Return "{""nodes"":[],""edges"":[],""properties"":{},""labels"":{}}"
	
	// Parse edge type filter
	Set hasFilter = 0
	If edgeTypesJson '= "" {
		Try {
			Set filterArr = ##class(%DynamicArray).%FromJSON(edgeTypesJson)
			If filterArr.%Size() > 0 {
				Set hasFilter = 1
				For fi = 0:1:(filterArr.%Size()-1) {
					Set allowedPred(filterArr.%Get(fi)) = ""
				}
			}
		} Catch {
		}
	}
	
	// Initialize BFS: frontier = seeds, seen = {}
	Set nodeCount = 0
	For i = 0:1:(seedArr.%Size()-1) {
		Set s = seedArr.%Get(i)
		// Accept seed if it has outgoing OR incoming edges in ^KG
		If $Data(^KG("deg", s)) || ($Order(^KG("in", s, "")) '= "") {
			Set frontier(s) = ""
			If '$Data(seen(s)) {
				Set seen(s) = ""
				Set nodeCount = nodeCount + 1
			}
		}
	}
	
	// BFS: expand frontier hop by hop
	For hop = 1:1:maxHops {
		Kill nextFrontier
		Set node = ""
		For {
			Set node = $Order(frontier(node))
			Quit:node=""
			
			// Walk outgoing edges: ^KG("out", node, predicate, object)
			Set p = ""
			For {
				Set p = $Order(^KG("out", node, p))
				Quit:p=""
				
				// Edge type filter
				If hasFilter && '$Data(allowedPred(p)) Continue
				
				Set o = ""
				For {
					Set o = $Order(^KG("out", node, p, o))
					Quit:o=""
					
					// Record edge
					Set edgeKey = node_$Char(1)_p_$Char(1)_o
					If '$Data(edgeSeen(edgeKey)) {
						Set edgeSeen(edgeKey) = ""
						Set edges($Increment(edgeCount)) = $ListBuild(node, p, o)
					}
					
					// Add neighbor to next frontier if not seen
					If '$Data(seen(o)) {
						If nodeCount >= maxNodes Quit
						Set seen(o) = ""
						Set nodeCount = nodeCount + 1
						Set nextFrontier(o) = ""
					}
				}
				If nodeCount >= maxNodes Quit
			}
			If nodeCount >= maxNodes Quit
		}
		
		// Swap frontiers
		Kill frontier
		Merge frontier = nextFrontier
		
		// Stop if no new nodes discovered or at limit
		Set check = $Order(frontier(""))
		If check = "" Quit
		If nodeCount >= maxNodes Quit
	}
	
	// Build result JSON
	Set nodesArr = ##class(%DynamicArray).%New()
	Set edgesArr = ##class(%DynamicArray).%New()
	Set propsObj = ##class(%DynamicObject).%New()
	Set labelsObj = ##class(%DynamicObject).%New()
	
	// Collect nodes + properties + labels
	Set n = ""
	For {
		Set n = $Order(seen(n))
		Quit:n=""
		Do nodesArr.%Push(n)
		
		// Properties: ^KG("prop", node, key) = val
		Set propObj = ##class(%DynamicObject).%New()
		Set hasProp = 0
		Set k = ""
		For {
			Set k = $Order(^KG("prop", n, k), 1, val)
			Quit:k=""
			Do propObj.%Set(k, val)
			Set hasProp = 1
		}
		If hasProp Do propsObj.%Set(n, propObj)
		
		// Labels: scan all known labels for this node
		// ^KG("label", labelName, nodeId) = ""
		Set labelArr = ##class(%DynamicArray).%New()
		Set hasLabel = 0
		Set lbl = ""
		For {
			Set lbl = $Order(^KG("label", lbl))
			Quit:lbl=""
			If $Data(^KG("label", lbl, n)) {
				Do labelArr.%Push(lbl)
				Set hasLabel = 1
			}
		}
		If hasLabel Do labelsObj.%Set(n, labelArr)
	}
	
	// Collect edges
	Set ei = ""
	For {
		Set ei = $Order(edges(ei), 1, edgeList)
		Quit:ei=""
		Set edgeObj = ##class(%DynamicObject).%New()
		Do edgeObj.%Set("s", $ListGet(edgeList, 1))
		Do edgeObj.%Set("p", $ListGet(edgeList, 2))
		Do edgeObj.%Set("o", $ListGet(edgeList, 3))
		Do edgesArr.%Push(edgeObj)
	}
	
	Set result = ##class(%DynamicObject).%New()
	Do result.%Set("nodes", nodesArr)
	Do result.%Set("edges", edgesArr)
	Do result.%Set("properties", propsObj)
	Do result.%Set("labels", labelsObj)
	
	Return result.%ToJSON()
}

}
