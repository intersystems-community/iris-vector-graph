/// Pure ObjectScript BFS implementation for maximum performance and stability
Class Graph.KG.Traversal Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

/// Rebuilds the ^KG functional index from SQL tables with high-performance batching
ClassMethod BuildKG() As %Status
{
	Try {
		// Disable journaling for this process to maximize speed (requires %Admin_Operate)
		// Do $System.Process.SetJournaling(0) 
		
		Kill ^KG
		TStart
		
		// 1. Labels
		&sql(DECLARE c1 CURSOR FOR SELECT s, label FROM rdf_labels)
		&sql(OPEN c1)
		For {
			&sql(FETCH c1 INTO :s, :label)
			Quit:SQLCODE'=0
			Set ^KG("label", label, s) = ""
		}
		&sql(CLOSE c1)
		
		// 2. Props
		&sql(DECLARE c2 CURSOR FOR SELECT s, key, val FROM rdf_props)
		&sql(OPEN c2)
		For {
			&sql(FETCH c2 INTO :s, :key, :val)
			Quit:SQLCODE'=0
			Set ^KG("prop", s, key) = val
		}
		&sql(CLOSE c2)
		
		// 3. Edges & Stats (including weights)
		Set count = 0
		&sql(DECLARE c3 CURSOR FOR SELECT s, p, o_id, qualifiers FROM rdf_edges)
		&sql(OPEN c3)
		For {
			&sql(FETCH c3 INTO :s, :p, :o, :qual)
			Quit:SQLCODE'=0
			
			If (s = "") || (p = "") || (o = "") Continue
			
			Set weight = 1.0
			If qual '= "" {
				Try {
					Set obj = ##class(%DynamicObject).%FromJSON(qual)
					If (obj '= "") && (obj.weight '= "") Set weight = obj.weight
				} Catch {}
			}
			
			Set ^KG("out", s, p, o) = weight
			Set ^KG("in", o, p, s) = weight
			Set tmp = $Increment(^KG("deg", s))
			Set tmp = $Increment(^KG("degp", s, p))
			
			Set count = count + 1
			// Commit every 50k records to manage transaction buffer
			If count # 50000 = 0 {
				TCommit
				TStart
			}
		}
		&sql(CLOSE c3)
		
		TCommit
		Return $$$OK
	} Catch ex {
		TRollback
		Return ex.AsStatus()
	}
}

/// Helper for single predicate traversal
ClassMethod TraverseWithPredicate(s As %String, p As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef pathId As %Integer, results As %DynamicArray, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set o = ""
    For {
        Set o = $Order(^KG("out", s, p, o))
        If o = "" Quit
        
        // Filter by label if requested
        If (dstLabel '= "") && ('$Data(^KG("label", dstLabel, o))) Continue
        
        // T014: O(1) Hop Rejection
        If (hop < maxHops) && (nextP '= "") && ('$Data(^KG("degp", o, nextP))) Continue
        
        Set pathId = pathId + 1
        Set obj = ##class(%DynamicObject).%New()
        Set obj.id = pathId
        Set obj.step = hop
        Set obj.s = s
        Set obj.p = p
        Set obj.o = o
        Set obj.w = $Get(^KG("out", s, p, o), 1.0)
        Do results.%Push(obj)
        
        If '$Data(seen(o)) Set nextFrontier(o) = ""
    }
}

/// Helper for all predicates traversal
ClassMethod TraverseAllPredicates(s As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef pathId As %Integer, results As %DynamicArray, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set p = ""
    For {
        Set p = $Order(^KG("out", s, p))
        If p = "" Quit
        Do ..TraverseWithPredicate(s, p, hop, maxHops, nextP, dstLabel, .pathId, results, .nextFrontier, .seen)
    }
}

/// Optimized BFS using Process-Private Globals and $ListBuild to bypass object bottleneck
/// @returns Number of edges found. Results are in ^||BFS.Results(i) = $lb(s, p, o, w, step)
ClassMethod BFSFast(srcId As %String, preds As %DynamicArray = "", maxHops As %Integer = 2, dstLabel As %String = "") As %Integer
{
    Kill ^||BFS.Results
    If (srcId = "") || (maxHops <= 0) Return 0
    
    Set count = 0
    Set seen(srcId) = ""
    Set frontier(srcId) = ""
    
    For hop = 1:1:maxHops {
        Kill nextFrontier
        Set s = ""
        Set fixedP = $Select($IsObject(preds):preds.%Get(hop-1), 1:"")
        Set nextP = $Select($IsObject(preds):preds.%Get(hop), 1:"")
        
        For {
            Set s = $Order(frontier(s))
            If s = "" Quit
            
            If fixedP '= "" {
                Do ..TraverseWithPredicateFast(s, fixedP, hop, maxHops, nextP, dstLabel, .count, .nextFrontier, .seen)
            } Else {
                Do ..TraverseAllPredicatesFast(s, hop, maxHops, nextP, dstLabel, .count, .nextFrontier, .seen)
            }
        }
        
        If '$Data(nextFrontier) Quit
        Merge frontier = nextFrontier
        Set node = ""
        For {
            Set node = $Order(nextFrontier(node))
            If node = "" Quit
            Set seen(node) = ""
        }
    }
    Return count
}

ClassMethod TraverseWithPredicateFast(s As %String, p As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef count As %Integer, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set o = ""
    For {
        Set o = $Order(^KG("out", s, p, o))
        If o = "" Quit
        If (dstLabel '= "") && ('$Data(^KG("label", dstLabel, o))) Continue
        If (hop < maxHops) && (nextP '= "") && ('$Data(^KG("degp", o, nextP))) Continue
        
        Set count = count + 1
        Set ^||BFS.Results(count) = $ListBuild(s, p, o, $Get(^KG("out", s, p, o), 1.0), hop)
        
        If '$Data(seen(o)) Set nextFrontier(o) = ""
    }
}

ClassMethod TraverseAllPredicatesFast(s As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef count As %Integer, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set p = ""
    For {
        Set p = $Order(^KG("out", s, p))
        If p = "" Quit
        Do ..TraverseWithPredicateFast(s, p, hop, maxHops, nextP, dstLabel, .count, .nextFrontier, .seen)
    }
}

/// Main BFS implementation
ClassMethod BFS(srcId As %String, preds As %DynamicArray = "", maxHops As %Integer = 2, dstLabel As %String = "") As %DynamicArray
{
    Set results = ##class(%DynamicArray).%New()
    If (srcId = "") || (maxHops <= 0) Return results
    
    Set pathId = 0
    Set seen(srcId) = ""
    Set frontier(srcId) = ""
    
    For hop = 1:1:maxHops {
        Kill nextFrontier
        Set s = ""
        Set fixedP = $Select($IsObject(preds):preds.%Get(hop-1), 1:"")
        Set nextP = $Select($IsObject(preds):preds.%Get(hop), 1:"")
        
        For {
            Set s = $Order(frontier(s))
            If s = "" Quit
            
            If fixedP '= "" {
                Do ..TraverseWithPredicate(s, fixedP, hop, maxHops, nextP, dstLabel, .pathId, results, .nextFrontier, .seen)
            } Else {
                Do ..TraverseAllPredicates(s, hop, maxHops, nextP, dstLabel, .pathId, results, .nextFrontier, .seen)
            }
        }
        
        If '$Data(nextFrontier) Quit
        Merge frontier = nextFrontier
        
        Set node = ""
        For {
            Set node = $Order(nextFrontier(node))
            If node = "" Quit
            Set seen(node) = ""
        }
    }
    Return results
}

/// Python implementation for US1 readability targets
ClassMethod TraverseWithPredicatePy(s As %String, p As %String, hop As %Integer, dstLabel As %String) [ Internal, Private, Language = python ]
{
    import iris
    kg = iris.gref("^KG")
    matches = []
    o = ""
    while True:
        o = kg.order(["out", s, p, o])
        if not o: break
        if dstLabel and not kg.data(["label", dstLabel, o]):
            continue
        weight = kg.get(["out", s, p, o]) or 1.0
        matches.append({"s": s, "p": p, "o": o, "step": hop, "w": float(weight)})
    return matches
}

/// Compatibility wrapper for existing API
ClassMethod BFSJSON(srcId As %String, preds As %DynamicArray, maxHops As %Integer = 2, dstLabel As %String = "") As %DynamicArray
{
    Return ..BFS(srcId, preds, maxHops, dstLabel)
}

}
