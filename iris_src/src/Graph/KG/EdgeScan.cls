/// Graph.KG.EdgeScan — adjacency-global maintenance + scan procedures.
///
/// ============================================================================
/// ^KG ADJACENCY LAYOUT OF RECORD (spec 214 — named-graph aware)
/// ============================================================================
///
///     ^KG("out", graph, s, p, o) = weight
///     ^KG("in",  graph, o, p, s) = weight
///     ^KG("gen", graph, s)           = generation of a source (bumped by every write)
///     ^KG("degc", graph, s, key)     = $ListBuild(gen, count): cached out-degree
///
/// The stored counters ^KG("deg") and ^KG("degp") are gone (spec 248): the degree is
/// counted, and Graph.KG.Adj.Degree caches the count against the generation.
///
/// graph is the derived index key, not the graph name: integer 0 for the default
/// graph, the name itself otherwise. Derive it with Graph.KG.GraphKey.ForIndex()
/// (ADR-0001, ADR-0003). The earlier note here said "" was the default sentinel;
/// it is not, and never was in this global — WriteAdjacency has always defaulted
/// the parameter to 0.
///
/// Merged-view traversal iterates the full ^KG("out") tree (all graph subscripts)
/// and is spelled MatchEdgesAllGraphs. Graph-filtered traversal uses a fixed graph
/// subscript and is spelled MatchEdges. Both read 0 as "the default graph", the
/// same as WriteAdjacency and DeleteAdjacency, so one graph key means one thing
/// everywhere in this class.
///
/// It did not. MatchEdges alone read 0 as "every graph" — the value every caller
/// passes for the default graph, and the parameter's own default — so asking for
/// the default graph returned every graph's edges, and there was no way to ask for
/// the default graph alone. A cross-graph read now requires naming the method that
/// does it, which makes every one of them greppable (spec 230, FR-002).
///
/// ^NKG is per-identifier (no graph dimension); InternNode unchanged.
///
/// Who writes these stores: Graph.KG.Adjacency, and only it (spec 245). The adjacency
/// primitives below delegate there. This class scans them and holds the bulk paths.
/// ============================================================================
Class Graph.KG.EdgeScan Extends %RegisteredObject
{

/// Scan one graph's adjacency. graph is a derived index key: 0 for the default
/// graph, the graph name otherwise — WriteAdjacency's convention (ADR-0003).
///
/// For every graph at once, call MatchEdgesAllGraphs. There is deliberately no
/// value of this parameter that means "all of them".
ClassMethod MatchEdges(sourceId As %String, predicate As %String, shard As %Integer = 0, graph = 0) As %String [ SqlProc, SqlName = MatchEdges ]
{
	Set results = ##class(%DynamicArray).%New()
	// "" is the default graph here as it is everywhere else in IVG, and the
	// subscript that holds it is 0 — WriteAdjacency has never written under "".
	If graph = "" {
		Set graph = 0
	}
	Do ..ScanOneGraph(graph, $Get(sourceId, ""), $Get(predicate, ""), results)
	Return results.%ToJSON()
}

/// Scan every graph's adjacency and merge the results.
///
/// The cross-graph read, named. "Every graph" is not a key value, so it cannot be
/// expressed by handing MatchEdges a particular graph — the walk over the graph
/// subscripts has to live somewhere, and putting it behind its own name is what
/// makes a cross-graph read visible at the call site.
ClassMethod MatchEdgesAllGraphs(sourceId As %String, predicate As %String, shard As %Integer = 0) As %String [ SqlProc, SqlName = MatchEdgesAllGraphs ]
{
	Set results = ##class(%DynamicArray).%New()
	Set sourceId = $Get(sourceId, "")
	Set predicate = $Get(predicate, "")
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Do ..ScanOneGraph(g, sourceId, predicate, results)
	}
	Return results.%ToJSON()
}

/// Push one graph subscript's matching edges onto results.
///
/// The shared walk behind both scans, so the scoped read and the merged read cannot
/// come to disagree about what an edge is or how it is shaped.
///
/// results is a %DynamicArray, passed by reference as every object is; it is
/// appended to, never replaced.
ClassMethod ScanOneGraph(graph, sourceId As %String, predicate As %String, results As %DynamicArray) [ Internal, Private ]
{
	If sourceId '= "" {
		If $Length(predicate) > 0 {
			Set o = ""
			For {
				Set o = $Order(^KG("out", graph, sourceId, predicate, o))
				Quit:o=""
				Set w = $Get(^KG("out", graph, sourceId, predicate, o), 1.0)
				Set entry = ##class(%DynamicObject).%New()
				Do entry.%Set("s", sourceId) Do entry.%Set("p", predicate) Do entry.%Set("o", o) Do entry.%Set("w", +w)
				Do results.%Push(entry)
			}
		} Else {
			Set p = ""
			For {
				Set p = $Order(^KG("out", graph, sourceId, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("out", graph, sourceId, p, o))
					Quit:o=""
					Set w = $Get(^KG("out", graph, sourceId, p, o), 1.0)
					Set entry = ##class(%DynamicObject).%New()
					Do entry.%Set("s", sourceId) Do entry.%Set("p", p) Do entry.%Set("o", o) Do entry.%Set("w", +w)
					Do results.%Push(entry)
				}
			}
		}
	} Else {
		Set s = ""
		For {
			Set s = $Order(^KG("out", graph, s))
			Quit:s=""
			Set p = ""
			For {
				Set p = $Order(^KG("out", graph, s, p))
				Quit:p=""
				If ($Length(predicate) > 0) && (p '= predicate) Continue
				Set o = ""
				For {
					Set o = $Order(^KG("out", graph, s, p, o))
					Quit:o=""
					Set w = $Get(^KG("out", graph, s, p, o), 1.0)
					Set entry = ##class(%DynamicObject).%New()
					Do entry.%Set("s", s) Do entry.%Set("p", p) Do entry.%Set("o", o) Do entry.%Set("w", +w)
					Do results.%Push(entry)
				}
			}
		}
	}
}

/// Write adjacency entries for one edge. graph="" = default graph (spec 214).
///
/// Delegates to Graph.KG.Adjacency, the one writer of ^KG("out") and ^KG("in") (spec
/// 245). The name and signature are kept so existing callers, Python included, are
/// unchanged.
ClassMethod WriteAdjacency(s As %String, p As %String, o As %String, w As %String = "1.0", graph = 0)
{
	Do ##class(Graph.KG.Adjacency).Put(graph, s, p, o, w)
}

/// Delete adjacency entries for one edge.
///
/// graph is an already-derived index key, as WriteAdjacency takes: integer 0 for
/// the default graph, the graph name otherwise. Derive it with
/// Graph.KG.GraphKey.ForIndex() before calling — ForIndex() rejects "0", so it
/// cannot be applied to a key that has already been derived (ADR-0003).
///
/// Delegates to Graph.KG.Adjacency.Remove, which owns the stores WriteAdjacency
/// writes (spec 245). The history of the two modules disagreeing is in that class.
ClassMethod DeleteAdjacency(s As %String, p As %String, o As %String, graph = 0)
{
	Do ##class(Graph.KG.Adjacency).Remove(graph, s, p, o)
}

/// Write one keyed edge of a multigraph (spec 234). Beside WriteAdjacency, not
/// through it: with the mode off nothing calls this, and the single-edge writer
/// stays exactly what it was.
///
/// Delegates to Graph.KG.Adjacency.PutKeyed (spec 245). The layout is documented there.
ClassMethod WriteAdjacencyKeyed(s As %String, p As %String, o As %String, w As %String = "1.0", graph = 0, ekey As %Integer = 0) As %Status
{
	Quit ##class(Graph.KG.Adjacency).PutKeyed(graph, s, p, o, ekey, w)
}

/// Delete one keyed edge of a multigraph (spec 234); the inverse of
/// WriteAdjacencyKeyed. Delegates to Graph.KG.Adjacency.RemoveKeyed (spec 245).
ClassMethod DeleteAdjacencyKeyed(s As %String, p As %String, o As %String, graph = 0, ekey As %Integer = 0) As %Status
{
	Quit ##class(Graph.KG.Adjacency).RemoveKeyed(graph, s, p, o, ekey)
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

/// Register one node in the default graph, without a failed INSERT (spec 230 FR-034).
///
/// The bulk paths used to insert every endpoint and tolerate SQLCODE -119, the
/// duplicate. Inside the transaction they wrap a whole batch in, that is the most
/// expensive thing they do: IRIS rolls a failed statement back to its own implicit
/// savepoint, measured at ~40ms. Edges arrive chained — an edge's target is the next
/// edge's source — so about half of every batch's node inserts were duplicates, which
/// paced ingest at 25 edges/s regardless of batch size or caller. Measured live on
/// 200 chained edges in one transaction: the -119 idiom 39.53 ms per edge, a seen-set
/// plus an existence check 0.16, an existence check alone 0.03.
///
/// So ask first. pSeen remembers what this batch already registered, so a repeated
/// endpoint costs nothing at all; -119 is still tolerated below, for the row another
/// process inserts between the check and the write.
///
/// Returns 0 when the node is registered, otherwise the SQLCODE that stopped it —
/// which the caller has to treat as "this edge did not land", because rdf_edges'
/// foreign keys are composite since spec 227 ((graph_id, s) and (graph_id, o_id)
/// against nodes (graph_id, node_id)) and an unregistered endpoint makes the edge
/// INSERT SQLCODE -121.
ClassMethod RegisterDefaultNode(pNodeId As %String, ByRef pSeen) As %Integer
{
	If pNodeId = "" { Return -1 }
	If $Data(pSeen(pNodeId)) { Return 0 }
	Set pSeen(pNodeId) = ""
	Set tFound = 0
	&sql(SELECT 1 INTO :tFound FROM Graph_KG.nodes WHERE node_id = :pNodeId AND graph_id = '')
	If SQLCODE = 0 { Return 0 }
	&sql(INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (:pNodeId, ''))
	If (SQLCODE '= 0) && (SQLCODE '= -119) { Return SQLCODE }
	Return 0
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
	Kill tSeen
	TSTART
	Set iter = data.%GetIterator()
	While iter.%GetNext(.idx, .edge) {
		Set s = edge.s
		Set p = $Select(edge.p'="":edge.p, 1:predicate)
		Set o = edge.o
		If (s = "") || (o = "") { Continue }
		If s = o { Continue }
		// The endpoints first, through RegisterDefaultNode: an edge needs both of them
		// registered in its own graph — always the default one on this path — or the
		// INSERT below is SQLCODE -121 and the Continue that follows skips the row
		// without counting it, so the caller is told how many edges landed and it is
		// wrong.
		If ..RegisterDefaultNode(s, .tSeen) '= 0 { Continue }
		If ..RegisterDefaultNode(o, .tSeen) '= 0 { Continue }
		// The edge itself is asked for before it is written, for the same reason: on a
		// re-ingest every one of these INSERTs is the duplicate u_spo_graph refuses,
		// and a refused statement inside this transaction costs ~40ms. -119 is still
		// tolerated, for the row a concurrent writer lands in between.
		Set tEdge = 0
		&sql(SELECT 1 INTO :tEdge FROM Graph_KG.rdf_edges WHERE graph_id = '' AND s = :s AND p = :p AND o_id = :o)
		If SQLCODE '= 0 {
			&sql(INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (:s, :p, :o, ''))
			If (SQLCODE '= 0) && (SQLCODE '= -119) { Continue }
		}
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

/// Bulk ingest nodes, their labels and their properties, in the default graph.
///
/// Every write here names graph_id and writes '', the spelling create_node uses.
/// The label and property inserts used to omit the column, which left NULL: the
/// default graph then had two spellings in one table and a reader matching one
/// silently missed the rows written the other way (spec 230, FR-035).
///
/// Nothing is written by a failed statement either — see RegisterDefaultNode for what
/// that costs inside this transaction. A label is asked for before it is inserted, and
/// a property is written with INSERT OR UPDATE, which also means a re-ingest carrying
/// a changed value now updates it instead of silently failing on pk_props.
ClassMethod BulkIngestNodesSQL(nodesJson As %String) As %Integer
{
	Set data = {}.%FromJSON(nodesJson)
	Set n = 0
	Kill tSeen
	TSTART
	Set iter = data.%GetIterator()
	While iter.%GetNext(.idx, .node) {
		Set id = node.id
		If id = "" { Continue }
		If ..RegisterDefaultNode(id, .tSeen) '= 0 { Continue }
		If $IsObject(node.labels) {
			Set liter = node.labels.%GetIterator()
			While liter.%GetNext(.li, .label) {
				If label = "" { Continue }
				Set tHasLabel = 0
				&sql(SELECT 1 INTO :tHasLabel FROM Graph_KG.rdf_labels WHERE graph_id = '' AND s = :id AND label = :label)
				If SQLCODE '= 0 {
					&sql(INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES ('', :id, :label))
				}
			}
		}
		If $IsObject(node.props) {
			Set piter = node.props.%GetIterator()
			While piter.%GetNext(.key, .val) {
				If key = "" { Continue }
				Try {
					// The caller sends prop_text() strings; stored as given, like create_node.
					// Other JSON types are for direct callers: a boolean as true/false.
					Set tType = node.props.%GetTypeOf(key)
					Set valStr = $Select($IsObject(val):val.%ToJSON(), tType="boolean":$Select(val:"true",1:"false"), 1:val_"")
					&sql(INSERT OR UPDATE INTO Graph_KG.rdf_props (graph_id, s, "key", val) VALUES ('', :id, :key, :valStr))
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
/// Graph.KG.Adjacency.RemoveNode does the work for each graph that holds the node,
/// because removing a node touches both ends of each of its edges:
///
///   its outbound edges leave a mirror ^KG("in") entry at each target, and
///   its inbound edges leave every source holding an out entry that points at it.
///
/// Killing ^KG("out", g, node) and ^KG("in", g, node) directly reached neither, and left
/// the sources' cached degrees stale. One owner keeps the per-edge rule in one place.
ClassMethod BulkDeleteAdjacency(nodeIdsJSON As %String) As %Integer
{
	Set arr = ##class(%DynamicArray).%FromJSON(nodeIdsJSON)
	Set count = 0
	Set iter = arr.%GetIterator()
	While iter.%GetNext(.idx, .nodeId) {
		If nodeId = "" { Continue }
		// Collect the graphs first: RemoveNode mutates the trees walked here.
		Kill tGraphs
		Set g = ""
		For {
			Set g = $Order(^KG("out", g))
			Quit:g=""
			If $Data(^KG("out", g, nodeId)) || $Data(^KG("in", g, nodeId)) { Set tGraphs(g) = "" }
		}
		Set g = ""
		For {
			Set g = $Order(^KG("in", g))
			Quit:g=""
			If $Data(^KG("in", g, nodeId)) { Set tGraphs(g) = "" }
		}
		Set g = ""
		For {
			Set g = $Order(tGraphs(g))
			Quit:g=""
			Do ##class(Graph.KG.Adjacency).RemoveNode(g, nodeId)
		}
		Set count = count + 1
	}
	Quit count
}

}
