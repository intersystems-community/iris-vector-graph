/// NKG-accelerated traversal (K-hop, BFS) over the interned ^NKG index.
///
/// NOTE: the `If useNKG { ^NKG... } Else { ^KG... }` fallback branches read the
/// LEGACY no-shard ^KG("out", node, pred, dst) layout, NOT the production
/// shard-0 layout (^KG("out", 0, ...) — see Graph.KG.EdgeScan for the layout of
/// record). These Else branches are effectively dead in production: UseNKG()
/// returns true whenever ^NKG("$meta") exists, which is true after any sync().
/// If you ever make them reachable, fix them to scan the shard-0 layout first.
Class Graph.KG.NKGAccelTraversal Extends %RegisteredObject
{

ClassMethod KHopNeighbors(seedId As %String, k As %Integer = 2, maxNodes As %Integer = 1000) As %String
{
    Set useNKG = ##class(Graph.KG.NKGAccelLoader).UseNKG()
    If useNKG {
        Set seedIdx = $Get(^NKG("$NI", seedId))
        If seedIdx = "" Return "{""error"":""node not found"",""node"":"""_seedId_"""}"
    }

    Kill ^||visited, ^||frontier, ^||nextFrontier
    If useNKG {
        Set ^||visited(seedIdx) = 0
        Set ^||frontier(seedIdx) = ""
    } Else {
        Set ^||visited(seedId) = 0
        Set ^||frontier(seedId) = ""
    }
    Set totalNodes = 1

    For hop = 1:1:k {
        Kill ^||nextFrontier
        Set node = ""
        For {
            Set node = $Order(^||frontier(node))
            Quit:node=""
            If useNKG {
                Set pred = -99999999
                For {
                    Set pred = $Order(^NKG(-1, node, pred))
                    Quit:(pred="")!(pred>=0)
                    Set dst = ""
                    For {
                        Set dst = $Order(^NKG(-1, node, pred, dst))
                        Quit:dst=""
                        If '$Data(^||visited(dst)) {
                            Set ^||visited(dst) = hop
                            Set ^||nextFrontier(dst) = ""
                            Set totalNodes = totalNodes + 1
                            If totalNodes >= maxNodes Goto KHDone
                        }
                    }
                }
            } Else {
                Set pred = ""
                For {
                    Set pred = $Order(^KG("out", node, pred))
                    Quit:pred=""
                    Set dst = ""
                    For {
                        Set dst = $Order(^KG("out", node, pred, dst))
                        Quit:dst=""
                        If '$Data(^||visited(dst)) {
                            Set ^||visited(dst) = hop
                            Set ^||nextFrontier(dst) = ""
                            Set totalNodes = totalNodes + 1
                            If totalNodes >= maxNodes Goto KHDone
                        }
                    }
                }
            }
        }
        Kill ^||frontier
        Merge ^||frontier = ^||nextFrontier
    }

KHDone
    // Serialize result
    Set result = "{""seed"":"""_seedId_""",""hops"":"_k_",""totalNodes"":"_totalNodes_",""nodes"":["
    Set node = "", first = 1
    For {
        Set node = $Order(^||visited(node), 1, dist)
        Quit:node=""
        Set:'first result = result _ ","
        Set first = 0
        If useNKG {
            Set nodeStr = $Get(^NKG("$ND", node), node)
        } Else {
            Set nodeStr = node
        }
        Set result = result _ "{""id"":"""_nodeStr_""",""dist"":"_dist_"}"
    }
    Set result = result _ "]}"
    Return result
}
ClassMethod KHopNeighborsSorted(seedId As %String, k As %Integer = 2, maxNodes As %Integer = 1000) As %String
{
    Set useNKG = ##class(Graph.KG.NKGAccelLoader).UseNKG()
    If useNKG {
        Set seedIdx = $Get(^NKG("$NI", seedId))
        If seedIdx = "" Return "SORTED:0"
    }

    Kill ^||visited, ^||frontier, ^||nextFrontier
    If useNKG {
        Set ^||visited(seedIdx) = 0
        Set ^||frontier(seedIdx) = ""
    } Else {
        Set ^||visited(seedId) = 0
        Set ^||frontier(seedId) = ""
    }
    Set totalNodes = 1

    For hop = 1:1:k {
        Kill ^||nextFrontier
        Set node = ""
        For {
            Set node = $Order(^||frontier(node))
            Quit:node=""
            If useNKG {
                Set pred = -99999999
                For {
                    Set pred = $Order(^NKG(-1, node, pred))
                    Quit:(pred="")!(pred>=0)
                    Set dst = ""
                    For {
                        Set dst = $Order(^NKG(-1, node, pred, dst))
                        Quit:dst=""
                        If '$Data(^||visited(dst)) {
                            Set ^||visited(dst) = hop
                            Set ^||nextFrontier(dst) = ""
                            Set totalNodes = totalNodes + 1
                            If totalNodes >= maxNodes Goto KHSDone
                        }
                    }
                }
            } Else {
                Set pred = ""
                For {
                    Set pred = $Order(^KG("out", node, pred))
                    Quit:pred=""
                    Set dst = ""
                    For {
                        Set dst = $Order(^KG("out", node, pred, dst))
                        Quit:dst=""
                        If '$Data(^||visited(dst)) {
                            Set ^||visited(dst) = hop
                            Set ^||nextFrontier(dst) = ""
                            Set totalNodes = totalNodes + 1
                            If totalNodes >= maxNodes Goto KHSDone
                        }
                    }
                }
            }
        }
        Kill ^||frontier
        Merge ^||frontier = ^||nextFrontier
    }

KHSDone
    Set tag = $TR($ZHex($Now())_$ZHex($RANDOM(999999)),":.","__")
    Set node = ""
    For {
        Set node = $Order(^||visited(node), 1, dist)
        Quit:node=""
        If useNKG {
            Set nodeStr = $Get(^NKG("$ND", node), node)
        } Else {
            Set nodeStr = node
        }
        Set ^ArnoKG("khop_r", tag, dist, nodeStr) = ""
    }
    Return "SORTED:" _ tag _ ":" _ (totalNodes - 1) _ ":" _ seedId _ ":" _ k
}
ClassMethod KHopJson(seedId As %String, k As %Integer = 2, maxNodes As %Integer = 1000) As %String
{
    Return ##class(Graph.KG.NKGAccelAdjacency).StoreLargeOut("KHOP", ..KHopNeighbors(seedId, k, maxNodes))
}
ClassMethod PPRNative(seedId As %String, alpha As %Double = 0.85, maxIter As %Integer = 20, topK As %Integer = 20) As %String
{
    Set useNKG = ##class(Graph.KG.NKGAccelLoader).UseNKG()

    // Initialize: collect seed's local neighborhood (2-hop)
    // and run PPR only on the subgraph
    Set khJson = ..KHopNeighbors(seedId, 2, 5000)
    Set kh = ##class(%DynamicObject).%FromJSON(khJson)
    If kh.%IsDefined("error") Return khJson

    // Build local adjacency from the visited nodes
    Kill ^||pprAdj, ^||pprScore, ^||pprNext
    Set nodes = kh.nodes
    Set n = nodes.%Size()

    // Index the local nodes
    Kill ^||localIdx
    For i = 0:1:n-1 {
        Set nodeId = nodes.%Get(i).id
        Set ^||localIdx(nodeId) = i
    }

    // Build local adjacency
    For i = 0:1:n-1 {
        Set nodeId = nodes.%Get(i).id
        Set deg = 0
        If useNKG {
            Set nkgIdx = $Get(^NKG("$NI", nodeId), "")
            If nkgIdx '= "" {
                Set pred = -99999999
                For {
                    Set pred = $Order(^NKG(-1, nkgIdx, pred))
                    Quit:(pred="")!(pred>=0)
                    Set dst = ""
                    For {
                        Set dst = $Order(^NKG(-1, nkgIdx, pred, dst))
                        Quit:dst=""
                        Set dstStr = $Get(^NKG("$ND", dst), "")
                        If $Data(^||localIdx(dstStr)) {
                            Set ^||pprAdj(i, deg) = ^||localIdx(dstStr)
                            Set deg = deg + 1
                        }
                    }
                }
            }
        } Else {
            Set pred = ""
            For {
                Set pred = $Order(^KG("out", nodeId, pred))
                Quit:pred=""
                Set dst = ""
                For {
                    Set dst = $Order(^KG("out", nodeId, pred, dst))
                    Quit:dst=""
                    If $Data(^||localIdx(dst)) {
                        Set ^||pprAdj(i, deg) = ^||localIdx(dst)
                        Set deg = deg + 1
                    }
                }
            }
        }
        Set ^||pprAdj(i) = deg
    }

    // Find seed index
    Set seedLocalIdx = $Get(^||localIdx(seedId), 0)

    // Initialize scores: teleport vector = 1 for seed, 0 for others
    For i = 0:1:n-1 { Set ^||pprScore(i) = 0 }
    Set ^||pprScore(seedLocalIdx) = 1

    // Power iteration
    For iter = 1:1:maxIter {
        For i = 0:1:n-1 { Set ^||pprNext(i) = 0 }
        For i = 0:1:n-1 {
            Set deg = $Get(^||pprAdj(i), 0)
            If deg = 0 Continue
            Set contrib = ^||pprScore(i) / deg
            For j = 0:1:deg-1 {
                Set nbr = ^||pprAdj(i, j)
                Set ^||pprNext(nbr) = ^||pprNext(nbr) + (alpha * contrib)
            }
        }
        // Add teleport
        For i = 0:1:n-1 {
            Set ^||pprNext(i) = ^||pprNext(i) + ((1 - alpha) * $Select(i=seedLocalIdx:1, 1:0))
        }
        // Swap
        For i = 0:1:n-1 { Set ^||pprScore(i) = ^||pprNext(i) }
    }

    // Collect top-K by score (simple selection)
    Kill ^||ranked
    For i = 0:1:n-1 {
        Set score = ^||pprScore(i)
        Set ^||ranked(-score, i) = ""
    }

    Set result = "{""seed"":"""_seedId_""",""alpha"":"_alpha_",""iterations"":"_maxIter_",""scores"":["
    Set first = 1, count = 0
    Set sk = ""
    For {
        Set sk = $Order(^||ranked(sk))
        Quit:sk=""
        Set idx = ""
        For {
            Set idx = $Order(^||ranked(sk, idx))
            Quit:idx=""
            Set nodeId = nodes.%Get(idx).id
            Set score = -sk
            Set:'first result = result _ ","
            Set first = 0
            Set result = result _ "{""id"":"""_nodeId_""",""score"":"_$FNumber(score,"",6)_"}"
            Set count = count + 1
            If topK > 0 && count >= topK Goto PPRDone
        }
    }

PPRDone
    Set result = result _ "]}"
    Return result
}
ClassMethod PPRJson(seedId As %String, alpha As %Double = 0.85, maxIter As %Integer = 20) As %String
{
    Return ##class(Graph.KG.NKGAccelAdjacency).StoreLargeOut("PPR", ..PPRNative(seedId, alpha, maxIter))
}
ClassMethod RandomWalkJson(seedId As %String, walkLength As %Integer = 20, numWalks As %Integer = 10) As %String
{
    Set useNKG = ##class(Graph.KG.NKGAccelLoader).UseNKG()
    Set result = "["
    For w = 1:1:numWalks {
        Set:'(w=1) result = result _ ","
        Set result = result _ "["
        Set current = seedId
        If useNKG { Set currentIdx = $Get(^NKG("$NI", seedId), "") }
        Set result = result _ """" _ seedId _ """"
        For step = 1:1:walkLength {
            // Collect neighbors
            Kill ^||walkNbrs
            Set nbrCount = 0
            If useNKG && (currentIdx '= "") {
                Set pred = -99999999  For { Set pred=$Order(^NKG(-1,currentIdx,pred)) Quit:(pred="")||(pred>=0)  Set dst="" For { Set dst=$Order(^NKG(-1,currentIdx,pred,dst)) Quit:dst=""  Set nbrCount=nbrCount+1, ^||walkNbrs(nbrCount)=dst } }
            } Else {
                Set pred="" For { Set pred=$Order(^KG("out",current,pred)) Quit:pred=""  Set dst="" For { Set dst=$Order(^KG("out",current,pred,dst)) Quit:dst=""  Set nbrCount=nbrCount+1, ^||walkNbrs(nbrCount)=dst } }
            }
            If nbrCount = 0 Quit
            // Pick random neighbor
            Set pick = $Random(nbrCount) + 1
            If useNKG {
                Set currentIdx = ^||walkNbrs(pick)
                Set current = $Get(^NKG("$ND", currentIdx), currentIdx)
            } Else {
                Set current = ^||walkNbrs(pick)
            }
            Set result = result _ ",""" _ current _ """"
        }
        Set result = result _ "]"
    }
    Set result = result _ "]"
    Return ##class(Graph.KG.NKGAccelAdjacency).StoreLargeOut("RW", result)
}
ClassMethod BFSJson(seed As %String, predicatesJson As %String = "[]", maxHops As %Integer = 3, maxResults As %Integer = 0) As %String
{
    // arno 1d75d97: kg_bfs_global takes pre-serialized adjacency string as first arg.
    // ExportAdjacencyWithPreds() builds srcStrpredStrdstStr wire format.
    // Rust parses it entirely in memory — no global reads needed from $ZF(-5).
    Set dllPath = $Get(^||NKGAccel("libPath"), "/usr/irissys/mgr/libarno_callout.so")
    If '$Data(^||NKGAccel("bfs_fn")) {
        Set dllid = $ZF(-4, 1, dllPath)
        If dllid = 0 {
            Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults)
        }
        Set ^||NKGAccel("dllid") = dllid
        Set ^||NKGAccel("bfs_fn") = $ZF(-4, 3, dllid, "kg_bfs_global")
    }
    Set dllid = ^||NKGAccel("dllid")
    Set fnid  = ^||NKGAccel("bfs_fn")
    If fnid = 0 {
        Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults)
    }
    Set adjStr = ##class(Graph.KG.NKGAccelAdjacency).ExportAdjacencyWithPreds()
    Set raw = $ZF(-5, dllid, fnid, adjStr, seed, predicatesJson, maxHops, maxResults)
    If raw = "" || $Extract(raw, 1, 6) = "DEBUG:" {
        Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults)
    }
    Set tag = $Job _ "_bfs"
    Kill ^ArnoKG("bfs_r", tag)
    Set arr = ##class(%DynamicArray).%FromJSON(raw)
    Set sz = arr.%Size()
    For i = 0:1:(sz-1) {
        Set item = arr.%Get(i)
        Set step = +item.%Get("step")
        Set o    = item.%Get("o")
        Set s    = item.%Get("s")
        Set p    = item.%Get("p")
        Set w    = +item.%Get("w")
        Set ^ArnoKG("bfs_r", tag, step, o) = $ListBuild(s, p, w)
    }
    Return "SORTED:" _ tag
}
ClassMethod ShortestPathNKG(srcId As %String, dstId As %String, maxHops As %Integer = 10) As %String
{
    If srcId = "" || dstId = "" { Return "{""hops"":-1}" }

    Set srcIdx = $Get(^NKG("$NI", srcId), "")
    Set dstIdx = $Get(^NKG("$NI", dstId), "")
    If srcIdx = "" { Return "{""hops"":-1}" }
    If dstIdx = "" { Return "{""hops"":-1}" }
    If srcIdx = dstIdx { Return "{""hops"":0}" }

    Kill ^||BF, ^||BB, ^||SF, ^||SB, ^||NF, ^||NB
    Set ^||BF(srcIdx) = "", ^||SF(srcIdx) = 0
    Set ^||BB(dstIdx) = "", ^||SB(dstIdx) = 0

    For fHop = 1:1:maxHops {
        Kill ^||NF
        Set ni = ""
        For {
            Set ni = $Order(^||BF(ni))
            Quit:ni=""
            Set pred = -99999999
            For {
                Set pred = $Order(^NKG(-1, ni, pred))
                Quit:(pred="")!(pred>=0)
                Set di = ""
                For {
                    Set di = $Order(^NKG(-1, ni, pred, di))
                    Quit:di=""
                    If $Data(^||SB(di)) {
                        Set result = fHop + ^||SB(di)
                        Kill ^||BF,^||BB,^||SF,^||SB,^||NF,^||NB
                        Return "{""hops"":"_result_"}"
                    }
                    If '$Data(^||SF(di)) { Set ^||SF(di) = fHop, ^||NF(di) = "" }
                }
            }
        }
        If '$Data(^||NF) { Kill ^||BF,^||BB,^||SF,^||SB,^||NF,^||NB  Return "{""hops"":-1}" }
        Kill ^||BF  Merge ^||BF = ^||NF

        Kill ^||NB
        Set bHop = fHop
        Set ni = ""
        For {
            Set ni = $Order(^||BB(ni))
            Quit:ni=""
            Set pred = -99999999
            For {
                Set pred = $Order(^NKG(-2, ni, pred))
                Quit:(pred="")!(pred>=0)
                Set si = ""
                For {
                    Set si = $Order(^NKG(-2, ni, pred, si))
                    Quit:si=""
                    If $Data(^||SF(si)) {
                        Set result = ^||SF(si) + bHop
                        Kill ^||BF,^||BB,^||SF,^||SB,^||NF,^||NB
                        Return "{""hops"":"_result_"}"
                    }
                    If '$Data(^||SB(si)) { Set ^||SB(si) = bHop, ^||NB(si) = "" }
                }
            }
        }
        If '$Data(^||NB) { Kill ^||BF,^||BB,^||SF,^||SB,^||NF,^||NB  Return "{""hops"":-1}" }
        Kill ^||BB  Merge ^||BB = ^||NB
    }
    Kill ^||BF,^||BB,^||SF,^||SB,^||NF,^||NB
    Return "{""hops"":-1}"
}
ClassMethod CountDistinctKHop(srcId As %String, predsJson As %String = "", maxHops As %Integer = 2, direction As %String = "both") As %String
{
    Set sIdx = ##class(Graph.KG.GraphIndex).GetNodeIdx(srcId)
    If sIdx = "" Return "{""estimate"":0,""registers"":256,""std_error"":0.065,""note"":""node not found""}"

    Set preds = ""
    If (predsJson '= "") && (predsJson '= "null") && (predsJson '= "[]") {
        Try {
            Set preds = ##class(%DynamicArray).%FromJSON(predsJson)
        } Catch {
            Set preds = ""
        }
    }

    Set predsIdxArr = ""
    If $IsObject(preds) {
        Set predsIdxArr = ##class(%DynamicArray).%New()
        For pi = 0:1:(preds.%Size()-1) {
            Set pName = preds.%Get(pi)
            Set pIdx = ##class(Graph.KG.GraphIndex).GetLabelIdx(pName)
            If pIdx '= "" Do predsIdxArr.%Push(pIdx)
        }
    }

    Set merged = ##class(Graph.KG.GraphIndex).EmptyHLL()
    Kill seen, frontier
    Set seen(sIdx) = ""
    Set frontier(sIdx) = ""

    // Strategy: expand (maxHops-1) hops to reach the "penultimate" frontier,
    // then merge those nodes' pre-aggregated HLL sketches to estimate the final hop count.
    // For *1..2: expand 1 hop (get direct friends), merge their HLLs (estimates 2nd-hop count).
    // For *1..3: expand 2 hops, merge those nodes' HLLs (estimates 3rd-hop count).
    // This avoids materializing the final hop entirely -- O(frontier) not O(result_set).
    Set expansionHops = maxHops - 1

    For hop = 1:1:expansionHops {
        Kill nextFrontier
        Set s = ""
        For {
            Set s = $Order(frontier(s))
            Quit:s=""
            If direction = "out" || (direction = "both") {
                Set pIdx2 = -99999999
                For {
                    Set pIdx2 = $Order(^NKG(-1, s, pIdx2))
                    Quit:(pIdx2="")!(pIdx2>=0)
                    If $IsObject(predsIdxArr) {
                        Set matched = 0
                        For pi = 0:1:(predsIdxArr.%Size()-1) {
                            If -(pIdx2+1) = predsIdxArr.%Get(pi) { Set matched = 1  Quit }
                        }
                        If 'matched Continue
                    }
                    Set oIdx = ""
                    For {
                        Set oIdx = $Order(^NKG(-1, s, pIdx2, oIdx))
                        Quit:oIdx=""
                        If '$Data(seen(oIdx)) {
                            Set seen(oIdx) = ""
                            Set nextFrontier(oIdx) = ""
                        }
                    }
                }
            }
            If direction = "in" || (direction = "both") {
                Set pIdx2 = -99999999
                For {
                    Set pIdx2 = $Order(^NKG(-2, s, pIdx2))
                    Quit:(pIdx2="")!(pIdx2>=0)
                    If $IsObject(predsIdxArr) {
                        Set matched = 0
                        For pi = 0:1:(predsIdxArr.%Size()-1) {
                            If -(pIdx2+1) = predsIdxArr.%Get(pi) { Set matched = 1  Quit }
                        }
                        If 'matched Continue
                    }
                    Set oIdx = ""
                    For {
                        Set oIdx = $Order(^NKG(-2, s, pIdx2, oIdx))
                        Quit:oIdx=""
                        If '$Data(seen(oIdx)) {
                            Set seen(oIdx) = ""
                            Set nextFrontier(oIdx) = ""
                        }
                    }
                }
            }
        }
        If '$Data(nextFrontier) Quit
        Merge frontier = nextFrontier
    }

    // Merge pre-aggregated HLL for each node in the final frontier.
    // Each node's HLL pre-aggregates its outbound neighbors for each predicate.
    Set s = ""
    For {
        Set s = $Order(frontier(s))
        Quit:s=""
        If $IsObject(predsIdxArr) {
            For pi = 0:1:(predsIdxArr.%Size()-1) {
                Set pIdx = predsIdxArr.%Get(pi)
                Set hll = $Get(^NKG("$agg", s, pIdx, "hll"))
                If hll '= "" Do ##class(Graph.KG.GraphIndex).MergeHLL(.merged, hll)
            }
        } Else {
            Set pIdx = ""
            For {
                Set pIdx = $Order(^NKG("$agg", s, pIdx))
                Quit:pIdx=""
                Set hll = $Get(^NKG("$agg", s, pIdx, "hll"))
                If hll '= "" Do ##class(Graph.KG.GraphIndex).MergeHLL(.merged, hll)
            }
        }
    }

    Set estimate = ##class(Graph.KG.GraphIndex).EstimateHLL(merged)
    Return "{""estimate"":"_estimate_",""registers"":256,""std_error"":0.065}"
}
/// Returns 1 if nodeIdx has at least one edge for every predicate index in requiredPreds.
/// Uses $Data — no value fetch — for structural check only.
ClassMethod StructuralGuard(nodeIdx As %Integer, requiredPreds As %List) As %Boolean
{
    Set len = $ListLength(requiredPreds)
    If len = 0 Return 1
    For i = 1:1:len {
        Set predIdx = $List(requiredPreds, i)
        // NKG stores pred keys as -(pIdx+1) to keep them negative (sort before "")
        If '$Data(^NKG(-1, nodeIdx, -(predIdx+1))) Return 0
    }
    Return 1
}

ClassMethod Build2HopExact() As %Boolean
{
    If '##class(Graph.KG.NKGAccelLoader).IsLoaded() {
        Do ##class(Graph.KG.NKGAccelLoader).Load("/tmp/libarno_callout.so")
        If '##class(Graph.KG.NKGAccelLoader).IsLoaded() {
            If ##class(Graph.KG.ArnoAccel).IsAvailable() {
                Set ^||NKGAccel("dllid") = $Get(^||ArnoAccel("dllid"), 0)
            }
        }
    }
    If '##class(Graph.KG.NKGAccelLoader).IsLoaded() Return 0
    Set dllid = $Get(^||NKGAccel("dllid"), 0)
    If dllid = 0 Return 0
    Try {
        Set fid = $ZF(-4, 3, dllid, "kg_build_2hop_exact_stream")
    } Catch ex {
        Return 0
    }
    If fid = 0 Return 0
    Kill ^ArnoKG("2hs")
    Set raw = $ZF(-5, dllid, fid)
    If $Extract(raw, 1, 8) '= "CHUNKED:" Return 0
    Set n = +$Piece(raw, ":", 3)
    Set data = ""
    For i = 1:1:n {
        Set data = data _ $Get(^ArnoKG("2hs", i), "")
    }
    Kill ^ArnoKG("2hs")
    For pos = 1:1:$Length(data, $Char(10)) {
        Set line = $Piece(data, $Char(10), pos)
        If line = "" Continue
        Set s    = $Piece(line, $Char(31), 1)
        Set p    = $Piece(line, $Char(31), 2)
        Set cnt  = +$Piece(line, $Char(31), 3)
        If (s '= "") && (p '= "") && (cnt > 0) {
            Set ^KG("deg2p_exact", s, p) = cnt
        }
     }
     Return 1
}
}
