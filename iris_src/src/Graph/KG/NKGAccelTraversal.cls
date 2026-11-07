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

/// Most characters the callout's BFS entry takes across its four string arguments
/// (adjacency, result global, seed, predicates) at the default zfheap. Each input is copied
/// into the heap at 2 bytes a character beside a 65,534-byte output reservation, so the
/// budget is (135,168 - 65,534) / 2 = 34,817 units; measured 34,791 adjacency characters
/// passing beside 17 characters of other arguments (34,808 in all; spec 236 FR-013).
Parameter ZFMAXCHARS = 34808;


ClassMethod KHopNeighbors(seedId As %String, k As %Integer = 2, maxNodes As %Integer = 1000) As %String
{
    Set useNKG = ##class(Graph.KG.NKGAccelLoader).UseNKG()
    If useNKG {
        Set seedIdx = $Get(^NKG("$NI", seedId))
        If seedIdx = "" Return "{""error"":""node not found"",""node"":"""_##class(Graph.KG.TraversalBFS).jsonEsc(seedId)_"""}"
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
    Set result = "{""seed"":"""_##class(Graph.KG.TraversalBFS).jsonEsc(seedId)_""",""hops"":"_k_",""totalNodes"":"_totalNodes_",""nodes"":["
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
        // escaped (4.6.0 R306: an id with a quote made the answer unreadable)
        Set result = result _ "{""id"":"""_##class(Graph.KG.TraversalBFS).jsonEsc(nodeStr)_""",""dist"":"_dist_"}"
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
/// Which way the last BFSJson call in this process went: "native", "size-gate" (adjacency
/// over ZFMAXARG, answered from ObjectScript before the callout) or "fallback" (the callout
/// raised). Process-private; for tests and diagnostics.
ClassMethod LastBfsPath() As %String
{
    Return $Get(^||NKGAccel("last_bfs_path"), "")
}

/// The error behind the last "fallback" in this process, "" if none.
ClassMethod LastBfsError() As %String
{
    Return $Get(^||NKGAccel("last_bfs_error"), "")
}

/// How many chunks the last native BFS reply in this process came in (0 when it came whole), 4.6.0 R265.
ClassMethod LastBfsChunks() As %Integer
{
    Return +$Get(^||NKGAccel("last_bfs_chunks"), 0)
}

/// 4.6.0 R265: the library's kg_bfs_global writes a chunked reply into USER's ^ArnoKG("bfs_result") and returns with
/// the process in USER, whatever namespace called it. The chunks are read where the library left the process, and the
/// caller's namespace is put back before anything else: BFSJson staged its hits in USER's ^ArnoKG("bfs_r") and
/// returned in USER, so the connection's next statement read USER's tables.
/// predicatesJson is a set of types, as the library reads it; the ObjectScript answer it falls back to reads it so too
/// (BFSFastJsonSorted's pAnyType; 4.6.0: it walked them as a per-hop sequence).
ClassMethod BFSJson(seed As %String, predicatesJson As %String = "[]", maxHops As %Integer = 3, maxResults As %Integer = 0) As %String
{
    Set tNs = $NAMESPACE, tLibNs = $NAMESPACE
    Set ^||NKGAccel("last_bfs_chunks") = 0
    // arno 1d75d97: kg_bfs_global takes pre-serialized adjacency string as first arg.
    // ExportAdjacencyWithPreds() builds srcStrpredStrdstStr wire format.
    // Rust parses it entirely in memory — no global reads needed from $ZF(-5).
    // spec 210/213: prefer the persistent lib path recorded by ArnoAccel.Load so TCP job
    // processes (which never ran Load themselves) resolve the same .so as the loader did;
    // 4.6.0 R132: through the one resolver both loaders use.
    Set dllPath = $Get(^||NKGAccel("libPath"), ##class(Graph.KG.ArnoAccel).LibPath())
    If '$Data(^||NKGAccel("bfs_fn")) {
        Set dllid = ##class(Graph.KG.ArnoAccel).OpenLibrary(dllPath, .tError)
        If dllid = 0 {
            Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults, "", 1)
        }
        Set ^||NKGAccel("dllid") = dllid, ^||NKGAccel("libPath") = dllPath
        Set ^||NKGAccel("bfs_fn") = $ZF(-4, 3, dllid, "kg_bfs_global")
    }
    Set dllid = ^||NKGAccel("dllid")
    Set fnid  = ^||NKGAccel("bfs_fn")
    If fnid = 0 {
        Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults, "", 1)
    }
    // 4.1.1: the whole adjacency is one $ZF argument. 300 nodes (30,532 chars) pass;
    // 600 (61,132) raise <OUT OF $ZF HEAP SPACE>, 1,000 (101,864) <MAX $ZF STRING>.
    // Answer from ObjectScript instead. See tests/unit/test_411_bfs_zf_limit.py.
    Try {
        Set resultGlobal = "ArnoKG"
        // The export stops at the adjacency's share of the budget: building 617,000
        // characters only to turn the call away cost 34 ms a query on a 22,000-edge graph.
        Set tCap = ..#ZFMAXCHARS - $Length(resultGlobal) - $Length(seed) - $Length(predicatesJson)
        Set adjStr = ##class(Graph.KG.NKGAccelAdjacency).ExportAdjacencyWithPreds(, tCap)
        // Spec 236: never hand the callout more than it can take. 34,791 adjacency characters
        // pass and 34,792 raise <OUT OF $ZF HEAP SPACE> at the default zfheap; 65,536 and up
        // raise <MAX $ZF STRING> whatever the heap (docs/dev/notes/research/zf-heap-and-exstr.md).
        // The other string arguments share that heap, so they count. A "CHUNKED:n" marker is
        // short but is not an adjacency, so it goes the same way.
        If (adjStr = "OVERSIZE") || ($Length(adjStr) + $Length(resultGlobal) + $Length(seed) + $Length(predicatesJson) > ..#ZFMAXCHARS) || ($Extract(adjStr, 1, 8) = "CHUNKED:") {
            Set ^||NKGAccel("last_bfs_path") = "size-gate"
            Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults, "", 1)
        }
        Kill ^ArnoKG("bfs_result")
        Set raw = $ZF(-5, dllid, fnid, adjStr, resultGlobal, seed, predicatesJson, maxHops, maxResults)
        Set tLibNs = $NAMESPACE
        Set $NAMESPACE = tNs
        Set ^||NKGAccel("last_bfs_path") = "native"
    } Catch tEx {
        Set $NAMESPACE = tNs
        Set ^||NKGAccel("last_bfs_path") = "fallback"
        Set ^||NKGAccel("last_bfs_error") = tEx.DisplayString()
        Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults, "", 1)
    }
    If (raw = "") || ($Extract(raw, 1, 6) = "DEBUG:") {
        Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults, "", 1)
    }
    // spec 230: "CHUNKED:BFS" is 11 characters, so the old $Extract(raw, 1, 10)
    // test could never match its own literal and a chunked reply reached %FromJSON
    // as the marker text. Match the prefix the producer actually writes.
    If $Extract(raw, 1, 8) = "CHUNKED:" {
        Set n = +$Piece(raw, ":", 3)
        Set ^||NKGAccel("last_bfs_chunks") = n
        Set json = ""
        // in the namespace the library wrote them in (USER), from the caller's
        For i = 1:1:n { Set json = json _ $Get(^|tLibNs|ArnoKG("bfs_result", i), "") }
        Kill ^|tLibNs|ArnoKG("bfs_result")
        Set raw = json
    }
    // spec 230: the callout answers with an error string for a seed it cannot find,
    // and %FromJSON turns that into <THROW> *%Exception.General Parsing error 3.
    // BFSFastJsonSorted returns "SORTED:0" for the same seed, so answer through it
    // rather than raising out of an accelerated path that has a working fallback.
    If $Extract(raw, 1) '= "[" {
        Return ##class(Graph.KG.Traversal).BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults, "", 1)
    }
    Set tag = $Job _ "_bfs"
    Kill ^ArnoKG("bfs_r", tag)
    // 4.6.0: the library answers "p": "R" for every hit (Arno 1.2.1 x86_64), though ExportAdjacencyWithPreds hands it
    // each edge's type. Each hit's type is read back from the default graph's ^KG("out") (the only graph this path
    // walks), one of the types the filter names when there is one, so every reader of the staged hits gets the edge's
    // own type, as BFSFastJsonSorted answers it.
    Set tG = ##class(Graph.KG.GraphKey).ForIndex("")
    Set tAny = 1
    If (predicatesJson '= "") && (predicatesJson '= "[]") && (predicatesJson '= "null") && (predicatesJson '= $Char(0)) {
        Set tPA = ##class(%DynamicArray).%FromJSON(predicatesJson)
        For tI = 0:1:(tPA.%Size() - 1) {
            Set tPred(tPA.%Get(tI)) = "", tAny = 0
        }
    }
    Set arr = ##class(%DynamicArray).%FromJSON(raw)
    Set sz = arr.%Size()
    For i = 0:1:(sz-1) {
        Set item = arr.%Get(i)
        Set step = +item.%Get("step")
        Set o    = item.%Get("o")
        Set s    = item.%Get("s")
        Set p    = ..EdgeType(tG, s, o, item.%Get("p"), tAny, .tPred)
        Set w    = +item.%Get("w")
        Set ^ArnoKG("bfs_r", tag, step, o) = $ListBuild(s, p, w)
    }
    Return "SORTED:" _ tag
}

/// The type of an edge pS -> pO in the graph whose ^KG key is pG: pP when the edge has it (and, unless pAny, pPred
/// names it), else the first such type in collation order; pP when there is none (the adjacency changed under the
/// walk).
ClassMethod EdgeType(pG As %String, pS As %String, pO As %String, pP As %String, pAny As %Boolean, ByRef pPred) As %String [ Private ]
{
    Quit:(pP'="")&&$Data(^KG("out", pG, pS, pP, pO))&&(pAny||$Data(pPred(pP))) pP
    Set tP = ""
    For {
        Set tP = $Order(^KG("out", pG, pS, tP))
        Quit:tP=""
        Continue:('pAny)&&'$Data(pPred(tP))
        Return:$Data(^KG("out", pG, pS, tP, pO)) tP
    }
    Quit pP
}
ClassMethod ShortestPathNKG(srcId As %String, dstId As %String, maxHops As %Integer = 10) As %String
{
    If (srcId = "") || (dstId = "") { Return "{""hops"":-1}" }

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
    // the library's kg_build_2hop_exact_stream reads USER's ^NKG and leaves the process in USER, so the sketch below
    // would be written into USER's ^KG: refused elsewhere (4.4.0 E2E gate; the merged sketch is a USER-only store)
    Quit:$NAMESPACE'="USER" 0
    If '##class(Graph.KG.NKGAccelLoader).IsLoaded() {
        Do ##class(Graph.KG.NKGAccelLoader).Load()
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
    If $Extract(raw, 1, 8) = "CHUNKED:" {
        Set n = +$Piece(raw, ":", 3)
        Set data = ""
        For i = 1:1:n {
            Set data = data _ $Get(^ArnoKG("2hs", i), "")
        }
        Kill ^ArnoKG("2hs")
    } ElseIf $Extract(raw, 1, 1) = "{" {
        // A result small enough to travel in the reply comes back as {"nodes":N,"data":"..."},
        // not chunked. Only the chunked form was read, so no graph small enough to fit inline
        // ever got a sketch.
        Try {
            Set data = {}.%FromJSON(raw).data
        } Catch {
            Return 0
        }
    } Else {
        Return 0
    }
    // Killed here rather than on entry: a callout that never produced a stream leaves
    // the previous sketch in place instead of an empty one, which no reader can tell
    // from "nothing has two-hop reach".
    Kill ^KG("deg2p_exact_merged")
    For pos = 1:1:$Length(data, $Char(10)) {
        Set line = $Piece(data, $Char(10), pos)
        If line = "" Continue
        Set s    = $Piece(line, $Char(31), 1)
        Set p    = $Piece(line, $Char(31), 2)
        Set cnt  = +$Piece(line, $Char(31), 3)
        If (s '= "") && (p '= "") && (cnt > 0) {
            // ^KG("deg2p_exact_merged"), not ^KG("deg2p_exact"): this count is computed
            // over ^NKG, whose node interning has no graph dimension, so it belongs to
            // no single graph and may not fill a graph-scoped store (spec 230 FR-011).
            // Graph.KG.TraversalBuild builds the scoped store per graph from
            // ^KG("out", graph, ...) instead.
            Set ^KG("deg2p_exact_merged", s, p) = cnt
        }
     }
     Return 1
}
}
