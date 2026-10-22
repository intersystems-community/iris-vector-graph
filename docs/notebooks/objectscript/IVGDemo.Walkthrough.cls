/// Calling iris-vector-graph from code that runs inside IRIS: ObjectScript and Embedded Python.
/// Loaded and called by docs/notebooks/ivg_walkthrough.ipynb (part 9) in the IVGDEMO namespace.
Class IVGDemo.Walkthrough Extends %RegisteredObject
{

/// Cypher from ObjectScript: IVG.CypherEngine runs IVG's Python engine inside this process, on this namespace.
/// The result is a %DynamicObject {columns, rows, error}.
ClassMethod CypherFromObjectScript(pSeverity As %String = "high") As %String
{
	Set tEngine = ##class(IVG.CypherEngine).Local(8)
	Set tParams = {"severity": (pSeverity)}
	Set tResult = tEngine.Query("MATCH (f:Finding)-[:ABOUT]->(i:Instance) WHERE f.severity = $severity RETURN i.name AS instance, count(f) AS findings ORDER BY findings DESC, instance", tParams.%ToJSON())
	Quit tResult.%ToJSON()
}

/// The adjacency global directly: a node's neighbours over one relationship type are the subscripts under
/// ^KG("out", graph, node, type). No SQL, no Cypher: one $Order per neighbour.
ClassMethod Neighbours(pNode As %String, pType As %String) As %String
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(""), tOut = [], tTarget = ""
	For {
		Set tTarget = $Order(^KG("out", tKey, pNode, pType, tTarget))
		Quit:tTarget=""
		Do tOut.%Push(tTarget)
	}
	Quit tOut.%ToJSON()
}

/// A node's degree from IVG's server-side API (all relationship types when pType is "").
ClassMethod Degree(pNode As %String, pType As %String = "") As %Integer
{
	Quit ##class(Graph.KG.Adj).Degree(##class(Graph.KG.GraphKey).ForIndex(""), pNode, pType)
}

/// Record an event as opsreview does, one node per event: (ts, instance, ACTION_AT, action:<event id>), the details as
/// the edge's attributes. Then read the instance's window back.
ClassMethod RecordAndRead(pInstance As %String, pEventId As %String, pTs As %Integer, pKind As %String) As %String
{
	Set tSC = ##class(Graph.KG.TemporalIndex).InsertEdge("", pInstance, "ACTION_AT", "action:"_pEventId, pTs, 1, {"kind": (pKind), "event_id": (pEventId)}.%ToJSON())
	$$$ThrowOnError(tSC)
	Quit ##class(Graph.KG.TemporalIndex).QueryWindow("", pInstance, "ACTION_AT", pTs - 3600, pTs)
}

/// The graph's tables are ordinary SQL tables: findings per severity, from ObjectScript's dynamic SQL.
ClassMethod SeverityCounts() As %String
{
	Set tOut = {}
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, "SELECT p.val, COUNT(*) FROM Graph_KG.rdf_labels l JOIN Graph_KG.rdf_props p ON p.s = l.s AND p.""key"" = 'severity' WHERE l.label = 'Finding' GROUP BY p.val")
	While tRS.%Next() {
		Do tOut.%Set(tRS.%GetData(1), tRS.%GetData(2))
	}
	Quit tOut.%ToJSON()
}

/// Embedded Python with the iris module: the same global, a class method and SQL, from Python running in IRIS.
ClassMethod NeighboursPy(pNode As %String, pType As %String) As %String [ Language = python ]
{
	import iris, json
	key = iris.cls("Graph.KG.GraphKey").ForIndex("")
	kg = iris.gref("^KG")
	targets, t = [], kg.order(["out", key, pNode, pType, ""])
	while t:
		targets.append(t)
		t = kg.order(["out", key, pNode, pType, t])
	degree = iris.cls("Graph.KG.Adj").Degree(key, pNode, "")
	rows = iris.sql.exec("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE o_id = ? AND p = 'ABOUT'", pNode)
	about = next(iter(rows))[0]
	return json.dumps({"neighbours": targets, "degree": degree, "findings_about_it": about})
}

/// Embedded Python running IVG's own Python engine in-process, on an embedded connection: the same API as
/// IRISGraphEngine over a network connection, with no network.
ClassMethod CypherPy(pName As %String) As %String [ Language = python ]
{
	import json
	import iris
	from iris_vector_graph.embedded import EmbeddedConnection
	from iris_vector_graph.engine import IRISGraphEngine
	engine = IRISGraphEngine(EmbeddedConnection(iris.sql), embedding_dimension=8)
	r = engine.execute_cypher(
		"MATCH p = (a:Instance {name: $name})-[:MIRRORS*1..3]->(b:Instance) RETURN b.name AS reaches, length(p) AS hops ORDER BY hops",
		{"name": pName})
	return json.dumps({"columns": r.columns, "rows": r.rows, "error": r.error})
}

}
