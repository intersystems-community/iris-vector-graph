/// Graph.KG.NKGAccel - NICHE Knowledge Graph accelerated analytics.
/// Seed-local algorithms run in ObjectScript at $ORDER speed.
/// Full-graph algorithms export adjacency for Rust consumption.
///
/// Auto-detects ^NKG (integer-encoded) vs ^KG (string-subscripted).
Class Graph.KG.NKGAccel Extends %RegisteredObject
{

/// Detect whether ^NKG is available. Cache per-process.
ClassMethod UseNKG() As %Boolean
{
    If $Data(^||NKGAccel("useNKG")) Return ^||NKGAccel("useNKG")
    Set use = ($Data(^NKG("$meta")) > 0)
    Set ^||NKGAccel("useNKG") = use
    Return use
}

/// Clear cached detection (call after BuildKG or schema change)
ClassMethod ResetCache()
{
    Kill ^||NKGAccel
}

/// Load arno callout library and register all function IDs for this process.
/// Call once at startup: Do ##class(Graph.KG.NKGAccel).Load("/path/to/libarno_callout.so")
/// Returns 1 on success, 0 if library not found or load failed.
/// Safe to call multiple times - no-ops if already loaded in this process.
ClassMethod Load(libPath As %String = "/usr/irissys/mgr/libarno_callout.so") As %Boolean
{
    If $Data(^||NKGAccel("dllid")) Return 1
    Set dllid = $ZF(-4, 1, libPath)
    If dllid = 0 Return 0
    Set ^||NKGAccel("dllid") = dllid
    Set ^||NKGAccel("libPath") = libPath
    Set ^||NKGAccel("bfs_fn") = $ZF(-4, 3, dllid, "kg_bfs_global")
    Return 1
}

ClassMethod Unload()
{
    Kill ^||NKGAccel
}

/// Returns 1 if arno is loaded and bfs function is available in this process.
ClassMethod IsLoaded() As %Boolean
{
    If '$Data(^||NKGAccel("dllid")) Return 0
    Return (^||NKGAccel("bfs_fn") > 0)
}

// ============================================================
// T015: ExportAdjacency - full graph for Rust consumption
// ============================================================

/// Export full graph adjacency as integer-indexed lines.
/// Format: srcIdx:dstIdx1,dstIdx2,...\n
/// Returns string directly if < 3MB, otherwise chunks to ^||adj.
ClassMethod ExportAdjacency() As %String
{
    If ..UseNKG() Return ..ExportAdjacencyNKG()
    Return ..ExportAdjacencyKG()
}

/// Export from ^NKG (integer subscripts - zero string overhead)
ClassMethod ExportAdjacencyNKG() As %String [ Private ]
{
    Set chunkNum = 0, chunk = ""
    Set src = ""
    For {
        Set src = $Order(^NKG(-3, src))
        Quit:src=""
        Set nbrs = "", pred = ""
        For {
            Set pred = $Order(^NKG(-1, src, pred))
            Quit:pred=""
            Set dst = ""
            For {
                Set dst = $Order(^NKG(-1, src, pred, dst))
                Quit:dst=""
                Set:nbrs'="" nbrs = nbrs _ ","
                Set nbrs = nbrs _ dst
                If $Length(nbrs) > 7000 {
                    Set line = src _ ":" _ nbrs _ $Char(10)
                    If $Length(chunk) > 0 {
                        Set chunkNum = chunkNum + 1
                        Set ^||adj(chunkNum) = chunk
                    }
                    Set chunkNum = chunkNum + 1
                    Set ^||adj(chunkNum) = line
                    Set chunk = "", nbrs = ""
                }
            }
        }
        If nbrs '= "" {
            Set line = src _ ":" _ nbrs _ $Char(10)
            If $Length(chunk) + $Length(line) > 7000 && $Length(chunk) > 0 {
                Set chunkNum = chunkNum + 1
                Set ^||adj(chunkNum) = chunk
                Set chunk = ""
            }
            Set chunk = chunk _ line
        }
    }
    If chunk '= "" {
        Set chunkNum = chunkNum + 1
        Set ^||adj(chunkNum) = chunk
    }
    If chunkNum > 0 {
        Set n = $Get(^NKG("$meta", "nodeCount"), 0)
        Set nodemap = "NODEMAP:" _ n _ $Char(10)
        For i = 0:1:n-1 {
            Set nodemap = nodemap _ i _ "=" _ $Get(^NKG("$ND", i), "") _ $Char(10)
        }
        Set nodemap = nodemap _ "---" _ $Char(10)
        Set ^||adj = chunkNum + 1
        For i = chunkNum:-1:1 { Set ^||adj(i+1) = ^||adj(i) }
        Set ^||adj(1) = nodemap
        Return "CHUNKED:" _ (chunkNum + 1) _ "|NODEMAP"
    }
    Return ""
}

/// Export from ^KG (string subscripts - builds node index on the fly)
ClassMethod ExportAdjacencyKG() As %String [ Private ]
{
    // Pass 1: assign integer indices to all nodes
    Kill ^||nodeIdx
    Set n = 0, node = ""
    For {
        Set node = $Order(^KG("deg", node))
        Quit:node=""
        Set ^||nodeIdx(node) = n
        Set ^||nodeMap(n) = node
        Set n = n + 1
    }
    // Also pick up nodes only in "in" (no outgoing edges)
    Set node = ""
    For {
        Set node = $Order(^KG("in", node))
        Quit:node=""
        If '$Data(^||nodeIdx(node)) {
            Set ^||nodeIdx(node) = n
            Set ^||nodeMap(n) = node
            Set n = n + 1
        }
    }

    // Pass 2: build adjacency
    Set result = "", chunk = "", chunkNum = 0
    Set src = ""
    For {
        Set src = $Order(^KG("out", src))
        Quit:src=""
        Set sIdx = $Get(^||nodeIdx(src))
        If sIdx = "" Continue
        Set nbrs = "", pred = ""
        For {
            Set pred = $Order(^KG("out", src, pred))
            Quit:pred=""
            Set dst = ""
            For {
                Set dst = $Order(^KG("out", src, pred, dst))
                Quit:dst=""
                Set dIdx = $Get(^||nodeIdx(dst))
                If dIdx = "" Continue
                Set:nbrs'="" nbrs = nbrs _ ","
                Set nbrs = nbrs _ dIdx
            }
        }
        Set line = sIdx _ ":" _ nbrs _ $Char(10)
        If $Length(result) + $Length(line) > 3000000 {
            If chunkNum = 0 {
                Set chunkNum = 1
                Set ^||adj(1) = result
                Set result = ""
            }
            If $Length(chunk) + $Length(line) > 32000 {
                Set chunkNum = chunkNum + 1
                Set ^||adj(chunkNum) = chunk
                Set chunk = ""
            }
            Set chunk = chunk _ line
        } Else {
            Set result = result _ line
        }
    }
    If chunkNum > 0 {
        If chunk '= "" { Set chunkNum = chunkNum + 1, ^||adj(chunkNum) = chunk }
        Set ^||adj = chunkNum
        Return "CHUNKED:" _ chunkNum _ "|NODEMAP"
    }
    // Append node map header so Rust can resolve indices to strings
    Set header = "NODEMAP:" _ n _ $Char(10)
    For i = 0:1:n-1 { Set header = header _ i _ "=" _ ^||nodeMap(i) _ $Char(10) }
    Return header _ "---" _ $Char(10) _ result
}

// ============================================================
// T019: KHopNeighbors - seed-local BFS at $ORDER speed
// ============================================================

/// k-hop BFS from a seed node. Returns JSON with visited nodes and distances.
/// Runs entirely in ObjectScript - no Rust, no graph serialization.
/// Cost: ~0.5ms for 2-hop fanout-16 regardless of graph size.
ClassMethod KHopNeighbors(seedId As %String, k As %Integer = 2, maxNodes As %Integer = 1000) As %String
{
    Set useNKG = ..UseNKG()
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
                Set pred = ""
                For {
                    Set pred = $Order(^NKG(-1, node, pred))
                    Quit:pred=""
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
    Set useNKG = ..UseNKG()
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
                Set pred = ""
                For {
                    Set pred = $Order(^NKG(-1, node, pred))
                    Quit:pred=""
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

ClassMethod ReadKHopResults(tag As %String) As %String
{
    Set dist = "", first = 1
    Set result = ""
    Set totalNodes = 0
    For {
        Set dist = $Order(^ArnoKG("khop_r", tag, dist))
        Quit:dist=""
        Set node = ""
        For {
            Set node = $Order(^ArnoKG("khop_r", tag, dist, node))
            Quit:node=""
            If 'first Set result = result _ ","
            Set first = 0
            Set result = result _ "{""id"":"""_$Replace(node,"""","\\""")_""",""dist"":"_dist_"}"
            Set totalNodes = totalNodes + 1
        }
    }
    Kill ^ArnoKG("khop_r", tag)
    Return result
}

/// Personalized PageRank from seed node(s). Runs in ObjectScript.
/// Returns top-K nodes with scores as JSON.
ClassMethod PPRNative(seedId As %String, alpha As %Double = 0.85, maxIter As %Integer = 20, topK As %Integer = 20) As %String
{
    Set useNKG = ..UseNKG()

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
                Set pred = ""
                For {
                    Set pred = $Order(^NKG(-1, nkgIdx, pred))
                    Quit:pred=""
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

// ============================================================
// Wrappers that replace CacheGraphJson path for seed-local
// ============================================================

/// K-hop neighborhood - pure ObjectScript, no Rust needed
ClassMethod KHopJson(seedId As %String, k As %Integer = 2, maxNodes As %Integer = 1000) As %String
{
    Return ..StoreLargeOut("KHOP", ..KHopNeighbors(seedId, k, maxNodes))
}

/// Personalized PageRank - pure ObjectScript on local subgraph
ClassMethod PPRJson(seedId As %String, alpha As %Double = 0.85, maxIter As %Integer = 20) As %String
{
    Return ..StoreLargeOut("PPR", ..PPRNative(seedId, alpha, maxIter))
}

/// Random walk - pure ObjectScript
ClassMethod RandomWalkJson(seedId As %String, walkLength As %Integer = 20, numWalks As %Integer = 10) As %String
{
    Set useNKG = ..UseNKG()
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
                Set pred = "" For { Set pred=$Order(^NKG(-1,currentIdx,pred)) Quit:pred=""  Set dst="" For { Set dst=$Order(^NKG(-1,currentIdx,pred,dst)) Quit:dst=""  Set nbrCount=nbrCount+1, ^||walkNbrs(nbrCount)=dst } }
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
    Return ..StoreLargeOut("RW", result)
}

ClassMethod BFSJson(seed As %String, predicatesJson As %String = "[]", maxHops As %Integer = 3, maxResults As %Integer = 0) As %String
{
    // arno 1d75d97: kg_bfs_global takes pre-serialized adjacency string as first arg.
    // ExportAdjacencyWithPreds() builds srcStr\x1fpredStr\x1fdstStr\n wire format;
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
    Set adjStr = ..ExportAdjacencyWithPreds()
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

ClassMethod InvalidateAdjCache()
{
    Kill ^ArnoKG("KG", "nkg_adj_ver")
    Kill ^ArnoKG("KG", "nkg_adj")
}

ClassMethod GetFirstNKGNode() As %String
{
    Return $Get(^NKG("$ND", 0), "")
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
            Set pred = ""
            For {
                Set pred = $Order(^NKG(-1, ni, pred))
                Quit:pred=""
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
            Set pred = ""
            For {
                Set pred = $Order(^NKG(-2, ni, pred))
                Quit:pred=""
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

ClassMethod WarmAdjCache() As %Integer
{
    Do ..CacheNKGAdj()
    Return $Get(^ArnoKG("KG", "nkg_adj"), 0)
}

ClassMethod StoreLargeOut(tag As %String, json As %String) As %String [ Private ]
{
    If $Length(json) <= 9000 { Return json }
    Kill ^||LargeOut(tag)
    Set chunkSize = 9000, pos = 1, n = 0
    While pos <= $Length(json) {
        Set n = n + 1
        Set ^||LargeOut(tag, n) = $Extract(json, pos, pos + chunkSize - 1)
        Set pos = pos + chunkSize
    }
    Return "CHUNKED:" _ tag _ ":" _ n
}

ClassMethod ReadLargeOutChunk(tag As %String, chunkNum As %Integer) As %String
{
    If tag = "BFS" { Return $Get(^ArnoKG("bfs_result", chunkNum), "") }
    Return $Get(^||LargeOut(tag, chunkNum), "")
}

ClassMethod CacheNKGAdj() [ Private ]
{
    Set currentVer = $Get(^NKG("$meta", "version"), 0)
    Set cachedVer = $Get(^ArnoKG("KG", "nkg_adj_ver"), -1)
    If cachedVer = currentVer Quit
    Kill ^ArnoKG("KG", "nkg_adj")
    Set adj = ..ExportAdjacency()
    If $Extract(adj, 1, 8) = "CHUNKED:" {
        Set n = $Piece($Piece(adj, ":", 2), "|", 1)
        For i = 1:1:n {
            Set ^ArnoKG("KG", "nkg_adj", i) = $Get(^||adj(i), "")
        }
        Set ^ArnoKG("KG", "nkg_adj") = n
    } ElseIf $Length(adj) <= 32000 {
        Set ^ArnoKG("KG", "nkg_adj") = 1
        Set ^ArnoKG("KG", "nkg_adj", 1) = adj
    } Else {
        Set pos = 1, chunkNum = 0
        While pos <= $Length(adj) {
            Set chunkNum = chunkNum + 1
            Set ^ArnoKG("KG", "nkg_adj", chunkNum) = $Extract(adj, pos, pos + 32000 - 1)
            Set pos = pos + 32000
        }
        Set ^ArnoKG("KG", "nkg_adj") = chunkNum
    }
    Set ^ArnoKG("KG", "nkg_adj_ver") = currentVer
}

ClassMethod ExportAdjacencyFromSeed(seed As %String, maxHops As %Integer = 4, predicatesJson As %String = "[]") As %String [ Private ]
{
    Set seedIdx = $Get(^NKG("$NI", seed), "")
    If seedIdx = "" Return ""

    Set preds = ""
    If predicatesJson '= "[]" && (predicatesJson '= "") {
        Try { Set preds = ##class(%DynamicArray).%FromJSON(predicatesJson) } Catch { Set preds = "" }
    }

    // BFS to collect reachable node indices within maxHops
    Kill ^||seedBFS
    Set ^||seedBFS("frontier", seedIdx) = ""
    Set ^||seedBFS("seen", seedIdx) = ""

    For hop = 1:1:maxHops {
        Kill ^||seedBFS("nextFrontier")
        Set s = ""
        For {
            Set s = $Order(^||seedBFS("frontier", s))
            Quit:s=""
            Set pred = ""
            For {
                Set pred = $Order(^NKG(-1, s, pred))
                Quit:pred=""
                If $IsObject(preds) {
                    Set allowed = 0
                    Set predStr = $Get(^NKG("$LS", -pred - 1), "")
                    For pi = 0:1:preds.%Size()-1 {
                        If preds.%Get(pi) = predStr { Set allowed = 1 Quit }
                    }
                    If 'allowed Continue
                }
                Set dst = ""
                For {
                    Set dst = $Order(^NKG(-1, s, pred, dst))
                    Quit:dst=""
                    Set ^||seedBFS("nodes", s) = ""
                    Set ^||seedBFS("nodes", dst) = ""
                    If '$Data(^||seedBFS("seen", dst)) {
                        Set ^||seedBFS("seen", dst) = ""
                        Set ^||seedBFS("nextFrontier", dst) = ""
                    }
                }
            }
            // Inbound traversal for undirected
            Set pred = ""
            For {
                Set pred = $Order(^NKG(-2, s, pred))
                Quit:pred=""
                Set dst = ""
                For {
                    Set dst = $Order(^NKG(-2, s, pred, dst))
                    Quit:dst=""
                    Set ^||seedBFS("nodes", s) = ""
                    Set ^||seedBFS("nodes", dst) = ""
                    If '$Data(^||seedBFS("seen", dst)) {
                        Set ^||seedBFS("seen", dst) = ""
                        Set ^||seedBFS("nextFrontier", dst) = ""
                    }
                }
            }
        }
        If '$Data(^||seedBFS("nextFrontier")) Quit
        Merge ^||seedBFS("frontier") = ^||seedBFS("nextFrontier")
        Kill ^||seedBFS("nextFrontier")
    }

    // Export only edges between collected nodes
    Set result = ""
    Set nodeIdx = ""
    For {
        Set nodeIdx = $Order(^||seedBFS("nodes", nodeIdx))
        Quit:nodeIdx=""
        Set srcStr = $Get(^NKG("$ND", nodeIdx), nodeIdx)
        Set pred = ""
        For {
            Set pred = $Order(^NKG(-1, nodeIdx, pred))
            Quit:pred=""
            Set predStr = $Get(^NKG("$LS", -pred - 1), pred)
            Set dst = ""
            For {
                Set dst = $Order(^NKG(-1, nodeIdx, pred, dst))
                Quit:dst=""
                If '$Data(^||seedBFS("nodes", dst)) Continue
                Set dstStr = $Get(^NKG("$ND", dst), dst)
                Set result = result _ srcStr _ $Char(31) _ predStr _ $Char(31) _ dstStr _ $Char(10)
            }
        }
    }
    Kill ^||seedBFS
    Return result
}

ClassMethod ExportAdjacencyWithPreds() As %String [ Private ]
{
    Set result = "", chunk = "", chunkNum = 0
    Set src = ""
    For {
        Set src = $Order(^NKG(-1, src))
        Quit:src=""
        Set srcStr = $Get(^NKG("$ND", src), src)
        Set pred = ""
        For {
            Set pred = $Order(^NKG(-1, src, pred))
            Quit:pred=""
            Set predStr = $Get(^NKG("$LS", -pred - 1), pred)
            Set dst = ""
            For {
                Set dst = $Order(^NKG(-1, src, pred, dst))
                Quit:dst=""
                Set dstStr = $Get(^NKG("$ND", dst), dst)
                Set line = srcStr _ $Char(31) _ predStr _ $Char(31) _ dstStr _ $Char(10)
                If $Length(result) + $Length(line) > 3000000 {
                    If chunkNum = 0 {
                        Set chunkNum = 1
                        Set ^||adjWP(1) = result
                        Set result = ""
                    }
                    If $Length(chunk) + $Length(line) > 32000 {
                        Set chunkNum = chunkNum + 1
                        Set ^||adjWP(chunkNum) = chunk
                        Set chunk = ""
                    }
                    Set chunk = chunk _ line
                } Else {
                    Set result = result _ line
                }
            }
        }
    }
    If chunkNum > 0 {
        If chunk '= "" { Set chunkNum = chunkNum + 1, ^||adjWP(chunkNum) = chunk }
        Set ^||adjWP = chunkNum
        Return "CHUNKED:" _ chunkNum
    }
    Return result
}

ClassMethod BuildNKGRust() As %String
{
    Try {
        If '##class(Graph.KG.ArnoAccel).IsAvailable() {
            Return "{""error"":""Rust callout not available — load ArnoAccel first""}"
        }
        Set dllid = $Get(^||ArnoAccel("dllid"), 0)
        If dllid = 0 Return "{""error"":""ArnoAccel DLL not loaded""}"
        Set fid = $ZF(-4, 3, dllid, "kg_build_nkg")
        If fid = 0 Return "{""error"":""kg_build_nkg not found in DLL""}"
        Return $ZF(-5, dllid, fid)
    } Catch ex {
        Return "{""error"":""Rust callout failed: "_ex.DisplayString()_"""}"
    }
}

ClassMethod Capabilities() As %String
{
    Set cap = {}
    Do cap.%Set("version", "0.1.0")
    Do cap.%Set("nkg_accel", 1, "boolean")
    Do cap.%Set("nkg_data", ..UseNKG(), "boolean")
    Do cap.%Set("global_set", ##class(%Dictionary.ClassDefinition).%ExistsId("User.GlobalSet"), "boolean")
    Do cap.%Set("vec_index", ##class(%Dictionary.ClassDefinition).%ExistsId("Graph.KG.VecIndex"), "boolean")
    Set hasRust = 0
    Try {
        If ##class(Graph.KG.ArnoAccel).IsAvailable() Set hasRust = 1
    } Catch {}
    Do cap.%Set("rust_callout", hasRust, "boolean")
    Set algos = ["khop", "ppr", "random_walk", "export_adjacency"]
    Do cap.%Set("algorithms", algos)
    Set rustAlgos = []
    If hasRust {
        Do rustAlgos.%Push("pagerank")
        Do rustAlgos.%Push("wcc")
        Do rustAlgos.%Push("cdlp")
        Do rustAlgos.%Push("bfs")
    }
    Do cap.%Set("rust_algorithms", rustAlgos)
    Do cap.%Set("bfs", hasRust, "boolean")
    Do cap.%Set("count_distinct_khop", 1, "boolean")
    Return cap.%ToJSON()
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
                Set pIdx2 = ""
                For {
                    Set pIdx2 = $Order(^NKG(-1, s, pIdx2))
                    Quit:pIdx2=""
                    If pIdx2 >= 0 Quit
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
                Set pIdx2 = ""
                For {
                    Set pIdx2 = $Order(^NKG(-2, s, pIdx2))
                    Quit:pIdx2=""
                    If pIdx2 >= 0 Quit
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

ClassMethod Build2HopExact() As %Boolean
{
    If '..IsLoaded() {
        Do ..Load("/tmp/libarno_callout.so")
        If '..IsLoaded() {
            If ##class(Graph.KG.ArnoAccel).IsAvailable() {
                Set ^||NKGAccel("dllid") = $Get(^||ArnoAccel("dllid"), 0)
            }
        }
    }
    If '..IsLoaded() Return 0
    Set dllid = $Get(^||NKGAccel("dllid"), 0)
    If dllid = 0 Return 0
    Set fid = $ZF(-4, 3, dllid, "kg_build_2hop_exact_stream")
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

/// Spec 168 (FR-168-001..007): Closeness centrality via ^NKG BFS.
/// Runs BFS from every node server-side; computes harmonic or classical closeness.
/// Returns "OK:[{""id"":""..."",""score"":0.5}]" or "ERROR:<msg>".
ClassMethod ClosenessGlobal(formula As %String = "harmonic", direction As %String = "out", maxHops As %Integer = 0, topK As %Integer = 10000) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"
    Set n = $Get(^NKG("$meta","nodeCount"), 0)
    If n = 0 Quit "ERROR:^NKG nodeCount=0"

    Set json = "["
    Set sep = ""
    Set emitted = 0
    Set nid = ""
    For {
        Set nid = $Order(^NKG("$NI",nid))
        Quit:nid=""
        Set sIdx = ^NKG("$NI",nid)
        Kill ^||bfsDist
        Set ^||bfsDist(sIdx) = 0
        Set queue = sIdx _ ","
        Set qStart = 1, qLen = 1
        Set hop = 0
        While (qLen > 0) && ((maxHops = 0) || (hop < maxHops)) {
            Set hop = hop + 1
            Set newQueue = "", newLen = 0
            For qi = 1:1:qLen {
                Set vIdx = $Piece(queue,",",qi)
                If direction = "in" {
                    Set negP = $Order(^NKG(-2,vIdx,""))
                    While negP '= "" {
                        Set oIdx = $Order(^NKG(-2,vIdx,negP,""))
                        While oIdx '= "" {
                            If '$Data(^||bfsDist(oIdx)) {
                                Set ^||bfsDist(oIdx) = hop
                                Set newQueue = newQueue_oIdx_","
                                Set newLen = newLen + 1
                            }
                            Set oIdx = $Order(^NKG(-2,vIdx,negP,oIdx))
                        }
                        Set negP = $Order(^NKG(-2,vIdx,negP))
                    }
                } Else {
                    Set negP = $Order(^NKG(-1,vIdx,""))
                    While negP '= "" {
                        Set oIdx = $Order(^NKG(-1,vIdx,negP,""))
                        While oIdx '= "" {
                            If '$Data(^||bfsDist(oIdx)) {
                                Set ^||bfsDist(oIdx) = hop
                                Set newQueue = newQueue_oIdx_","
                                Set newLen = newLen + 1
                            }
                            Set oIdx = $Order(^NKG(-1,vIdx,negP,oIdx))
                        }
                        Set negP = $Order(^NKG(-1,vIdx,negP))
                    }
                }
            }
            Set queue = newQueue, qLen = newLen
        }
        Set sumDist = 0, sumInv = 0, reached = 0
        Set uIdx = ""
        For {
            Set uIdx = $Order(^||bfsDist(uIdx))
            Quit:uIdx=""
            If uIdx = sIdx Continue
            Set d = ^||bfsDist(uIdx)
            Set sumDist = sumDist + d
            Set sumInv = sumInv + (1 / d)
            Set reached = reached + 1
        }
        If formula = "classical" {
            // Classical (Bavelas) closeness is defined only when the node reaches
            // every other node (reached = n-1). On a disconnected graph no node
            // reaches the full graph, so score is 0 — matches the standard
            // (non-Wasserman-Faust) definition.
            Set score = $Select((sumDist > 0) && (reached = (n - 1)): reached / sumDist, 1: 0)
        } Else {
            // Harmonic centrality: raw sum of 1/d — matches networkx.harmonic_centrality default
            Set score = $Select(reached > 0: sumInv, 1: 0)
        }
        Set json = json_sep_"{""id"":"""_nid_""",""score"":"_score_"}"
        Set sep = ","
        Set emitted = emitted + 1
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||bfsDist
    Quit "OK:"_json_"]"
}

/// Spec 169: Eigenvector centrality via ^NKG power iteration (FR-169-001).
/// L2-normalized power iteration over raw adjacency A (NOT transition matrix D^-1*A).
/// Returns "OK:[{""id"":""..."",""score"":0.5}]" or "ERROR:<msg>".
ClassMethod EigenvectorGlobal(maxIter As %Integer = 30, tol As %Float = 0.0001, topK As %Integer = 10000) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"

    Kill ^||x, ^||xNew, ^||nodeList
    Set n = 0, nid = ""
    For {
        Set nid = $Order(^NKG("$NI",nid))
        Quit:nid=""
        Set idx = ^NKG("$NI",nid)
        Set ^||x(idx) = 1
        Set ^||nodeList(idx) = nid
        Set n = n + 1
    }
    If n = 0 Quit "ERROR:no nodes"
    For i = 0:1:n-1 { Set ^||x(i) = 1/n }

    For iter = 1:1:maxIter {
        Set normSq = 0
        For i = 0:1:n-1 { Set ^||xNew(i) = 0 }
        Set src = ""
        For {
            Set src = $Order(^NKG("$NI",src))
            Quit:src=""
            Set sIdx = ^NKG("$NI",src)
            Set xSrc = $Get(^||x(sIdx),0)
            If xSrc = 0 Continue
            Set negP = $Order(^NKG(-1,sIdx,""))
            While negP '= "" {
                Set oIdx = $Order(^NKG(-1,sIdx,negP,""))
                While oIdx '= "" {
                    Set ^||xNew(oIdx) = $Get(^||xNew(oIdx),0) + xSrc
                    Set oIdx = $Order(^NKG(-1,sIdx,negP,oIdx))
                }
                Set negP = $Order(^NKG(-1,sIdx,negP))
            }
        }
        For i = 0:1:n-1 { Set normSq = normSq + ($Get(^||xNew(i),0) * $Get(^||xNew(i),0)) }
        If normSq <= 0 Quit
        Set norm = $ZSqr(normSq)
        Set maxDelta = 0
        For i = 0:1:n-1 {
            Set newVal = $Get(^||xNew(i),0) / norm
            Set delta = $ZAbs(newVal - $Get(^||x(i),0))
            If delta > maxDelta Set maxDelta = delta
            Set ^||x(i) = newVal
        }
        If maxDelta < tol Quit
    }

    Set json = "[", sep = "", cnt = 0
    Set idx = -1
    For {
        Set idx = $Order(^||nodeList(idx))
        Quit:idx=""
        Set nid2 = ^||nodeList(idx)
        Set score = $Get(^||x(idx),0)
        Set json = json_sep_"{""id"":"""_nid2_""",""score"":"_score_"}"
        Set sep = ","
        Set cnt = cnt + 1
    }
    Kill ^||x, ^||xNew, ^||nodeList
    Quit "OK:"_json_"]"
}

/// Spec 170: Betweenness centrality via ^NKG Brandes 2001 (FR-170-001).
/// Sampled Brandes betweenness: capped at maxSources (default 200) to bound runtime.
/// For graphs with n <= maxSources, runs exact full Brandes. For larger graphs,
/// samples maxSources random-ish sources and scales scores by n/maxSources.
/// Returns "OK:[{""id"":""..."",""score"":0.5}]" or "ERROR:<msg>".
ClassMethod BetweennessGlobal(sampleSize As %Integer = 0, topK As %Integer = 10000, maxSources As %Integer = 200) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"

    If ##class(Graph.KG.NKGAccel).IsLoaded() {
        Set dllid = $Get(^||NKGAccel("dllid"), 0)
        If dllid '= 0 {
            If '$Data(^ArnoKG("KG", "nkg_adj")) { Do ..WarmAdjCache() }
            Set fnidV = $ZF(-4, 3, dllid, "kg_betweenness_global_v")
            If fnidV > 0 {
                Set nkgVer = +$Get(^NKG("$meta", "version"), -1)
                Set raw = $ZF(-5, dllid, fnidV, "^NKG", sampleSize, topK, nkgVer)
                If $Extract(raw, 1, 3) = "OK:" Quit raw
            } Else {
                Set fnid = $ZF(-4, 3, dllid, "kg_betweenness_global")
                If fnid > 0 {
                    Set raw = $ZF(-5, dllid, fnid, "^NKG", sampleSize, topK)
                    If $Extract(raw, 1, 3) = "OK:" Quit raw
                }
            }
        }
    }

    Quit ..BetweennessGlobalParallel(sampleSize, topK, maxSources)
}

/// Spec 172: Parallel Brandes worker — called by %SYSTEM.WorkMgr via BetweennessGlobalParallel.
/// Runs Brandes over sources[srcFirst..srcLast] from ^bcParSrc(parentPid, i),
/// writes partial scores to ^bcParScore(parentPid, workerN, nodeIdx).
ClassMethod BetweennessWorker(parentPid As %Integer, workerN As %Integer, srcFirst As %Integer, srcLast As %Integer, scale As %Double) As %Status
{
    Set n = +$Get(^NKG("$meta","nodeCount"),0)
    If n = 0 Return $$$OK

    Kill ^||bwSigma, ^||bwDist, ^||bwPred, ^||bwStack, ^||bwDelta

    For si = srcFirst:1:srcLast {
        Set srcNid = $Get(^bcParSrc(parentPid, si),"")
        If srcNid = "" Continue
        Set sIdx = $Get(^NKG("$NI",srcNid),-1)
        If sIdx = -1 Continue

        Kill ^||bwSigma, ^||bwDist, ^||bwPred, ^||bwStack
        Set ^||bwSigma(sIdx)=1, ^||bwDist(sIdx)=0
        Set frontier = ""
        Set $BIT(frontier, sIdx+1) = 1
        Set visited = frontier
        Set stackTop = 0, bfsLevel = 0
        Set ^||bwStack(0) = sIdx

        While $BITCOUNT(frontier,1) > 0 {
            Set bfsLevel = bfsLevel + 1
            Set candidates = ""
            Set pos = 0
            For {
                Set pos = $BITFIND(frontier, 1, pos+1)
                Quit:pos=0
                Set v = pos-1
                If $Data(^bcBitAdj(parentPid,v)) {
                    Set candidates = $BITLOGIC(candidates | ^bcBitAdj(parentPid,v))
                }
            }
            Set newFrontier = $BITLOGIC(candidates & ~visited)
            Quit:$BITCOUNT(newFrontier,1)=0

            Set pos = 0
            For {
                Set pos = $BITFIND(newFrontier, 1, pos+1)
                Quit:pos=0
                Set w = pos-1
                Set ^||bwDist(w) = bfsLevel
                Set ^||bwSigma(w) = 0
                Set stackTop = stackTop+1
                Set ^||bwStack(stackTop) = w
            }
            Set pos = 0
            For {
                Set pos = $BITFIND(frontier, 1, pos+1)
                Quit:pos=0
                Set v = pos-1
                If $Data(^bcBitAdj(parentPid,v)) {
                    Set vToNew = $BITLOGIC(^bcBitAdj(parentPid,v) & newFrontier)
                    Set pos2 = 0
                    For {
                        Set pos2 = $BITFIND(vToNew, 1, pos2+1)
                        Quit:pos2=0
                        Set w = pos2-1
                        Set ^||bwSigma(w) = +$Number($Get(^||bwSigma(w),0)+$Get(^||bwSigma(v),1),15)
                        Set ^||bwPred(w,v) = ""
                    }
                }
            }
            Set visited = $BITLOGIC(visited | newFrontier)
            Set frontier = newFrontier
        }

        Kill ^||bwDelta
        Set wi = stackTop
        While wi >= 0 {
            Set wIdx = ^||bwStack(wi), wi = wi-1
            Set v2 = $Order(^||bwPred(wIdx,""))
            While v2 '= "" {
                Set sv = +$Number($Get(^||bwSigma(v2),1),15)
                Set sw = +$Number($Get(^||bwSigma(wIdx),1),15)
                If sw > 0 {
                    Set dv = +$Number($Get(^||bwDelta(v2),0),15)
                    Set dw = +$Number($Get(^||bwDelta(wIdx),0),15)
                    Set r  = +$Number(sv/sw,15)
                    Set c  = +$Number(r*(1+dw),15)
                    Set ^||bwDelta(v2) = +$Number(dv+c,15)
                }
                Set v2 = $Order(^||bwPred(wIdx,v2))
            }
            If wIdx '= sIdx {
                Set d   = +$Number($Get(^||bwDelta(wIdx),0),15)
                Set cur = $Get(^bcParScore(parentPid,workerN,wIdx),0)
                Set ^bcParScore(parentPid,workerN,wIdx) = +$Number(cur + +$Number(d*scale,15),15)
            }
        }
    }

    Kill ^||bwSigma, ^||bwDist, ^||bwPred, ^||bwStack, ^||bwDelta
    Return $$$OK
}

/// Spec 172: Parallel Brandes dispatcher using %SYSTEM.WorkMgr.
/// Splits sources across numJobs workers, each writing partial scores to
/// ^bcParScore(parentPid, workerN, nodeIdx). Waits via Sync(), then merges.
/// Returns "OK:[{""id"":""..."",""score"":0.5}]" or "ERROR:<msg>".
ClassMethod BetweennessGlobalParallel(sampleSize As %Integer = 0, topK As %Integer = 10000, maxSources As %Integer = 200, numJobs As %Integer = 0) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"

    Set n = 0, nid = ""
    Kill ^||parNodes
    For {
        Set nid = $Order(^NKG("$NI",nid))
        Quit:nid=""
        Set ^||parNodes(n) = nid
        Set n = n + 1
    }
    If n = 0 Quit "ERROR:no nodes"

    If (sampleSize > 0) && (sampleSize < n) {
        Set srcCount = sampleSize
    } ElseIf (maxSources > 0) && (maxSources < n) {
        Set srcCount = maxSources
    } Else {
        Set srcCount = n
    }
    Set scale = +$Number(n / srcCount, 15)

    If numJobs <= 0 {
        Set numJobs = $System.Util.NumberOfCPUs()
        If numJobs > 8 { Set numJobs = 8 }
        If numJobs < 1 { Set numJobs = 1 }
    }
    If numJobs > srcCount { Set numJobs = srcCount }

    Set parentPid = $Job
    Kill ^bcParSrc(parentPid), ^bcParScore(parentPid)

    For si = 0:1:srcCount-1 {
        Set ^bcParSrc(parentPid, si) = ^||parNodes(si)
    }

    Set queue = ##class(%SYSTEM.WorkMgr).%New("", numJobs)
    Set chunkSize = srcCount \ numJobs
    Set remainder = srcCount # numJobs
    Set cursor = 0

    Do ..BuildBitAdj(parentPid)

    For w = 1:1:numJobs {
        Set wSize = chunkSize + $Select(w <= remainder: 1, 1: 0)
        Set srcFirst = cursor
        Set srcLast  = cursor + wSize - 1
        Set cursor   = cursor + wSize
        Set sc = queue.Queue("##class(Graph.KG.NKGAccel).BetweennessWorker", parentPid, w, srcFirst, srcLast, scale)
        If $$$ISERR(sc) {
            Kill ^bcParSrc(parentPid), ^bcParScore(parentPid), ^||parNodes
            Quit "ERROR:Queue failed "_$System.Status.GetErrorText(sc)
        }
    }

    Set sc = queue.Sync()
    Kill ^bcParSrc(parentPid)
    If $$$ISERR(sc) {
        Kill ^bcParScore(parentPid), ^||parNodes
        Quit "ERROR:Worker failed "_$System.Status.GetErrorText(sc)
    }

    Set json = "[", sep = "", cnt = 0, i2 = ""
    For {
        Set i2 = $Order(^NKG("$NI",i2))
        Quit:i2=""
        Set idx = ^NKG("$NI",i2)
        Set total = 0
        For w = 1:1:numJobs {
            Set total = total + $Get(^bcParScore(parentPid, w, idx), 0)
        }
        If total = 0 Continue
        Set json = json_sep_"{""id"":"""_i2_""",""score"":"_total_"}"
        Set sep = ","
        Set cnt = cnt + 1
        If (topK > 0) && (cnt >= topK) Quit
    }

    Kill ^bcParScore(parentPid), ^bcBitAdj(parentPid), ^||parNodes
    Quit "OK:"_json_"]"
}

ClassMethod BuildBitAdj(parentPid As %Integer) [ Private ]
{
    Kill ^bcBitAdj(parentPid)
    Set srcIdx = ""
    For {
        Set srcIdx = $Order(^NKG(-3,srcIdx))
        Quit:srcIdx=""
        Set negP = $Order(^NKG(-1,srcIdx,""))
        While negP '= "" {
            Set dstIdx = $Order(^NKG(-1,srcIdx,negP,""))
            While dstIdx '= "" {
                Set $BIT(^bcBitAdj(parentPid,srcIdx), dstIdx+1) = 1
                Set dstIdx = $Order(^NKG(-1,srcIdx,negP,dstIdx))
            }
            Set negP = $Order(^NKG(-1,srcIdx,negP))
        }
    }
}

/// Spec 173: Betweenness centrality on a neighborhood subgraph.
/// Extracts the hops-hop neighborhood around seedId, then runs Brandes
/// restricted to that subgraph. Scales to any total graph size — performance
/// depends only on neighborhood size (~500-5K nodes for biomedical KGs).
/// Returns "OK:[{""id"":""..."",""score"":0.5}]" or "ERROR:<msg>".
ClassMethod BetweennessNeighborhood(seedId As %String, hops As %Integer = 2, sampleSize As %Integer = 200, topK As %Integer = 20) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"
    If $Get(^NKG("$NI",seedId),-1) = -1 Quit "ERROR:seed node not found: "_seedId

    Set hopsRaw = ..KHopNeighbors(seedId, hops, 1000000)
    If $Extract(hopsRaw,1,7) = "ERROR: " Quit hopsRaw
    If hopsRaw = "" Quit "ERROR:empty neighborhood"

    // Parse node list from KHopNeighbors JSON response
    Kill ^||nbhIdx, ^||nbhNodes
    Set n = 0, parseErr = ""
    Try {
        Set obj = ##class(%DynamicObject).%FromJSON(hopsRaw)
        Set nodesArr = obj.nodes
        If '$IsObject(nodesArr) { Set parseErr = "ERROR:no nodes in neighborhood" }
        Else {
            Set iter = nodesArr.%GetIterator()
            While iter.%GetNext(.k, .nodeObj) {
                Set nodeId = $Select($IsObject(nodeObj):nodeObj.id, 1:nodeObj)
                Set idx = $Get(^NKG("$NI",nodeId),-1)
                If idx >= 0 {
                    Set ^||nbhIdx(idx) = ""
                    Set ^||nbhNodes(n) = nodeId
                    Set n = n + 1
                }
            }
        }
    } Catch ex {
        Set parseErr = "ERROR:parse failed "_ex.DisplayString()
    }
    If parseErr '= "" Quit parseErr
    If n = 0 Quit "ERROR:no valid nodes in neighborhood"

    // Try arno fast path — reuses BETWEENNESS_ADJ_CACHE, extracts subgraph in Rust
    If ..IsLoaded() {
        Set dllid = $Get(^||NKGAccel("dllid"), 0)
        If dllid '= 0 {
            If '$Data(^ArnoKG("KG","nkg_adj")) { Do ..WarmAdjCache() }
            Set fnidN = $ZF(-4, 3, dllid, "kg_betweenness_neighborhood_v")
            If fnidN > 0 {
                Set idxList = "", idx = ""
                For {
                    Set idx = $Order(^||nbhIdx(idx))
                    Quit:idx=""
                    Set:idxList'="" idxList = idxList_","
                    Set idxList = idxList_idx
                }
                Set nkgVer = +$Get(^NKG("$meta","version"),-1)
                // Ensure full adj is cached in Rust (warm if needed)
                Set fnidG = $ZF(-4, 3, dllid, "kg_betweenness_global_v")
                If fnidG > 0 {
                    Set warmRaw = $ZF(-5, dllid, fnidG, "^NKG", 1, 1, nkgVer)
                }
                Set raw = $ZF(-5, dllid, fnidN, "^NKG", idxList, sampleSize, topK, nkgVer)
                If $Extract(raw,1,3) = "OK:" {
                    Kill ^||nbhIdx, ^||nbhNodes
                    Quit raw
                }
            }
        }
    }

    // OS fallback: $BITLOGIC parallel Brandes on subgraph
    Quit ..BetweennessNeighborhoodOS(n, sampleSize, topK)
}

/// OS fallback for BetweennessNeighborhood: $BITLOGIC parallel Brandes
/// restricted to ^||nbhNodes(0..n-1). Caller must populate ^||nbhNodes.
ClassMethod BetweennessNeighborhoodOS(n As %Integer, sampleSize As %Integer, topK As %Integer) As %String [ Private ]
{
    If n = 0 Quit "ERROR:no nodes"

    Set srcCount = $Select((sampleSize>0)&&(sampleSize<n):sampleSize, 1:n)
    Set scale = +$Number(n/srcCount,15)

    Set parentPid = $Job
    Kill ^bcParSrc(parentPid), ^bcParScore(parentPid), ^bcBitAdj(parentPid)

    // Build $BIT adjacency restricted to neighborhood
    Set idx = "", pos = 0
    For {
        Set idx = $Order(^||nbhIdx(idx))
        Quit:idx=""
        Set negP = $Order(^NKG(-1,idx,""))
        While negP '= "" {
            Set dstIdx = $Order(^NKG(-1,idx,negP,""))
            While dstIdx '= "" {
                If $Data(^||nbhIdx(dstIdx)) {
                    Set $BIT(^bcBitAdj(parentPid,idx),dstIdx+1) = 1
                    Set $BIT(^bcBitAdj(parentPid,dstIdx),idx+1) = 1
                }
                Set dstIdx = $Order(^NKG(-1,idx,negP,dstIdx))
            }
            Set negP = $Order(^NKG(-1,idx,negP))
        }
    }

    For si = 0:1:srcCount-1 {
        Set ^bcParSrc(parentPid,si) = ^||nbhNodes(si)
    }

    Set numJobs = $System.Util.NumberOfCPUs()
    If numJobs > 8 { Set numJobs = 8 }
    If numJobs > srcCount { Set numJobs = srcCount }

    Set queue = ##class(%SYSTEM.WorkMgr).%New("",numJobs)
    Set chunkSize = srcCount\numJobs, remainder = srcCount#numJobs, cursor = 0
    For w = 1:1:numJobs {
        Set wSize = chunkSize + $Select(w<=remainder:1,1:0)
        Set sc = queue.Queue("##class(Graph.KG.NKGAccel).BetweennessWorker",parentPid,w,cursor,cursor+wSize-1,scale)
        Set cursor = cursor + wSize
        If $$$ISERR(sc) {
            Kill ^bcParSrc(parentPid),^bcParScore(parentPid),^bcBitAdj(parentPid),^||nbhIdx,^||nbhNodes
            Quit "ERROR:Queue failed"
        }
    }

    Do queue.Sync()
    Kill ^bcParSrc(parentPid), ^bcBitAdj(parentPid)

    Set json = "[", sep = "", cnt = 0
    Set idx = ""
    For {
        Set idx = $Order(^||nbhIdx(idx))
        Quit:idx=""
        Set nid = $Get(^NKG("$ND",idx),"")
        If nid = "" Continue
        Set total = 0
        For w = 1:1:numJobs { Set total = total + $Get(^bcParScore(parentPid,w,idx),0) }
        If total = 0 Continue
        Set json = json_sep_"{""id"":"""_nid_""",""score"":"_total_"}"
        Set sep = ","
        Set cnt = cnt+1
        If (topK>0)&&(cnt>=topK) Quit
    }

    Kill ^bcParScore(parentPid), ^||nbhIdx, ^||nbhNodes
    Quit "OK:"_json_"]"
}

}
