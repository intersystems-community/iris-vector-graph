Class Graph.KG.QBucket Extends %RegisteredObject
{

/// BFSWithBuckets v2: precompute eligible node set from buckets, then BFS.
/// Eliminates per-node bucket checks — O(candidates) instead of O(BFS_nodes * max_buckets).
/// bucketList: comma-separated bucket IDs from Python numpy centroid search.
ClassMethod BFSWithBuckets(seedId As %String, maxHops As %Integer = 2, queryVecStr As %String, bucketList As %String, topK As %Integer = 10) As %String
{
    Set seedIdx = $Get(^NKG("$NI", seedId), -1)
    If seedIdx = -1 Quit "ERROR:seed not found: "_seedId
    Kill ^||qvec
    Set dim = 0
    For i=1:1:$Length(queryVecStr,",") {
        Set ^||qvec(dim) = +$Piece(queryVecStr,",",i)
        Set dim = dim + 1
    }

    // Precompute eligible node set: union of all nodes in near buckets
    // This replaces 32 $Data checks per BFS node with 1 $Data check
    Kill ^||eligible
    For i=1:1:$Length(bucketList,",") {
        Set b = +$Piece(bucketList,",",i)
        Set nodeIdx = ""
        For {
            Set nodeIdx = $Order(^NKG("q", b, nodeIdx))
            Quit:nodeIdx=""
            Set ^||eligible(nodeIdx) = 1
        }
    }

    // BFS: mark candidates when node is in eligible set
    Kill ^||visited, ^||queue, ^||candidates
    Set ^||visited(seedIdx) = 0
    Set qHead = 1, qTail = 1
    Set ^||queue(1) = $ListBuild(seedIdx, 0)
    While qHead <= qTail {
        Set entry = ^||queue(qHead)
        Set qHead = qHead + 1
        Set vIdx = $List(entry, 1)
        Set depth = $List(entry, 2)
        If $Data(^||eligible(vIdx)) {
            Set ^||candidates(vIdx) = depth
        }
        If depth < maxHops {
            Set negP = $Order(^NKG(-1, vIdx, ""))
            While negP '= "" {
                Set oIdx = $Order(^NKG(-1, vIdx, negP, ""))
                While oIdx '= "" {
                    If '$Data(^||visited(oIdx)) {
                        Set ^||visited(oIdx) = depth + 1
                        Set qTail = qTail + 1
                        Set ^||queue(qTail) = $ListBuild(oIdx, depth + 1)
                    }
                    Set oIdx = $Order(^NKG(-1, vIdx, negP, oIdx))
                }
                Set negP = $Order(^NKG(-1, vIdx, negP))
            }
        }
    }

    // Rerank candidates by full cosine similarity
    Kill ^||scores
    Set cIdx = ""
    For {
        Set cIdx = $Order(^||candidates(cIdx))
        Quit:cIdx=""
        Set nid = $Get(^NKG("$ND", cIdx), "")
        If nid = "" Continue
        Set embStr = $Get(^DRKGEmb(nid), "")
        If embStr = "" Continue
        Set dot = 0
        For i=0:1:dim-1 {
            Set dot = dot + ^||qvec(i) * +$Piece(embStr,",",i+1)
        }
        Set ^||scores(-dot, cIdx) = $ListBuild(nid, $Get(^||candidates(cIdx),0))
    }
    Set json = "[", sep = "", cnt = 0, scoreKey = ""
    For {
        Set scoreKey = $Order(^||scores(scoreKey))
        Quit:(scoreKey="")!(cnt >= topK)
        Set vKey = ""
        For {
            Set vKey = $Order(^||scores(scoreKey,vKey))
            Quit:(vKey="")!(cnt >= topK)
            Set entry = ^||scores(scoreKey,vKey)
            Set json = json_sep_"{""id"":"""_$List(entry,1)_""",""score"":"_$Number(-scoreKey,6)_",""hops"":"_$List(entry,2)_"}"
            Set sep = ","
            Set cnt = cnt + 1
        }
    }
    Kill ^||qvec,^||eligible,^||visited,^||queue,^||candidates,^||scores
    Quit "OK:"_json_"]"
}

}
