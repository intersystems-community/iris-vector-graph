Class Graph.KG.NKGAccelCentrality Extends %RegisteredObject
{

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
        If $Extract(score)="." Set score = "0"_score
        If $Extract(score,1,2)="-." Set score = "-0"_$Extract(score,2,*)
        Set json = json_sep_"{""id"":"""_nid_""",""score"":"_score_"}"
        Set sep = ","
        Set emitted = emitted + 1
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||bfsDist
    Quit "OK:"_json_"]"
}
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
ClassMethod BetweennessGlobal(sampleSize As %Integer = 0, topK As %Integer = 10000, maxSources As %Integer = 200) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"

    If ##class(Graph.KG.NKGAccel).IsLoaded() {
        Set dllid = $Get(^||NKGAccel("dllid"), 0)
        If dllid '= 0 {
            If '$Data(^ArnoKG("KG", "nkg_adj")) { Do ##class(Graph.KG.NKGAccelLoader).WarmAdjCache() }
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
ClassMethod BetweennessNeighborhood(seedId As %String, hops As %Integer = 2, sampleSize As %Integer = 200, topK As %Integer = 20) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"
    If $Get(^NKG("$NI",seedId),-1) = -1 Quit "ERROR:seed node not found: "_seedId

    Set hopsRaw = ##class(Graph.KG.NKGAccelTraversal).KHopNeighbors(seedId, hops, 1000000)
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
    If ##class(Graph.KG.NKGAccelLoader).IsLoaded() {
        Set dllid = $Get(^||NKGAccel("dllid"), 0)
        If dllid '= 0 {
            If '$Data(^ArnoKG("KG","nkg_adj")) { Do ##class(Graph.KG.NKGAccelLoader).WarmAdjCache() }
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

ClassMethod ClosenessGlobalMSBFS(formula As %String = "harmonic", direction As %String = "out", maxHops As %Integer = 0, topK As %Integer = 10000) As %String
{
    Set firstNode = $Order(^NKG("$NI",""))
    If firstNode = "" Quit "ERROR:^NKG not built"
    Set n = $Get(^NKG("$meta","nodeCount"), 0)
    If n = 0 Quit "ERROR:^NKG nodeCount=0"

    Kill ^||msAdj, ^||msVisit, ^||msNext, ^||msSeen, ^||msFar, ^||msReach, ^||msIdxNid
    Set adjDir = $Select(direction="in": -2, 1: -1)
    Set nid = ""
    For {
        Set nid = $Order(^NKG("$NI",nid))
        Quit:nid=""
        Set vIdx = ^NKG("$NI",nid)
        Set ^||msIdxNid(vIdx) = nid
        Set negP = $Order(^NKG(adjDir,vIdx,""))
        While negP '= "" {
            Set oIdx = $Order(^NKG(adjDir,vIdx,negP,""))
            While oIdx '= "" {
                Set $BIT(^||msAdj(vIdx), oIdx+1) = 1
                Set oIdx = $Order(^NKG(adjDir,vIdx,negP,oIdx))
            }
            Set negP = $Order(^NKG(adjDir,vIdx,negP))
        }
    }

    Set sIdx = ""
    For {
        Set sIdx = $Order(^||msIdxNid(sIdx))
        Quit:sIdx=""
        Set $BIT(^||msVisit(sIdx), sIdx+1) = 1
        Set $BIT(^||msSeen(sIdx), sIdx+1) = 1
    }

    Set hop = 0
    For {
        Quit:(maxHops>0)&&(hop>=maxHops)
        Set hop = hop + 1
        Kill ^||msNext
        Set anyFrontier = 0
        Set v = ""
        For {
            Set v = $Order(^||msVisit(v))
            Quit:v=""
            Set vb = ^||msVisit(v)
            If $BITCOUNT(vb,1)=0 Continue
            If '$Data(^||msAdj(v)) Continue
            Set adj = ^||msAdj(v)
            Set wpos = 0
            For {
                Set wpos = $BITFIND(adj, 1, wpos+1)
                Quit:wpos=0
                Set w = wpos-1
                Set nxw = $Get(^||msNext(w))
                Set ^||msNext(w) = $BITLOGIC(nxw|vb)
                Set anyFrontier = 1
            }
        }
        Quit:anyFrontier=0

        Kill ^||msVisit
        Set w = ""
        For {
            Set w = $Order(^||msNext(w))
            Quit:w=""
            Set nb = ^||msNext(w)
            Set seenw = $Get(^||msSeen(w))
            Set new = $BITLOGIC(nb&~seenw)
            If $BITCOUNT(new,1)=0 Continue
            Set ^||msSeen(w) = $BITLOGIC(seenw|new)
            Set ^||msVisit(w) = new
            Set spos = 0
            For {
                Set spos = $BITFIND(new, 1, spos+1)
                Quit:spos=0
                Set s = spos-1
                If formula = "classical" {
                    Set ^||msFar(s) = $Get(^||msFar(s),0) + hop
                    Set ^||msReach(s) = $Get(^||msReach(s),0) + 1
                } Else {
                    Set ^||msFar(s) = $Get(^||msFar(s),0) + (1/hop)
                }
            }
        }
    }

    Set json = "["
    Set sep = ""
    Set emitted = 0
    Set sIdx = ""
    For {
        Set sIdx = $Order(^||msIdxNid(sIdx))
        Quit:sIdx=""
        Set snid = ^||msIdxNid(sIdx)
        If formula = "classical" {
            Set reached = $Get(^||msReach(sIdx),0)
            Set fdist = $Get(^||msFar(sIdx),0)
            Set score = $Select((fdist>0)&&(reached=(n-1)): reached/fdist, 1: 0)
        } Else {
            Set score = $Get(^||msFar(sIdx),0)
        }
        If $Extract(score)="." Set score = "0"_score
        If $Extract(score,1,2)="-." Set score = "-0"_$Extract(score,2,*)
        Set json = json_sep_"{""id"":"""_snid_""",""score"":"_score_"}"
        Set sep = ","
        Set emitted = emitted + 1
        If (topK>0)&&(emitted>=topK) Quit
    }
    Kill ^||msAdj, ^||msVisit, ^||msNext, ^||msSeen, ^||msFar, ^||msReach, ^||msIdxNid
    Quit "OK:"_json_"]"
}

}
