/// Graph.KG.TraversalBuild — (re)build ^KG and ^NKG adjacency indexes.
/// Spec 214: ^KG layout is now graph-aware: ^KG("out", graph, 0, s, p, o).
/// BuildKG reads graph_id from rdf_edges; BuildNKG iterates the merged view.
Class Graph.KG.TraversalBuild Extends %RegisteredObject
{

ClassMethod BuildKG() As %Status
{
	Try {
		// Kill only the subscripts we rebuild. Spec 206: do NOT Kill ^KG bare —
		// that would destroy temporal subscripts. Spec 214: kill full "out"/"in"
		// trees (all graph subscripts) plus "deg"/"degp"/"label"/"prop".
		Kill ^KG("label"), ^KG("prop"), ^KG("out"), ^KG("in"), ^KG("deg"), ^KG("degp")
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

		// 3. Edges — spec 214: read graph_id from rdf_edges; write to ^KG("out", graph, 0, ...)
		Set count = 0
		&sql(DECLARE c3 CURSOR FOR SELECT s, p, o_id, qualifiers, graph_id FROM Graph_KG.rdf_edges)
		&sql(OPEN c3)
		For {
			&sql(FETCH c3 INTO :s, :p, :o, :qual, :edgeGraph)
			Quit:SQLCODE'=0
			If (s = "") || (p = "") || (o = "") Continue
			// graph_id is '' (empty) for the default graph (spec 214 FR-003 sentinel)
			Set g = $Select(edgeGraph="":0, 1:edgeGraph)
			Set weight = 1.0
			If qual '= "" {
				Try {
					Set obj = ##class(%DynamicObject).%FromJSON(qual)
					If ($IsObject(obj)) && (obj.weight '= "") Set weight = obj.weight
				} Catch {}
			}
			Set ^KG("out", g, s, p, o) = weight
			Set ^KG("in", g, o, p, s) = weight
			Set tmp = $Increment(^KG("deg", g, s))
			Set tmp = $Increment(^KG("degp", g, s, p))
			Set count = count + 1
			If count # 50000 = 0 { TCommit  TStart }
		}
		&sql(CLOSE c3)
		TCommit

		Try { Do ..BuildNKG() } Catch buildEx {
			Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.Traversal: BuildNKG skipped: "_buildEx.DisplayString(), 0, 1)
		}
		Return $$$OK
	} Catch ex {
		If $TLevel > 0 { TRollback }
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

/// BuildNKG iterates the merged ^KG("out") view across all graph subscripts.
/// ^NKG is per-identifier (no graph dimension) — merged-view BFS acceleration.
ClassMethod BuildNKG() As %Status
{
	Try { Do ##class(Graph.KG.NKGAccel).InvalidateAdjCache() } Catch invEx {
		Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.Traversal: InvalidateAdjCache failed: "_invEx.DisplayString(), 0, 1)
	}
	Kill ^NKG
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	Set edgeCount = 0
	// Iterate all graph subscripts (merged view)
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set src = ""
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:src=""
			Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(src)
			Set pred = ""
			For {
				Set pred = $Order(^KG("out", g, src, pred))
				Quit:pred=""
				Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(pred)
				Set dst = ""
				For {
					Set dst = $Order(^KG("out", g, src, pred, dst), 1, weight)
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
			// deg from merged view (sum across all graphs for this identifier)
			Set mergedDeg = 0
			Set gg = ""
			For {
				Set gg = $Order(^KG("deg", gg))
				Quit:gg=""
				Set mergedDeg = mergedDeg + $Get(^KG("deg", gg, src), 0)
			}
			Set ^NKG(-3, sIdx) = mergedDeg
		}
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
	Set cnt = 0
	// Iterate all graph subscripts
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set src = ""
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:src=""
			Set pred = ""
			For {
				Set pred = $Order(^KG("out", g, src, pred))
				Quit:pred=""
				Set deg = 0
				Set o = ""
				For {
					Set o = $Order(^KG("out", g, src, pred, o))
					Quit:o=""
					Set deg = deg + 1
				}
				Set tmp = $Increment(^KG("deg", g, src), deg)
				Set ^KG("degp", g, src, pred) = deg
				Set cnt = cnt + 1
			}
		}
	}
	Return cnt
}

ClassMethod Build2HopStats() As %Integer
{
	Kill ^KG("deg2p")
	// Merged-view 2-hop (spec 214: iterate all graph subscripts)
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set src = ""
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:src=""
			Set pred = ""
			For {
				Set pred = $Order(^KG("out", g, src, pred))
				Quit:pred=""
				Set sum = 0
				Set mid = ""
				For {
					Set mid = $Order(^KG("out", g, src, pred, mid))
					Quit:mid=""
					// Sum degp across all graphs for mid
					Set gg = ""
					For {
						Set gg = $Order(^KG("degp", gg))
						Quit:gg=""
						Set sum = sum + $Get(^KG("degp", gg, mid, pred), 0)
					}
				}
				If sum > 0 Set ^KG("deg2p", src, pred) = sum
			}
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
	// Fallback: merged-view calculation
	Set cnt = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set src = ""
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:src=""
			Set pred = ""
			For {
				Set pred = $Order(^KG("out", g, src, pred))
				Quit:pred=""
				Kill ^||h2s
				Set ^||h2s(src) = ""
				Set mid = ""
				For {
					Set mid = $Order(^KG("out", g, src, pred, mid))
					Quit:mid=""
					Set ^||h2s(mid) = ""
				}
				Set exact = 0
				Set mid = ""
				For {
					Set mid = $Order(^KG("out", g, src, pred, mid))
					Quit:mid=""
					Set gg = ""
					For {
						Set gg = $Order(^KG("out", gg))
						Quit:gg=""
						Set o2 = ""
						For {
							Set o2 = $Order(^KG("out", gg, 0, mid, pred, o2))
							Quit:o2=""
							If '$Data(^||h2s(o2)) {
								Set ^||h2s(o2) = "" Set exact = exact + 1
							}
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
	}
	Return cnt
}

ClassMethod KGEdgeCount(maxCount As %Integer = 10000) As %Integer
{
	Set count = 0
	// Count across all graph subscripts (merged view)
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set s = ""
		For {
			Set s = $Order(^KG("out", g, s))
			Quit:s=""
			Set count = count + 1
			If count >= maxCount {
				Quit
			}
		}
		If count >= maxCount {
			Quit
		}
	}
	Return count
}

ClassMethod NKGPopulated() As %Integer
{
	Return ($Data(^NKG) > 0)
}

}
