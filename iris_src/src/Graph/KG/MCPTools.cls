/// Knowledge graph tools for MCP — Cypher query, graph loading, stats, PPR, evidence search.
/// Exposed via Graph.KG.MCPService → iris-mcp-server → Claude Desktop.
Class Graph.KG.MCPTools Extends %AI.Tool
{

/// Execute a Cypher query against the knowledge graph.
/// The query is translated to SQL via the iris-vector-graph Cypher translator, executed, and results returned as JSON.
/// Example: MATCH (a)-[r]->(b) WHERE a.id = 'hla-b27' RETURN a.id, r, b.id LIMIT 10
Method CypherQuery(query As %String) As %DynamicObject [ Language = python ]
{
	try:
		from iris_vector_graph.cypher.parser import parse_query
		from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix
		import iris
		
		set_schema_prefix("Graph_KG")
		parsed = parse_query(query)
		result = translate_to_sql(parsed)
		sql = result.sql
		params = result.parameters
		
		stmt = iris.sql.prepare(sql)
		rs = stmt.execute(*params)
		
		cols = [rs.getColumnName(i+1) for i in range(rs.columnCount)]
		rows = []
		while rs.next():
			row = [rs.getColumn(i+1) for i in range(rs.columnCount)]
			rows.append(row)
		
		return {"status": "OK", "columns": cols, "rows": rows, "rowCount": len(rows), "sql": sql}
	except Exception as e:
		return {"status": "ERROR", "error": str(e)}
}

/// Execute a SQL query directly against the Graph_KG schema.
/// Use this when you know the exact SQL or when Cypher translation is not needed.
/// Example: SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE s = 'hla-b27' FETCH FIRST 10 ROWS ONLY
Method RunSQL(query As %String) As %DynamicObject
{
	Set result = {}
	Try {
		Set stmt = ##class(%SQL.Statement).%New()
		Set sc = stmt.%Prepare(query)
		If $$$ISERR(sc) {
			Do result.%Set("status", "ERROR")
			Do result.%Set("error", $System.Status.GetErrorText(sc))
			Return result
		}
		Set rs = stmt.%Execute()
		Set cols = ##class(%DynamicArray).%New()
		For i = 1:1:rs.%ResultColumnCount {
			Do cols.%Push(rs.%GetColumnName(i))
		}
		Set rows = ##class(%DynamicArray).%New()
		While rs.%Next() {
			Set row = ##class(%DynamicArray).%New()
			For i = 1:1:rs.%ResultColumnCount {
				Do row.%Push(rs.%GetData(i))
			}
			Do rows.%Push(row)
		}
		Do result.%Set("status", "OK")
		Do result.%Set("columns", cols)
		Do result.%Set("rows", rows)
		Do result.%Set("rowCount", rows.%Size())
	} Catch ex {
		Do result.%Set("status", "ERROR")
		Do result.%Set("error", ex.DisplayString())
	}
	Return result
}

/// Load a graph file (GraphML or OBO format) into the knowledge graph.
/// After loading, BuildKG() is called to populate the ^KG adjacency index.
/// format: "graphml" (default) or "obo"
Method LoadGraph(filePath As %String, format As %String = "graphml") As %DynamicObject [ Language = python ]
{
	try:
		import iris
		from iris_vector_graph.engine import IRISGraphEngine
		
		conn = iris.sql._connection()
		engine = IRISGraphEngine(conn)
		
		if format.lower() == "obo":
			stats = engine.load_obo(filePath)
		else:
			import networkx as nx
			G = nx.read_graphml(filePath)
			stats = engine.load_networkx(G)
		
		# Build ^KG adjacency index
		iris.cls("Graph.KG.Traversal").BuildKG()
		
		return {
			"status": "OK",
			"nodesLoaded": stats.get("nodes_created", 0),
			"edgesLoaded": stats.get("edges_created", 0),
			"buildKGCompleted": True,
			"format": format
		}
	except Exception as e:
		return {"status": "ERROR", "error": str(e)}
}

/// Get knowledge graph statistics: node count, edge count, top predicates, top labels.
/// Call this first to understand what's in the graph before querying.
Method GraphStats() As %DynamicObject
{
	Set result = {}
	Try {
		Set stmt = ##class(%SQL.Statement).%New()
		
		// Node count
		Set sc = stmt.%Prepare("SELECT COUNT(*) AS cnt FROM Graph_KG.nodes")
		Set rs = stmt.%Execute()
		Do rs.%Next()
		Set nodeCount = rs.%GetData(1)
		
		// Edge count
		Set sc = stmt.%Prepare("SELECT COUNT(*) AS cnt FROM Graph_KG.rdf_edges")
		Set rs = stmt.%Execute()
		Do rs.%Next()
		Set edgeCount = rs.%GetData(1)
		
		// Top predicates
		Set sc = stmt.%Prepare("SELECT TOP 10 p, COUNT(*) AS cnt FROM Graph_KG.rdf_edges GROUP BY p ORDER BY cnt DESC")
		Set rs = stmt.%Execute()
		Set preds = ##class(%DynamicArray).%New()
		While rs.%Next() {
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("predicate", rs.%GetData(1))
			Do obj.%Set("count", rs.%GetData(2))
			Do preds.%Push(obj)
		}
		
		// Top labels
		Set sc = stmt.%Prepare("SELECT TOP 10 label, COUNT(*) AS cnt FROM Graph_KG.rdf_labels GROUP BY label ORDER BY cnt DESC")
		Set rs = stmt.%Execute()
		Set labels = ##class(%DynamicArray).%New()
		While rs.%Next() {
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("label", rs.%GetData(1))
			Do obj.%Set("count", rs.%GetData(2))
			Do labels.%Push(obj)
		}
		
		Do result.%Set("status", "OK")
		Do result.%Set("nodeCount", nodeCount)
		Do result.%Set("edgeCount", edgeCount)
		Do result.%Set("topPredicates", preds)
		Do result.%Set("topLabels", labels)
	} Catch ex {
		Do result.%Set("status", "ERROR")
		Do result.%Set("error", ex.DisplayString())
	}
	Return result
}

/// Run Personalized PageRank from seed nodes to find structurally important related concepts.
/// Returns top-K ranked nodes with PPR scores. Use this to discover surprising connections.
/// seeds: array of node IDs, e.g. ["hla-b27", "spondyloarthritis"]
Method PPRWalk(seeds As %DynamicArray, topK As %Integer = 10, damping As %Double = 0.85) As %DynamicObject
{
	Set result = {}
	Try {
		Set seedJson = seeds.%ToJSON()
		Set pprJson = ##class(Graph.KG.PageRank).RunJson(seedJson, damping, 50, topK)
		Set pprResult = ##class(%DynamicArray).%FromJSON(pprJson)
		
		Set results = ##class(%DynamicArray).%New()
		For i = 0:1:(pprResult.%Size()-1) {
			Set item = pprResult.%Get(i)
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("nodeId", item.%Get(0))
			Do obj.%Set("score", item.%Get(1), "number")
			Do results.%Push(obj)
		}
		
		Do result.%Set("status", "OK")
		Do result.%Set("results", results)
		Do result.%Set("seedCount", seeds.%Size())
	} Catch ex {
		Do result.%Set("status", "ERROR")
		Do result.%Set("error", ex.DisplayString())
	}
	Return result
}

/// Search for supporting evidence in the literature using vector similarity.
/// Requires document embeddings loaded in kg_NodeEmbeddings table.
/// query: natural language description of what to search for
Method EvidenceSearch(query As %String, topK As %Integer = 5) As %DynamicObject [ Language = python ]
{
	try:
		import iris
		
		# Check if embeddings exist
		stmt = iris.sql.prepare("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
		rs = stmt.execute()
		rs.next()
		count = rs.getColumn(1)
		
		if count == 0:
			return {"status": "OK", "results": [], "message": "No embeddings loaded in kg_NodeEmbeddings"}
		
		# Use IRIS vector search
		sql = """
			SELECT TOP ? id, VECTOR_DOT_PRODUCT(emb, (SELECT TOP 1 emb FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?)) AS score
			FROM Graph_KG.kg_NodeEmbeddings
			ORDER BY score DESC
		"""
		stmt = iris.sql.prepare(sql)
		rs = stmt.execute(topK, query)
		
		results = []
		while rs.next():
			results.append({"id": rs.getColumn(1), "score": float(rs.getColumn(2))})
		
		return {"status": "OK", "results": results, "resultCount": len(results)}
	except Exception as e:
		return {"status": "ERROR", "error": str(e)}
}

}
