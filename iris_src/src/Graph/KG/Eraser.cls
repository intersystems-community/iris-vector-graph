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
/// include globals or the stores reached by node id: those are derived from the
/// rows or shared with other graphs, so adding them would make the number mean
/// "work done" instead of "content removed", and no caller can act on that.
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

		// --- what this graph names, before its rows are gone ----------------
		//
		// ^KG("label") and ^KG("prop") carry no graph subscript, so they can only
		// be reached through the node ids this erase is removing. Collected first
		// because the rows that name them are about to be deleted.
		Kill tNode, tLabel
		Set tS = ""
		&sql(DECLARE ivgEraseNodes CURSOR FOR
			SELECT node_id INTO :tS
			FROM Graph_KG.nodes
			WHERE COALESCE(graph_id, '') = COALESCE(:tName, ''))
		&sql(OPEN ivgEraseNodes)
		For {
			&sql(FETCH ivgEraseNodes)
			Quit:SQLCODE'=0
			Set tNode(tS) = ""
		}
		&sql(CLOSE ivgEraseNodes)

		Set tS = "", tL = ""
		&sql(DECLARE ivgEraseLabels CURSOR FOR
			SELECT l.s, l.label INTO :tS, :tL
			FROM Graph_KG.rdf_labels l, Graph_KG.nodes n
			WHERE l.s = n.node_id
			  AND COALESCE(n.graph_id, '') = COALESCE(:tName, ''))
		&sql(OPEN ivgEraseLabels)
		For {
			&sql(FETCH ivgEraseLabels)
			Quit:SQLCODE'=0
			Set tLabel(tL, tS) = ""
		}
		&sql(CLOSE ivgEraseLabels)

		// --- the rows, in FK-safe order -------------------------------------
		//
		// COALESCE on BOTH sides of every predicate. Left side: graph_id is
		// nullable and the default graph has two spellings — create_edge writes
		// '' explicitly, an INSERT omitting the column leaves NULL. Right side: in
		// IRIS embedded SQL an empty host variable binds as SQL NULL, not as the
		// empty string, so "graph_id = :tName" for the default graph compiles to
		// "graph_id = NULL" and matches nothing. Either omission erases half the
		// default graph and returns a count that looks like success.
		&sql(DELETE FROM Graph_KG.rdf_labels
			WHERE s IN (SELECT node_id FROM Graph_KG.nodes
				WHERE COALESCE(graph_id, '') = COALESCE(:tName, '')))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_labels", SQLCODE)) }

		&sql(DELETE FROM Graph_KG.rdf_props
			WHERE s IN (SELECT node_id FROM Graph_KG.nodes
				WHERE COALESCE(graph_id, '') = COALESCE(:tName, '')))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_props", SQLCODE)) }

		&sql(DELETE FROM Graph_KG.kg_NodeEmbeddings
			WHERE id IN (SELECT node_id FROM Graph_KG.nodes
				WHERE COALESCE(graph_id, '') = COALESCE(:tName, '')))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_NodeEmbeddings", SQLCODE)) }

		&sql(DELETE FROM Graph_KG.kg_NodeEmbeddings_optimized
			WHERE id IN (SELECT node_id FROM Graph_KG.nodes
				WHERE COALESCE(graph_id, '') = COALESCE(:tName, '')))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("kg_NodeEmbeddings_optimized", SQLCODE)) }

		// Reifications name an edge id, so they have to go before the edge rows.
		&sql(DELETE FROM Graph_KG.rdf_reifications
			WHERE edge_id IN (SELECT %ID FROM Graph_KG.rdf_edges
				WHERE COALESCE(graph_id, '') = COALESCE(:tName, '')))
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_reifications", SQLCODE)) }

		// Correlated EXISTS rather than a row-value IN: IRIS SQL rejects
		// "(s, p, o_id) IN (SELECT ...)" at prepare time with SQLCODE -1.
		&sql(DELETE FROM Graph_KG.kg_EdgeEmbeddings ee
			WHERE EXISTS (SELECT 1 FROM Graph_KG.rdf_edges e
				WHERE e.s = ee.s AND e.p = ee.p AND e.o_id = ee.o_id
				  AND COALESCE(e.graph_id, '') = COALESCE(:tName, '')))
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
		// ^KG is nine Kills rather than nine walks.
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

		// --- the globals with no graph subscript, reached by node id ---------
		Set tS = ""
		For {
			Set tS = $Order(tNode(tS))
			Quit:tS=""
			Kill ^KG("prop", tS)
		}
		Set tL = ""
		For {
			Set tL = $Order(tLabel(tL))
			Quit:tL=""
			Set tS = ""
			For {
				Set tS = $Order(tLabel(tL, tS))
				Quit:tS=""
				Kill ^KG("label", tL, tS)
			}
		}
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
	Quit tRemoved
}

/// Remove every graph's content from every store, including the stores no
/// per-graph erase can reach.
///
/// The stores keyed by node id or by triple alone cannot be split by graph, which
/// is why they accumulate: rdf_labels and rdf_props for a node named by two
/// graphs, ^NKG's graph-blind interning, ^KG("labelset")'s append-only hashes,
/// ^ArnoKG's whole-database snapshot. "All" has no attribution problem, so one
/// inventory serves both operations and this is the only place those stores can be
/// emptied honestly.
///
/// Returns the number of Graph_KG.nodes and Graph_KG.rdf_edges rows removed.
ClassMethod EraseAll() As %Integer
{
	Set tRemoved = 0
	Try {
		TSTART

		&sql(DELETE FROM Graph_KG.rdf_reifications)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_reifications", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.rdf_labels)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_labels", SQLCODE)) }
		&sql(DELETE FROM Graph_KG.rdf_props)
		If (SQLCODE '= 0) && (SQLCODE '= 100) { $$$ThrowStatus(..SqlErr("rdf_props", SQLCODE)) }
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
