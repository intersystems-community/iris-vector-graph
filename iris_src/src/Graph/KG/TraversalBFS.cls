Class Graph.KG.TraversalBFS Extends %RegisteredObject
{

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
        // Reuse last predicate when preds has fewer entries than hops.
        If $IsObject(preds) && (preds.%Size()>0) {
            Set predIdx = hop-1
            If predIdx >= preds.%Size() Set predIdx = preds.%Size()-1
            Set fixedP = preds.%Get(predIdx)
            If hop < maxHops {
                Set nextPredIdx = hop
                If nextPredIdx >= preds.%Size() Set nextPredIdx = preds.%Size()-1
                Set nextP = preds.%Get(nextPredIdx)
            } Else {
                Set nextP = ""
            }
        } Else {
            Set fixedP = ""
            Set nextP = ""
        }
        
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
        // Reuse last predicate when preds has fewer entries than hops.
        // [:BINDS*1..3] passes preds=["BINDS"] — apply "BINDS" at every hop.
        If $IsObject(preds) && (preds.%Size()>0) {
            Set predIdx = hop-1
            If predIdx >= preds.%Size() Set predIdx = preds.%Size()-1
            Set fixedP = preds.%Get(predIdx)
            // nextP is only used as a lookahead for hops before the last.
            // At the last hop, nextP="" disables the lookahead filter.
            If hop < maxHops {
                Set nextPredIdx = hop
                If nextPredIdx >= preds.%Size() Set nextPredIdx = preds.%Size()-1
                Set nextP = preds.%Get(nextPredIdx)
            } Else {
                Set nextP = ""
            }
        } Else {
            Set fixedP = ""
            Set nextP = ""
        }
        
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
ClassMethod BFSJSON(srcId As %String, preds As %DynamicArray, maxHops As %Integer = 2, dstLabel As %String = "") As %DynamicArray
{
    Return ..BFS(srcId, preds, maxHops, dstLabel)
}
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
/// BFSFastJsonDirect — same semantics as BFSFastJson but builds the JSON string
/// via direct concatenation instead of %DynamicObject allocation (one per row).
/// Drop-in replacement: identical signature, identical output format.
ClassMethod BFSFastJsonDirect(srcId As %String, predsJson As %String = "", maxHops As %Integer = 2, dstLabel As %String = "", direction As %String = "out") As %String
{
    Set preds = ""
    If (predsJson '= "") && (predsJson '= "null") {
        Try { Set preds = ##class(%DynamicArray).%FromJSON(predsJson) } Catch { Set preds = "" }
    }

    Set count = ..BFSFast(srcId, preds, maxHops, dstLabel, direction)
    If count = 0 Return "[]"

    Set result = "["
    Set first = 1
    For i = 1:1:count {
        Set lb = $Get(^||BFS.Results(i))
        If lb = "" Continue
        If 'first Set result = result _ ","
        Set first = 0
        Set s = $ListGet(lb, 1)
        Set p = $ListGet(lb, 2)
        Set o = $ListGet(lb, 3)
        // Only escape when a quote is actually present (fast $Find check before $Replace)
        If $Find(s, """") Set s = $Replace(s, """", "\""")
        If $Find(p, """") Set p = $Replace(p, """", "\""")
        If $Find(o, """") Set o = $Replace(o, """", "\""")
        Set result = result
            _ "{""s"":""" _ s _ ""","
            _ """p"":""" _ p _ ""","
            _ """o"":""" _ o _ ""","
            _ """w"":" _ +$ListGet(lb, 4) _ ","
            _ """step"":" _ +$ListGet(lb, 5) _ "}"
    }
    Return result _ "]"
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
        Set obj = "{""s"":"""_$Replace($ListGet(lb,1),"""","\""")_""",""p"":"""_$Replace($ListGet(lb,2),"""","\""")_""",""o"":"""_$Replace($ListGet(lb,3),"""","\""")_""",""w"":"_(+$ListGet(lb,4))_",""step"":"_(+$ListGet(lb,5))_"}"
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
ClassMethod ReadBFSChunk(tag As %String, chunkNum As %Integer) As %String
{
    Set val = $Get(^ArnoKG("bfs_chunk", tag, chunkNum), "")
    Kill ^ArnoKG("bfs_chunk", tag, chunkNum)
    Return val
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
            Set result = result _ "{""s"":"""_$Replace(s,"""","\""")_""",""p"":"""_$Replace(p,"""","\""")_""",""o"":"""_$Replace(o,"""","\""")_""",""w"":"_w_",""step"":"_step_"}"
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
        Set entry = "{""s"":"""_$Replace(s,"""","\""")_""",""p"":"""_$Replace(p,"""","\""")_""",""o"":"""_$Replace(o,"""","\""")_""",""w"":"_w_",""step"":"_step_"}"
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
            Set oVal = """"_$Replace(nextO,"""","\""")_""""
            Return "{""items"":["_result_"],""next_step"":"_stepVal_",""next_o"":"_oVal_",""done"":false}"
        }
    }

    Kill ^ArnoKG("bfs_r", tag)
    Return "{""items"":["_result_"],""next_step"":-1,""next_o"":"""",""done"":true}"
}
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
ClassMethod TraverseAllPredicates(s As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef pathId As %Integer, results As %DynamicArray, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set p = ""
    For {
        Set p = $Order(^KG("out", 0, s, p))
        If p = "" Quit
        Do ..TraverseWithPredicate(s, p, hop, maxHops, nextP, dstLabel, .pathId, results, .nextFrontier, .seen)
    }
}
ClassMethod TraverseWithPredicateFast(s As %String, p As %String, hop As %Integer, maxHops As %Integer, nextP As %String, dstLabel As %String, ByRef count As %Integer, ByRef nextFrontier, ByRef seen) [ Internal, Private ]
{
    Set o = ""
    For {
        Set o = $Order(^KG("out", 0, s, p, o))
        If o = "" Quit
        If (dstLabel '= "") && ('$Data(^KG("label", dstLabel, o))) Continue
        
        // Always record this hop's result — leaf nodes at hop N are valid results
        // even when hop N < maxHops.
        Set count = count + 1
        Set ^||BFS.Results(count) = $ListBuild(s, p, o, $Get(^KG("out", 0, s, p, o), 1.0), hop)
        
        // Only add to nextFrontier if the node can continue on the next hop.
        // The nextP lookahead prunes the frontier, not the result set.
        If '$Data(seen(o)) {
            If (hop < maxHops) && (nextP '= "") && ('$Data(^KG("degp", o, nextP))) {
                // Node has no next-hop edges — don't add to frontier, but result was recorded.
            } Else {
                Set nextFrontier(o) = ""
            }
        }
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
}
