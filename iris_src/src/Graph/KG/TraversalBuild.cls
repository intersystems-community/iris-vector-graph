/// Graph.KG.TraversalBuild — (re)build ^KG and ^NKG adjacency indexes.
///
/// Every node store is graph-keyed, and the graph key is the first subscript after
/// the store name:
///
///   ^KG("out",   graph, s, p, o)      ^KG("in",    graph, o, p, s)
///   ^KG("gen",   graph, s)            ^KG("degc",  graph, s, key)
///   ^KG("label", graph, label, s)     ^KG("prop",  graph, s, key)
///   ^KG("deg2p", graph, s, p)         ^KG("deg2p_exact", graph, s, p)
///
/// out and in are written by Graph.KG.Adjacency alone (BeginRebuild, FillEdge, FillTriple,
/// EndRebuild); gen and degc are the degree cache (spec 248), which BuildKG leaves warm.
/// The stored counters ^KG("deg") and ^KG("degp") no longer exist.
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
	Set tLocked = 0
	Try {
		// The shared writer lock on every graph, held from here to the end of the rebuild
		// (Graph.KG.Adjacency.BeginRebuild), so that no reader caches a count taken from
		// the half-built tree. Taken before the transaction: it is not tied to it.
		Set tSC = ##class(Graph.KG.Adjacency).BeginRebuild()
		$$$ThrowOnError(tSC)
		Set tLocked = 1
		TStart
		// Kill only the subscripts we rebuild. Spec 206: do NOT Kill ^KG bare —
		// that would destroy temporal subscripts. Spec 214: kill full "out"/"in"
		// trees (all graph subscripts), now with the degree cache that rides on them
		// (Adjacency.RemoveAll: out, in, gen, degc), plus "label"/"prop".
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
		// adjacency intact. The cache is killed with the adjacency, so a partial
		// rebuild is cold, never wrong.
		// Spec 252: with the adjacency functional indexes, out / in / label are rebuilt from the rows by
		// %BuildIndices after this transaction (their PurgeIndex empties them); only prop is refilled here.
		Set tIndexed = ##class(Graph.KG.Adjacency).Indexed()
		Kill ^KG("prop")
		If 'tIndexed {
			Kill ^KG("label")
			Set tSC = ##class(Graph.KG.Adjacency).RemoveAll()
			$$$ThrowOnError(tSC)
		}

		// 1. Labels — spec 230: the graph key leads, read off the row's own graph_id.
		// (Dynamic SQL for the two scans below: a static &sql naming graph_id cannot compile while rdf_labels /
		// rdf_props still have the 3.2.0 shape, which left this class half-compiled in a pre-migration namespace and made
		// the upgrade's ALTER TABLE wait out IRIS's 120 s lock timeout.)
		Set tLblStmt = ##class(%SQL.Statement).%New()
		Set tLblSC = tLblStmt.%Prepare("SELECT s, label, graph_id FROM Graph_KG.rdf_labels")
		$$$ThrowOnError(tLblSC)
		Set tLblRS = tLblStmt.%Execute()
		While ('tIndexed) && (tLblRS.%Next()) {
			Set s = tLblRS.%Get("s"), label = tLblRS.%Get("label"), rowGraph = tLblRS.%Get("graph_id")
			Set g = ##class(Graph.KG.GraphKey).ForIndex(rowGraph)
			Set ^KG("label", g, label, s) = ""
		}

		// 2. Props — spec 230, same rule. A row with no graph_id is a default-graph
		// row (ForIndex -> 0); its graph is never looked up in Graph_KG.nodes, which
		// after spec 227 can answer two graphs for one node ID (FR-011).
		Set tPropStmt = ##class(%SQL.Statement).%New()
		Set tPropSC = tPropStmt.%Prepare("SELECT s, key, val, graph_id FROM Graph_KG.rdf_props")
		$$$ThrowOnError(tPropSC)
		Set tPropRS = tPropStmt.%Execute()
		While tPropRS.%Next() {
			Set s = tPropRS.%Get("s"), key = tPropRS.%Get("key"), val = tPropRS.%Get("val"), rowGraph = tPropRS.%Get("graph_id")
			Set g = ##class(Graph.KG.GraphKey).ForIndex(rowGraph)
			Set ^KG("prop", g, s, key) = val
		}

		// 3. Edges — spec 214: read graph_id from rdf_edges; write to
		// ^KG("out", graph, s, p, o). The layout 214 first shipped carried a 0 between
		// the graph and the subject; it does not any more, and a reader still passing
		// that 0 finds nothing and reports zero rather than erroring (FR-015).
		Set count = 0
		// Spec 234: once any graph is a multigraph, rebuild from the keyed rows
		// instead. With no graph in that mode the loop below runs exactly as before.
		If tIndexed {
			// Spec 252: the edge pass is the index build below.
		} ElseIf $Data(^IVG.GraphMode) {
			Set count = ..BuildKGKeyed()
		} Else {
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
				Do ##class(Graph.KG.Adjacency).FillEdge(g, s, p, o, weight)
				Set count = count + 1
				If count # 50000 = 0 { TCommit  TStart }
			}
			&sql(CLOSE c3)
		}
		TCommit
		If tIndexed {
			// Purge and refill out / in (and their degree cache) and label from every row (spec 252).
			// $ClassMethod: the table classes are DDL-generated, and this class compiles before they exist.
			Set tSC = $ClassMethod("Graph.KG.rdfedges", "%BuildIndices", $ListBuild("idxedgesadj"), 1)
			$$$ThrowOnError(tSC)
			Set tSC = $ClassMethod("Graph.KG.rdflabels", "%BuildIndices", $ListBuild("idxlabelsadj"), 1)
			$$$ThrowOnError(tSC)
		}
		// Release the rebuild lock and leave the degree cache warm: this walk has just
		// visited every edge, so the readers and Build2HopStats find an O(1) answer
		// (Adjacency.EndRebuild -> Adj.WarmCache).
		Set tLocked = 0
		Set tSC = ##class(Graph.KG.Adjacency).EndRebuild(1)
		$$$ThrowOnError(tSC)
		// A rebuilt ^KG is new content to ^ArnoKG's cached snapshot (Adjacency.Put).
		Set tmp = $Increment(^KG("__version"))

		Try { Do ..BuildNKG() } Catch buildEx {
			Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.Traversal: BuildNKG skipped: "_buildEx.DisplayString(), 0, 1)
		}
		Return $$$OK
	} Catch ex {
		If $TLevel > 0 { TRollback }
		If tLocked {
			Do ##class(Graph.KG.Adjacency).EndRebuild(0)
		}
		// A failed rebuild has already shown readers a partial ^KG (reads are
		// uncommitted in IRIS), and the rollback does not restore what they saw.
		Set tmp = $Increment(^KG("__version"))
		Return ex.AsStatus()
	}
}

/// BuildKG's edge pass when some graph is a multigraph (spec 234). Rows arrive
/// ordered by triple and ekey, so each triple is written once: the leaf takes the
/// lowest ekey's weight, and a multigraph triple with two or more edges also gets
/// its children and "#" counter — the layout Adjacency.PutKeyed keeps incrementally
/// (Adjacency.FillTriple writes it). A graph not in multigraph mode gets leaves only,
/// whatever its rows. Dynamic SQL, because
/// ekey is not a column until the 234 migration has run. Returns the row count.
ClassMethod BuildKGKeyed() As %Integer [ Private ]
{
	Set tSQL = "SELECT s, p, o_id, qualifiers, graph_id, ekey FROM Graph_KG.rdf_edges"
	Set tSQL = tSQL_" ORDER BY graph_id, s, p, o_id, ekey"
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, tSQL)
	If tRS.%SQLCODE < 0 {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "BuildKG keyed SQLCODE "_tRS.%SQLCODE_": "_tRS.%Message))
	}
	Set tCount = 0
	Set (tG, tS, tP, tO) = "", tN = 0
	Kill tEdges
	For {
		Set tMore = tRS.%Next()
		If tMore {
			Set s = tRS.%GetData(1), p = tRS.%GetData(2), o = tRS.%GetData(3)
			If (s = "") || (p = "") || (o = "") Continue
			Set g = ##class(Graph.KG.GraphKey).ForIndex(tRS.%GetData(5))
			Set tSame = (tN > 0) && (g = tG) && (s = tS) && (p = tP) && (o = tO)
		}
		If (tN > 0) && ('tMore || 'tSame) {
			Do ##class(Graph.KG.Adjacency).FillTriple(tG, tS, tP, tO, .tEdges, tN)
			Kill tEdges
			Set tN = 0
		}
		Quit:'tMore
		Set tG = g, tS = s, tP = p, tO = o
		Set weight = 1.0
		Set qual = tRS.%GetData(4)
		If qual '= "" {
			Try {
				Set obj = $Select($IsObject(qual): qual, 1: ##class(%DynamicObject).%FromJSON(qual))
				If ($IsObject(obj)) && (obj.weight '= "") Set weight = obj.weight
			} Catch {}
		}
		Set tN = tN + 1
		Set tEdges(tN) = $ListBuild(+tRS.%GetData(6), weight)
		Set tCount = tCount + 1
		If tCount # 50000 = 0 { TCommit  TStart }
	}
	Quit tCount
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
	Do ##class(Graph.KG.GraphIndex).DropNKG()
	Do ##class(Graph.KG.GraphIndex).InitStructuralLabels()
	Set edgeCount = 0
	// Iterate all graph subscripts (merged view). ^NKG(-3, s) is the merged out-degree
	// of s over every graph. Each graph adds its own count, from Graph.KG.Adj.Degree: an
	// O(1) read of the degree cache, which BuildKG left warm (spec 248), and a recount
	// that refills it when the entry is stale. DropNKG above kills ^NKG, so the sum starts
	// at zero. A parallel edge counts once per edge.
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set tWarm = ##class(Graph.KG.Adj).AllWarm(g)
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
			If tWarm {
				Set tDeg = $ListGet($Get(^KG("degc", g, src, $Char(1))), 2, 0)
			} Else {
				Set tDeg = ##class(Graph.KG.Adj).Degree(g, src)
			}
			Set ^NKG(-3, sIdx) = $Get(^NKG(-3, sIdx), 0) + tDeg
		}
	}
	Set ^NKG("$meta", "edgeCount") = edgeCount
	Set tmp = $Increment(^NKG("$meta", "version"))
	Do ..Build2HopStats()
	Do ..Build2HopExactStats()
	Return $$$OK
}

/// Fill the degree cache for every source of every graph (Graph.KG.Adj.WarmCache). It used
/// to rebuild the stored counters ^KG("deg") and ^KG("degp"), which no longer exist; the
/// name stays for the callers that ask for it. Returns the number of sources filled.
ClassMethod BackfillDegp() As %Integer
{
	Quit ##class(Graph.KG.Adj).WarmCache()
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
	// Spec 248: the sum over a source's middle nodes reads each middle node's out-degree on
	// the predicate from the degree cache, inline: BuildKG left every entry warm, so this is
	// two global reads per middle node (the baseline read one stored counter). A missing or
	// stale entry goes through Graph.KG.Adj.Degree, which recounts and refills it. A middle
	// node with no out entry on the predicate contributes nothing and is not looked up.
	Kill ^KG("deg2p")
	Set cnt = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		// One check per graph: while the whole-graph stamp holds (Graph.KG.Adj.AllWarm) every cache
		// entry is valid and an absent entry is zero, so each middle node costs one global read, as
		// the stored degp did. Otherwise each node is validated one by one.
		Set tWarm = ##class(Graph.KG.Adj).AllWarm(g)
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
					If tWarm {
						// This graph's count only: it belongs to the graph whose edges produced it.
						Set sum = sum + $ListGet($Get(^KG("degc", g, mid, pred)), 2, 0)
						Continue
					}
					Continue:'$Data(^KG("out", g, mid, pred))
					Set tC = $Get(^KG("degc", g, mid, pred))
					If (tC '= "") && ($List(tC, 1) = +$Get(^KG("gen", g, mid))) {
						Set sum = sum + $List(tC, 2)
					} Else {
						Set sum = sum + ##class(Graph.KG.Adj).Degree(g, mid, pred)
					}
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
	// The accelerator's exact-stats build loads the callout, whose init entry moves the
	// process to USER. Put the caller's namespace back: BuildKG, BuildNKG and sync() all
	// pass through here, and everything after them would run in USER (spec 236 R3).
	Set tNs = $NAMESPACE
	Try { Do ##class(Graph.KG.NKGAccel).Build2HopExact() } Catch {}
	Set $NAMESPACE = tNs
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
