/// The nodes with no edge to another node, in the answers of the Cypher procedures (4.6.0 R306).
///
/// The algorithms read the adjacency globals, which hold no node without an edge, and the symmetrised adjacency
/// of triangles, k-core and Leiden holds no node whose only edges are self-loops. Before R306 such a node was in no
/// answer, and the n of degree and classical closeness counted only the nodes with an edge. The kg_* SQL functions
/// behind CALL ivg.degreeCentrality, ivg.closeness, ivg.eigenvector, ivg.triangleCount, ivg.kcore, ivg.scc and
/// ivg.leiden read these nodes with Loners, run the algorithm with no topK cut, and Complete adds each its row, ranks
/// and cuts. The Python methods do the same (iris_vector_graph/_edgeless.py).
Class Graph.KG.Edgeless Extends %RegisteredObject
{

/// The graph's nodes with no edge to or from another node: pIds(id) = 1 when its only edges are self-loops, 0 when
/// it has none. Returns their number. pSchema is the IVG Schema holding the nodes table.
ClassMethod Loners(pSchema As %String = "Graph_KG", pGraph As %String = "", Output pIds) As %Integer
{
	Kill pIds
	If pGraph = $Char(0) Set pGraph = ""
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, "SELECT node_id FROM "_pSchema_".nodes WHERE COALESCE(graph_id, '') = COALESCE(?, '')", pGraph)
	If tRS.%SQLCODE < 0 {
		Throw ##class(%Exception.SQL).CreateFromSQLCODE(tRS.%SQLCODE, tRS.%Message)
	}
	Set tCount = 0
	While tRS.%Next() {
		Set tId = tRS.%Get("node_id")
		Continue:..HasOther(tKey, tId, "out")
		Continue:..HasOther(tKey, tId, "in")
		Set pIds(tId) = ($Data(^KG("out", tKey, tId)) > 0)
		Set tCount = tCount + 1
	}
	Quit tCount
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
/// 0, since no node reaches the nodes with no edge), "eigenvector", "triangles", "kcore" (value descending, ties by
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
