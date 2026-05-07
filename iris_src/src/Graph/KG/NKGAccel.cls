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
        Set ^||adj = chunkNum
        Return "CHUNKED:" _ chunkNum
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
    Set dllPath = $Get(^||NKGAccel("libPath"), "/usr/irissys/mgr/libarno_callout.so")
    If '$Data(^||NKGAccel("compute_fn")) {
        Set dllid = $ZF(-4, 1, dllPath)
        If dllid = 0 {
            Return ##class(Graph.KG.Traversal).BFSFastJson(seed, predicatesJson, maxHops, "")
        }
        Set ^||NKGAccel("dllid") = dllid
        Set ^||NKGAccel("compute_fn") = $ZF(-4, 3, dllid, "kg_bfs_compute")
        Set ^||NKGAccel("read_fn") = $ZF(-4, 3, dllid, "kg_bfs_read_chunk")
        Set ^||NKGAccel("bfs_fn") = $ZF(-4, 3, dllid, "kg_bfs_global")
    }
    Set dllid = ^||NKGAccel("dllid")
    Set computeFn = ^||NKGAccel("compute_fn")
    If computeFn = 0 {
        Return ##class(Graph.KG.Traversal).BFSFastJson(seed, predicatesJson, maxHops, "")
    }
    Do ..CacheNKGAdj()
    If '$Data(^ArnoKG("KG", "nkg_adj")) {
        Return ##class(Graph.KG.Traversal).BFSFastJson(seed, predicatesJson, maxHops, "")
    }
    Kill ^ArnoKG("bfs_result")
    Set raw = $ZF(-5, dllid, computeFn, "^ArnoKG", "^ArnoKG", seed, predicatesJson, maxHops, maxResults)
    If $Extract(raw, 1, 8) '= "CHUNKED:" {
        Return raw
    }
    Set n = +$Piece(raw, ":", 3)
    Set readFn = ^||NKGAccel("read_fn")
    If readFn = 0 {
        Return ##class(Graph.KG.Traversal).BFSFastJson(seed, predicatesJson, maxHops, "")
    }
    Set result = ""
    For i = 1:1:n {
        Set chunk = $ZF(-5, dllid, readFn, "^ArnoKG", i)
        Set result = result _ chunk
    }
    Kill ^ArnoKG("bfs_result")
    Return result
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
        // ExportAdjacencyNKG already wrote chunks to ^||adj(1..N)
        // Transfer them directly to ^ArnoKG("KG","nkg_adj",1..N)
        Set n = $Piece(adj, ":", 2)
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
    Set algos = ["khop", "ppr", "random_walk", "export_adjacency", "bfs"]
    Do cap.%Set("algorithms", algos)
    Set rustAlgos = []
    If hasRust {
        Do rustAlgos.%Push("pagerank")
        Do rustAlgos.%Push("wcc")
        Do rustAlgos.%Push("cdlp")
        Do rustAlgos.%Push("bfs")
    }
    Do cap.%Set("rust_algorithms", rustAlgos)
    Do cap.%Set("bfs", 1, "boolean")
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

}
