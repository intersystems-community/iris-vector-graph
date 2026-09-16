/// Graph.KG.EdgeScan — adjacency-global maintenance + scan procedures.
///
/// ============================================================================
/// ^KG ADJACENCY LAYOUT OF RECORD (spec 214 — named-graph aware)
/// ============================================================================
///
///     ^KG("out", graph, s, p, o) = weight
///     ^KG("in",  graph, o, p, s) = weight
///     ^KG("deg", graph, s)           = out-degree
///     ^KG("degp", graph, s, p)       = per-predicate out-degree
///
/// graph is the derived index key, not the graph name: integer 0 for the default
/// graph, the name itself otherwise. Derive it with Graph.KG.GraphKey.ForIndex()
/// (ADR-0001, ADR-0003). The earlier note here said "" was the default sentinel;
/// it is not, and never was in this global — WriteAdjacency has always defaulted
/// the parameter to 0.
///
/// Merged-view traversal iterates the full ^KG("out") tree (all graph subscripts).
/// Graph-filtered traversal uses a fixed graph subscript. KNOWN AMBIGUITY:
/// MatchEdges reads 0 as "every graph", while WriteAdjacency and DeleteAdjacency
/// read 0 as "the default graph". MatchEdges therefore cannot ask for the default
/// graph alone and answers across graphs when asked for it.
///
/// ^NKG is per-identifier (no graph dimension); InternNode unchanged.
/// ============================================================================
Class Graph.KG.EdgeScan Extends %RegisteredObject
{

/// Scan adjacency globals. graph="" returns merged view across all graphs.
ClassMethod MatchEdges(sourceId As %String, predicate As %String, shard As %Integer = 0, graph = 0) As %String [ SqlProc, SqlName = MatchEdges ]
{
	Set results = ##class(%DynamicArray).%New()
	Set sourceId = $Get(sourceId, "")
	Set predicate = $Get(predicate, "")

	// Build the list of graph subscripts to iterate
	Kill tGraphs
	// graph=0 means merged view (all graphs); non-0 = specific named graph
	If (graph '= 0) && (graph '= "") {
		Set tGraphs(graph) = ""
	} Else {
		Set g = ""
		For {
			Set g = $Order(^KG("out", g))
			Quit:g=""
			Set tGraphs(g) = ""
		}
	}

	Set g = ""
	For {
		Set g = $Order(tGraphs(g))
		Quit:g=""
		If sourceId '= "" {
			If $Length(predicate) > 0 {
				Set o = ""
				For {
					Set o = $Order(^KG("out", g, sourceId, predicate, o))
					Quit:o=""
					Set w = $Get(^KG("out", g, sourceId, predicate, o), 1.0)
					Set entry = ##class(%DynamicObject).%New()
					Do entry.%Set("s", sourceId) Do entry.%Set("p", predicate) Do entry.%Set("o", o) Do entry.%Set("w", +w)
					Do results.%Push(entry)
				}
			} Else {
				Set p = ""
				For {
					Set p = $Order(^KG("out", g, sourceId, p))
					Quit:p=""
					Set o = ""
					For {
						Set o = $Order(^KG("out", g, sourceId, p, o))
						Quit:o=""
						Set w = $Get(^KG("out", g, sourceId, p, o), 1.0)
						Set entry = ##class(%DynamicObject).%New()
						Do entry.%Set("s", sourceId) Do entry.%Set("p", p) Do entry.%Set("o", o) Do entry.%Set("w", +w)
						Do results.%Push(entry)
					}
				}
			}
		} Else {
			Set s = ""
			For {
				Set s = $Order(^KG("out", g, s))
				Quit:s=""
				Set p = ""
				For {
					Set p = $Order(^KG("out", g, s, p))
					Quit:p=""
					If ($Length(predicate) > 0) && (p '= predicate) Continue
					Set o = ""
					For {
						Set o = $Order(^KG("out", g, s, p, o))
						Quit:o=""
						Set w = $Get(^KG("out", g, s, p, o), 1.0)
						Set entry = ##class(%DynamicObject).%New()
						Do entry.%Set("s", s) Do entry.%Set("p", p) Do entry.%Set("o", o) Do entry.%Set("w", +w)
						Do results.%Push(entry)
					}
				}
			}
		}
	}

	Return results.%ToJSON()
}

/// Write adjacency entries for one edge. graph="" = default graph (spec 214).
ClassMethod WriteAdjacency(s As %String, p As %String, o As %String, w As %String = "1.0", graph = 0)
{
	Set ^KG("out", graph, s, p, o) = +w
	Set ^KG("in", graph, o, p, s) = +w
	Set tmp = $Increment(^KG("deg", graph, s))
	Set tmp = $Increment(^KG("degp", graph, s, p))
	// ^NKG is per-identifier (no graph dimension); unchanged by spec 214
	If $Data(^NKG("$meta", "nodeCount")) {
		Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(s)
		Set oIdx = ##class(Graph.KG.GraphIndex).InternNode(o)
		Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(p)
		If '$Data(^NKG(-1, sIdx, -(pIdx+1), oIdx)) {
			Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = +w
			Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = +w
			Set tmp = $Increment(^NKG(-3, sIdx))
			Set tmp = $Increment(^NKG("$meta", "edgeCount"))
		}
		Set tmp = $Increment(^NKG("$meta", "version"))
		Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(sIdx, pIdx, oIdx)
	}
}

/// Write the four structural adjacency stores for one edge, without interning.
///
/// WriteAdjacency's twin, for callers whose edges must be visible to the ^KG
/// readers but are deliberately absent from ^NKG: a temporal edge keeps a
/// structural shadow so MatchEdges and the centrality readers see it, and is
/// interned only by a BuildNKG. That is the ADJACENCY CONTRACT documented on
/// create_edge_temporal, and calling WriteAdjacency instead would quietly break
/// it by interning on every temporal insert.
///
/// Idempotent in the counters, which WriteAdjacency is not, because several
/// temporal edges at different timestamps are one structural edge and ^KG("deg")
/// counts structural edges. TemporalIndex used to write ^KG("out") once and
/// increment ^KG("deg") once per timestamp — so the degree over-counted — and
/// never wrote ^KG("degp") at all. That also left DeleteAdjacency unusable as the
/// inverse, since it decrements each counter exactly once.
ClassMethod WriteAdjacencyShadow(s As %String, p As %String, o As %String, w As %String = "1.0", graph = 0)
{
	// Same structural edge at a new timestamp: refresh the weight, leave the
	// counters alone. Killing and rewriting would be the same thing more slowly.
	Set tExists = $Data(^KG("out", graph, s, p, o))
	Set ^KG("out", graph, s, p, o) = +w
	Set ^KG("in", graph, o, p, s) = +w
	If 'tExists {
		Set tmp = $Increment(^KG("deg", graph, s))
		Set tmp = $Increment(^KG("degp", graph, s, p))
	}
}

/// Delete adjacency entries for one edge, including its counters.
///
/// graph is an already-derived index key, as WriteAdjacency takes: integer 0 for
/// the default graph, the graph name otherwise. Derive it with
/// Graph.KG.GraphKey.ForIndex() before calling — ForIndex() rejects "0", so it
/// cannot be applied to a key that has already been derived (ADR-0003).
///
/// This owns all four stores WriteAdjacency writes. It used to remove only
/// ^KG("out") and ^KG("in") and delegate the counters to
/// Graph.KG.GraphIndex:DeleteIndex, which hardcoded the graph key to 0 and
/// spelled the counters ^KG("deg", s) — one subscript short, so the node id
/// landed in the graph-key position. The real counter was never decremented, a
/// bogus entry appeared beside every real graph, and the hardcoded 0 reached into
/// the default graph and deleted its entry for the same triple. Splitting one
/// global across two modules is what allowed the two to disagree.
ClassMethod DeleteAdjacency(s As %String, p As %String, o As %String, graph = 0)
{
	// Only decrement when the entry was really there. A blind decrement makes a
	// repeated delete drive the counter negative, which reads as fresh drift.
	If $Data(^KG("out", graph, s, p, o)) {
		Kill ^KG("out", graph, s, p, o)
		Set tDeg = $Increment(^KG("deg", graph, s), -1)
		Set tDegP = $Increment(^KG("degp", graph, s, p), -1)
		// A zero counter is not neutral: it still claims the node is in the graph.
		If tDeg <= 0 {
			Kill ^KG("deg", graph, s)
		}
		If tDegP <= 0 {
			Kill ^KG("degp", graph, s, p)
		}
	}
	Kill ^KG("in", graph, o, p, s)
	If $Data(^NKG("$meta", "nodeCount")) {
		Do ##class(Graph.KG.GraphIndex).DeleteIndex("", s, p, o, "")
	}
}

/// Remove one edge's adjacency from every graph it appears in.
///
/// "Every graph" is not a key value, so DeleteAdjacency cannot express it by
/// being handed one — it would have to be called once per graph, and the caller
/// would need the list. The walk therefore lives beside the delete it drives
/// rather than in Python, on the far side of the seam from ^KG.
///
/// Collect first, delete after: DeleteAdjacency mutates the tree being walked.
ClassMethod DeleteAdjacencyAllGraphs(s As %String, p As %String, o As %String)
{
	Kill tGraphs
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		If $Data(^KG("out", g, s, p, o)) {
			Set tGraphs(g) = ""
		}
	}
	// ^KG("in") is checked separately: a half-written edge may have only the
	// mirror, and leaving it behind is the drift this method exists to remove.
	Set g = ""
	For {
		Set g = $Order(^KG("in", g))
		Quit:g=""
		If $Data(^KG("in", g, o, p, s)) {
			Set tGraphs(g) = ""
		}
	}
	Set g = ""
	For {
		Set g = $Order(tGraphs(g))
		Quit:g=""
		Do ..DeleteAdjacency(s, p, o, g)
	}
}

/// Bulk ingest edges via SQL INSERT + persistent ^KG global writes.
///
/// graph_id is named explicitly and written as '', the same spelling create_edge
/// uses. Omitting the column left NULL, giving the default graph two spellings in
/// one table: every reader matching one of them silently missed the other half.
/// This writes the default graph only, which is what the hardcoded
/// WriteAdjacency graph key below has always meant.
ClassMethod BulkIngestEdgesSQL(edgesJson As %String, predicate As %String = "KNOWS") As %Integer
{
	Set data = {}.%FromJSON(edgesJson)
	Set n = 0
	TSTART
	Set iter = data.%GetIterator()
	While iter.%GetNext(.idx, .edge) {
		Set s = edge.s
		Set p = $Select(edge.p'="":edge.p, 1:predicate)
		Set o = edge.o
		If (s = "") || (o = "") { Continue }
		If s = o { Continue }
		&sql(INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (:s, :p, :o, ''))
		If (SQLCODE '= 0) && (SQLCODE '= -119) { Continue }
		Do ..WriteAdjacency(s, p, o, "1.0", 0)
		Set n = n + 1
	}
	TCOMMIT
	Quit n
}

ClassMethod BulkIngestEdgesFile(filePath As %String, predicate As %String = "KNOWS") As %Integer
{
	Set stream = ##class(%Stream.FileCharacter).%New()
	Do stream.LinkToFile(filePath)
	Set json = ""
	While 'stream.AtEnd {
		Set json = json _ stream.Read(32000)
	}
	Quit ..BulkIngestEdgesSQL(json, predicate)
}

ClassMethod BulkIngestNodesSQL(nodesJson As %String) As %Integer
{
	Set data = {}.%FromJSON(nodesJson)
	Set n = 0
	TSTART
	Set iter = data.%GetIterator()
	While iter.%GetNext(.idx, .node) {
		Set id = node.id
		If id = "" { Continue }
		&sql(INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (:id, ''))
		If (SQLCODE '= 0) && (SQLCODE '= -119) { Continue }
		If $IsObject(node.labels) {
			Set liter = node.labels.%GetIterator()
			While liter.%GetNext(.li, .label) {
				If label = "" { Continue }
				&sql(INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (:id, :label))
			}
		}
		If $IsObject(node.props) {
			Set piter = node.props.%GetIterator()
			While piter.%GetNext(.key, .val) {
				If key = "" { Continue }
				Try {
					Set valStr = $Select($IsObject(val):val.%ToJSON(), $IsValidNum(val):val, 1:""""_$Replace($ZConvert(val,"O","UTF8"),"""","""""")_"""")
					&sql(INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (:id, :key, :valStr))
				} Catch ex { }
			}
		}
		Set n = n + 1
	}
	TCOMMIT
	Quit n
}

/// Remove all ^KG adjacency for each node in a JSON array, in every graph.
///
/// Delegates every edge to DeleteAdjacency instead of killing subtrees, because
/// removing a node touches both ends of each of its edges:
///
///   its outbound edges leave a mirror ^KG("in") entry at each target, and
///   its inbound edges leave every source over-counting its own out-degree.
///
/// Killing ^KG("out", g, node) and ^KG("in", g, node) directly reached neither.
/// It also left ^KG("degp") behind entirely. Iterating and delegating keeps the
/// per-edge invariant in one place rather than restating it here, where it drifted.
ClassMethod BulkDeleteAdjacency(nodeIdsJSON As %String) As %Integer
{
	Set arr = ##class(%DynamicArray).%FromJSON(nodeIdsJSON)
	Set count = 0
	Set iter = arr.%GetIterator()
	While iter.%GetNext(.idx, .nodeId) {
		If nodeId = "" { Continue }
		// Collect first, delete after: DeleteAdjacency mutates the tree being walked.
		// Both directions are collected as (graph, source, predicate, target) so a
		// single delete loop covers edges out of the node and into it.
		Kill tEdges
		Set g = ""
		For {
			Set g = $Order(^KG("out", g))
			Quit:g=""
			Set p = ""
			For {
				Set p = $Order(^KG("out", g, nodeId, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("out", g, nodeId, p, o))
					Quit:o=""
					Set tEdges(g, nodeId, p, o) = ""
				}
			}
		}
		Set g = ""
		For {
			Set g = $Order(^KG("in", g))
			Quit:g=""
			Set p = ""
			For {
				Set p = $Order(^KG("in", g, nodeId, p))
				Quit:p=""
				Set s = ""
				For {
					Set s = $Order(^KG("in", g, nodeId, p, s))
					Quit:s=""
					Set tEdges(g, s, p, nodeId) = ""
				}
			}
		}
		Set g = ""
		For {
			Set g = $Order(tEdges(g))
			Quit:g=""
			Set s = ""
			For {
				Set s = $Order(tEdges(g, s))
				Quit:s=""
				Set p = ""
				For {
					Set p = $Order(tEdges(g, s, p))
					Quit:p=""
					Set o = ""
					For {
						Set o = $Order(tEdges(g, s, p, o))
						Quit:o=""
						Do ..DeleteAdjacency(s, p, o, g)
					}
				}
			}
		}
		Set count = count + 1
	}
	Quit count
}

}
