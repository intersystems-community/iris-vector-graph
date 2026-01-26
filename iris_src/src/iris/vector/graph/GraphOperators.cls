Class iris.vector.graph.GraphOperators Extends %RegisteredObject
{

/// Validates and caps the 'k' parameter (TOP clause limit)
/// 1 <= k <= 1000, defaults to 50
ClassMethod ValidateK(k As %RawString) As %Integer [ SqlProc ]
{
	Set k = $Get(k, 50)
	If k = "" Set k = 50
	Set k = +k
	If k < 1 Return 1
	If k > 1000 Return 1000
	Return k
}

/// Enhanced text search using JSON_TABLE for structured qualifier filtering
ClassMethod kgTXT(queryText As %String, k As %Integer = 50, minConfidence As %Integer = 0) As %DynamicArray [ SqlName = kg_TXT, SqlProc ]
{
	Set k = ..ValidateK(k)
	Set pattern = "%"_queryText_"%"
	
	Set sql = "SELECT TOP ? "
	Set sql = sql_" e.s AS entity_id, "
	Set sql = sql_" (CAST(jt.confidence AS FLOAT) / 1000.0 + CASE WHEN e.o_id LIKE ? THEN 0.5 ELSE 0.0 END) AS relevance_score "
	Set sql = sql_" FROM SQLUSER.rdf_edges e, "
	Set sql = sql_" JSON_TABLE(e.qualifiers, '$' COLUMNS(confidence INTEGER PATH '$.confidence')) jt "
	Set sql = sql_" WHERE jt.confidence >= ? OR e.o_id LIKE ? "
	Set sql = sql_" ORDER BY relevance_score DESC"
	
	Set stmt = ##class(%SQL.Statement).%New()
	Set sc = stmt.%Prepare(sql)
	If $$$ISERR(sc) Return ##class(%DynamicArray).%New()
	
	Set rs = stmt.%Execute(k, pattern, minConfidence, pattern)
	
	Set results = ##class(%DynamicArray).%New()
	While rs.%Next() {
		Set obj = ##class(%DynamicObject).%New()
		Do obj.%Set("id", rs.%Get("entity_id"))
		Do obj.%Set("score", +rs.%Get("relevance_score"))
		Do results.%Push(obj)
	}
	Return results
}

/// HNSW-optimized vector search using native IRIS VECTOR functions
ClassMethod kgKNNVEC(queryVector As %String, k As %Integer = 50, labelFilter As %String = "") As %DynamicArray [ SqlName = kg_KNN_VEC, SqlProc ]
{
	Set k = ..ValidateK(k)
	
	Set sql = "SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?)) as similarity "
	Set sql = sql_" FROM SQLUSER.kg_NodeEmbeddings_optimized n "
	
	If labelFilter '= "" {
		Set sql = sql_" LEFT JOIN SQLUSER.rdf_labels L ON L.s = n.id "
		Set sql = sql_" WHERE L.label = ? "
	}
	
	Set sql = sql_" ORDER BY similarity DESC"
	
	Set stmt = ##class(%SQL.Statement).%New()
	Set sc = stmt.%Prepare(sql)
	If $$$ISERR(sc) Return ##class(%DynamicArray).%New()
	
	If labelFilter '= "" {
		Set rs = stmt.%Execute(k, queryVector, labelFilter)
	} Else {
		Set rs = stmt.%Execute(k, queryVector)
	}
	
	Set results = ##class(%DynamicArray).%New()
	While rs.%Next() {
		Set obj = ##class(%DynamicObject).%New()
		Do obj.%Set("id", rs.%Get("id"))
		Do obj.%Set("score", +rs.%Get("similarity"))
		Do results.%Push(obj)
	}
	Return results
}

/// Core graph path implementation
ClassMethod kgGRAPHPATH(srcId As %String, pred1 As %String, pred2 As %String, maxHops As %Integer = 2) As %DynamicArray [ SqlName = kg_GRAPH_PATH, SqlProc ]
{
	Set results = ##class(%DynamicArray).%New()
	
	Set sql = "SELECT 1 AS path_id, 1 AS step, e1.s, e1.p, e1.o_id "
	Set sql = sql_"FROM SQLUSER.rdf_edges e1 WHERE e1.s = ? AND e1.p = ? "
	Set sql = sql_"UNION ALL "
	Set sql = sql_"SELECT 1 AS path_id, 2 AS step, e2.s, e2.p, e2.o_id "
	Set sql = sql_"FROM SQLUSER.rdf_edges e2 WHERE e2.p = ? "
	Set sql = sql_"AND EXISTS (SELECT 1 FROM SQLUSER.rdf_edges e1 WHERE e1.s = ? AND e1.p = ? AND e1.o_id = e2.s) "
	Set sql = sql_"ORDER BY step"
	
	Set stmt = ##class(%SQL.Statement).%New()
	Set sc = stmt.%Prepare(sql)
	If $$$ISERR(sc) Return results
	
	Set rs = stmt.%Execute(srcId, pred1, pred2, srcId, pred1)
	While rs.%Next() {
		Set obj = ##class(%DynamicObject).%New()
		Do obj.%Set("id", rs.%Get("path_id"))
		Do obj.%Set("step", rs.%Get("step"))
		Do obj.%Set("s", rs.%Get("s"))
		Do obj.%Set("p", rs.%Get("p"))
		Do obj.%Set("o", rs.%Get("o_id"))
		Do results.%Push(obj)
	}
	Return results
}

/// Reciprocal Rank Fusion combining vector and text search results
ClassMethod kgRRFFUSE(k As %Integer, k1 As %Integer, k2 As %Integer, c As %Integer, queryVector As %String, queryText As %String) As %DynamicArray [ SqlName = kg_RRF_FUSE, SqlProc ]
{
	Set k = ..ValidateK(k)
	Set k1 = ..ValidateK(k1)
	Set k2 = ..ValidateK(k2)
	
	// Get results from existing methods
	Set vectorResults = ..kgKNNVEC(queryVector, k1, "")
	Set textResults = ..kgTXT(queryText, k2, 0)
	
	Kill data
	Set iter = vectorResults.%GetIterator()
	Set rank = 0
	While iter.%GetNext(.key, .val) {
		Set rank = rank + 1
		Set id = val.id
		Set data(id, "v_rank") = rank
		Set data(id, "v_score") = val.score
	}
	Set iter = textResults.%GetIterator()
	Set rank = 0
	While iter.%GetNext(.key, .val) {
		Set rank = rank + 1
		Set id = val.id
		Set data(id, "t_rank") = rank
		Set data(id, "t_score") = val.score
	}
	Kill scores
	Set id = ""
	For {
		Set id = $Order(data(id))
		If id = "" Quit
		Set vRank = $Get(data(id, "v_rank"), 1000000)
		Set tRank = $Get(data(id, "t_rank"), 1000000)
		Set vScore = $Get(data(id, "v_score"), 0)
		Set tScore = $Get(data(id, "t_score"), 0)
		Set rrfScore = (1.0 / (c + vRank)) + (1.0 / (c + tRank))
		Set scores(-rrfScore, id) = $ListBuild(vScore, tScore)
	}
	Set results = ##class(%DynamicArray).%New()
	Set rrfScoreNeg = ""
	Set count = 0
	For {
		Set rrfScoreNeg = $Order(scores(rrfScoreNeg))
		If rrfScoreNeg = "" Quit
		Set id = ""
		For {
			Set id = $Order(scores(rrfScoreNeg, id), 1, val)
			If id = "" Quit
			Set count = count + 1
			If count > k Return results
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("id", id)
			Do obj.%Set("rrf_score", -rrfScoreNeg)
			Do obj.%Set("vector_score", $List(val, 1))
			Do obj.%Set("text_score", $List(val, 2))
			Do results.%Push(obj)
		}
		If count >= k Quit
	}
	Return results
}

}
