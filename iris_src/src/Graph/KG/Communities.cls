/// Graph.KG.Communities - Community detection over the ^NKG integer-index global.
///
/// Four pure-ObjectScript algorithms, no external libraries, no embedded Python:
///   TriangleCountJson - symmetrized triangle count + local clustering coefficient
///   SCCJson           - strongly connected components (directed, iterative Tarjan)
///   KCoreJson         - k-core decomposition (Batagelj-Zaversnik, symmetrized)
///   LeidenJson        - greedy-modularity community detection (symmetrized)
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

ClassMethod TriangleCountJson(topK As %Integer = 10000) As %String
{
    If $Order(^NKG("$NI", "")) = "" Return "ERROR:^NKG not built"
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
        Set ^||ccTri(-tri, idx) = $LB(tri, lcc, deg)
    }

    Set json = "[", sep = "", emitted = 0
    Set negTri = ""
    For {
        Set negTri = $Order(^||ccTri(negTri))
        Quit:negTri=""
        Set idx = ""
        For {
            Set idx = $Order(^||ccTri(negTri, idx), 1, data)
            Quit:idx=""
            Set nid = $Get(^NKG("$ND", idx), idx)
            Set tri = $List(data, 1), lcc = $List(data, 2)
            Set json = json _ sep _ "{""id"":""" _ nid _ """,""triangles"":" _ tri _ ",""lcc"":" _ lcc _ "}"
            Set sep = ","
            Set emitted = emitted + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccAdj, ^||ccDeg, ^||ccTri
    Quit "OK:" _ json _ "]"
}

ClassMethod KCoreJson(topK As %Integer = 10000) As %String
{
    If $Order(^NKG("$NI", "")) = "" Return "ERROR:^NKG not built"
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
        Set ^||ccCoreSort(-c, idx) = c
    }
    Set json = "[", sep = "", emitted = 0
    Set negC = ""
    For {
        Set negC = $Order(^||ccCoreSort(negC))
        Quit:negC=""
        Set idx = ""
        For {
            Set idx = $Order(^||ccCoreSort(negC, idx), 1, c)
            Quit:idx=""
            Set nid = $Get(^NKG("$ND", idx), idx)
            Set json = json _ sep _ "{""id"":""" _ nid _ """,""coreness"":" _ c _ "}"
            Set sep = ","
            Set emitted = emitted + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccAdj, ^||ccCoreDeg, ^||ccCoreness, ^||ccBucket, ^||ccCoreSort
    Quit "OK:" _ json _ "]"
}

ClassMethod SCCJson(topK As %Integer = 10000) As %String
{
    If $Order(^NKG("$NI", "")) = "" Return "ERROR:^NKG not built"

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
    Set json = "[", sep = "", emitted = 0, newId = 0
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
                Set json = json _ sep _ "{""id"":""" _ nid _ """,""component"":" _ newId _ ",""size"":" _ msize _ "}"
                Set sep = ","
                Set emitted = emitted + 1
            }
            Set newId = newId + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccIndex, ^||ccLow, ^||ccOnStk, ^||ccSccStk, ^||ccComp, ^||ccFrame, ^||ccCompSort, ^||ccNbr, ^||ccNbrN
    Quit "OK:" _ json _ "]"
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

/// Greedy-modularity community detection (single-level local moving + iteration).
/// Symmetrized adjacency. gamma=1.0 → modularity; gamma!=1.0 → CPM resolution.
/// Pure ObjectScript (no leidenalg); for canonical Leiden use engine.leiden_communities().
ClassMethod LeidenJson(maxLevels As %Integer = 10, gamma As %Double = 1.0, tol As %Double = 0.0001, topK As %Integer = 10000, memBudgetMB As %Integer = 256, randomSeed As %Integer = -1) As %String
{
    If $Order(^NKG("$NI", "")) = "" Return "ERROR:^NKG not built"
    Do ..BuildSymAdj()

    Kill ^||ccComm, ^||ccKi, ^||ccCommTot
    Set m2 = 0
    Set idx = ""
    For {
        Set idx = $Order(^||ccAdj(idx))
        Quit:idx=""
        Set ki = 0, v = ""
        For {
            Set v = $Order(^||ccAdj(idx, v))
            Quit:v=""
            Set ki = ki + 1
        }
        Set ^||ccKi(idx) = ki
        Set ^||ccComm(idx) = idx
        Set ^||ccCommTot(idx) = ki
        Set m2 = m2 + ki
    }
    If m2 = 0 {
        Set json = "[", sep = "", emitted = 0
        Set idx = ""
        For {
            Set idx = $Order(^NKG("$NI", idx), 1, ix)
            Quit:idx=""
            Set json = json _ sep _ "{""id"":""" _ idx _ """,""community"":0,""size"":1}"
            Set sep = ","
        }
        Kill ^||ccAdj, ^||ccComm, ^||ccKi, ^||ccCommTot
        Quit "OK:" _ json _ "]"
    }

    Set level = 0, changed = 1
    While (level < maxLevels) && changed {
        Set level = level + 1, changed = 0
        Set idx = ""
        For {
            Set idx = $Order(^||ccAdj(idx))
            Quit:idx=""
            Set curComm = ^||ccComm(idx)
            Set ki = ^||ccKi(idx)

            Kill ^||ccLinks
            Set v = ""
            For {
                Set v = $Order(^||ccAdj(idx, v))
                Quit:v=""
                Set vc = ^||ccComm(v)
                Set ^||ccLinks(vc) = +$Get(^||ccLinks(vc), 0) + 1
            }

            Set ^||ccCommTot(curComm) = ^||ccCommTot(curComm) - ki
            Set bestComm = curComm, bestGain = 0
            Set kiInCur = +$Get(^||ccLinks(curComm), 0)

            Set vc = ""
            For {
                Set vc = $Order(^||ccLinks(vc), 1, kiIn)
                Quit:vc=""
                Set totC = +$Get(^||ccCommTot(vc), 0)
                Set gain = +$Number(kiIn - (gamma * ki * totC / m2), 15)
                If gain > bestGain {
                    Set bestGain = gain
                    Set bestComm = vc
                }
            }

            Set ^||ccCommTot(bestComm) = +$Get(^||ccCommTot(bestComm), 0) + ki
            If bestComm '= curComm {
                Set ^||ccComm(idx) = bestComm
                Set changed = 1
            }
        }
    }

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
    Set json = "[", sep = "", emitted = 0, newId = 0
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
                Set json = json _ sep _ "{""id"":""" _ nid _ """,""community"":" _ newId _ ",""size"":" _ sz _ "}"
                Set sep = ","
                Set emitted = emitted + 1
            }
            Set newId = newId + 1
            If (topK > 0) && (emitted >= topK) Quit
        }
        If (topK > 0) && (emitted >= topK) Quit
    }
    Kill ^||ccAdj, ^||ccComm, ^||ccKi, ^||ccCommTot, ^||ccCommSize, ^||ccCommMembers, ^||ccCommBySize, ^||ccLinks
    Quit "OK:" _ json _ "]"
}

}
