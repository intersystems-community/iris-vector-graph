/// Graph.KG.Communities - Community detection over the ^NKG integer-index global.
///
/// Four pure-ObjectScript algorithms, no external libraries, no embedded Python:
///   TriangleCountJson - symmetrized triangle count + local clustering coefficient
///   SCCJson           - strongly connected components (directed, iterative Tarjan)
///   KCoreJson         - k-core decomposition (Batagelj-Zaversnik, symmetrized)
///   LeidenJson        - Louvain (multi-level modularity) community detection (symmetrized)
///
/// All read ^NKG (built by Graph.KG.Traversal.BuildNKG):
///   ^NKG("$NI",nodeId)=idx, ^NKG("$ND",idx)=nodeId, ^NKG("$meta","nodeCount")=N
///   ^NKG(-1,src,negP,dst)=w (outbound), ^NKG(-2,dst,negP,src)=w (inbound)
/// Results are JSON arrays "OK:[...]" sorted by descending group size, or "ERROR:...".
Class Graph.KG.Communities Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

/// Build symmetrized adjacency ^||ccAdj(idx,nbrIdx)=1 from ^NKG outbound+inbound.
/// Skips self-loops. Returns node count.
ClassMethod BuildSymAdj() As %Integer [ Private ]
{
    Kill ^||ccAdj, ^||ccDeg
    Set srcIdx = ""
    For {
        Set srcIdx = $Order(^NKG("$NI", srcIdx), 1, idx)
        Quit:srcIdx=""
        Set negP = ""
        For {
            Set negP = $Order(^NKG(-1, idx, negP))
            Quit:negP=""
            Set dst = ""
            For {
                Set dst = $Order(^NKG(-1, idx, negP, dst))
                Quit:dst=""
                If dst = idx Continue
                If '$Data(^||ccAdj(idx, dst)) {
                    Set ^||ccAdj(idx, dst) = 1
                    Set ^||ccAdj(dst, idx) = 1
                }
            }
        }
    }
    Quit +$Get(^NKG("$meta", "nodeCount"), 0)
}

/// One answer row: the node id as a JSON string whatever it looks like ("10" stays "10"; quotes and backslashes
/// escaped), the values as JSON numbers (0.5, not .5). Before 4.6.0 R306 the rows were concatenated text, so an id
/// with a quote made the answer unreadable and an lcc below 1 was ".5".
ClassMethod Row(arr As %DynamicArray, id As %String, k1 As %String, v1, k2 As %String = "", v2 = "") [ Private ]
{
    Set obj = ##class(%DynamicObject).%New()
    Do obj.%Set("id", id, "string")
    Do obj.%Set(k1, v1, "number")
    Do:k2'="" obj.%Set(k2, v2, "number")
    Do arr.%Push(obj)
}

ClassMethod TriangleCountJson(topK As %Integer = 10000) As %String
{
    If '##class(Graph.KG.TraversalBuild).EnsureNKG() Return "ERROR:^NKG not built"
    Do ..BuildSymAdj()

    Kill ^||ccTri
    Set idx = ""
    For {
        Set idx = $Order(^||ccAdj(idx))
        Quit:idx=""
        Set deg = 0, tri = 0
        Set v = ""
        For {
            Set v = $Order(^||ccAdj(idx, v))
            Quit:v=""
            Set deg = deg + 1
        }
        Set v = ""
        For {
            Set v = $Order(^||ccAdj(idx, v))
            Quit:v=""
            Set w = ""
            For {
                Set w = $Order(^||ccAdj(idx, w))
                Quit:w=""
                If w '> v Continue
                If $Data(^||ccAdj(v, w)) Set tri = tri + 1
            }
        }
        Set lcc = 0
        If deg > 1 Set lcc = +$Number((2 * tri) / (deg * (deg - 1)), 15)
        // by node ID, not ^NKG index: a tie at the topK cut goes in IRIS collation, as triangle_count() ranks (4.6.0 R050)
        Set ^||ccTri(-tri, $Get(^NKG("$ND", idx), idx)) = $LB(tri, lcc, deg)
    }

    Set arr = ##class(%DynamicArray).%New(), emitted = 0
    Set negTri = ""
    For {
        Set negTri = $Order(^||ccTri(negTri))
        Quit:negTri=""
        Set nid = ""
        For {
            Set nid = $Order(^||ccTri(negTri, nid), 1, data)
            Quit:nid=""
            Set tri = $List(data, 1), lcc = $List(data, 2)
            Do ..Row(arr, nid, "triangles", tri, "lcc", lcc)
            Set emitted = emitted + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccAdj, ^||ccDeg, ^||ccTri
    Quit "OK:" _ arr.%ToJSON()
}

ClassMethod KCoreJson(topK As %Integer = 10000) As %String
{
    If '##class(Graph.KG.TraversalBuild).EnsureNKG() Return "ERROR:^NKG not built"
    Do ..BuildSymAdj()

    Kill ^||ccCoreDeg, ^||ccCoreness, ^||ccBucket
    Set idx = ""
    For {
        Set idx = $Order(^||ccAdj(idx))
        Quit:idx=""
        Set deg = 0
        Set v = ""
        For {
            Set v = $Order(^||ccAdj(idx, v))
            Quit:v=""
            Set deg = deg + 1
        }
        Set ^||ccCoreDeg(idx) = deg
        Set ^||ccBucket(deg, idx) = 1
    }

    For {
        Set minDeg = $Order(^||ccBucket(""))
        Quit:minDeg=""
        Set idx = $Order(^||ccBucket(minDeg, ""))
        If idx = "" {
            Kill ^||ccBucket(minDeg)
            Continue
        }
        Kill ^||ccBucket(minDeg, idx)
        Set curDeg = ^||ccCoreDeg(idx)
        Set ^||ccCoreness(idx) = curDeg
        Kill ^||ccCoreDeg(idx)
        Set v = ""
        For {
            Set v = $Order(^||ccAdj(idx, v))
            Quit:v=""
            If '$Data(^||ccCoreDeg(v)) Continue
            Set vDeg = ^||ccCoreDeg(v)
            If vDeg > curDeg {
                Kill ^||ccBucket(vDeg, v)
                Set newDeg = vDeg - 1
                Set ^||ccCoreDeg(v) = newDeg
                Set ^||ccBucket(newDeg, v) = 1
            }
        }
    }

    Kill ^||ccCoreSort
    Set idx = ""
    For {
        Set idx = $Order(^||ccCoreness(idx), 1, c)
        Quit:idx=""
        // by node ID, not ^NKG index: a tie at the topK cut goes in IRIS collation, as k_core() ranks (4.6.0 R050)
        Set ^||ccCoreSort(-c, $Get(^NKG("$ND", idx), idx)) = c
    }
    Set arr = ##class(%DynamicArray).%New(), emitted = 0
    Set negC = ""
    For {
        Set negC = $Order(^||ccCoreSort(negC))
        Quit:negC=""
        Set nid = ""
        For {
            Set nid = $Order(^||ccCoreSort(negC, nid), 1, c)
            Quit:nid=""
            Do ..Row(arr, nid, "coreness", c)
            Set emitted = emitted + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccAdj, ^||ccCoreDeg, ^||ccCoreness, ^||ccBucket, ^||ccCoreSort
    Quit "OK:" _ arr.%ToJSON()
}

ClassMethod SCCJson(topK As %Integer = 10000) As %String
{
    If '##class(Graph.KG.TraversalBuild).EnsureNKG() Return "ERROR:^NKG not built"

    Kill ^||ccIndex, ^||ccLow, ^||ccOnStk, ^||ccSccStk, ^||ccComp, ^||ccFrame, ^||ccNbr, ^||ccNbrN
    Set counter = 0, sccStkTop = 0, compId = 0

    Set startNid = ""
    For {
        Set startNid = $Order(^NKG("$NI", startNid), 1, startIdx)
        Quit:startNid=""
        If $Data(^||ccIndex(startIdx)) Continue

        Set frameTop = 1
        Set ^||ccFrame(frameTop, "v") = startIdx
        Set ^||ccFrame(frameTop, "ci") = 0
        Set ^||ccIndex(startIdx) = counter, ^||ccLow(startIdx) = counter
        Set counter = counter + 1
        Set sccStkTop = sccStkTop + 1, ^||ccSccStk(sccStkTop) = startIdx, ^||ccOnStk(startIdx) = 1
        Do ..MaterializeNbrs(startIdx)

        While frameTop > 0 {
            Set v = ^||ccFrame(frameTop, "v")
            Set ci = ^||ccFrame(frameTop, "ci")
            Set nbrN = +$Get(^||ccNbrN(v), 0)

            Set advanced = 0
            While ci < nbrN {
                Set ci = ci + 1
                Set dst = ^||ccNbr(v, ci)
                If dst = v Continue
                If '$Data(^||ccIndex(dst)) {
                    Set ^||ccFrame(frameTop, "ci") = ci
                    Set frameTop = frameTop + 1
                    Set ^||ccFrame(frameTop, "v") = dst
                    Set ^||ccFrame(frameTop, "ci") = 0
                    Set ^||ccIndex(dst) = counter, ^||ccLow(dst) = counter
                    Set counter = counter + 1
                    Set sccStkTop = sccStkTop + 1, ^||ccSccStk(sccStkTop) = dst, ^||ccOnStk(dst) = 1
                    Do ..MaterializeNbrs(dst)
                    Set advanced = 1
                    Quit
                } ElseIf $Data(^||ccOnStk(dst)) {
                    If ^||ccIndex(dst) < ^||ccLow(v) Set ^||ccLow(v) = ^||ccIndex(dst)
                }
            }
            If advanced Continue

            If ^||ccLow(v) = ^||ccIndex(v) {
                Set members = "", msize = 0
                For {
                    Set w = ^||ccSccStk(sccStkTop)
                    Set sccStkTop = sccStkTop - 1
                    Kill ^||ccOnStk(w)
                    Set members = members _ w _ ",", msize = msize + 1
                    Quit:w=v
                }
                Set ^||ccComp(compId) = $LB(msize, members)
                Set compId = compId + 1
            }

            Set frameTop = frameTop - 1
            If frameTop > 0 {
                Set parent = ^||ccFrame(frameTop, "v")
                If ^||ccLow(v) < ^||ccLow(parent) Set ^||ccLow(parent) = ^||ccLow(v)
            }
        }
    }

    Kill ^||ccCompSort
    Set c = ""
    For {
        Set c = $Order(^||ccComp(c), 1, cdata)
        Quit:c=""
        Set ^||ccCompSort(-$List(cdata, 1), c) = cdata
    }
    Set arr = ##class(%DynamicArray).%New(), emitted = 0, newId = 0
    Set negSize = ""
    For {
        Set negSize = $Order(^||ccCompSort(negSize))
        Quit:negSize=""
        Set c = ""
        For {
            Set c = $Order(^||ccCompSort(negSize, c), 1, cdata)
            Quit:c=""
            Set msize = $List(cdata, 1), members = $List(cdata, 2)
            For mi = 1:1:$Length(members, ",") - 1 {
                Set memIdx = $Piece(members, ",", mi)
                Set nid = $Get(^NKG("$ND", memIdx), memIdx)
                Do ..Row(arr, nid, "component", newId, "size", msize)
                Set emitted = emitted + 1
            }
            Set newId = newId + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccIndex, ^||ccLow, ^||ccOnStk, ^||ccSccStk, ^||ccComp, ^||ccFrame, ^||ccCompSort, ^||ccNbr, ^||ccNbrN
    Quit "OK:" _ arr.%ToJSON()
}

/// Materialize directed out-neighbors of node idx into ^||ccNbr(idx,1..N)=dstIdx
/// with count in ^||ccNbrN(idx). Snapshots adjacency so iterative Tarjan can
/// resume neighbor iteration by integer cursor (avoids re-scanning ^NKG, which
/// caused an infinite loop when resuming $Order state across frames).
ClassMethod MaterializeNbrs(idx As %Integer) [ Private ]
{
    If $Data(^||ccNbrN(idx)) Quit
    Set k = 0
    Set negP = ""
    For {
        Set negP = $Order(^NKG(-1, idx, negP))
        Quit:negP=""
        Set dst = ""
        For {
            Set dst = $Order(^NKG(-1, idx, negP, dst))
            Quit:dst=""
            Set k = k + 1
            Set ^||ccNbr(idx, k) = dst
        }
    }
    Set ^||ccNbrN(idx) = k
}

/// One Louvain local-moving phase over ^||lvW: each node in turn joins the neighbouring community (or stays in its
/// own) with the largest gain, until a full pass moves nothing. Leaves ^||lvC(node) = community.
/// movedAny is 1 when any node moved. m2 is twice the total edge weight, the same at every level.
/// gamma = 1 scores modularity: the links into a community less degree times the community's degree over m2. Any
/// other gamma scores the Constant Potts Model, as leidenalg's CPMVertexPartition does: the links into a community
/// less gamma times the node's size times the community's size, a size being the number of original nodes a level
/// node holds (^||lvS). Before 4.6.0 (R231) gamma scaled modularity's second term instead, a different objective.
ClassMethod LouvainLevel(gamma As %Double, m2 As %Double, Output movedAny As %Boolean) [ Private ]
{
    Kill ^||lvK, ^||lvC, ^||lvTot
    // the leidenalg routes' test: within 1E-9 of 1 is modularity
    Set tCPM = ($ZAbs(gamma - 1) > 1E-9)
    Set movedAny = 0
    Set i = ""
    For {
        Set i = $Order(^||lvW(i))
        Quit:i=""
        Set k = 0, j = ""
        For {
            Set j = $Order(^||lvW(i, j), 1, wij)
            Quit:j=""
            // a self-loop adds its weight to the degree twice
            Set k = k + wij
            If j = i Set k = k + wij
        }
        // under CPM a node's weight is its size and a community's total the size of its members
        Set:tCPM k = ^||lvS(i)
        Set ^||lvK(i) = k, ^||lvC(i) = i, ^||lvTot(i) = k
    }
    Set pass = 0
    For {
        Set pass = pass + 1, changed = 0
        Set i = ""
        For {
            Set i = $Order(^||lvW(i))
            Quit:i=""
            Set cur = ^||lvC(i), k = ^||lvK(i)
            Kill ^||lvL
            Set j = ""
            For {
                Set j = $Order(^||lvW(i, j), 1, wij)
                Quit:j=""
                Continue:j=i
                Set c = ^||lvC(j)
                Set ^||lvL(c) = $Get(^||lvL(c), 0) + wij
            }
            Set ^||lvTot(cur) = ^||lvTot(cur) - k
            // staying is a candidate like any other: its gain is the links into what is left of cur
            Set best = cur
            Set tDiv = $Select(tCPM: 1, 1: m2)
            Set bestGain = $Get(^||lvL(cur), 0) - (((gamma * k) * ^||lvTot(cur)) / tDiv)
            Set c = ""
            For {
                Set c = $Order(^||lvL(c), 1, kin)
                Quit:c=""
                Continue:c=cur
                Set gain = kin - (((gamma * k) * $Get(^||lvTot(c), 0)) / tDiv)
                If gain > (bestGain + 1E-12) {
                    Set bestGain = gain, best = c
                }
            }
            Set ^||lvTot(best) = $Get(^||lvTot(best), 0) + k
            If best '= cur {
                Set ^||lvC(i) = best, changed = 1, movedAny = 1
            }
        }
        Quit:'changed
        Quit:pass>=1000
    }
    Kill ^||lvL
}

/// Replace ^||lvW by the graph of its communities (^||lvC): an edge between two communities weighs the sum of the
/// edges between their members; a community's self-loop holds the weight of the edges inside it, once. A
/// community's size (^||lvS) is the sum of its members' sizes.
ClassMethod LouvainAggregate() [ Private ]
{
    Kill ^||lvW2, ^||lvS2
    Set i = ""
    For {
        Set i = $Order(^||lvS(i), 1, tSz)
        Quit:i=""
        Set ci = ^||lvC(i)
        Set ^||lvS2(ci) = $Get(^||lvS2(ci), 0) + tSz
    }
    Kill ^||lvS
    Merge ^||lvS = ^||lvS2
    Kill ^||lvS2
    Set i = ""
    For {
        Set i = $Order(^||lvW(i))
        Quit:i=""
        Set ci = ^||lvC(i)
        Set j = ""
        For {
            Set j = $Order(^||lvW(i, j), 1, wij)
            Quit:j=""
            Set cj = ^||lvC(j)
            // an edge inside a community is met from both ends, a self-loop from one: count the loop twice too
            Set add = wij
            If j = i Set add = wij * 2
            Set ^||lvW2(ci, cj) = $Get(^||lvW2(ci, cj), 0) + add
        }
    }
    Kill ^||lvW
    Set ci = ""
    For {
        Set ci = $Order(^||lvW2(ci))
        Quit:ci=""
        Set cj = ""
        For {
            Set cj = $Order(^||lvW2(ci, cj), 1, w)
            Quit:cj=""
            If cj = ci Set w = w / 2
            Set ^||lvW(ci, cj) = w
        }
    }
    Kill ^||lvW2
}

/// Louvain community detection over the symmetrized ^NKG adjacency (Blondel et al. 2008): local moving until no node
/// improves its gain, then each community becomes one node (internal edges a self-loop, edges between communities
/// summed) and the next level starts, up to maxLevels levels or until a level moves nothing. Before 4.6.0 (R220) it
/// ran the local-moving phase only, which stops well short of Louvain (0.311 on Zachary's karate club, Louvain 0.42).
/// The graph is read undirected and simple: a pair of nodes linked in either direction, by any number of edges,
/// counts as one edge of weight 1, and edge weights are ignored, as on IVG's other Leiden routes.
/// gamma = 1.0 optimizes modularity; any other gamma the Constant Potts Model with resolution gamma, as leidenalg and
/// docs/COMMUNITIES.md define it (4.6.0 R231: it was modularity with gamma scaling the expected edges). randomSeed
/// and tol are unused: nodes are visited in ^NKG's order, so the answer is deterministic.
/// Pure ObjectScript (no leidenalg); LeidenJsonAuto prefers leidenalg in IRIS's Python when it is installed.
ClassMethod LeidenJson(maxLevels As %Integer = 10, gamma As %Double = 1.0, tol As %Double = 0.0001, topK As %Integer = 10000, memBudgetMB As %Integer = 256, randomSeed As %Integer = -1) As %String
{
    If '##class(Graph.KG.TraversalBuild).EnsureNKG() Return "ERROR:^NKG not built"
    Do ..BuildSymAdj()

    // ^||lvW(i, j) = w: the current level's symmetric weighted graph (a self-loop holds its community's internal
    // weight once); ^||lvOf(node) = the level node holding it.
    Kill ^||ccComm, ^||lvW, ^||lvOf, ^||lvS
    Set m2 = 0
    Set idx = ""
    For {
        Set idx = $Order(^||ccAdj(idx))
        Quit:idx=""
        Set ^||lvOf(idx) = idx, ^||lvS(idx) = 1
        Set v = ""
        For {
            Set v = $Order(^||ccAdj(idx, v))
            Quit:v=""
            Set ^||lvW(idx, v) = 1
            Set m2 = m2 + 1
        }
    }
    If m2 = 0 {
        // no edge but self-loops: every node a community of its own
        Set arr = ##class(%DynamicArray).%New(), newId = 0
        Set nid = ""
        For {
            Set nid = $Order(^NKG("$NI", nid))
            Quit:nid=""
            Do ..Row(arr, nid, "community", newId, "size", 1)
            Set newId = newId + 1
            Quit:(topK>0)&&(newId>=topK)
        }
        Kill ^||ccAdj, ^||lvW, ^||lvOf, ^||lvS
        Quit "OK:" _ arr.%ToJSON()
    }

    Set level = 0
    For {
        Quit:level>=maxLevels
        Set level = level + 1
        Do ..LouvainLevel(gamma, m2, .movedAny)
        Quit:'movedAny
        // every original node follows its level node into that node's community
        Set idx = ""
        For {
            Set idx = $Order(^||lvOf(idx), 1, lvNode)
            Quit:idx=""
            Set ^||lvOf(idx) = ^||lvC(lvNode)
        }
        Do ..LouvainAggregate()
    }
    Merge ^||ccComm = ^||lvOf
    Kill ^||lvW, ^||lvOf, ^||lvC, ^||lvK, ^||lvTot, ^||lvL, ^||lvS

    Kill ^||ccCommSize, ^||ccCommMembers
    Set idx = ""
    For {
        Set idx = $Order(^||ccComm(idx), 1, c)
        Quit:idx=""
        Set ^||ccCommSize(c) = +$Get(^||ccCommSize(c), 0) + 1
        Set ^||ccCommMembers(c, idx) = 1
    }
    Kill ^||ccCommBySize
    Set c = ""
    For {
        Set c = $Order(^||ccCommSize(c), 1, sz)
        Quit:c=""
        Set ^||ccCommBySize(-sz, c) = sz
    }
    Set arr = ##class(%DynamicArray).%New(), emitted = 0, newId = 0
    Set negSz = ""
    For {
        Set negSz = $Order(^||ccCommBySize(negSz))
        Quit:negSz=""
        Set c = ""
        For {
            Set c = $Order(^||ccCommBySize(negSz, c), 1, sz)
            Quit:c=""
            Set mem = ""
            For {
                Set mem = $Order(^||ccCommMembers(c, mem))
                Quit:mem=""
                Set nid = $Get(^NKG("$ND", mem), mem)
                Do ..Row(arr, nid, "community", newId, "size", sz)
                Set emitted = emitted + 1
            }
            Set newId = newId + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccAdj, ^||ccComm, ^||ccKi, ^||ccCommTot, ^||ccCommSize, ^||ccCommMembers, ^||ccCommBySize, ^||ccLinks
    Quit "OK:" _ arr.%ToJSON()
}

/// Canonical Leiden via embedded-Python leidenalg (igraph backend), when those
/// packages are installed in mgr/python. Reads ^NKG adjacency via gref, runs
ClassMethod ClosenessJsonPy(formula As %String = "harmonic", topK As %Integer = 10000) As %String [ Language = python ]
{
    import iris, json
    try:
        import igraph
    except ImportError:
        return "PYUNAVAIL"

    iris.cls("Graph.KG.TraversalBuild").EnsureNKG()
    g = iris.gref("^NKG")
    if not g["$meta", "nodeCount"]:
        return "ERROR:^NKG not built"

    nd = {}
    cur = ""
    while True:
        cur = g.order(["$ND", cur])
        if cur is None or cur == "":
            break
        nd[int(cur)] = g["$ND", cur]
    if not nd:
        return "ERROR:^NKG not built"

    edge_set = set()
    src = ""
    while True:
        src = g.order([-1, src])
        if src is None or src == "":
            break
        srci = int(src)
        negp = ""
        while True:
            negp = g.order([-1, src, negp])
            if negp is None or negp == "":
                break
            dst = ""
            while True:
                dst = g.order([-1, src, negp, dst])
                if dst is None or dst == "":
                    break
                dsti = int(dst)
                if srci != dsti:
                    a, b = (srci, dsti) if srci < dsti else (dsti, srci)
                    edge_set.add((a, b))

    sorted_idx = sorted(nd.keys())
    pos = {ix: i for i, ix in enumerate(sorted_idx)}
    ig = igraph.Graph(n=len(sorted_idx), directed=False)
    ig.add_edges([(pos[a], pos[b]) for (a, b) in edge_set if a in pos and b in pos])
    n = len(sorted_idx)

    if formula == "classical":
        scores = ig.closeness(mode="all", normalized=True)
    else:
        norm = ig.harmonic_centrality(mode="all")
        scores = [s * (n - 1) for s in norm]

    # highest first, ties by id (4.6.0 R050)
    ranked = sorted(range(n), key=lambda i: (-scores[i], str(nd[sorted_idx[i]])))
    out = []
    tk = int(topK)
    for rank, i in enumerate(ranked):
        out.append({"id": nd[sorted_idx[i]], "score": scores[i]})
        if tk > 0 and rank + 1 >= tk:
            break
    return "OK:" + json.dumps(out)
}

/// leidenalg.find_partition, returns the same "OK:[{id,community,size}]" format
/// as LeidenJson (contiguous 0..K-1 by descending size). Returns the sentinel
/// "PYUNAVAIL" if igraph/leidenalg are not importable so the caller can fall
/// back to the pure-ObjectScript greedy LeidenJson.
ClassMethod LeidenJsonPy(gamma As %Double = 1.0, topK As %Integer = 10000, randomSeed As %Integer = -1) As %String [ Language = python ]
{
    import iris, json
    try:
        import igraph
        import leidenalg
    except ImportError:
        return "PYUNAVAIL"

    iris.cls("Graph.KG.TraversalBuild").EnsureNKG()
    g = iris.gref("^NKG")
    nodecount = g["$meta", "nodeCount"]
    if not nodecount:
        return "ERROR:^NKG not built"

    # idx -> nodeId via ^NKG("$ND", idx)
    nd = {}
    cur = ""
    while True:
        cur = g.order(["$ND", cur])
        if cur is None or cur == "":
            break
        nd[int(cur)] = g["$ND", cur]
    if not nd:
        return "ERROR:^NKG not built"

    # symmetrized edges from ^NKG(-1, src, negP, dst)
    edge_set = set()
    src = ""
    while True:
        src = g.order([-1, src])
        if src is None or src == "":
            break
        srci = int(src)
        negp = ""
        while True:
            negp = g.order([-1, src, negp])
            if negp is None or negp == "":
                break
            dst = ""
            while True:
                dst = g.order([-1, src, negp, dst])
                if dst is None or dst == "":
                    break
                dsti = int(dst)
                if srci != dsti:
                    a, b = (srci, dsti) if srci < dsti else (dsti, srci)
                    edge_set.add((a, b))

    sorted_idx = sorted(nd.keys())
    pos = {ix: i for i, ix in enumerate(sorted_idx)}
    ig = igraph.Graph(n=len(sorted_idx))
    ig.add_edges([(pos[a], pos[b]) for (a, b) in edge_set if a in pos and b in pos])

    seed = None if (randomSeed is None or int(randomSeed) < 0) else int(randomSeed)
    if abs(float(gamma) - 1.0) < 1e-9:
        part = leidenalg.find_partition(ig, leidenalg.ModularityVertexPartition, seed=seed)
    else:
        part = leidenalg.find_partition(
            ig, leidenalg.CPMVertexPartition,
            resolution_parameter=float(gamma), seed=seed)

    groups = {}
    for vpos, comm in enumerate(part.membership):
        groups.setdefault(comm, []).append(sorted_idx[vpos])

    out = []
    new_id = 0
    emitted = 0
    tk = int(topK)
    for _comm, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        size = len(members)
        for ix in members:
            out.append({"id": nd[ix], "community": new_id, "size": size})
            emitted += 1
        new_id += 1
        if tk > 0 and emitted >= tk:
            break
    return "OK:" + json.dumps(out)
}

ClassMethod LeidenJsonAuto(maxLevels As %Integer = 10, gamma As %Double = 1.0, tol As %Double = 0.0001, topK As %Integer = 10000, memBudgetMB As %Integer = 256, randomSeed As %Integer = -1) As %String
{
    Set py = ..LeidenJsonPy(gamma, topK, randomSeed)
    If py '= "PYUNAVAIL" Return py
    Return ..LeidenJson(maxLevels, gamma, tol, topK, memBudgetMB, randomSeed)
}

}
