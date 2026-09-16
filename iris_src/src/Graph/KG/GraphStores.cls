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

	// --- graph-scoped globals: structural adjacency (spec 214) -------------
	Do tStores.%Push(..Entry("^KG(""out"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""in"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""deg"")", "global", "subscript 2", 1))
	Do tStores.%Push(..Entry("^KG(""degp"")", "global", "subscript 2", 1))

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
	// Everything below can be emptied wholesale but not split by graph. A
	// per-graph erase reaches these by node id or by triple, accepting that a node
	// or triple named by two graphs loses content shared with the other. That is
	// the schema gap, stated once here rather than rediscovered by each consumer.
	Do tStores.%Push(..Entry("Graph_KG.rdf_labels", "sql", "", 0, "keyed by node id alone, so a node's labels are shared by every graph naming that node"))
	Do tStores.%Push(..Entry("Graph_KG.rdf_props", "sql", "", 0, "keyed by node id and key alone, so a node's properties are shared by every graph naming that node"))
	Do tStores.%Push(..Entry("Graph_KG.rdf_reifications", "sql", "", 0, "keyed by reifier and edge id alone, with no graph column and no join to one"))
	Do tStores.%Push(..Entry("Graph_KG.kg_NodeEmbeddings", "sql", "", 0, "keyed by node id alone, so a node's embedding is shared by every graph naming that node"))
	Do tStores.%Push(..Entry("Graph_KG.kg_NodeEmbeddings_optimized", "sql", "", 0, "the reindexed twin of kg_NodeEmbeddings, keyed by node id alone"))
	Do tStores.%Push(..Entry("Graph_KG.kg_EdgeEmbeddings", "sql", "", 0, "keyed by the (s, p, o_id) triple alone, so an edge embedding is shared by every graph holding that triple"))
	Do tStores.%Push(..Entry("^NKG", "global", "", 0, "node interning is append-only and ignores graph_id, so its counters over-count and its ids cannot be attributed to one graph"))
	Do tStores.%Push(..Entry("^KG(""label"")", "global", "", 0, "keyed (label, node) with no graph subscript; read by the destination-label filter in Graph.KG.TraversalBFS, so a stale entry keeps answering label-filtered traversals"))
	Do tStores.%Push(..Entry("^KG(""prop"")", "global", "", 0, "keyed (node, key) with no graph subscript; read by Graph.KG.Subgraph when materializing node properties"))
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
