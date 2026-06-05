Class Graph.KG.TraversalKHop Extends %RegisteredObject
{

ClassMethod KHopCount(srcId As %String, pred As %String = "") As %Integer
{
    If pred '= "" {
        Set cnt = $Get(^KG("degp", srcId, pred), -1)
        If cnt >= 0 Return +cnt
    } Else {
        Set cnt = $Get(^KG("deg", srcId), -1)
        If cnt >= 0 Return +cnt
    }
    Set cnt = 0
    If pred '= "" {
        Set o = ""
        For {
            Set o = $Order(^KG("out", 0, srcId, pred, o))
            Quit:o=""
            Set cnt = cnt + 1
        }
    } Else {
        Set p = ""
        For {
            Set p = $Order(^KG("out", 0, srcId, p))
            Quit:p=""
            Set o = ""
            For {
                Set o = $Order(^KG("out", 0, srcId, p, o))
                Quit:o=""
                Set cnt = cnt + 1
            }
        }
    }
    Return cnt
}
ClassMethod KHopNeighborIds(srcId As %String, pred As %String = "", requiredPreds As %List = "") As %String
{
    Set result = ""
    Set hasGuard = (requiredPreds '= "") && ($ListLength(requiredPreds) > 0)
    If pred '= "" {
        Set o = ""
        For {
            Set o = $Order(^KG("out", 0, srcId, pred, o))
            Quit:o=""
            If hasGuard {
                Set dstIdx = $Get(^NKG("$NI", o))
                If dstIdx = "" || '##class(Graph.KG.NKGAccelTraversal).StructuralGuard(dstIdx, requiredPreds) Continue
            }
            Set result = result _ o _ $Char(10)
        }
    } Else {
        Set p = ""
        For {
            Set p = $Order(^KG("out", 0, srcId, p))
            Quit:p=""
            Set o = ""
            For {
                Set o = $Order(^KG("out", 0, srcId, p, o))
                Quit:o=""
                If hasGuard {
                    Set dstIdx = $Get(^NKG("$NI", o))
                    If dstIdx = "" || '##class(Graph.KG.NKGAccelTraversal).StructuralGuard(dstIdx, requiredPreds) Continue
                }
                Set result = result _ o _ $Char(10)
            }
        }
    }
    Return result
}
ClassMethod KHop2Count(srcId As %String, pred As %String = "") As %Integer
{
    Kill ^||kh2s, ^||kh2all
    Set ^||kh2all(srcId) = ""

    If pred '= "" {
        Set o1 = ""
        For {
            Set o1 = $Order(^KG("out", 0, srcId, pred, o1))
            Quit:o1=""
            If '$Data(^||kh2all(o1)) {
                Set ^||kh2all(o1) = ""
                Set ^||kh2s(o1) = ""
            }
        }
    } Else {
        Set p1 = ""
        For {
            Set p1 = $Order(^KG("out", 0, srcId, p1))
            Quit:p1=""
            Set o1 = ""
            For {
                Set o1 = $Order(^KG("out", 0, srcId, p1, o1))
                Quit:o1=""
                If '$Data(^||kh2all(o1)) {
                    Set ^||kh2all(o1) = ""
                    Set ^||kh2s(o1) = ""
                }
            }
        }
    }

    Set cnt = 0
    Set mid = ""
    For {
        Set mid = $Order(^||kh2s(mid))
        Quit:mid=""
        If pred '= "" {
            Set o2 = ""
            For {
                Set o2 = $Order(^KG("out", 0, mid, pred, o2))
                Quit:o2=""
                If '$Data(^||kh2all(o2)) {
                    Set ^||kh2all(o2) = ""
                    Set cnt = cnt + 1
                }
            }
        } Else {
            Set p2 = ""
            For {
                Set p2 = $Order(^KG("out", 0, mid, p2))
                Quit:p2=""
                Set o2 = ""
                For {
                    Set o2 = $Order(^KG("out", 0, mid, p2, o2))
                    Quit:o2=""
                    If '$Data(^||kh2all(o2)) {
                        Set ^||kh2all(o2) = ""
                        Set cnt = cnt + 1
                    }
                }
            }
        }
    }
    Kill ^||kh2s, ^||kh2all
    Return cnt
}
ClassMethod KHop2NeighborIds(srcId As %String, pred As %String = "", maxResults As %Integer = 0) As %String
{
    Kill ^||kh2s, ^||kh2all
    Set ^||kh2all(srcId) = ""

    If pred '= "" {
        Set o1 = ""
        For {
            Set o1 = $Order(^KG("out", 0, srcId, pred, o1))
            Quit:o1=""
            If '$Data(^||kh2all(o1)) {
                Set ^||kh2all(o1) = ""
                Set ^||kh2s(o1) = ""
            }
        }
    } Else {
        Set p1 = ""
        For {
            Set p1 = $Order(^KG("out", 0, srcId, p1))
            Quit:p1=""
            Set o1 = ""
            For {
                Set o1 = $Order(^KG("out", 0, srcId, p1, o1))
                Quit:o1=""
                If '$Data(^||kh2all(o1)) {
                    Set ^||kh2all(o1) = ""
                    Set ^||kh2s(o1) = ""
                }
            }
        }
    }

    Set result = ""
    Set cnt = 0
    Set mid = ""
    For {
        Set mid = $Order(^||kh2s(mid))
        Quit:mid=""
        If pred '= "" {
            Set o2 = ""
            For {
                Set o2 = $Order(^KG("out", 0, mid, pred, o2))
                Quit:o2=""
                If '$Data(^||kh2all(o2)) {
                    Set ^||kh2all(o2) = ""
                    Set result = result _ o2 _ $Char(10)
                    Set cnt = cnt + 1
                    If (maxResults > 0) && (cnt >= maxResults) {
                        Kill ^||kh2s, ^||kh2all
                        Return result
                    }
                }
            }
        } Else {
            Set p2 = ""
            For {
                Set p2 = $Order(^KG("out", 0, mid, p2))
                Quit:p2=""
                Set o2 = ""
                For {
                    Set o2 = $Order(^KG("out", 0, mid, p2, o2))
                    Quit:o2=""
                    If '$Data(^||kh2all(o2)) {
                        Set ^||kh2all(o2) = ""
                        Set result = result _ o2 _ $Char(10)
                        Set cnt = cnt + 1
                        If (maxResults > 0) && (cnt >= maxResults) {
                            Kill ^||kh2s, ^||kh2all
                            Return result
                        }
                    }
                }
            }
        }
    }
    Kill ^||kh2s, ^||kh2all
    Return result
}
ClassMethod KHop2CountFast(srcId As %String, pred As %String = "") As %Integer
{
    If pred '= "" {
        Set cnt = $Get(^KG("deg2p", srcId, pred), -1)
        If cnt >= 0 Return +cnt
    }
    Return ..KHop2Count(srcId, pred)
}
ClassMethod KHop2CountExact(srcId As %String, pred As %String = "") As %Integer
{
    If pred '= "" {
        Set cnt = $Get(^KG("deg2p_exact", srcId, pred), -1)
        If cnt >= 0 Return +cnt
    }
    Return ..KHop2Count(srcId, pred)
}
}
