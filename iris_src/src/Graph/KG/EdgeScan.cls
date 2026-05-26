Class Graph.KG.EdgeScan Extends %RegisteredObject
{

ClassMethod MatchEdges(sourceId As %String, predicate As %String, shard As %Integer = 0) As %String [ SqlProc, SqlName = MatchEdges ]
{
	Set results = ##class(%DynamicArray).%New()
	Set sourceId = $Get(sourceId, "")
	Set predicate = $Get(predicate, "")

	If sourceId '= "" {
		If $Length(predicate) > 0 {
			Set o = ""
			For {
				Set o = $Order(^KG("out", 0, sourceId, predicate, o))
				Quit:o=""
				Set w = $Get(^KG("out", 0, sourceId, predicate, o), 1.0)
				Set entry = ##class(%DynamicObject).%New()
				Do entry.%Set("s", sourceId)
				Do entry.%Set("p", predicate)
				Do entry.%Set("o", o)
				Do entry.%Set("w", +w)
				Do results.%Push(entry)
			}
		} Else {
			Set p = ""
			For {
				Set p = $Order(^KG("out", 0, sourceId, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("out", 0, sourceId, p, o))
					Quit:o=""
					Set w = $Get(^KG("out", 0, sourceId, p, o), 1.0)
					Set entry = ##class(%DynamicObject).%New()
					Do entry.%Set("s", sourceId)
					Do entry.%Set("p", p)
					Do entry.%Set("o", o)
					Do entry.%Set("w", +w)
					Do results.%Push(entry)
				}
			}
		}
	} Else {
		Set s = ""
		For {
			Set s = $Order(^KG("out", 0, s))
			Quit:s=""
			Set p = ""
			For {
				Set p = $Order(^KG("out", 0, s, p))
				Quit:p=""
				If ($Length(predicate) > 0) && (p '= predicate) Continue
				Set o = ""
				For {
					Set o = $Order(^KG("out", 0, s, p, o))
					Quit:o=""
					Set w = $Get(^KG("out", 0, s, p, o), 1.0)
					Set entry = ##class(%DynamicObject).%New()
					Do entry.%Set("s", s)
					Do entry.%Set("p", p)
					Do entry.%Set("o", o)
					Do entry.%Set("w", +w)
					Do results.%Push(entry)
				}
			}
		}
	}

	Return results.%ToJSON()
}

ClassMethod WriteAdjacency(s As %String, p As %String, o As %String, w As %String = "1.0")
{
	Set ^KG("out", 0, s, p, o) = +w
	Set ^KG("in", 0, o, p, s) = +w
	Set tmp = $Increment(^KG("deg", s))
	Set tmp = $Increment(^KG("degp", s, p))
	If $Data(^NKG("$meta", "nodeCount")) {
		Set sIdx = ##class(Graph.KG.GraphIndex).InternNode(s)
		Set oIdx = ##class(Graph.KG.GraphIndex).InternNode(o)
		Set pIdx = ##class(Graph.KG.GraphIndex).InternLabel(p)
		Set ^NKG(-1, sIdx, -(pIdx+1), oIdx) = +w
		Set ^NKG(-2, oIdx, -(pIdx+1), sIdx) = +w
		Set tmp = $Increment(^NKG(-3, sIdx))
		Set tmp = $Increment(^NKG("$meta", "version"))
		Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(sIdx, pIdx, oIdx)
	}
}

ClassMethod DeleteAdjacency(s As %String, p As %String, o As %String)
{
	Kill ^KG("out", 0, s, p, o)
	Kill ^KG("in", 0, o, p, s)
	If $Data(^NKG("$meta", "nodeCount")) {
		Do ##class(Graph.KG.GraphIndex).DeleteIndex("", s, p, o, "")
	}
}


/// DEPRECATED: writes to process-private ^||KG via Python iris.gref — data lost on process exit.
/// Use BulkIngestEdgesSQL instead. Kept for backward compatibility only.
ClassMethod BulkIngestEdges(edgesJson As %String, predicate As %String = "R") As %Integer [ Internal, Language = python ]
{
    import iris, json, warnings
    data = json.loads(edgesJson)
    kg = iris.gref("^KG")
    n = 0
    for edge in data:
        if isinstance(edge, (list, tuple)):
            s, o = str(edge[0]), str(edge[1])
            p = str(edge[2]) if len(edge) > 2 else predicate
        else:
            s, p, o = str(edge["s"]), str(edge.get("p", predicate)), str(edge["o"])
        if s == o:
            continue
        kg["out", 0, s, p, o] = 1
        kg["in", 0, o, p, s] = 1
        kg["degp", s, p] = (kg.get(["degp", s, p]) or 0) + 1
        kg["deg", s] = (kg.get(["deg", s]) or 0) + 1
        n += 1
    kg["$bie_dirty"] = 1
    return n
}

/// Bulk ingest edges via SQL INSERT + persistent ^KG global writes.
/// Writes to BOTH rdf_edges (SQL, queryable via Cypher) AND ^KG globals (BFS acceleration).
/// edges_json: JSON array of {"s":s,"p":p,"o":o} objects.
/// Returns count of edges written.
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
		&sql(INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (:s, :p, :o))
		If (SQLCODE '= 0) && (SQLCODE '= -119) { Continue }
		Do ..WriteAdjacency(s, p, o, "1.0")
		Set n = n + 1
	}
	TCOMMIT
	Quit n
}

/// Bulk ingest edges from a JSON file path — streams large datasets without MAXSTRING limit.
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

/// Bulk ingest nodes + labels + properties via batched SQL.
/// nodesJson: JSON array of {"id":id, "labels":["L1",...], "props":{"k":"v",...}} objects.
/// Returns count of nodes written.
ClassMethod BulkIngestNodesSQL(nodesJson As %String) As %Integer
{
	Set data = {}.%FromJSON(nodesJson)
	Set n = 0
	TSTART
	Set iter = data.%GetIterator()
	While iter.%GetNext(.idx, .node) {
		Set id = node.id
		If id = "" { Continue }
		&sql(INSERT INTO Graph_KG.nodes (node_id) VALUES (:id))
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
				Set valStr = $Select($IsObject(val):val.%ToJSON(), 1:""""_$Replace(val,"""","""""")_"""")
				&sql(INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (:id, :key, :valStr))
			}
		}
		Set n = n + 1
	}
	TCOMMIT
	Quit n
}

}
