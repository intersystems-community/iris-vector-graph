Class Graph.KG.TraversalBuild Extends %RegisteredObject
{

ClassMethod BuildKG() As %Status
{
	Try {
		// Disable journaling for this process to maximize speed (requires %Admin_Operate)
		// Do $System.Process.SetJournaling(0) 
		
		Kill ^KG
		TStart
		
		// 1. Labels
		&sql(DECLARE c1 CURSOR FOR SELECT s, label FROM Graph_KG.rdf_labels)
		&sql(OPEN c1)
		For {
			&sql(FETCH c1 INTO :s, :label)
			Quit:SQLCODE'=0
			Set ^KG("label", label, s) = ""
		}
		&sql(CLOSE c1)
		
		// 2. Props
		&sql(DECLARE c2 CURSOR FOR SELECT s, key, val FROM Graph_KG.rdf_props)
		&sql(OPEN c2)
		For {
			&sql(FETCH c2 INTO :s, :key, :val)
			Quit:SQLCODE'=0
			Set ^KG("prop", s, key) = val
		}
		&sql(CLOSE c2)
		
		// 3. Edges & Stats (including weights)
		Set count = 0
		&sql(DECLARE c3 CURSOR FOR SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges)
		&sql(OPEN c3)
		For {
			&sql(FETCH c3 INTO :s, :p, :o, :qual)
			Quit:SQLCODE'=0
			
			If (s = "") || (p = "") || (o = "") Continue
			
			Set weight = 1.0
			If qual '= "" {
				Try {
					Set obj = ##class(%DynamicObject).%FromJSON(qual)
					If (obj '= "") && (obj.weight '= "") Set weight = obj.weight
				} Catch {}
			}
			
			Set ^KG("out", 0, s, p, o) = weight
			Set ^KG("in", 0, o, p, s) = weight
			Set tmp = $Increment(^KG("deg", s))
			Set tmp = $Increment(^KG("degp", s, p))
			
			Set count = count + 1
			// Commit every 50k records to manage transaction buffer
			If count # 50000 = 0 {
				TCommit
				TStart
			}
		}
		&sql(CLOSE c3)
		
		TCommit

		// BuildNKG requires Graph.KG.GraphIndex — optional, non-fatal if missing
		Try { Do ..BuildNKG() } Catch buildEx { Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.Traversal: BuildNKG skipped: "_buildEx.DisplayString(), 0, 1) }

		Return $$$OK
	} Catch ex {
		TRollback
		Return ex.AsStatus()
	}
}
ClassMethod InitNKGSkeleton() As %Status
{
	If $Data(^NKG("$meta", "nodeCount")) Return $$$OK
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	Set ^NKG("$meta", "nodeCount") = 0
	Set ^NKG("$meta", "edgeCount") = 0
	If '$Data(^NKG("$meta", "version")) Set ^NKG("$meta", "version") = 0
	Return $$$OK
}
ClassMethod NKGEdgeCount() As %Integer
{
	Return +$Get(^NKG("$meta", "edgeCount"), 0)
}
ClassMethod NKGNodeCount() As %Integer
{
	Return +$Get(^NKG("$meta", "nodeCount"), 0)
}
ClassMethod BuildNKG() As %Status
{
	// Invalidate the Arno adjacency cache BEFORE killing ^NKG.
	// CacheNKGAdj() compares ^NKG("$meta","version") to ^ArnoKG("KG","nkg_adj_ver").
	// Kill ^NKG resets the version to 0; $Increment makes it 1.
	// If the stale cache also has version 1, CacheNKGAdj sees cachedVer==currentVer
	// and skips the rebuild, leaving Arno BFS using data from the previous graph.
	Try { Do ##class(Graph.KG.NKGAccel).InvalidateAdjCache() } Catch invEx { Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.Traversal: InvalidateAdjCache failed: "_invEx.DisplayString(), 0, 1) }
	Kill ^NKG
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	
	Set edgeCount = 0
	Set src = ""
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(src)
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(pred)
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", 0, src, pred, dst), 1, weight)
				Quit:dst=""
				Set oIdx = ##class(Graph.KG.GraphIndex).InternNode(dst)
				If '$Data(^NKG(-1, sIdx, -(pIdx+1), oIdx)) {
					Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight
					Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight
					Set edgeCount = edgeCount + 1
				}
				Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(sIdx, pIdx, oIdx)
			}
		}
		Set ^NKG(-3, sIdx) = $Get(^KG("deg", src))
	}
	Set ^NKG("$meta", "edgeCount") = edgeCount
	Set tmp = $Increment(^NKG("$meta", "version"))

	Do ..Build2HopStats()
	Do ..Build2HopExactStats()

	Return $$$OK
}
ClassMethod BackfillDegp() As %Integer
{
	Kill ^KG("deg")
	Kill ^KG("degp")
	Set src = ""
	Set cnt = 0
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set deg = 0
			Set o = ""
			For {
				Set o = $Order(^KG("out", 0, src, pred, o))
				Quit:o=""
				Set deg = deg + 1
			}
			Set tmp = $Increment(^KG("deg", src), deg)
			Set ^KG("degp", src, pred) = deg
			Set cnt = cnt + 1
		}
	}
	Return cnt
}
ClassMethod Build2HopStats() As %Integer
{
	Kill ^KG("deg2p")
	Set src = ""
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set sum = 0
			Set mid = ""
			For {
				Set mid = $Order(^KG("out", 0, src, pred, mid))
				Quit:mid=""
				Set sum = sum + $Get(^KG("degp", mid, pred), 0)
			}
			If sum > 0 Set ^KG("deg2p", src, pred) = sum
		}
	}
	Set n = ""
	Set cnt = 0
	For {
		Set n = $Order(^KG("deg2p", n))
		Quit:n=""
		Set cnt = cnt + 1
	}
	Return cnt
}
ClassMethod Build2HopExactStats() As %Integer
{
    Kill ^KG("deg2p_exact")
    Set arnoOk = 0
    Try { Set arnoOk = ##class(Graph.KG.NKGAccel).Build2HopExact() } Catch { Set arnoOk = 0 }
    If arnoOk {
        Set n = ""
        Set cnt = 0
        For {
            Set n = $Order(^KG("deg2p_exact", n))
            Quit:n=""
            Set cnt = cnt + 1
        }
        Return cnt
    }
    Set src = ""
    Set cnt = 0
    For {
        Set src = $Order(^KG("out", 0, src))
        Quit:src=""
        Set pred = ""
        For {
            Set pred = $Order(^KG("out", 0, src, pred))
            Quit:pred=""
            Kill ^||h2s
            Set ^||h2s(src) = ""
            Set mid = ""
            For {
                Set mid = $Order(^KG("out", 0, src, pred, mid))
                Quit:mid=""
                Set ^||h2s(mid) = ""
            }
            Set exact = 0
            Set mid = ""
            For {
                Set mid = $Order(^KG("out", 0, src, pred, mid))
                Quit:mid=""
                Set o2 = ""
                For {
                    Set o2 = $Order(^KG("out", 0, mid, pred, o2))
                    Quit:o2=""
                    If '$Data(^||h2s(o2)) {
                        Set ^||h2s(o2) = ""
                        Set exact = exact + 1
                    }
                }
            }
            Kill ^||h2s
            If exact > 0 {
                Set ^KG("deg2p_exact", src, pred) = exact
                Set cnt = cnt + 1
            }
        }
    }
    Return cnt
}
ClassMethod KGEdgeCount(maxCount As %Integer = 10000) As %Integer
{
    Set count = 0
    Set s = ""
    For {
        Set s = $Order(^KG("out", 0, s))
        Quit:s=""
        Set count = count + 1
        If count >= maxCount Quit
    }
    Return count
}
ClassMethod NKGPopulated() As %Integer
{
    Return ($Data(^NKG) > 0)
}
}
