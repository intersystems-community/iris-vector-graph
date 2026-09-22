/// Graph.KG.TraversalBuild — (re)build ^KG and ^NKG adjacency indexes.
///
/// Every node store is graph-keyed, and the graph key is the first subscript after
/// the store name:
///
///   ^KG("out",   graph, s, p, o)      ^KG("in",    graph, o, p, s)
///   ^KG("deg",   graph, s)            ^KG("degp",  graph, s, p)
///   ^KG("label", graph, label, s)     ^KG("prop",  graph, s, key)
///   ^KG("deg2p", graph, s, p)         ^KG("deg2p_exact", graph, s, p)
///
/// Spec 214 keyed the first four; spec 230 keyed the last four, which had been keyed
/// by node, label and predicate alone — so a per-graph erase could only reach them
/// through the node IDs it was deleting, and after spec 227 broke UNIQUE (node_id)
/// the same ID lives in two graphs (FR-010).
///
/// Every graph is read off the row that produced the entry — rdf_edges, rdf_labels
/// and rdf_props each carry graph_id — and a row with none is a default-graph row.
/// No entry's graph is inferred from anywhere else (FR-011).
///
/// BuildNKG iterates the merged view: ^NKG interns identifiers with no graph
/// dimension, so it is graph-blind by construction and declared that way in
/// Graph.KG.GraphStores.
Class Graph.KG.TraversalBuild Extends %RegisteredObject
{

ClassMethod BuildKG() As %Status
{
	Try {
		TStart
		// Kill only the subscripts we rebuild. Spec 206: do NOT Kill ^KG bare —
		// that would destroy temporal subscripts. Spec 214: kill full "out"/"in"
		// trees (all graph subscripts) plus "deg"/"degp"/"label"/"prop".
		//
		// Inside the transaction rather than one line above it. Killing first meant
		// that any row the rebuild could not process — one whose graph_id is not a
		// legal subscript (ADR-0003), a <STORE>, a halted process — left the
		// adjacency deleted while the rows stayed, and the Catch's TRollback had
		// nothing to restore. Stale adjacency is recoverable by re-running the
		// rebuild; destroyed adjacency looks to every reader like a graph with no
		// edges, which is the drift Graph.KG.Eraser exists to end (ADR-0004)
		// arriving from the rebuild path instead of the delete path.
		//
		// The chunked TCommit further down bounds this: past 50000 edges the Kill
		// is already committed and a later failure still leaves a partial rebuild.
		// A rebuild of that size cannot be one transaction, so the guarantee is
		// deliberately narrow — a rebuild that fails early leaves the previous
		// adjacency intact.
		Kill ^KG("label"), ^KG("prop"), ^KG("out"), ^KG("in"), ^KG("deg"), ^KG("degp")

		// 1. Labels — spec 230: the graph key leads, read off the row's own graph_id.
		&sql(DECLARE c1 CURSOR FOR SELECT s, label, graph_id FROM Graph_KG.rdf_labels)
		&sql(OPEN c1)
		For {
			&sql(FETCH c1 INTO :s, :label, :rowGraph)
			Quit:SQLCODE'=0
			Set g = ##class(Graph.KG.GraphKey).ForIndex(rowGraph)
			Set ^KG("label", g, label, s) = ""
		}
		&sql(CLOSE c1)

		// 2. Props — spec 230, same rule. A row with no graph_id is a default-graph
		// row (ForIndex -> 0); its graph is never looked up in Graph_KG.nodes, which
		// after spec 227 can answer two graphs for one node ID (FR-011).
		&sql(DECLARE c2 CURSOR FOR SELECT s, key, val, graph_id FROM Graph_KG.rdf_props)
		&sql(OPEN c2)
		For {
			&sql(FETCH c2 INTO :s, :key, :val, :rowGraph)
			Quit:SQLCODE'=0
			Set g = ##class(Graph.KG.GraphKey).ForIndex(rowGraph)
			Set ^KG("prop", g, s, key) = val
		}
		&sql(CLOSE c2)

		// 3. Edges — spec 214: read graph_id from rdf_edges; write to
		// ^KG("out", graph, s, p, o). The layout 214 first shipped carried a 0 between
		// the graph and the subject; it does not any more, and a reader still passing
		// that 0 finds nothing and reports zero rather than erroring (FR-015).
		Set count = 0
		&sql(DECLARE c3 CURSOR FOR SELECT s, p, o_id, qualifiers, graph_id FROM Graph_KG.rdf_edges)
		&sql(OPEN c3)
		For {
			&sql(FETCH c3 INTO :s, :p, :o, :qual, :edgeGraph)
			Quit:SQLCODE'=0
			If (s = "") || (p = "") || (o = "") Continue
			// graph_id is '' (empty) for the default graph (spec 214 FR-003 sentinel).
			// IRIS SQL %GetData returns $C(0) for empty-string VARCHAR NOT NULL; ForIndex
			// strips control characters, so default and named graphs stay distinct.
			Set g = ##class(Graph.KG.GraphKey).ForIndex(edgeGraph)
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

/// Build2HopStats — per graph, an upper bound on two-hop reach: for each
/// (graph, src, pred) the sum of the middle nodes' out-degree on that predicate.
///
/// Spec 230: the sum used to run over every graph's "degp" and the entry was keyed by
/// node and predicate alone, so a graph holding one edge could report a neighbour
/// count borrowed from another graph's copy of the same node ID — and a per-graph
/// erase could not reach the entry to correct it (FR-010).
ClassMethod Build2HopStats() As %Integer
{
	Kill ^KG("deg2p")
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
				Set sum = 0
				Set mid = ""
				For {
					Set mid = $Order(^KG("out", g, src, pred, mid))
					Quit:mid=""
					// This graph's degp only: the count belongs to the graph whose
					// edges produced it.
					Set sum = sum + $Get(^KG("degp", g, mid, pred), 0)
				}
				If sum > 0 {
					Set ^KG("deg2p", g, src, pred) = sum
					Set cnt = cnt + 1
				}
			}
		}
	}
	Return cnt
}

/// Build2HopExactStats — per graph, the exact number of distinct nodes reachable in two
/// hops on a predicate, counting each node once however many paths lead to it.
///
/// Spec 230: this is computed here for every graph rather than taken from the Arno
/// sketch. Arno counts over ^NKG, whose node interning has no graph dimension, so its
/// numbers cannot be attributed to a graph at all (FR-011) — it writes
/// ^KG("deg2p_exact_merged") and a caller that wants the merged number reads it there.
/// The acceleration is lost for the scoped store; the alternative was a scoped reader
/// answering from a merged count, which is the leak this story closes.
ClassMethod Build2HopExactStats() As %Integer
{
	Kill ^KG("deg2p_exact")
	// Best effort, and its result is not consulted: the merged sketch is kept current
	// for the callers that read it, but it cannot fill the scoped store.
	Try { Do ##class(Graph.KG.NKGAccel).Build2HopExact() } Catch {}
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
					// The second hop stays inside graph g. The walk it replaces read
					// ^KG("out", gg, 0, mid, ...) — one subscript more than the writer
					// sets, so it found nothing in every graph and every exact count
					// came back 0 (FR-015).
					Set o2 = ""
					For {
						Set o2 = $Order(^KG("out", g, mid, pred, o2))
						Quit:o2=""
						If '$Data(^||h2s(o2)) {
							Set ^||h2s(o2) = "" Set exact = exact + 1
						}
					}
				}
				Kill ^||h2s
				If exact > 0 {
					Set ^KG("deg2p_exact", g, src, pred) = exact
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
