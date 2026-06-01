Class Graph.KG.NKGAccelAdjacency Extends %RegisteredObject
{

ClassMethod ExportAdjacency() As %String
{
    If ##class(Graph.KG.NKGAccelLoader).UseNKG() Return ..ExportAdjacencyNKG()
    Return ..ExportAdjacencyKG()
}
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
            Set result = result _ "{""id"":"""_$Replace(node,"""","\""")_""",""dist"":"_dist_"}"
            Set totalNodes = totalNodes + 1
        }
    }
    Kill ^ArnoKG("khop_r", tag)
    Return result
}
}
