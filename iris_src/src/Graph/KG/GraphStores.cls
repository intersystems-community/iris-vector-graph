/// Graph.KG.GraphStores - the one declared inventory of stores holding graph content.
///
/// Every deletion, verification and migration path needs the same answer to
/// "where does a graph's content live?", and each used to derive it separately:
/// Engine.drop_graph names five tables inline, verify_sync counts two of them,
/// Graph.KG.TraversalBuild:BuildKG rebuilds a different set again, and none of
/// them touch the temporal globals or the ledger. When a store was added, some of
/// those lists were updated and some were not.
///
/// This class states it once. Two consumers read it: Graph.KG.GraphVerify
/// (reports drift) and the Eraser (removes content). No consumer may keep its own
/// list. See ADR-0004.
///
/// The inventory also records the stores that are NOT graph-scoped. That is not a
/// hole in the inventory; it is the inventory stating a hole in the schema, so
/// that verification reports those stores rather than silently skipping them.
Class Graph.KG.GraphStores Extends %RegisteredObject
{

/// Every store that holds graph content.
///
/// Each entry carries:
///   name         how the store is named in the schema or in a global reference
///   kind         "sql" or "global"
///   locator      the graph column, or the subscript position of the graph key
///   graphScoped  1 when a graph's content can be isolated in this store, else 0
///   reason       why it is not graph-scoped; empty when graphScoped is 1
ClassMethod Inventory() As %DynamicArray
{
	Set tStores = []

	// --- graph-scoped SQL ---------------------------------------------------
	Do tStores.%Push(..Entry("Graph_KG.rdf_edges", "sql", "graph_id", 1))
	Do tStores.%Push(..Entry("Graph_KG.nodes", "sql", "graph_id", 1))
	// Spec 227 gave the child tables and both legacy embedding tables a graph_id
	// of their own, so the Eraser reaches them by that column instead of through
	// "s IN (SELECT node_id FROM nodes WHERE graph_id = ...)" — a subquery that
	// returned the shared node id after the re-key and took both graphs' rows.
	Do tStores.%Push(..Entry("Graph_KG.rdf_labels", "sql", "graph_id", 1))
	Do tStores.%Push(..Entry("Graph_KG.rdf_props", "sql", "graph_id", 1))
	Do tStores.%Push(..Entry("Graph_KG.kg_NodeEmbeddings", "sql", "graph_id", 1))
	Do tStores.%Push(..Entry("Graph_KG.kg_NodeEmbeddings_optimized", "sql", "graph_id", 1))
	// Spec 230 gave these two the same treatment. kg_EdgeEmbeddings was keyed by
	// the (s, p, o_id) triple alone, which the Eraser reached through a correlated
	// EXISTS against rdf_edges — correct only while a triple belonged to one graph.
	// Graph_KG.docs was in no inventory and in no erase path at all, so a graph's
	// BM25 corpus outlived the graph.
	Do tStores.%Push(..Entry("Graph_KG.kg_EdgeEmbeddings", "sql", "graph_id", 1))
	Do tStores.%Push(..Entry("Graph_KG.docs", "sql", "graph_id", 1))
	// The routed tables kg_emb_<hash> and kg_eemb_<hash> are not listed one by one:
	// which of them exists is a fact about the registry, not about this class. The
	// Eraser reads Graph_KG.embedding_registry for a graph's routes and drops each
	// after the commit; the registry itself is named below.
	Do tStores.%Push(..Entry("Graph_KG.embedding_registry", "sql", "graph_id", 1))
	// A FHIR graph's references to resources it does not hold (spec 231). Its
	// registry row, Graph_KG.fhir_graphs, is configuration: erasing keeps it and
	// zeroes its watermarks.
	Do tStores.%Push(..Entry("Graph_KG.fhir_unresolved", "sql", "graph_id", 1))

	// --- graph-scoped globals: structural adjacency (spec 214) -------------
	Do tStores.%Push(..Entry("^KG(""out"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""in"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""deg"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""degp"")", "global", "subscript 2", 1))

	// --- graph-scoped globals: node stores and two-hop counts (spec 230) ----
	// Spec 214 keyed adjacency and left these behind, so a per-graph erase had to
	// walk them by node id — which stopped being correct once the same node id
	// could live in two graphs. They are keyed the same way now, and the erase is
	// four more Kills instead of two SQL cursors and two nested walks.
	Do tStores.%Push(..Entry("^KG(""label"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""prop"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""deg2p"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""deg2p_exact"")", "global", "subscript 2", 1))

	// --- graph-scoped globals: temporal index (spec 223) -------------------
	Do tStores.%Push(..Entry("^KG(""tout"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""tin"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""bucket"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""tagg"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""edgeprop"")", "global", "subscript 2", 1))

	// --- graph-scoped globals: revision ledger (spec 213) ------------------
	// The tuple index spells the graph key LAST, so it cannot be walked by
	// graph the way ^KG can. The default graph is $Char(1) here, not 0
	// (ADR-0003).
	Do tStores.%Push(..Entry("^IVG.Ledger(""tuple"")", "global", "subscript 5", 1))
	Do tStores.%Push(..Entry("^IVG.Ledger(""stmt"")", "global", "graph in the stored value", 1))

	// --- not graph-scoped ---------------------------------------------------
	//
	// Everything below can be emptied wholesale but not split by graph. Spec 230
	// removed the entries that were only reachable by node id or by triple, so what
	// is left is the content whose graph identity does not exist to be recovered:
	// interning that never carried one, a statistic computed over that interning, an
	// append-only content-addressed store, a whole-database snapshot, and one table
	// (rdf_reifications) that still has no graph column. Stated once here rather
	// than rediscovered by each consumer.
	Do tStores.%Push(..Entry("Graph_KG.rdf_reifications", "sql", "", 0, "keyed by reifier and edge id alone, with no graph column and no join to one"))
	Do tStores.%Push(..Entry("^NKG", "global", "", 0, "node interning is append-only and ignores graph_id, so its counters over-count and its ids cannot be attributed to one graph"))
	Do tStores.%Push(..Entry("^KG(""deg2p_exact_merged"")", "global", "", 0, "Arno's two-hop counts, computed over ^NKG by Graph.KG.NKGAccelTraversal:Build2HopExact; since ^NKG's interning carries no graph, the number cannot be attributed to one graph and is kept apart from the scoped ^KG(""deg2p_exact"") rather than written into it"))
	Do tStores.%Push(..Entry("^KG(""labelset"")", "global", "", 0, "content-addressed interning of temporal label sets, deliberately append-only: Graph.KG.TemporalIndex documents that no purge touches it"))
	Do tStores.%Push(..Entry("^ArnoKG", "global", "", 0, "a whole-database JSON snapshot keyed by graph name ""KG"" with no graph dimension, invalidated by comparing ^KG(""__version"") rather than by content, so erasing content without bumping that stamp leaves Arno serving the erased graph"))

	Quit tStores
}

/// The stores whose content can be isolated to one graph.
ClassMethod Scoped() As %DynamicArray
{
	Set tOut = []
	Set tIter = ..Inventory().%GetIterator()
	While tIter.%GetNext(.tIdx, .tEntry) {
		If tEntry.graphScoped = 1 {
			Do tOut.%Push(tEntry)
		}
	}
	Quit tOut
}

/// The stores that hold graph content but cannot isolate it, ready to be
/// reported. Keyed "store" rather than "name" because this is the shape that
/// reaches a caller reading a verification report.
ClassMethod Unscoped() As %DynamicArray
{
	Set tOut = []
	Set tIter = ..Inventory().%GetIterator()
	While tIter.%GetNext(.tIdx, .tEntry) {
		If tEntry.graphScoped '= 1 {
			Do tOut.%Push({"store": (tEntry.name), "kind": (tEntry.kind), "reason": (tEntry.reason)})
		}
	}
	Quit tOut
}

/// The inventory as JSON, for the callers that live outside ObjectScript.
/// A %DynamicArray does not survive a Native API return, and a Python-side copy of
/// the inventory would be a second declaration of it — free to drift, which is the
/// failure the one-inventory rule exists to remove (ADR-0004).
ClassMethod InventoryJSON() As %String
{
	Quit ..Inventory().%ToJSON()
}

/// Build one inventory entry.
ClassMethod Entry(pName As %String, pKind As %String, pLocator As %String, pScoped As %Integer, pReason As %String = "") As %DynamicObject [ Private ]
{
	Quit {"name": (pName), "kind": (pKind), "locator": (pLocator), "graphScoped": (pScoped), "reason": (pReason)}
}

}
