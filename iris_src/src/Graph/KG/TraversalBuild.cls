/// Graph.KG.TraversalBuild — (re)build ^KG and ^NKG adjacency indexes.
///
/// Every node store is graph-keyed, and the graph key is the first subscript after
/// the store name:
///
///   ^KG("out",   graph, s, p, o)      ^KG("in",    graph, o, p, s)
///   ^KG("gen",   graph, s)            ^KG("degc",  graph, s, key)
///   ^KG("label", graph, label, s)
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
/// Every graph is read off the row that produced the entry — rdf_edges and
/// rdf_labels each carry graph_id — and a row with none is a default-graph row.
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
		// (Adjacency.RemoveAll: out, in, gen, degc), plus "label" — and the retired
		// "prop" store, emptied once more as legacy clean-up and never refilled.
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
		// %BuildIndices after this transaction (their PurgeIndex empties them); then nothing is refilled here.
		Set tIndexed = ##class(Graph.KG.Adjacency).Indexed()
		Kill ^KG("prop")
		If 'tIndexed {
			Kill ^KG("label")
			Set tSC = ##class(Graph.KG.Adjacency).RemoveAll()
			$$$ThrowOnError(tSC)
		}

		// 1. Labels — spec 230: the graph key leads, read off the row's own graph_id.
		// (Dynamic SQL for the scan below: a static &sql naming graph_id cannot compile while rdf_labels still has
		// the 3.2.0 shape, which left this class half-compiled in a pre-migration namespace and made the upgrade's
		// ALTER TABLE wait out IRIS's 120 s lock timeout.)
		Set tLblStmt = ##class(%SQL.Statement).%New()
		Set tLblSC = tLblStmt.%Prepare("SELECT s, label, graph_id FROM Graph_KG.rdf_labels")
		$$$ThrowOnError(tLblSC)
		Set tLblRS = tLblStmt.%Execute()
		While ('tIndexed) && (tLblRS.%Next()) {
			Set s = tLblRS.%Get("s"), label = tLblRS.%Get("label"), rowGraph = tLblRS.%Get("graph_id")
			Set g = ##class(Graph.KG.GraphKey).ForIndex(rowGraph)
			Set ^KG("label", g, label, s) = ""
		}

		// 2. Props: none. Spec 252: Graph.KG.Subgraph reads rdf_props; ^KG("prop") is a retired store, emptied above.

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
			// Parallel jobs: one process runs every row's callback in turn (SNB SF1: 701 s for the edge index); 8 jobs
			// built SF0.1 3.6x faster with byte-identical out / in.
			Set tCPU = ##class(%SYSTEM.Util).NumberOfCPUs()
			Set tJobs = $Select(tCPU >= 16: 8, tCPU >= 4: tCPU \ 2, 1: 1)
			Set tSC = $ClassMethod("Graph.KG.rdfedges", "%BuildIndices", $ListBuild("idxedgesadj"), 1, 0, "", "", "", "", 0, tJobs)
			$$$ThrowOnError(tSC)
			Set tSC = $ClassMethod("Graph.KG.rdflabels", "%BuildIndices", $ListBuild("idxlabelsadj"), 1, 0, "", "", "", "", 0, tJobs)
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

		// ^KG is rebuilt and committed; ^NKG is rebuilt from it, and a failure there is BuildKG's failure (4.6.0
		// R152). It was written to messages.log and BuildKG answered $$$OK, so engine.sync() reported success over
		// an ^NKG as stale as before the call. BuildNKG returns a status and also throws.
		Try { Set tNKG = ..BuildNKG() } Catch buildEx { Set tNKG = buildEx.AsStatus() }
		If $$$ISERR(tNKG) {
			Return $$$ERROR($$$GeneralError, "BuildKG rebuilt ^KG but not ^NKG: "_$System.Status.GetErrorText(tNKG))
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

/// ^NKG("$meta","nodeCount") is the INTERNING HIGH-WATER MARK: the next index InternNode
/// hands out. It is never lowered (lowering it would give a live index to a new name), so once
/// a rebuild has removed dead names it exceeds the number of nodes, and indices below it can be
/// unassigned (gaps). It answers "how many indices exist", not "how many nodes": for that read
/// NKGLiveNodeCount.
ClassMethod NKGNodeCount() As %Integer
{
	Return +$Get(^NKG("$meta", "nodeCount"), 0)
}

/// The number of nodes with at least one row, as the last rewrite counted them
/// (^NKG("$meta","liveNodes")); the incremental writers do not maintain it, so it is as of that
/// rewrite. Falls back to nodeCount for an ^NKG no rewrite has counted (a fresh build counts it).
ClassMethod NKGLiveNodeCount() As %Integer
{
	Return +$Get(^NKG("$meta", "liveNodes"), $Get(^NKG("$meta", "nodeCount"), 0))
}

/// Seconds BuildNKG waits for another build's lock on ^IVG.NKGBuild.
Parameter NKGBUILDWAIT = 900;

/// BuildNKG rewrites ^NKG in place from the merged ^KG("out") view (every graph
/// subscript; ^NKG has no graph dimension). Spec 260 D-NKG-SWAP; it fixes
/// D-NKG-LIVE-READER: the build used to DropNKG and rebuild, and the eighteen classes that
/// read ^NKG hold no lock, so a reader during the rebuild saw an incomplete ^NKG (lab, 1.9M
/// edges: 92 % of the reads of a node interned last were "no such node" or, worse, "node with
/// no edge rows", for 155 s of a 167 s build).
///
/// THE RULE: never kill; rewrite, then sweep.
///   1. Raise $meta("version") and mark the rewrite: ^NKG("$meta","rebuild") =
///      $ListBuild(pid, Graph-Version Token at the start, phase, progress).
///   2. Walk ^KG("out", g, s, p, o) graph by graph. Every -1/-2 row, -3 degree and dictionary
///      entry that is missing or different is Set; one that is right is not touched. Names
///      keep their index: an interned name is never re-interned, a new name is interned as
///      Adjacency.Put does (GraphIndex.InternNode / InternLabel); the reserved labels
///      out/in/deg stay 0/1/2. The walk remembers what it wrote in process-private globals.
///   3. Sweep what the walk did not write: -1/-2 rows, -3 degrees, $agg sketches, node and
///      label dictionary entries. Each sweep kills, then re-reads ^KG and puts back what ^KG
///      still holds: the writers (Adjacency.Put) write ^KG before ^NKG, so an edge, node or
///      label they added during the rebuild is never lost. Sketches cannot lose an element, so
///      each (source, label) sketch is recomputed from the surviving rows and Set.
///   4. Set the edge count (the rows the sweep counted, plus any Put while it ran) and
///      $meta("builtFrom") (the token the walk started from), drop the mark, raise the version
///      again. The version says when the sweep finished.
///
/// What a reader sees: at every instant a mix of rows of the graph before and after, never an
/// empty ^NKG; a row removed from ^KG can linger until the sweep reaches it (stale, never
/// invented). nodeCount and labelCount are the interning counters (the next index) and are
/// never lowered: lowering one would hand a live index to a new name.
///
/// One build at a time: the lock ^IVG.NKGBuild, exclusive, waited for NKGBUILDWAIT seconds.
/// Not the writers' lock ^IVG.AdjW: every writer holds it shared, so taking it exclusive would
/// stall each Put for the whole build, and taking it shared would not exclude a second build.
///
/// A rewrite killed part-way leaves the mark and a valid mix; the next build starts over. A
/// concurrent GraphIndex.DropNKG (Eraser) kills the mark, which the rewrite checks at every
/// source: it then starts again on the emptied ^NKG (three attempts, then an error).
ClassMethod BuildNKG() As %Status
{
	Try { Do ##class(Graph.KG.NKGAccel).InvalidateAdjCache() } Catch invEx {
		Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.Traversal: InvalidateAdjCache failed: "_invEx.DisplayString(), 0, 1)
	}
	Lock +^IVG.NKGBuild:..#NKGBUILDWAIT
	If '$Test {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "BuildNKG: another ^NKG build held ^IVG.NKGBuild for "_..#NKGBUILDWAIT_" seconds"))
	}
	Try {
		Set tSC = ..RewriteNKG()
	} Catch ex {
		Set tSC = ex.AsStatus()
	}
	Kill ^||IVG.NKGSeen, ^||IVG.NKGDeg, ^||IVG.NKGLab
	Lock -^IVG.NKGBuild
	$$$ThrowOnError(tSC)
	// the cached adjacency may have been filled from the mix during the rewrite
	Try { Do ##class(Graph.KG.NKGAccel).InvalidateAdjCache() } Catch invEx2 {
		Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.Traversal: InvalidateAdjCache failed: "_invEx2.DisplayString(), 0, 1)
	}
	Do ..Build2HopStats()
	Do ..Build2HopExactStats()
	Return $$$OK
}

/// Rewrite passes until one completes without a concurrent DropNKG.
ClassMethod RewriteNKG() As %Status [ Private ]
{
	For tTry = 1:1:3 {
		If ..RewritePass() {
			Return $$$OK
		}
	}
	Return $$$ERROR($$$GeneralError, "BuildNKG: ^NKG was dropped (GraphIndex.DropNKG) during each of 3 rewrites")
}

/// One rewrite: walk, sweep, counts. 0 when ^NKG was dropped under it (the mark is gone).
///   ^||IVG.NKGSeen(sIdx, -(pIdx+1), oIdx)  every edge the walk (or a sweep's re-check) kept
///   ^||IVG.NKGDeg(sIdx)                     every source whose merged degree the walk set
///   ^||IVG.NKGLab(-(pIdx+1))                every label a surviving row uses
ClassMethod RewritePass() As %Boolean [ Private ]
{
	Kill ^||IVG.NKGSeen, ^||IVG.NKGDeg, ^||IVG.NKGLab
	Set tRemove = ..DictSweepAllowed()
	Set tToken = +$Get(^KG("__version"))
	Set tmp = $Increment(^NKG("$meta", "version"))
	// answered by this pass's sweep; a named graph's last edge removed from here on marks it again (Adjacency)
	Kill ^NKG("$meta", "stale")
	Set ^NKG("$meta", "rebuild") = $ListBuild($Job, tToken, "walk", 0)
	Do ..GuardCounters()
	Set tN = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set src = ""
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:src=""
			Return:'$Data(^NKG("$meta", "rebuild")) 0
			Do ..WalkSource(g, src)
			Set tN = tN + 1
			If tN # 1000 = 0 {
				Set ^NKG("$meta", "rebuild") = $ListBuild($Job, tToken, "walk", tN)
			}
		}
	}
	Return:'$Data(^NKG("$meta", "rebuild")) 0
	Set ^NKG("$meta", "rebuild") = $ListBuild($Job, tToken, "sweep", 0)
	Set tE0 = +$Get(^NKG("$meta", "edgeCount"))
	Set tEdges = ..SweepEdges(tToken)
	Return:tEdges<0 0
	Do ..SweepMirrors()
	Do ..SweepDegrees()
	Do ..SweepSketches()
	Set tLiveNodes = ..SweepNodes(tRemove)
	Set tLiveLabels = ..SweepLabels(tRemove)
	Return:'$Data(^NKG("$meta", "rebuild")) 0
	// The rows the sweep counted, plus what Put added while it ran: a Set here would drop the count of
	// an edge Put wrote behind the sweep. A Put in front of it is counted twice; the count errs high, as
	// Remove (which never counts down) already makes it, never low.
	Set tmp = $Increment(^NKG("$meta", "edgeCount"), tEdges - tE0)
	Set ^NKG("$meta", "builtFrom") = tToken
	Set ^NKG("$meta", "dictSwept") = tRemove
	Set ^NKG("$meta", "liveNodes") = tLiveNodes
	Set ^NKG("$meta", "liveLabels") = tLiveLabels
	Kill ^NKG("$meta", "rebuild")
	// Built from ^KG: an erase's invalidation (EnsureNKG) is answered.
	Kill ^NKG("$meta", "invalidated")
	Set tmp = $Increment(^NKG("$meta", "version"))
	Return 1
}

/// 1 when ^NKG has a dictionary for an algorithm reader to read, 0 when it is not built.
///
/// An erase that may have changed what ^NKG holds drops it and, when it had been built,
/// marks it ^NKG("$meta","invalidated") (the Eraser's InvalidateDerived). A reader that
/// finds that mark rebuilds ^NKG here, by BuildNKG from ^KG, before it reads: ^KG is
/// current after every write (ADR-0008), so the rebuild is what sync() would build. Before,
/// every CALL of an ^NKG procedure failed with "^NKG not built" after an erase, until the
/// caller ran sync() (4.6.0 R268). The first read after one or more erases pays one build;
/// the erase itself stays the size of what it removes.
///
/// An ^NKG that was never built, or one emptied by GraphIndex.DropNKG alone, is left as it
/// is: building it is sync()'s, as before. One marked ^NKG("$meta","stale") (a named graph's
/// last edge removed, Graph.KG.Adjacency.MarkGraphGone) is rebuilt here too (4.6.0 R274). Raises when the build fails, so the reader reports
/// why and not "not built".
///
/// The build lock is taken here so that readers queued behind one rebuild do not each
/// rebuild again: the second finds the dictionary there under the lock.
ClassMethod EnsureNKG() As %Boolean
{
	// ^NKG("$meta","stale"): a named graph's last edge went (Graph.KG.Adjacency), and its nodes are still interned,
	// where an algorithm over ^NKG answers them for the default graph; the rebuild's sweep drops them (4.6.0 R274)
	Set tStale = +$Get(^NKG("$meta", "stale"))
	If ($Order(^NKG("$NI", "")) '= "") && 'tStale {
		Return 1
	}
	If '$Get(^NKG("$meta", "invalidated")) && 'tStale {
		Return 0
	}
	Lock +^IVG.NKGBuild:..#NKGBUILDWAIT
	If '$Test {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "EnsureNKG: another ^NKG build held ^IVG.NKGBuild for "_..#NKGBUILDWAIT_" seconds"))
	}
	Try {
		If $Get(^NKG("$meta", "stale")) || (($Order(^NKG("$NI", "")) = "") && $Get(^NKG("$meta", "invalidated"))) {
			$$$ThrowOnError(..BuildNKG())
		}
	} Catch tEx {
		Lock -^IVG.NKGBuild
		Throw tEx
	}
	Lock -^IVG.NKGBuild
	Return ($Order(^NKG("$NI", "")) '= "")
}

/// The reserved labels at 0/1/2, and interning counters above every index in use (a counter
/// at or below one would hand that index to a new name). Counters are only ever raised.
ClassMethod GuardCounters() [ Private ]
{
	For tI = 0:1:2 {
		Set tL = $ListGet($ListBuild("out", "in", "deg"), tI + 1)
		Set:$Get(^NKG("$LS", tI))'=tL ^NKG("$LS", tI) = tL
		Set:$Get(^NKG("$LI", tL))'=tI ^NKG("$LI", tL) = tI
	}
	Set tTop = $Order(^NKG("$LS", ""), -1)
	If +$Get(^NKG("$meta", "labelCount")) <= tTop {
		Set ^NKG("$meta", "labelCount") = tTop + 1
	}
	Set tTop = $Order(^NKG("$ND", ""), -1)
	If (tTop '= "") && (+$Get(^NKG("$meta", "nodeCount")) <= tTop) {
		Set ^NKG("$meta", "nodeCount") = tTop + 1
	}
}

/// The walk for one source of one graph. A triple several graphs hold takes the weight of
/// the first graph in key order (the default graph, 0, first), as a fresh build does.
ClassMethod WalkSource(pG, pSrc As %String) [ Private ]
{
	Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(pSrc)
	Set pred = ""
	For {
		Set pred = $Order(^KG("out", pG, pSrc, pred))
		Quit:pred=""
		Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(pred), tPk = -(pIdx + 1)
		Set dst = ""
		For {
			Set dst = $Order(^KG("out", pG, pSrc, pred, dst), 1, weight)
			Quit:dst=""
			Set oIdx = ##class(Graph.KG.GraphIndex).InternNode(dst)
			Continue:$Data(^||IVG.NKGSeen(sIdx, tPk, oIdx))
			Set ^||IVG.NKGSeen(sIdx, tPk, oIdx) = ""
			If ($Get(^NKG(-1, sIdx, tPk, oIdx), $Char(0)) = weight) && ($Get(^NKG(-2, oIdx, tPk, sIdx), $Char(0)) = weight) {
				Continue
			}
			Set ^NKG(-1, sIdx, tPk, oIdx) = weight
			Set ^NKG(-2, oIdx, tPk, sIdx) = weight
			// A Remove between the read of ^KG above and the Set: Remove kills ^KG before ^NKG, so
			// reading ^KG again after the Set either sees the edge (and Remove's own kill of the row
			// is still to come) or does not (and the row goes now).
			If '..HeldAnywhere(pSrc, pred, dst) {
				Kill ^NKG(-1, sIdx, tPk, oIdx), ^NKG(-2, oIdx, tPk, sIdx)
			}
		}
	}
	If '$Data(^||IVG.NKGDeg(sIdx)) {
		Set tDeg = ..MergedDegree(pSrc)
		Set ^||IVG.NKGDeg(sIdx) = tDeg
		If $Get(^NKG(-3, sIdx), $Char(0)) '= tDeg {
			Set ^NKG(-3, sIdx) = tDeg
		}
	}
}

/// The -1 rows: keep what the walk wrote, sweep the rest (SweepEdge), recompute each
/// (source, label) sketch from the rows that stay. Returns the edge count, -1 when ^NKG was
/// dropped under it.
ClassMethod SweepEdges(pToken) As %Integer [ Private ]
{
	Set tEdges = 0, tN = 0
	Set tS = ""
	For {
		Set tS = $Order(^NKG(-1, tS))
		Quit:tS=""
		Return:'$Data(^NKG("$meta", "rebuild")) -1
		Set tPk = ""
		For {
			Set tPk = $Order(^NKG(-1, tS, tPk))
			Quit:tPk=""
			Set tH = ##class(Graph.KG.GraphIndex).EmptyHLL(), tAny = 0
			Set tO = ""
			For {
				Set tO = $Order(^NKG(-1, tS, tPk, tO))
				Quit:tO=""
				If ('$Data(^||IVG.NKGSeen(tS, tPk, tO))) && ('..SweepEdge(tS, tPk, tO)) {
					Continue
				}
				Set tAny = 1, tEdges = tEdges + 1
				Do ..HLLAdd(.tH, tO)
			}
			Continue:'tAny
			Set ^||IVG.NKGLab(tPk) = ""
			Set tPIdx = -tPk - 1
			If $Get(^NKG("$agg", tS, tPIdx, "hll")) '= tH {
				Set ^NKG("$agg", tS, tPIdx, "hll") = tH
			}
		}
		Set tN = tN + 1
		If tN # 1000 = 0 {
			Set ^NKG("$meta", "rebuild") = $ListBuild($Job, pToken, "sweep", tN)
		}
	}
	Quit tEdges
}

/// One -1 row the walk did not write: kill it and its mirror, then put it back if ^KG holds
/// the edge now (a Put during the rebuild) under the canonical indices. 1 when it stays.
ClassMethod SweepEdge(pS, pPk, pO) As %Boolean [ Private ]
{
	Kill ^NKG(-1, pS, pPk, pO), ^NKG(-2, pO, pPk, pS)
	Set tSN = $Get(^NKG("$ND", pS)), tON = $Get(^NKG("$ND", pO)), tPN = $Get(^NKG("$LS", -pPk - 1))
	Quit:(tSN="")||(tON="")||(tPN="") 0
	Quit:($Get(^NKG("$NI", tSN))'=pS)||($Get(^NKG("$NI", tON))'=pO)||($Get(^NKG("$LI", tPN))'=(-pPk - 1)) 0
	Set tW = ..FirstWeight(tSN, tPN, tON)
	Quit:tW="" 0
	Set ^NKG(-1, pS, pPk, pO) = $List(tW), ^NKG(-2, pO, pPk, pS) = $List(tW)
	// a Remove that ran between the read and the Set: as in WalkSource
	If '..HeldAnywhere(tSN, tPN, tON) {
		Kill ^NKG(-1, pS, pPk, pO), ^NKG(-2, pO, pPk, pS)
		Quit 0
	}
	Set ^||IVG.NKGSeen(pS, pPk, pO) = ""
	Quit 1
}

/// The -2 rows with no -1 row. Put writes -1 before -2, so a mirror killed while its -1 row
/// was being written is put back from that row.
ClassMethod SweepMirrors() [ Private ]
{
	Set tO = ""
	For {
		Set tO = $Order(^NKG(-2, tO))
		Quit:tO=""
		Set tPk = ""
		For {
			Set tPk = $Order(^NKG(-2, tO, tPk))
			Quit:tPk=""
			Set tS = ""
			For {
				Set tS = $Order(^NKG(-2, tO, tPk, tS))
				Quit:tS=""
				Continue:$Data(^||IVG.NKGSeen(tS, tPk, tO))
				Continue:$Data(^NKG(-1, tS, tPk, tO))
				Kill ^NKG(-2, tO, tPk, tS)
				If $Data(^NKG(-1, tS, tPk, tO), tW) {
					Set ^NKG(-2, tO, tPk, tS) = tW
				}
			}
		}
	}
}

/// The -3 degrees of nodes the walk did not meet as a source.
ClassMethod SweepDegrees() [ Private ]
{
	Set tS = ""
	For {
		Set tS = $Order(^NKG(-3, tS))
		Quit:tS=""
		Continue:$Data(^||IVG.NKGDeg(tS))
		Kill ^NKG(-3, tS)
		Set tName = $Get(^NKG("$ND", tS))
		Continue:tName=""
		Continue:$Get(^NKG("$NI", tName))'=tS
		If ..IsSource(tName) {
			Set ^NKG(-3, tS) = ..MergedDegree(tName)
		}
	}
}

/// The sketches of (source, label) pairs that have no row left.
ClassMethod SweepSketches() [ Private ]
{
	Set tS = ""
	For {
		Set tS = $Order(^NKG("$agg", tS))
		Quit:tS=""
		Set tP = ""
		For {
			Set tP = $Order(^NKG("$agg", tS, tP))
			Quit:tP=""
			Continue:$Data(^NKG(-1, tS, -(tP + 1)))
			Kill ^NKG("$agg", tS, tP)
			// rows a Put wrote after the check: the sketch of those rows
			If $Data(^NKG(-1, tS, -(tP + 1))) {
				Set ^NKG("$agg", tS, tP, "hll") = ..SketchOf(tS, tP)
			}
		}
	}
}

/// The node dictionary. With pRemove = 1 an index with no row goes, unless ^KG holds the name
/// now, which leaves a gap below nodeCount; with pRemove = 0 (DictSweepAllowed) the dead entry
/// stays, as it does between rebuilds, and only its rows, degree and sketch are gone (swept
/// before this). Either way a name whose index has no entry goes. The per-name lock InternNode
/// takes when it allocates guards the put-back, so the two never give one name two indices.
/// Returns the number of nodes with a row.
ClassMethod SweepNodes(pRemove As %Boolean) As %Integer [ Private ]
{
	Set tLive = 0
	Set tI = ""
	For {
		Set tI = $Order(^NKG("$ND", tI), 1, tName)
		Quit:tI=""
		If $Data(^NKG(-1, tI)) || $Data(^NKG(-2, tI)) {
			Set tLive = tLive + 1
			If '$Data(^NKG("$NI", tName)) {
				Do ..PutNodeBack(tI, tName)
			}
			Continue
		}
		If 'pRemove {
			Continue
		}
		If $Get(^NKG("$NI", tName)) = tI {
			Kill ^NKG("$NI", tName)
		}
		Kill ^NKG("$ND", tI), ^NKG(-3, tI), ^NKG("$agg", tI)
		If ..NodeInKG(tName) || $Data(^NKG(-1, tI)) || $Data(^NKG(-2, tI)) {
			Do ..PutNodeBack(tI, tName)
		}
	}
	Set tName = ""
	For {
		Set tName = $Order(^NKG("$NI", tName), 1, tI)
		Quit:tName=""
		If $Get(^NKG("$ND", tI), $Char(0)) '= tName {
			Kill ^NKG("$NI", tName)
		}
	}
	Quit tLive
}

ClassMethod PutNodeBack(pI, pName As %String) [ Private ]
{
	Lock +^NKG("$NI", pName):5
	If '$Test {
		Quit
	}
	If '$Data(^NKG("$NI", pName)) {
		Set ^NKG("$ND", pI) = pName
		Set ^NKG("$NI", pName) = pI
	}
	Lock -^NKG("$NI", pName)
}

/// The label dictionary: with pRemove = 1 a label no surviving row uses goes, unless ^KG uses
/// it now (that check scans every source, and runs only for a label about to go); with
/// pRemove = 0 it stays, as for nodes. 0, 1, 2 stay. Returns the number of labels a row uses.
ClassMethod SweepLabels(pRemove As %Boolean) As %Integer [ Private ]
{
	Set tLive = 0
	Set tI = 2
	For {
		Set tI = $Order(^NKG("$LS", tI), 1, tLabel)
		Quit:tI=""
		If $Data(^||IVG.NKGLab(-(tI + 1))) {
			Set tLive = tLive + 1
			If '$Data(^NKG("$LI", tLabel)) {
				Do ..PutLabelBack(tI, tLabel)
			}
			Continue
		}
		If 'pRemove {
			Continue
		}
		If $Get(^NKG("$LI", tLabel)) = tI {
			Kill ^NKG("$LI", tLabel)
		}
		Kill ^NKG("$LS", tI)
		If ..LabelInKG(tLabel) {
			Do ..PutLabelBack(tI, tLabel)
		}
	}
	Set tLabel = ""
	For {
		Set tLabel = $Order(^NKG("$LI", tLabel), 1, tI)
		Quit:tLabel=""
		If $Get(^NKG("$LS", tI), $Char(0)) '= tLabel {
			Kill ^NKG("$LI", tLabel)
		}
	}
	Quit tLive
}

ClassMethod PutLabelBack(pI, pLabel As %String) [ Private ]
{
	Lock +^NKG("$LI", pLabel):5
	If '$Test {
		Quit
	}
	If '$Data(^NKG("$LI", pLabel)) {
		Set ^NKG("$LS", pI) = pLabel
		Set ^NKG("$LI", pLabel) = pI
	}
	Lock -^NKG("$LI", pLabel)
}

/// 1 when the rewrite may remove dead dictionary entries, leaving index gaps below nodeCount
/// (D-NKG-SWAP option D, Tom 2026-10-31). The arno-callout readers (kg_pagerank_global,
/// kg_ppr_global, kg_wcc_global, kg_cdlp_global in 1.1.0) walk 0..nodeCount-1 over $ND and
/// count an undefined entry as a node named "", so a gap is only safe where no such library
/// can read this namespace's ^NKG, or the library says it skips undefined entries:
///   - no Arno library configured in the namespace (no ^ArnoAccel("lib_path")): 1;
///   - otherwise the library's "capabilities" report must carry "nkg_dict_gaps" >= 1
///     (GapsDeclared); anything else, including a library that cannot be loaded or asked,
///     keeps the dead entries: 0.
/// Never a version number: the library declares it.
ClassMethod DictSweepAllowed() As %Boolean
{
	If '$Data(^ArnoAccel("lib_path")) {
		Return 1
	}
	Return ..GapsDeclared(..ArnoCapabilities())
}

/// The loaded library's "capabilities" report (JSON), "" when it cannot be loaded or has no
/// such entry. Loads the library into this process through ArnoAccel.IsAvailable, and puts the
/// namespace back (the library's init moves the process to USER). "Cannot tell" answers ""
/// and so keeps the dictionary: the safe direction.
ClassMethod ArnoCapabilities() As %String [ Private ]
{
	Set tNs = $NAMESPACE, tOut = ""
	Try {
		If ##class(Graph.KG.ArnoAccel).IsAvailable() {
			Set tId = $Get(^||ArnoAccel("dllid"), 0)
			Set tFn = $ZF(-4, 3, tId, "capabilities")
			If tFn '= 0 {
				Set tOut = $ZF(-5, tId, tFn)
			}
		}
	} Catch {
		Set tOut = ""
	}
	Set $NAMESPACE = tNs
	Quit tOut
}

/// 1 when the capabilities report pCaps declares that the library's readers tolerate index
/// gaps in ^NKG's dictionary: a top-level integer "nkg_dict_gaps" >= 1 ("every reader of ^NKG
/// skips an undefined $ND(i) / $LS(i) for i below nodeCount / labelCount, and reads nodeCount
/// as an index bound, not a node count"). 0 for anything else.
ClassMethod GapsDeclared(pCaps As %String) As %Boolean
{
	Set tOk = 0
	Try {
		Set tObj = ##class(%DynamicObject).%FromJSON(pCaps)
		If $IsObject(tObj) && (tObj.%GetTypeOf("nkg_dict_gaps") = "number") {
			Set tOk = (tObj."nkg_dict_gaps" >= 1)
		}
	} Catch {
		Set tOk = 0
	}
	Quit tOk
}

/// 1 when some graph's ^KG("out") holds the triple.
ClassMethod HeldAnywhere(pS As %String, pP As %String, pO As %String) As %Boolean [ Private ]
{
	Quit ..FirstWeight(pS, pP, pO) '= ""
}

/// $ListBuild(weight) of the triple in the first graph that holds it, "" when none does.
ClassMethod FirstWeight(pS As %String, pP As %String, pO As %String) As %List [ Private ]
{
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		If $Data(^KG("out", g, pS, pP, pO), tW)#2 {
			Return $ListBuild(tW)
		}
	}
	Quit ""
}

/// The merged out-degree of pSrc: each graph's count, from the degree cache while that graph
/// is warm (Graph.KG.Adj.AllWarm) and from Graph.KG.Adj.Degree otherwise. A parallel edge
/// counts once per edge.
ClassMethod MergedDegree(pSrc As %String) As %Integer [ Private ]
{
	Set tDeg = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Continue:'$Data(^KG("out", g, pSrc))
		If ##class(Graph.KG.Adj).AllWarm(g) {
			Set tDeg = tDeg + $ListGet($Get(^KG("degc", g, pSrc, $Char(1))), 2, 0)
		} Else {
			Set tDeg = tDeg + ##class(Graph.KG.Adj).Degree(g, pSrc)
		}
	}
	Quit tDeg
}

ClassMethod IsSource(pName As %String) As %Boolean [ Private ]
{
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Return:$Data(^KG("out", g, pName)) 1
	}
	Quit 0
}

ClassMethod NodeInKG(pName As %String) As %Boolean [ Private ]
{
	Return:..IsSource(pName) 1
	Set g = ""
	For {
		Set g = $Order(^KG("in", g))
		Quit:g=""
		Return:$Data(^KG("in", g, pName)) 1
	}
	Quit 0
}

ClassMethod LabelInKG(pLabel As %String) As %Boolean [ Private ]
{
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set s = ""
		For {
			Set s = $Order(^KG("out", g, s))
			Quit:s=""
			Return:$Data(^KG("out", g, s, pLabel)) 1
		}
	}
	Quit 0
}

/// The sketch of the targets of the rows (pS, label index pP).
ClassMethod SketchOf(pS, pP) As %List [ Private ]
{
	Set tH = ##class(Graph.KG.GraphIndex).EmptyHLL()
	Set tO = ""
	For {
		Set tO = $Order(^NKG(-1, pS, -(pP + 1), tO))
		Quit:tO=""
		Do ..HLLAdd(.tH, tO)
	}
	Quit tH
}

/// GraphIndex.UpdateStructuralHLL on a local sketch: the same hash, register and rank, so a
/// sketch built here equals one built edge by edge from the same indices.
ClassMethod HLLAdd(ByRef pH As %List, pIdx) [ Private ]
{
	Set tHash = $SYSTEM.Encryption.SHA1Hash(pIdx _ "")
	Set tReg = ($ASCII(tHash, 1) # 256) + 1
	Set tB = $ASCII(tHash, 2), tLz = 1
	If tB = 0 {
		Set tLz = 9
	} Else {
		While (tB # 2) = 0 { Set tLz = tLz + 1, tB = tB \ 2 }
	}
	If tLz > $List(pH, tReg) {
		Set $List(pH, tReg) = tLz
	}
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

/// The edges in ^KG("out"), every graph, stopping once maxCount is reached: one per (s, p, o) leaf, and a
/// multigraph triple's "#" count of parallel edges. Before 4.6.0 it counted source nodes.
ClassMethod KGEdgeCount(maxCount As %Integer = 10000) As %Integer
{
	Set count = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:(g = "")||(count >= maxCount)
		Set s = ""
		For {
			Set s = $Order(^KG("out", g, s))
			Quit:(s = "")||(count >= maxCount)
			Set p = ""
			For {
				Set p = $Order(^KG("out", g, s, p))
				Quit:(p = "")||(count >= maxCount)
				Set o = ""
				For {
					Set o = $Order(^KG("out", g, s, p, o))
					Quit:(o = "")||(count >= maxCount)
					Set count = count + $Get(^KG("out", g, s, p, o, "#"), 1)
				}
			}
		}
	}
	Return count
}

/// 1 when ^NKG has a dictionary, as EnsureNKG reads "built". $Data(^NKG) answered 1 after GraphIndex.DropNKG, which
/// leaves ^NKG("$meta","version") behind (4.6.0 R274).
ClassMethod NKGPopulated() As %Integer
{
	Return ($Order(^NKG("$NI", "")) '= "")
}

}
