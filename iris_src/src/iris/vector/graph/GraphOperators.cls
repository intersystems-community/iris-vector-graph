Class iris.vector.graph.GraphOperators Extends %RegisteredObject
{

ClassMethod kgKNNVEC(queryVector As %String, k As %Integer = 50, labelFilter As %String = "") As %DynamicArray [ Language = python ]
{
    import iris
    import json
    import numpy as np

    query_array = np.array(json.loads(queryVector))

    if labelFilter == "":
        sql = "SELECT n.id, n.emb FROM kg_NodeEmbeddings n WHERE n.emb IS NOT NULL"
        rs = iris.sql.exec(sql)
    else:
        sql = "SELECT n.id, n.emb FROM kg_NodeEmbeddings n LEFT JOIN rdf_labels L ON L.s = n.id WHERE n.emb IS NOT NULL AND L.label = ?"
        rs = iris.sql.exec(sql, labelFilter)

    similarities = []
    for row in rs:
        entity_id = row[0]
        emb_csv = row[1]
        try:
            emb_array = np.array([float(x) for x in emb_csv.split(',')])
            cos_sim = np.dot(query_array, emb_array) / (np.linalg.norm(query_array) * np.linalg.norm(emb_array))
            similarities.append((entity_id, float(cos_sim)))
        except Exception:
            continue

    similarities.sort(key=lambda x: x[1], reverse=True)
    top_results = similarities[:k]

    results = iris.cls('%DynamicArray')._New()
    for entity_id, score in top_results:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("id", entity_id)
        obj._Set("score", score)
        results._Push(obj)

    return results
}

ClassMethod kgTXT(queryText As %String, k As %Integer = 50) As %DynamicArray [ Language = python ]
{
    import iris
    search_pattern = f'%{queryText}%'
    sql = f"SELECT TOP {k} e.s AS entity_id, (CASE WHEN e.qualifiers LIKE ? THEN 1.0 ELSE 0.0 END + CASE WHEN e.o_id LIKE ? THEN 0.5 ELSE 0.0 END) AS bm25_score FROM rdf_edges e WHERE e.qualifiers LIKE ? OR e.o_id LIKE ? ORDER BY bm25_score DESC"
    rs = iris.sql.exec(sql, search_pattern, search_pattern, search_pattern, search_pattern)
    
    results = iris.cls('%DynamicArray')._New()
    for row in rs:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("id", row[0])
        obj._Set("score", float(row[1]) if row[1] else 0.0)
        results._Push(obj)
    return results
}

ClassMethod kgRRF_FUSE(k As %Integer = 50, k1 As %Integer = 200, k2 As %Integer = 200, c As %Integer = 60, queryVector As %String = "", queryText As %String = "") As %DynamicArray [ Language = python ]
{
    import iris
    vector_results = []
    if queryVector:
        vector_arr = iris.cls("iris.vector.graph.GraphOperators").kgKNNVEC(queryVector, k1, "")
        for i in range(vector_arr._Size()):
            obj = vector_arr._Get(i)
            vector_results.append((obj._Get("id"), float(obj._Get("score"))))

    text_results = []
    if queryText:
        text_arr = iris.cls("iris.vector.graph.GraphOperators").kgTXT(queryText, k2)
        for i in range(text_arr._Size()):
            obj = text_arr._Get(i)
            text_results.append((obj._Get("id"), float(obj._Get("score"))))

    vector_ranks = {entity_id: rank + 1 for rank, (entity_id, _) in enumerate(vector_results)}
    text_ranks = {entity_id: rank + 1 for rank, (entity_id, _) in enumerate(text_results)}
    all_entities = set(vector_ranks.keys()) | set(text_ranks.keys())

    rrf_scores = []
    for entity_id in all_entities:
        vector_score = next((score for eid, score in vector_results if eid == entity_id), 0.0)
        text_score = next((score for eid, score in text_results if eid == entity_id), 0.0)
        vector_rank = vector_ranks.get(entity_id, 1000000)
        text_rank = text_ranks.get(entity_id, 1000000)
        rrf_score = (1.0 / (c + vector_rank)) + (1.0 / (c + text_rank))
        rrf_scores.append((entity_id, rrf_score, vector_score, text_score))

    rrf_scores.sort(key=lambda x: x[1], reverse=True)
    top_results = rrf_scores[:k]

    results = iris.cls('%DynamicArray')._New()
    for entity_id, rrf_score, vector_score, text_score in top_results:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("id", entity_id)
        obj._Set("rrf_score", rrf_score)
        obj._Set("vector_score", vector_score)
        obj._Set("text_score", text_score)
        results._Push(obj)
    return results
}

ClassMethod kgGRAPH_PATH(srcId As %String, pred1 As %String, pred2 As %String, maxHops As %Integer = 2) As %DynamicArray [ Language = cache ]
{
	&sql(DECLARE path_cursor CURSOR FOR
	     SELECT 1 AS path_id, 1 AS step, e1.s, e1.p, e1.o_id
	     FROM rdf_edges e1
	     WHERE e1.s = :srcId AND e1.p = :pred1
	     UNION ALL
	     SELECT 1 AS path_id, 2 AS step, e2.s, e2.p, e2.o_id
	     FROM rdf_edges e2
	     WHERE e2.p = :pred2
	       AND EXISTS (
	         SELECT 1 FROM rdf_edges e1
	         WHERE e1.s = :srcId AND e1.p = :pred1 AND e1.o_id = e2.s
	       )
	     ORDER BY step)

	&sql(OPEN path_cursor)
	Set results = []
	For {
		&sql(FETCH path_cursor INTO :pathId, :step, :s, :p, :o)
		Quit:SQLCODE'=0
		Set obj = {"id": (pathId), "step": (step), "s": (s), "p": (p), "o": (o)}
		Do results.%Push(obj)
	}
	&sql(CLOSE path_cursor)
	Return results
}

ClassMethod kgRERANK(topN As %Integer, queryVector As %String, queryText As %String) As %DynamicArray [ Language = python ]
{
    import iris
    rrf_arr = iris.cls("iris.vector.graph.GraphOperators").kgRRF_FUSE(topN, 200, 200, 60, queryVector, queryText)
    results = iris.cls('%DynamicArray')._New()
    for i in range(rrf_arr._Size()):
        obj = rrf_arr._Get(i)
        result_obj = iris.cls('%DynamicObject')._New()
        result_obj._Set("id", obj._Get("id"))
        result_obj._Set("score", obj._Get("rrf_score"))
        results._Push(result_obj)
    return results
}

}
