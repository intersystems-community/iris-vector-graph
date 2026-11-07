/// The nodes with no edge to another node, in the answers of the Cypher procedures (4.6.0 R306).
///
/// The algorithms read the adjacency globals, which hold no node without an edge, and the symmetrised adjacency
/// of triangles, k-core and Leiden holds no node whose only edges are self-loops. Before R306 such a node was in no
/// answer, and the n of degree and classical closeness counted only the nodes with an edge. The kg_* SQL functions
/// behind CALL ivg.degreeCentrality, ivg.closeness, ivg.triangleCount, ivg.kcore, ivg.scc and
/// ivg.leiden read these nodes with Loners, run the algorithm with no topK cut, and Complete adds each its row, ranks
/// and cuts. The Python methods do the same (iris_vector_graph/_edgeless.py). ivg.eigenvector is not one: its nodes
/// with no edge take part in the iteration, as networkx's do (Graph.KG.NKGAccelCentrality.EigenvectorGlobal).
Class Graph.KG.Edgeless Extends %RegisteredObject
{

/// The graph's nodes with no edge to or from another node: pIds(id) = 1 when its only edges are self-loops, 0 when
/// it has none. Returns their number. pSchema is the IVG Schema holding the nodes table.
ClassMethod Loners(pSchema As %String = "Graph_KG", pGraph As %String = "", Output pIds) As %Integer
{
	Do ..Scan(pSchema, pGraph, "", 0, .pIds, .tCount)
	Quit tCount
}

/// Loners for the Python methods (iris_vector_graph/_edgeless.py), a page at a time so no answer passes the string
/// limit: {"loners": {id: 1 or 0, ...}, "next": id}, the nodes after pAfter in node_id order until pLimit of them are
/// found; "next" is where the next page starts, "" after the last. One pass over the nodes table and a $Order per node
/// (4.6.0 R306: a correlated NOT EXISTS over rdf_edges took 370 s on 30,000 nodes).
ClassMethod LonersJson(pSchema As %String = "Graph_KG", pGraph As %String = "", pAfter As %String = "", pLimit As %Integer = 20000) As %String
{
	Set tNext = ..Scan(pSchema, pGraph, pAfter, pLimit, .tIds, .tCount)
	Set tLoners = ##class(%DynamicObject).%New()
	Set tId = ""
	For {
		Set tId = $Order(tIds(tId), 1, tLoop)
		Quit:tId=""
		Do tLoners.%Set(tId, tLoop, "number")
	}
	Set tOut = ##class(%DynamicObject).%New()
	Do tOut.%Set("loners", tLoners)
	Do tOut.%Set("next", tNext, "string")
	Quit tOut.%ToJSON()
}

/// The loners among the graph's nodes after pAfter in node_id order, into pIds (pCount of them), stopping once pLimit
/// are found (0: no limit). Returns the last node_id read when it stopped there, else "".
ClassMethod Scan(pSchema As %String, pGraph As %String, pAfter As %String, pLimit As %Integer, Output pIds, Output pCount) As %String [ Private ]
{
	Kill pIds
	Set pCount = 0
	If pGraph = $Char(0) Set pGraph = ""
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tSQL = "SELECT node_id FROM "_pSchema_".nodes WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
	// an empty host variable binds as NULL, so the first page has no node_id bound
	If pAfter = "" {
		Set tRS = ##class(%SQL.Statement).%ExecDirect(, tSQL_" ORDER BY node_id", pGraph)
	} Else {
		Set tRS = ##class(%SQL.Statement).%ExecDirect(, tSQL_" AND node_id > ? ORDER BY node_id", pGraph, pAfter)
	}
	If tRS.%SQLCODE < 0 {
		Throw ##class(%Exception.SQL).CreateFromSQLCODE(tRS.%SQLCODE, tRS.%Message)
	}
	While tRS.%Next() {
		Set tId = tRS.%Get("node_id")
		Continue:..HasOther(tKey, tId, "out")
		Continue:..HasOther(tKey, tId, "in")
		Set pIds(tId) = ($Data(^KG("out", tKey, tId)) > 0)
		Set pCount = pCount + 1
		Return:(pLimit>0)&&(pCount>=pLimit) tId
	}
	Quit ""
}

/// Whether node pId has an edge in direction pDir ("out" or "in") to a node other than itself.
ClassMethod HasOther(pKey As %String, pId As %String, pDir As %String) As %Boolean [ Private ]
{
	Set tP = ""
	For {
		Set tP = $Order(^KG(pDir, pKey, pId, tP))
		Quit:tP=""
		Set tO = ""
		For {
			Set tO = $Order(^KG(pDir, pKey, pId, tP, tO))
			Quit:tO=""
			Return:tO'=pId 1
		}
	}
	Quit 0
}

/// pJson, an answer with no topK cut, with a row for each node of pIds it lacks, ranked and cut to pTopK (0: all).
/// pKind: "degree" (every score degree / (n - 1), n now every node), "closeness" (pFormula "classical": every score
/// 0, since no node reaches the nodes with no edge), "triangles", "kcore" (value descending, ties by
/// id in collation), "scc", "leiden" (each such node a group of its own, numbered after the others).
ClassMethod Complete(pKind As %String, pJson As %String, pTopK As %Integer, ByRef pIds, pFormula As %String = "") As %String
{
	Set tIn = ##class(%DynamicArray).%FromJSON(pJson)
	Set tGroup = $Case(pKind, "scc":"component", "leiden":"community", :"")
	Set tValue = $Case(pKind, "triangles":"triangles", "kcore":"coreness", :"score")
	Set tNext = 0
	Set tIter = tIn.%GetIterator()
	While tIter.%GetNext(.tI, .tRow) {
		Set tHave(tRow.id) = ""
		If (tGroup '= "") && (tRow.%Get(tGroup) >= tNext) Set tNext = tRow.%Get(tGroup) + 1
	}
	// the rows of the nodes the answer lacks
	Set tAdded = ##class(%DynamicArray).%New()
	Set tId = ""
	For {
		Set tId = $Order(pIds(tId))
		Quit:tId=""
		Continue:$Data(tHave(tId))
		Set tRow = ##class(%DynamicObject).%New()
		Do tRow.%Set("id", tId, "string")
		If tGroup '= "" {
			Do tRow.%Set(tGroup, tNext, "number")
			Do tRow.%Set("size", 1, "number")
			Set tNext = tNext + 1
		} Else {
			Do tRow.%Set(tValue, 0, "number")
			Do:pKind="degree" tRow.%Set("degree", 0, "number")
			Do:pKind="triangles" tRow.%Set("lcc", 0, "number")
		}
		Do tAdded.%Push(tRow)
	}

	Set tOut = ##class(%DynamicArray).%New()
	If tGroup '= "" {
		// the groups in their order, then the new ones
		For tArr = tIn, tAdded {
			Set tIter = tArr.%GetIterator()
			While tIter.%GetNext(.tI, .tRow) {
				Return:(pTopK>0)&&(tOut.%Size()>=pTopK) tOut.%ToJSON()
				Do tOut.%Push(tRow)
			}
		}
		Quit tOut.%ToJSON()
	}

	Set tN = tIn.%Size() + tAdded.%Size()
	Set tNorm = 1 / $Select(tN > 1: tN - 1, 1: 1)
	Set tZero = (pKind = "closeness") && (pFormula = "classical") && (tAdded.%Size() > 0) && (tN > 1)
	For tArr = tIn, tAdded {
		Set tIter = tArr.%GetIterator()
		While tIter.%GetNext(.tI, .tRow) {
			Do:pKind="degree" tRow.%Set("score", (+tRow.degree) * tNorm, "number")
			Do:tZero tRow.%Set("score", 0, "number")
			Set tRank(-$Normalize(tRow.%Get(tValue), 12), tRow.id) = tRow
		}
	}
	Set tNeg = ""
	For {
		Set tNeg = $Order(tRank(tNeg))
		Quit:tNeg=""
		Set tId = ""
		For {
			Set tId = $Order(tRank(tNeg, tId), 1, tRow)
			Quit:tId=""
			Do tRow.%Set("id", tId, "string")
			Do tOut.%Push(tRow)
			Return:(pTopK>0)&&(tOut.%Size()>=pTopK) tOut.%ToJSON()
		}
	}
	Quit tOut.%ToJSON()
}

}
