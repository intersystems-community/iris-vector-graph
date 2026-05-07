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
		&sql(DECLARE c1 CURSOR FOR SELECT s, label FROM Graph_KG.rdf_labels)
		&sql(OPEN c1)
		For {
			&sql(FETCH c1 INTO :s, :label)
			Quit:SQLCODE'=0
			Set ^KG("label", label, s) = ""
		}
		&sql(CLOSE c1)
		
		// 2. Props
		&sql(DECLARE c2 CURSOR FOR SELECT s, key, val FROM Graph_KG.rdf_props)
		&sql(OPEN c2)
		For {
			&sql(FETCH c2 INTO :s, :key, :val)
			Quit:SQLCODE'=0
			Set ^KG("prop", s, key) = val
		}
		&sql(CLOSE c2)
		
		// 3. Edges & Stats (including weights)
		Set count = 0
		&sql(DECLARE c3 CURSOR FOR SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges)
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
			
			Set ^KG("out", 0, s, p, o) = weight
			Set ^KG("in", 0, o, p, s) = weight
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

		// BuildNKG requires Graph.KG.GraphIndex — optional, non-fatal if missing
		Try { Do ..BuildNKG() } Catch {}

		Return $$$OK
	} Catch ex {
		TRollback
		Return ex.AsStatus()
	}
}

ClassMethod BuildNKG() As %Status
{
	Kill ^NKG
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	
	Set edgeCount = 0
	Set src = ""
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(src)
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(pred)
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", 0, src, pred, dst), 1, weight)
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
		Set ^NKG(-3, sIdx) = $Get(^KG("deg", src))
	}
	Set ^NKG("$meta", "edgeCount") = edgeCount
	Set tmp = $Increment(^NKG("$meta", "version"))

	Do ..Build2HopStats()
	Do ..Build2HopExactStats()

	Return $$$OK
}

ClassMethod BackfillDegp() As %Integer
{
	Kill ^KG("deg")
	Kill ^KG("degp")
	Set src = ""
	Set cnt = 0
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set deg = 0
			Set o = ""
			For {
				Set o = $Order(^KG("out", 0, src, pred, o))
				Quit:o=""
				Set deg = deg + 1
			}
			Set tmp = $Increment(^KG("deg", src), deg)
			Set ^KG("degp", src, pred) = deg
			Set cnt = cnt + 1
		}
	}
	Return cnt
}

ClassMethod Build2HopStats() As %Integer
{
	Kill ^KG("deg2p")
	Set src = ""
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set sum = 0
			Set mid = ""
			For {
				Set mid = $Order(^KG("out", 0, src, pred, mid))
				Quit:mid=""
				Set sum = sum + $Get(^KG("degp", mid, pred), 0)
			}
			If sum > 0 Set ^KG("deg2p", src, pred) = sum
		}
	}
	Set n = ""
	Set cnt = 0
	For {
		Set n = $Order(^KG("deg2p", n))
		Quit:n=""
		Set cnt = cnt + 1
	}
	Return cnt
}

/// Helper for single predicate traversal
ClassMethod TraverseWithPredicate(s As %String, p As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef pathId As %Integer, results As %DynamicArray, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set o = ""
    For {
        Set o = $Order(^KG("out", 0, s, p, o))
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
        Set obj.w = $Get(^KG("out", 0, s, p, o), 1.0)
        Do results.%Push(obj)
        
        If '$Data(seen(o)) Set nextFrontier(o) = ""
    }
}

/// Helper for all predicates traversal
ClassMethod TraverseAllPredicates(s As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef pathId As %Integer, results As %DynamicArray, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set p = ""
    For {
        Set p = $Order(^KG("out", 0, s, p))
        If p = "" Quit
        Do ..TraverseWithPredicate(s, p, hop, maxHops, nextP, dstLabel, .pathId, results, .nextFrontier, .seen)
    }
}

/// Optimized BFS using Process-Private Globals and $ListBuild to bypass object bottleneck
/// @param direction "out" (default), "in", or "both" for undirected BFS
/// @returns Number of edges found. Results are in ^||BFS.Results(i) = $lb(s, p, o, w, step)
ClassMethod BFSFast(srcId As %String, preds As %DynamicArray = "", maxHops As %Integer = 2, dstLabel As %String = "", direction As %String = "out", maxResults As %Integer = 0) As %Integer
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
            If (maxResults > 0) && (count >= maxResults) Quit
            
            If direction = "out" || (direction = "both") {
                If fixedP '= "" {
                    Do ..TraverseWithPredicateFast(s, fixedP, hop, maxHops, nextP, dstLabel, .count, .nextFrontier, .seen)
                } Else {
                    Do ..TraverseAllPredicatesFast(s, hop, maxHops, nextP, dstLabel, .count, .nextFrontier, .seen)
                }
            }
            
            If direction = "in" || (direction = "both") {
                If fixedP '= "" {
                    Do ..TraverseWithPredicateFastInbound(s, fixedP, hop, .count, .nextFrontier, .seen)
                } Else {
                    Do ..TraverseAllPredicatesFastInbound(s, hop, .count, .nextFrontier, .seen)
                }
            }
        }
        
        If (maxResults > 0) && (count >= maxResults) Quit
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
        Set o = $Order(^KG("out", 0, s, p, o))
        If o = "" Quit
        If (dstLabel '= "") && ('$Data(^KG("label", dstLabel, o))) Continue
        If (hop < maxHops) && (nextP '= "") && ('$Data(^KG("degp", o, nextP))) Continue
        
        Set count = count + 1
        Set ^||BFS.Results(count) = $ListBuild(s, p, o, $Get(^KG("out", 0, s, p, o), 1.0), hop)
        
        If '$Data(seen(o)) Set nextFrontier(o) = ""
    }
}

ClassMethod TraverseAllPredicatesFast(s As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef count As %Integer, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set p = ""
    For {
        Set p = $Order(^KG("out", 0, s, p))
        If p = "" Quit
        Do ..TraverseWithPredicateFast(s, p, hop, maxHops, nextP, dstLabel, .count, .nextFrontier, .seen)
    }
}

/// Inbound BFS: traverse ^KG("in", 0, s, p, o) where s is the target, o is the source.
/// Result stored as (o, p, s) to preserve original edge direction.
ClassMethod TraverseWithPredicateFastInbound(s As %String, p As %String, hop As %Integer, ByRef count As %Integer, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set o = ""
    For {
        Set o = $Order(^KG("in", 0, s, p, o))
        If o = "" Quit
        Set count = count + 1
        Set ^||BFS.Results(count) = $ListBuild(s, p, o, $Get(^KG("out", 0, o, p, s), 1.0), hop)
        If '$Data(seen(o)) Set nextFrontier(o) = ""
    }
}

ClassMethod TraverseAllPredicatesFastInbound(s As %String, hop As %Integer, ByRef count As %Integer, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set p = ""
    For {
        Set p = $Order(^KG("in", 0, s, p))
        If p = "" Quit
        Do ..TraverseWithPredicateFastInbound(s, p, hop, .count, .nextFrontier, .seen)
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

/// Run BFSFast and return results as a JSON string.
/// Primary entry point for Python callers -- reads ^||BFS.Results in the same session.
/// 
/// @param srcId    Source node ID
/// @param predsJson JSON array of predicates per hop, e.g. '["rel1","rel2"]' or null/"" for all
/// @param maxHops  Maximum hops (default 2)
/// @param dstLabel Filter destination nodes by label (empty = no filter)
/// @returns JSON array: [{"s":..,"p":..,"o":..,"w":..,"step":..}, ...] in traversal order
ClassMethod BFSFastJson(srcId As %String, predsJson As %String = "", maxHops As %Integer = 2, dstLabel As %String = "", direction As %String = "out") As %String
{
    // Parse predicates JSON into a %DynamicArray (null/"" means all predicates)
    Set preds = ""
    If (predsJson '= "") && (predsJson '= "null") {
        Try {
            Set preds = ##class(%DynamicArray).%FromJSON(predsJson)
        } Catch {
            Set preds = ""
        }
    }

    // Run BFSFast -- writes to ^||BFS.Results(i) = $ListBuild(s,p,o,w,step)
    Set count = ..BFSFast(srcId, preds, maxHops, dstLabel, direction)

    If count = 0 Return "[]"

    // Build JSON array from ^||BFS.Results
    Set result = ##class(%DynamicArray).%New()
    For i = 1:1:count {
        Set lb = $Get(^||BFS.Results(i))
        If lb = "" Continue
        Set obj = ##class(%DynamicObject).%New()
        Do obj.%Set("s",    $ListGet(lb, 1))
        Do obj.%Set("p",    $ListGet(lb, 2))
        Do obj.%Set("o",    $ListGet(lb, 3))
        Do obj.%Set("w",    +$ListGet(lb, 4))
        Do obj.%Set("step", +$ListGet(lb, 5))
        Do result.%Push(obj)
    }

    Return result.%ToJSON()
}

ClassMethod BFSFastJsonChunked(srcId As %String, predsJson As %String = "", maxHops As %Integer = 2, dstLabel As %String = "", direction As %String = "out", maxResults As %Integer = 0) As %String
{
    Set preds = ""
    If (predsJson '= "") && (predsJson '= "null") {
        Try {
            Set preds = ##class(%DynamicArray).%FromJSON(predsJson)
        } Catch {
            Set preds = ""
        }
    }

    Set count = ..BFSFast(srcId, preds, maxHops, dstLabel, direction, maxResults)

    If count = 0 Return "CHUNKED:0"
    If (maxResults > 0) && (count > maxResults) Set count = maxResults

    Set tag = $TR($ZHex($Now())_$ZHex($RANDOM(999999)),":.","__")
    Set chunkSize = 9000
    Set chunk = ""
    Set chunkNum = 0
    Set first = 1

    For i = 1:1:count {
        Set lb = $Get(^||BFS.Results(i))
        If lb = "" Continue
        Set obj = "{""s"":"""_$Replace($ListGet(lb,1),"""","\\""")_""",""p"":"""_$Replace($ListGet(lb,2),"""","\\""")_""",""o"":"""_$Replace($ListGet(lb,3),"""","\\""")_""",""w"":"_(+$ListGet(lb,4))_",""step"":"_(+$ListGet(lb,5))_"}"
        If 'first Set obj = "," _ obj
        Set first = 0
        Set chunk = chunk _ obj
        If $Length(chunk) > chunkSize {
            Set chunkNum = chunkNum + 1
            Set ^ArnoKG("bfs_chunk", tag, chunkNum) = chunk
            Set chunk = ""
        }
    }
    If chunk '= "" {
        Set chunkNum = chunkNum + 1
        Set ^ArnoKG("bfs_chunk", tag, chunkNum) = chunk
    }

    Return "CHUNKED:" _ tag _ ":" _ chunkNum
}

ClassMethod ReadBFSChunk(tag As %String, chunkNum As %Integer) As %String
{
    Set val = $Get(^ArnoKG("bfs_chunk", tag, chunkNum), "")
    Kill ^ArnoKG("bfs_chunk", tag, chunkNum)
    Return val
}

ClassMethod BFSFastJsonSorted(srcId As %String, predsJson As %String = "", maxHops As %Integer = 2, dstLabel As %String = "", direction As %String = "out", maxResults As %Integer = 0) As %String
{
    Set preds = ""
    If (predsJson '= "") && (predsJson '= "null") {
        Try {
            Set preds = ##class(%DynamicArray).%FromJSON(predsJson)
        } Catch {
            Set preds = ""
        }
    }

    Set count = ..BFSFast(srcId, preds, maxHops, dstLabel, direction, maxResults)

    If count = 0 Return "SORTED:0"
    If (maxResults > 0) && (count > maxResults) Set count = maxResults

    Set tag = $TR($ZHex($Now())_$ZHex($RANDOM(999999)),":.","__")

    For i = 1:1:count {
        Set lb = $Get(^||BFS.Results(i))
        If lb = "" Continue
        Set s = $ListGet(lb, 1)
        Set p = $ListGet(lb, 2)
        Set o = $ListGet(lb, 3)
        Set w = +$ListGet(lb, 4)
        Set step = +$ListGet(lb, 5)
        Set ^ArnoKG("bfs_r", tag, step, o) = $ListBuild(s, p, w)
    }

    Return "SORTED:" _ tag _ ":" _ count
}

ClassMethod ReadBFSResults(tag As %String) As %String
{
    Set result = "["
    Set first = 1
    Set step = ""
    For {
        Set step = $Order(^ArnoKG("bfs_r", tag, step))
        Quit:step=""
        Set o = ""
        For {
            Set o = $Order(^ArnoKG("bfs_r", tag, step, o), 1, lb)
            Quit:o=""
            If 'first Set result = result _ ","
            Set first = 0
            Set s = $ListGet(lb, 1)
            Set p = $ListGet(lb, 2)
            Set w = +$ListGet(lb, 3)
            Set result = result _ "{""s"":"""_$Replace(s,"""","\\""")_""",""p"":"""_$Replace(p,"""","\\""")_""",""o"":"""_$Replace(o,"""","\\""")_""",""w"":"_w_",""step"":"_step_"}"
        }
    }
    Kill ^ArnoKG("bfs_r", tag)
    Return result _ "]"
}

ClassMethod ReadBFSPage(tag As %String, cursorStep As %String, cursorO As %String, pageSize As %Integer = 500) As %String
{
    Set result = ""
    Set count = 0
    Set nextStep = ""
    Set nextO = ""

    Set step = cursorStep
    Set o = cursorO

    If step = "" {
        Set step = $Order(^ArnoKG("bfs_r", tag, ""))
        Set o = ""
    }
    If step = "" {
        Kill ^ArnoKG("bfs_r", tag)
        Return "{""items"":[],""next_step"":-1,""next_o"":"""",""done"":true}"
    }

    For {
        Quit:step=""
        Set o = $Order(^ArnoKG("bfs_r", tag, step, o), 1, lb)
        If o = "" {
            Set step = $Order(^ArnoKG("bfs_r", tag, step))
            Set o = ""
            Continue
        }

        Set s = $ListGet(lb, 1)
        Set p = $ListGet(lb, 2)
        Set w = +$ListGet(lb, 3)
        Set entry = "{""s"":"""_$Replace(s,"""","\\""")_""",""p"":"""_$Replace(p,"""","\\""")_""",""o"":"""_$Replace(o,"""","\\""")_""",""w"":"_w_",""step"":"_step_"}"
        If result '= "" Set result = result _ ","
        Set result = result _ entry
        Set count = count + 1

        If count >= pageSize {
            Set nextO = $Order(^ArnoKG("bfs_r", tag, step, o))
            If nextO = "" {
                Set nextStep = $Order(^ArnoKG("bfs_r", tag, step))
                Set nextO = ""
            } Else {
                Set nextStep = step
            }
            Set stepVal = $Select(nextStep="":"-1",1:nextStep)
            Set oVal = """"_$Replace(nextO,"""","\\""")_""""
            Return "{""items"":["_result_"],""next_step"":"_stepVal_",""next_o"":"_oVal_",""done"":false}"
        }
    }

    Kill ^ArnoKG("bfs_r", tag)
    Return "{""items"":["_result_"],""next_step"":-1,""next_o"":"""",""done"":true}"
}

ClassMethod BFSFastCountDistinct(srcId As %String, predsJson As %String = "", maxHops As %Integer = 2, dstLabel As %String = "", direction As %String = "out") As %Integer
{
    Set preds = ""
    If (predsJson '= "") && (predsJson '= "null") {
        Try {
            Set preds = ##class(%DynamicArray).%FromJSON(predsJson)
        } Catch {
            Set preds = ""
        }
    }
    Set count = ..BFSFast(srcId, preds, maxHops, dstLabel, direction)
    Set distinct = 0
    Set seen2 = ""
    For i = 1:1:count {
        Set lb = $Get(^||BFS.Results(i))
        If lb = "" Continue
        Set o = $ListGet(lb, 3)
        If '$Data(seen2(o)) {
            Set seen2(o) = ""
            Set distinct = distinct + 1
        }
    }
    Return distinct
}

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

ClassMethod DijkstraProc(srcId As %String, dstId As %String, weightProp As %String = "", maxCost As %Double = 9999, maxHops As %Integer = 10, direction As %String = "out") As %String [ SqlProc, SqlName = DijkstraPath ]
{
	Quit ..DijkstraJson(srcId, dstId, weightProp, maxCost, maxHops, direction)
}

ClassMethod KGEdgeCount(maxCount As %Integer = 10000) As %Integer
{
    Set count = 0
    Set s = ""
    For {
        Set s = $Order(^KG("out", 0, s))
        Quit:s=""
        Set count = count + 1
        If count >= maxCount Quit
    }
    Return count
}

ClassMethod NKGPopulated() As %Integer
{
    Return ($Data(^NKG) > 0)
}

/// O(1) 1-hop neighbor count for a single predicate via ^KG("degp") counter.
/// Falls back to $Order scan when degp is not populated.
ClassMethod KHopCount(srcId As %String, pred As %String = "") As %Integer
{
    If pred '= "" {
        Set cnt = $Get(^KG("degp", srcId, pred), -1)
        If cnt >= 0 Return +cnt
    } Else {
        Set cnt = $Get(^KG("deg", srcId), -1)
        If cnt >= 0 Return +cnt
    }
    Set cnt = 0
    If pred '= "" {
        Set o = ""
        For {
            Set o = $Order(^KG("out", 0, srcId, pred, o))
            Quit:o=""
            Set cnt = cnt + 1
        }
    } Else {
        Set p = ""
        For {
            Set p = $Order(^KG("out", 0, srcId, p))
            Quit:p=""
            Set o = ""
            For {
                Set o = $Order(^KG("out", 0, srcId, p, o))
                Quit:o=""
                Set cnt = cnt + 1
            }
        }
    }
    Return cnt
}

/// Returns 1-hop neighbor IDs as a newline-delimited string — no JSON overhead.
/// ~10x faster than BFSFastJson for large neighbor sets when only IDs are needed.
ClassMethod KHopNeighborIds(srcId As %String, pred As %String = "") As %String
{
    Set result = ""
    If pred '= "" {
        Set o = ""
        For {
            Set o = $Order(^KG("out", 0, srcId, pred, o))
            Quit:o=""
            Set result = result _ o _ $Char(10)
        }
    } Else {
        Set p = ""
        For {
            Set p = $Order(^KG("out", 0, srcId, p))
            Quit:p=""
            Set o = ""
            For {
                Set o = $Order(^KG("out", 0, srcId, p, o))
                Quit:o=""
                Set result = result _ o _ $Char(10)
            }
        }
    }
    Return result
}

/// Distinct 2-hop neighbor count via pure ^KG $Order — no JSON, single round-trip.
/// Uses process-private globals for dedup. Excludes srcId from count.
ClassMethod KHop2Count(srcId As %String, pred As %String = "") As %Integer
{
    Kill ^||kh2s, ^||kh2all
    Set ^||kh2all(srcId) = ""

    If pred '= "" {
        Set o1 = ""
        For {
            Set o1 = $Order(^KG("out", 0, srcId, pred, o1))
            Quit:o1=""
            If '$Data(^||kh2all(o1)) {
                Set ^||kh2all(o1) = ""
                Set ^||kh2s(o1) = ""
            }
        }
    } Else {
        Set p1 = ""
        For {
            Set p1 = $Order(^KG("out", 0, srcId, p1))
            Quit:p1=""
            Set o1 = ""
            For {
                Set o1 = $Order(^KG("out", 0, srcId, p1, o1))
                Quit:o1=""
                If '$Data(^||kh2all(o1)) {
                    Set ^||kh2all(o1) = ""
                    Set ^||kh2s(o1) = ""
                }
            }
        }
    }

    Set cnt = 0
    Set mid = ""
    For {
        Set mid = $Order(^||kh2s(mid))
        Quit:mid=""
        If pred '= "" {
            Set o2 = ""
            For {
                Set o2 = $Order(^KG("out", 0, mid, pred, o2))
                Quit:o2=""
                If '$Data(^||kh2all(o2)) {
                    Set ^||kh2all(o2) = ""
                    Set cnt = cnt + 1
                }
            }
        } Else {
            Set p2 = ""
            For {
                Set p2 = $Order(^KG("out", 0, mid, p2))
                Quit:p2=""
                Set o2 = ""
                For {
                    Set o2 = $Order(^KG("out", 0, mid, p2, o2))
                    Quit:o2=""
                    If '$Data(^||kh2all(o2)) {
                        Set ^||kh2all(o2) = ""
                        Set cnt = cnt + 1
                    }
                }
            }
        }
    }
    Kill ^||kh2s, ^||kh2all
    Return cnt
}

/// Returns 2-hop distinct neighbor IDs as newline-delimited string.
/// Excludes srcId. Uses process-private globals for dedup — no JSON.
ClassMethod KHop2NeighborIds(srcId As %String, pred As %String = "", maxResults As %Integer = 0) As %String
{
    Kill ^||kh2s, ^||kh2all
    Set ^||kh2all(srcId) = ""

    If pred '= "" {
        Set o1 = ""
        For {
            Set o1 = $Order(^KG("out", 0, srcId, pred, o1))
            Quit:o1=""
            If '$Data(^||kh2all(o1)) {
                Set ^||kh2all(o1) = ""
                Set ^||kh2s(o1) = ""
            }
        }
    } Else {
        Set p1 = ""
        For {
            Set p1 = $Order(^KG("out", 0, srcId, p1))
            Quit:p1=""
            Set o1 = ""
            For {
                Set o1 = $Order(^KG("out", 0, srcId, p1, o1))
                Quit:o1=""
                If '$Data(^||kh2all(o1)) {
                    Set ^||kh2all(o1) = ""
                    Set ^||kh2s(o1) = ""
                }
            }
        }
    }

    Set result = ""
    Set cnt = 0
    Set mid = ""
    For {
        Set mid = $Order(^||kh2s(mid))
        Quit:mid=""
        If pred '= "" {
            Set o2 = ""
            For {
                Set o2 = $Order(^KG("out", 0, mid, pred, o2))
                Quit:o2=""
                If '$Data(^||kh2all(o2)) {
                    Set ^||kh2all(o2) = ""
                    Set result = result _ o2 _ $Char(10)
                    Set cnt = cnt + 1
                    If (maxResults > 0) && (cnt >= maxResults) {
                        Kill ^||kh2s, ^||kh2all
                        Return result
                    }
                }
            }
        } Else {
            Set p2 = ""
            For {
                Set p2 = $Order(^KG("out", 0, mid, p2))
                Quit:p2=""
                Set o2 = ""
                For {
                    Set o2 = $Order(^KG("out", 0, mid, p2, o2))
                    Quit:o2=""
                    If '$Data(^||kh2all(o2)) {
                        Set ^||kh2all(o2) = ""
                        Set result = result _ o2 _ $Char(10)
                        Set cnt = cnt + 1
                        If (maxResults > 0) && (cnt >= maxResults) {
                            Kill ^||kh2s, ^||kh2all
                            Return result
                        }
                    }
                }
            }
        }
    }
    Kill ^||kh2s, ^||kh2all
    Return result
}

ClassMethod KHop2CountFast(srcId As %String, pred As %String = "") As %Integer
{
    If pred '= "" {
        Set cnt = $Get(^KG("deg2p", srcId, pred), -1)
        If cnt >= 0 Return +cnt
    }
    Return ..KHop2Count(srcId, pred)
}

ClassMethod KHop2CountExact(srcId As %String, pred As %String = "") As %Integer
{
    If pred '= "" {
        Set cnt = $Get(^KG("deg2p_exact", srcId, pred), -1)
        If cnt >= 0 Return +cnt
    }
    Return ..KHop2Count(srcId, pred)
}

ClassMethod Build2HopExactStats() As %Integer
{
    Kill ^KG("deg2p_exact")
    If ##class(Graph.KG.ArnoAccel).IsAvailable() {
        Set dllid = $Get(^||ArnoAccel("dllid"), 0)
        If dllid > 0 {
            For fnName = "kg_build_2hop_exact_bitvec", "kg_build_2hop_exact_int" {
                Set fid = $ZF(-4, 3, dllid, fnName)
                If fid > 0 {
                    Set result = $ZF(-5, dllid, fid)
                    Set entries = +$Piece($Piece(result, "entries", 2), "}", 1)
                    If entries > 0 {
                        Do ..DecodeBuildResults()
                    }
                    Return entries
                }
            }
        }
    }
    Set src = ""
    Set cnt = 0
    For {
        Set src = $Order(^KG("out", 0, src))
        Quit:src=""
        Set pred = ""
        For {
            Set pred = $Order(^KG("out", 0, src, pred))
            Quit:pred=""
            Kill ^||h2s
            Set ^||h2s(src) = ""
            Set mid = ""
            For {
                Set mid = $Order(^KG("out", 0, src, pred, mid))
                Quit:mid=""
                Set ^||h2s(mid) = ""
            }
            Set exact = 0
            Set mid = ""
            For {
                Set mid = $Order(^KG("out", 0, src, pred, mid))
                Quit:mid=""
                Set o2 = ""
                For {
                    Set o2 = $Order(^KG("out", 0, mid, pred, o2))
                    Quit:o2=""
                    If '$Data(^||h2s(o2)) {
                        Set ^||h2s(o2) = ""
                        Set exact = exact + 1
                    }
                }
            }
            Kill ^||h2s
            If exact > 0 {
                Set ^KG("deg2p_exact", src, pred) = exact
                Set cnt = cnt + 1
            }
        }
    }
    Return cnt
}

ClassMethod DecodeBuildResults() [ Private ]
{
    Set sIdx = ""
    For {
        Set sIdx = $Order(^ArnoKG(42, sIdx))
        Quit:sIdx=""
        Set sName = $Get(^ArnoKG(43, sIdx))
        If sName = "" Continue
        Set pIdx = ""
        For {
            Set pIdx = $Order(^ArnoKG(42, sIdx, pIdx))
            Quit:pIdx=""
            Set cnt = +$Get(^ArnoKG(42, sIdx, pIdx))
            If cnt <= 0 Continue
            Set pName = $Get(^ArnoKG(44, pIdx))
            If pName = "" Continue
            Set ^KG("deg2p_exact", sName, pName) = cnt
        }
    }
    Kill ^ArnoKG(42), ^ArnoKG(43), ^ArnoKG(44)
}

}
