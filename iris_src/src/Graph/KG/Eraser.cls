/// Graph.KG.Eraser - the one path by which graph content is removed.
///
/// Before this class there were three deletion paths and none of them agreed.
/// Engine.drop_graph deleted rows from five tables and said so in its own
/// comment — "BYPASS: SQL rows deleted without touching ^KG/^NKG; flag stale" —
/// leaving the adjacency answering traversals for a graph that no longer had a
/// row, and the whole temporal index untouched. Engine.restore_snapshot cleared a
/// different six tables and killed whichever globals the snapshot metadata
/// happened to name, so restoring from a snapshot written without global metadata
/// left the previous database's ^KG in place. Graph.KG.EdgeScan:BulkDeleteAdjacency
/// removed globals without touching rows, producing the mirror-image drift.
///
/// Every one of those lists was maintained by hand and none was ever complete.
/// This class reaches every store Graph.KG.GraphStores declares, and derives the
/// list from it rather than restating it, so a store added to the inventory
/// cannot be silently missed here (see tests/unit/test_eraser_inventory_coverage.py).
///
/// TRANSACTION. The transaction is owned here rather than in Python. The Native
/// API rides the same connection as the DBAPI cursor, so Kill and SQL DELETE
/// share one transaction context — but $TLevel is invisible from Python, leaving a
/// caller unable to reason about the state after a mid-flight failure. Erasure
/// either completes or leaves the graph untouched: a partial erasure is a defect,
/// not an outcome, so this throws rather than returning a count on failure. A
/// returned 0 means nothing matched. See ADR-0004.
///
/// HISTORY. ^IVG.Ledger("rec", seq, ordinal) is never touched. It carries the
/// graph per record, so one revision's ordinals can belong to several graphs, and
/// pruning it by graph would punch holes in the ordinal sequence of revisions
/// describing graphs the caller did not ask to erase. The live statement and
/// tuple indexes ARE removed, because a ^IVG.Ledger("stmt") entry with an empty
/// deletedSeq asserts that a statement is live when its row is gone, and the next
/// ledger operation touching it fails. The invariant is therefore narrower than
/// "no trace remains": no live statement outlives its content.
Class Graph.KG.Eraser Extends %RegisteredObject
{

/// Remove one graph's content from every store that holds it.
///
/// Returns the number of Graph_KG.nodes and Graph_KG.rdf_edges rows removed,
/// which is what Engine.drop_graph returned. The count deliberately does not
/// include the globals: those are derived from the rows, so adding them would make
/// the number mean "work done" instead of "content removed", and no caller can act
/// on that.
///
/// Throws when the graph name cannot be represented as a subscript (ADR-0003),
/// before anything is deleted, and when any step fails, after rolling back.
ClassMethod EraseGraph(pGraph As %String = "") As %Integer
{
	// Both derivations, before TSTART: an invalid name must not open a transaction.
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tName = ##class(Graph.KG.GraphKey).ForName(pGraph)

	Set tRemoved = 0
	Try {
		TSTART

		// --- what this graph names ------------------------------------------
		//
		// Nothing to collect. Spec 230 gave ^KG("prop"), ^KG("label"), ^KG("deg2p")
		// and ^KG("deg2p_exact") a graph subscript, so this erase no longer reaches
		// them through the node ids it is deleting — which it could not do correctly:
		// since the re-key to UNIQUE (graph_id, node_id) a node id lives in two
		// graphs, and killing ^KG("prop", nodeId) took both graphs' entries. The two
		// cursors that collected those ids are gone with the walk they fed.

		// --- which physical tables hold this graph's vectors ------------------
		//
		// From the registry, restricted to routed tables. Never recomputed from the
		// (graph, model) hash: the registry row is the authority (FR-011), and a
		// recomputed name that finds nothing leaves a table full of this graph's
		// vectors standing after the erase.
		//
		// %STARTSWITH, not LIKE 'kg_emb_%': in LIKE, `_` is a single-character
		// wildcard, so the pattern would also match a legacy name and turn a
		// registry row for kg_NodeEmbeddings into a DROP TABLE.
		//
		// Both prefixes. A node route is kg_emb_<hash> and an edge route is
		// kg_eemb_<hash> (FR-006), and 'kg_eemb_x' does not start with 'kg_emb_' — so
		// an erase matching the node prefix alone left every edge route standing with
		// this graph's vectors in it.
		Kill tRoute
		Set tT = ""
		&sql(DECLARE ivgEraseRoutes CURSOR FOR
			SELECT table_name INTO :tT
			FROM Graph_KG.embedding_registry
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, '')
			  AND ((table_name %STARTSWITH 'kg_emb_') OR (table_name %STARTSWITH 'kg_eemb_')))
		&sql(OPEN ivgEraseRoutes)
		For {
			&sql(FETCH ivgEraseRoutes)
			Quit:SQLCODE'=0
			Set tRoute(tT) = "claimed"
		}
		&sql(CLOSE ivgEraseRoutes)

		// ...and then from the catalog, for the tables the registry does not name.
		//
		// A routed table outlives its registry row: CREATE TABLE is DDL and is durable
		// the moment it runs, while the row is an ordinary INSERT, so any rollback after
		// the create leaves an orphan. The orphan still carries its FK on
		// (graph_id, node_id), and a foreign key does not consult the registry — so a
		// route collected only from the registry is never emptied, the DELETE from
		// Graph_KG.nodes below fails its referential check with SQLCODE -124, and this
		// whole transaction rolls back. One orphan anywhere in the namespace makes every
		// erase fail and erase nothing.
		//
		// The registry stays the authority for what may be DROPPED (FR-011) — that is
		// why the two are told apart here, and why only "claimed" reaches the DDL below.
		Do ..CollectOrphanRoutes(.tRoute)

		// --- the rows, in FK-safe order -------------------------------------
		//
		// COALESCE on BOTH sides of every predicate. Left side: graph_id is
		// nullable and the default graph has two spellings — create_edge writes
		// '' explicitly, an INSERT omitting the column leaves NULL. Right side: in
		// IRIS embedded SQL an empty host variable binds as SQL NULL, not as the
		// empty string, so "graph_id = :tName" for the default graph compiles to
		// "graph_id = NULL" and matches nothing. Either omission erases half the
		// default graph and returns a count that looks like success.
		//
		// Every child table names its own graph. Reaching them through
		// "s IN (SELECT node_id FROM nodes WHERE graph_id = ...)" was correct while a
		// node id belonged to exactly one graph; after the re-key that subquery
		// returns the shared id and the DELETE takes both graphs' children with it,
		// then reports a count that reads like success.
		&sql(DELETE FROM Graph_KG.rdf_labels
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_labels", SQLCODE)) }

		&sql(DELETE FROM Graph_KG.rdf_props
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_props", SQLCODE)) }

		// The BM25 corpus. No erase path touched it before spec 230, so a graph's
		// documents outlived the graph itself and the next kg_BM25_SEARCH scored
		// text belonging to nothing — and once docs.id means a node id
		// (FR-004), the id it named came back attached to whoever created that
		// node next. It carries no FK, so its position here is free; it is deleted
		// with the other content tables rather than after the commit because a
		// half-erased corpus is worse than none.
		&sql(DELETE FROM Graph_KG.docs
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("docs", SQLCODE)) }

		// The legacy tables keep their rows scoped and keep their schema: both are
		// named statically by kg_KNN_VEC, by the bulk insert templates and by route
		// adoption, so dropping either would take the schema with the content. Only
		// the routed kg_emb_ and kg_eemb_ tables are dropped, further down.
		&sql(DELETE FROM Graph_KG.kg_NodeEmbeddings
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_NodeEmbeddings", SQLCODE)) }

		&sql(DELETE FROM Graph_KG.kg_NodeEmbeddings_optimized
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_NodeEmbeddings_optimized", SQLCODE)) }

		// Each routed table is emptied inside the transaction and dropped after the
		// commit. A routed table carries a FK on (graph_id, node_id), so its rows
		// have to go before the node rows in any case; emptying it first also means
		// that if the DDL later fails, what survives is an empty route at its
		// original width rather than an orphan table a later create=True would adopt
		// at the wrong width and then fail every INSERT with SQLCODE -104.
		Set tT = ""
		For {
			Set tT = $Order(tRoute(tT))
			Quit:tT=""
			Set tSql = "DELETE FROM Graph_KG." _ tT _ " WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
			Set tRs = ##class(%SQL.Statement).%ExecDirect(, tSql, tName)
			// -30 is "table not found": the registry named a route whose table is
			// already gone, and the row itself is removed after the commit.
			If (tRs.%SQLCODE < 0) && (tRs.%SQLCODE '= -30) {
				$$$ThrowStatus(..SqlErr(tT, tRs.%SQLCODE))
			}
		}

		// Reifications name an edge id, so they have to go before the edge rows.
		&sql(DELETE FROM Graph_KG.rdf_reifications
			WHERE edge_id IN (SELECT %ID FROM Graph_KG.rdf_edges
				WHERE COALESCE(graph_id, '') = COALESCE(:tName, '')))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_reifications", SQLCODE)) }

		// The table names its own graph since spec 230 (FR-006), so this is an
		// ordinary scoped delete. It used to reach the rows through a correlated
		// EXISTS against rdf_edges on (s, p, o_id) — which was only ever as correct
		// as the assumption that a triple belonged to one graph, and stopped being
		// correct the moment two graphs could hold the same node ids: an edge
		// embedding written for graph B survived B's erase if A held the same
		// triple, and vanished during A's.
		&sql(DELETE FROM Graph_KG.kg_EdgeEmbeddings
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_EdgeEmbeddings", SQLCODE)) }

		&sql(DELETE FROM Graph_KG.rdf_edges
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_edges", SQLCODE)) }
		Set tRemoved = tRemoved + %ROWCOUNT

		&sql(DELETE FROM Graph_KG.nodes
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("nodes", SQLCODE)) }
		Set tRemoved = tRemoved + %ROWCOUNT

		// --- the graph-scoped globals ---------------------------------------
		//
		// One Kill per store, because the graph key is the FIRST subscript in every
		// one of them. That is the whole return on ADR-0001: erasing a graph from
		// ^KG is thirteen Kills rather than thirteen walks.
		Set tHadEdges = $Data(^KG("out", tKey))
		Kill ^KG("out", tKey)
		Kill ^KG("in", tKey)
		Kill ^KG("deg", tKey)
		Kill ^KG("degp", tKey)
		Kill ^KG("tout", tKey)
		Kill ^KG("tin", tKey)
		Kill ^KG("bucket", tKey)
		Kill ^KG("tagg", tKey)
		Kill ^KG("edgeprop", tKey)
		Kill ^KG("prop", tKey)
		Kill ^KG("label", tKey)
		Kill ^KG("deg2p", tKey)
		Kill ^KG("deg2p_exact", tKey)
		// ^KG("labelset") is content-addressed interning of temporal label sets and
		// is deliberately append-only — Graph.KG.TemporalIndex documents that no
		// purge touches it. A surviving entry is a hash with no referent, not
		// content, so it is left alone here and emptied only by EraseAll.

		Do ..InvalidateDerived(tHadEdges)
		Do ..EraseLedgerFor(tName)

		TCOMMIT
	} Catch tEx {
		If $TLevel > 0 { TROLLBACK }
		Throw tEx
	}

	// --- after the commit: the routed tables themselves --------------------
	//
	// DDL is outside the transaction because a DDL rollback is not dependable in
	// IRIS: a DROP TABLE that participated in a rolled-back transaction can leave
	// the class definition and the table disagreeing. The content is already gone by
	// the time this runs, so the worst outcome here is a route that exists and is
	// empty — recoverable, and visible in the inventory.
	//
	// The drop comes first and the registry row second: dropping its table takes the
	// route's ANN index with it (research R3), and a registry row removed before a
	// failed drop would leave an orphan table nothing knows about.
	//
	// Only the routes the registry claims. An orphan was emptied of this graph's rows
	// above because a foreign key forced the issue, but it may still hold another
	// graph's vectors, and nothing here knows its declared width. Dropping a table on
	// the strength of its name alone is how a graph loses vectors it never asked to
	// erase; the orphan is left standing and shows up in the inventory as a route with
	// no identity.
	Set tT = ""
	For {
		Set tT = $Order(tRoute(tT))
		Quit:tT=""
		Continue:tRoute(tT)'="claimed"
		Try {
			Set tRs = ##class(%SQL.Statement).%ExecDirect(, "DROP TABLE Graph_KG." _ tT)
			If (tRs.%SQLCODE < 0) && (tRs.%SQLCODE '= -30) {
				$$$ThrowStatus(..SqlErr(tT, tRs.%SQLCODE))
			}
			&sql(DELETE FROM Graph_KG.embedding_registry
				WHERE table_name = :tT)
		} Catch tDrop {
			// Left deliberately: the route's rows are gone, and keeping its registry
			// row means the next resolve reuses the route at its recorded width
			// instead of adopting a table whose width nothing has checked.
		}
	}
	// Graph_KG.embedding_quarantine is not named anywhere above. A quarantined row
	// is there precisely because nobody could say which graph it belongs to, so no
	// graph's erase can claim it (FR-039).

	Quit tRemoved
}

/// Remove every graph's content from every store, including the stores no
/// per-graph erase can reach.
///
/// Some stores cannot be split by graph at all: ^NKG's graph-blind interning,
/// ^KG("labelset")'s append-only hashes, ^KG("deg2p_exact_merged")'s counts over
/// ^NKG, ^ArnoKG's whole-database snapshot. "All" has no attribution problem, so one
/// inventory serves both operations and this is the only place those stores can be
/// emptied honestly.
///
/// Returns the number of Graph_KG.nodes and Graph_KG.rdf_edges rows removed.
ClassMethod EraseAll() As %Integer
{
	Set tRemoved = 0
	Try {
		TSTART

		// Every routed table, whichever graph it belongs to, and both prefixes:
		// kg_emb_ for node routes, kg_eemb_ for edge routes (FR-006). Collected
		// before the registry is emptied, because the registry row is the only record
		// of the name (FR-011) — %STARTSWITH for the same reason as in EraseGraph:
		// `_` is a LIKE wildcard, and kg_NodeEmbeddings must not become a DROP TABLE.
		Kill tRoute
		Set tT = ""
		&sql(DECLARE ivgEraseAllRoutes CURSOR FOR
			SELECT table_name INTO :tT
			FROM Graph_KG.embedding_registry
			WHERE (table_name %STARTSWITH 'kg_emb_') OR (table_name %STARTSWITH 'kg_eemb_'))
		&sql(OPEN ivgEraseAllRoutes)
		For {
			&sql(FETCH ivgEraseAllRoutes)
			Quit:SQLCODE'=0
			Set tRoute(tT) = "claimed"
		}
		&sql(CLOSE ivgEraseAllRoutes)

		// Plus the orphans, for the reason given in EraseGraph: their FK on
		// (graph_id, node_id) blocks the DELETE from Graph_KG.nodes below whether or not
		// a registry row names them.
		Do ..CollectOrphanRoutes(.tRoute)

		Set tT = ""
		For {
			Set tT = $Order(tRoute(tT))
			Quit:tT=""
			Set tRs = ##class(%SQL.Statement).%ExecDirect(, "DELETE FROM Graph_KG." _ tT)
			If (tRs.%SQLCODE < 0) && (tRs.%SQLCODE '= -30) {
				$$$ThrowStatus(..SqlErr(tT, tRs.%SQLCODE))
			}
		}

		// The whole registry, not only the routed rows: a legacy table's identity row
		// describes vectors that no longer exist once this method has emptied it, and
		// a surviving row would make the next write conflict with a model nothing in
		// the namespace was written by. Graph_KG.embedding_quarantine is left alone —
		// it is the only copy of vectors whose graph could not be recovered, which is
		// evidence, not content (FR-039, FR-040).
		&sql(DELETE FROM Graph_KG.embedding_registry)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("embedding_registry", SQLCODE)) }

		&sql(DELETE FROM Graph_KG.rdf_reifications)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_reifications", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.rdf_labels)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_labels", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.rdf_props)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_props", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.docs)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("docs", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.kg_NodeEmbeddings)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_NodeEmbeddings", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.kg_NodeEmbeddings_optimized)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_NodeEmbeddings_optimized", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.kg_EdgeEmbeddings)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_EdgeEmbeddings", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.rdf_edges)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_edges", SQLCODE)) }
		Set tRemoved = tRemoved + %ROWCOUNT
		&sql(DELETE FROM Graph_KG.nodes)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("nodes", SQLCODE)) }
		Set tRemoved = tRemoved + %ROWCOUNT

		// Named subtrees rather than Kill ^KG, because ^KG also carries
		// ^KG("__version"), the stamp ^ArnoKG's cache is compared against.
		// Killing it would make a stale cache look current.
		Kill ^KG("out")
		Kill ^KG("in")
		Kill ^KG("deg")
		Kill ^KG("degp")
		Kill ^KG("tout")
		Kill ^KG("tin")
		Kill ^KG("bucket")
		Kill ^KG("tagg")
		Kill ^KG("edgeprop")
		Kill ^KG("label")
		Kill ^KG("prop")
		Kill ^KG("labelset")
		// The two-hop stores. Absent from this list until spec 230, so EraseAll
		// reported its row count and left ^KG("deg2p") holding the previous graph's
		// counts — and the next KHop2CountFast answered from them for a database with
		// no edges at all. "deg2p_exact_merged" is Arno's graph-blind sketch of the
		// same statistic and is content like the rest.
		Kill ^KG("deg2p")
		Kill ^KG("deg2p_exact")
		Kill ^KG("deg2p_exact_merged")

		Do ..InvalidateDerived(1)
		Kill ^ArnoKG
		Kill ^IVG.Ledger("stmt")
		Kill ^IVG.Ledger("tuple")
		// ^IVG.Ledger("rec") survives: erasure removes content, not history.

		TCOMMIT
	} Catch tEx {
		If $TLevel > 0 { TROLLBACK }
		Throw tEx
	}

	// After the commit, for the same reason as in EraseGraph: an emptied route that
	// survives the drop is reported by the inventory and can be reused at its
	// recorded width, whereas a DDL rollback is not dependable.
	//
	// Claimed routes only, as in EraseGraph. An orphan has been emptied by the loop
	// above and stays standing: this method wipes the whole registry, so after it every
	// surviving kg_emb_ table is unclaimed by definition, and dropping tables on the
	// strength of their names is not something an erase should start doing here.
	Set tT = ""
	For {
		Set tT = $Order(tRoute(tT))
		Quit:tT=""
		Continue:tRoute(tT)'="claimed"
		Try {
			Set tRs = ##class(%SQL.Statement).%ExecDirect(, "DROP TABLE Graph_KG." _ tT)
			If (tRs.%SQLCODE < 0) && (tRs.%SQLCODE '= -30) {
				$$$ThrowStatus(..SqlErr(tT, tRs.%SQLCODE))
			}
		} Catch tDrop {
			// An empty table left standing is not content; it is visible in the
			// inventory as a route with no rows.
		}
	}

	Quit tRemoved
}

/// Drop the derived indexes that cannot be attributed to one graph.
///
/// ^NKG interns node identifiers with no graph dimension and its counters are
/// append-only, so there is no subtree to kill and no way to decrement it. The
/// choice is between an absent ^NKG and a stale one. Absent is a state every
/// reader already handles — Graph.KG.EdgeScan:WriteAdjacency checks
/// $Data(^NKG("$meta","nodeCount")) before interning and the fast paths fall back
/// — while stale is not: the BFS fast path would traverse erased edges. This is
/// deliberately not the _nkg_dirty flag drop_graph set, which deferred a known
/// inconsistency into a rebuild that might never run.
///
/// ^ArnoKG caches a whole-database JSON snapshot and invalidates by comparing
/// ^KG("__version"), so bumping the stamp is what makes the cache notice. Killing
/// the snapshot outright is EraseAll's business; a per-graph erase cannot say
/// which parts of it are gone.
ClassMethod InvalidateDerived(pTouchedStructural As %Boolean) [ Private ]
{
	If pTouchedStructural {
		Kill ^NKG
	}
	Do ##class(Graph.KG.ArnoAccel).BumpVersion()
}

/// Add every routed table the catalog knows and pRoute does not, marked "orphan".
///
/// Both prefixes: kg_emb_ for a node route, kg_eemb_ for an edge route (FR-006).
/// An edge route carries no foreign key on the nodes table, so an orphaned one does
/// not block the erase the way a node route does — it just keeps its vectors, which
/// is the thing this whole class exists to prevent.
///
/// A routed table can outlive its registry row. CREATE TABLE is DDL and is durable the
/// moment it runs; the row that names it is an ordinary INSERT, so a rollback anywhere
/// after the create — the writer's own, the lost-race handler in
/// EmbeddingsMixin._create_route, a test fixture's — leaves a table nothing knows
/// about. The orphan keeps its foreign key on (graph_id, node_id), and a foreign key
/// does not read the registry: unless its rows go first, DELETE FROM Graph_KG.nodes
/// fails with SQLCODE -124 and the erase rolls back having removed nothing. One orphan
/// is enough to break every erase in the namespace.
///
/// Callers keep the distinction: "claimed" rows may be dropped, "orphan" rows may only
/// be emptied. The registry remains the authority for DDL (FR-011).
///
/// %STARTSWITH rather than LIKE 'kg_emb_%' for the same reason as the registry cursors:
/// in LIKE, `_` is a single-character wildcard, and kg_NodeEmbeddings must never be
/// collected as a route.
ClassMethod CollectOrphanRoutes(ByRef pRoute) [ Private ]
{
	Set tT = ""
	&sql(DECLARE ivgCatalogRoutes CURSOR FOR
		SELECT TABLE_NAME INTO :tT
		FROM INFORMATION_SCHEMA.TABLES
		WHERE TABLE_SCHEMA = 'Graph_KG'
		  AND ((TABLE_NAME %STARTSWITH 'kg_emb_') OR (TABLE_NAME %STARTSWITH 'kg_eemb_')))
	&sql(OPEN ivgCatalogRoutes)
	For {
		&sql(FETCH ivgCatalogRoutes)
		Quit:SQLCODE'=0
		Continue:tT=""
		Continue:$Data(pRoute(tT))
		Set pRoute(tT) = "orphan"
	}
	&sql(CLOSE ivgCatalogRoutes)
}

/// Remove one graph's live statements and their tuple entries.
///
/// One pass over ("stmt"), never over ("tuple"): the tuple index spells the graph
/// key LAST — ^IVG.Ledger("tuple", s, p, o, graphKey) — so it cannot be $Ordered
/// or Killed by graph. That is the shape ADR-0001 rejects for ^KG, for this exact
/// reason. ("stmt") carries the graph in value position 4, so each tuple key is
/// derived from the statement being removed. One scan, both trees consistent.
///
/// Collect first, kill after: killing inside the walk mutates the tree being
/// walked.
ClassMethod EraseLedgerFor(pName As %String) As %Integer [ Private ]
{
	Kill tDoomed
	Set tId = ""
	For {
		Set tId = $Order(^IVG.Ledger("stmt", tId))
		Quit:tId=""
		Set tRow = $Get(^IVG.Ledger("stmt", tId), "")
		Continue:tRow=""
		// Compared as names, not as keys: position 4 holds the canonical graph
		// name, which is what Graph.KG.LedgerApply passes to GKey() to build the
		// tuple subscript.
		Continue:##class(Graph.KG.GraphKey).ForName($ListGet(tRow, 4))'=pName
		Set tDoomed(tId) = tRow
	}

	Set tCount = 0
	Set tId = ""
	For {
		Set tId = $Order(tDoomed(tId))
		Quit:tId=""
		Set tRow = tDoomed(tId)
		Kill ^IVG.Ledger("tuple", $ListGet(tRow, 1), $ListGet(tRow, 2), $ListGet(tRow, 3), ##class(Graph.KG.GraphKey).ForLedger($ListGet(tRow, 4)))
		Kill ^IVG.Ledger("stmt", tId)
		Set tCount = tCount + 1
	}
	Quit tCount
}

/// One error shape for every failed step, so the rollback path has one reason.
ClassMethod SqlErr(pStore As %String, pCode As %Integer) As %Status [ Private ]
{
	Quit $$$ERROR($$$GeneralError, "erase failed on " _ pStore _ " with SQLCODE " _ pCode _ "; nothing was erased")
}

}
