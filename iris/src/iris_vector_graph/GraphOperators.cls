/// IRIS Vector Graph-AI Operators - ObjectScript Class with Embedded Python
///
/// This class implements the graph retrieval operators as IRIS methods using embedded Python
/// for complex vector operations while leveraging IRIS SQL for database access.
Class iris.vector.graph.GraphOperators Extends %RegisteredObject
{

/// Vector similarity search using embedded Python computation
ClassMethod kg_KNN_VEC(queryVector As %String, k As %Integer = 50, labelFilter As %String = "") As %SQL.StatementResult [ Language = python ]
{
    import json
    import numpy as np

    # Parse query vector from JSON string
    query_array = np.array(json.loads(queryVector))

    # Execute SQL query to get embeddings
    if labelFilter == "":
        sql = """
            SELECT n.id, n.emb
            FROM kg_NodeEmbeddings n
            WHERE n.emb IS NOT NULL
        """
        statement = iris.sql.prepare(sql)
        result = statement.execute()
    else:
        sql = """
            SELECT n.id, n.emb
            FROM kg_NodeEmbeddings n
            LEFT JOIN rdf_labels L ON L.s = n.id
            WHERE n.emb IS NOT NULL
              AND L.label = ?
        """
        statement = iris.sql.prepare(sql)
        result = statement.execute(labelFilter)

    # Compute similarities in Python
    similarities = []
    while result.fetch():
        entity_id = result.get_data(1)
        emb_csv = result.get_data(2)

        try:
            # Parse CSV embedding to numpy array
            emb_array = np.array([float(x) for x in emb_csv.split(',')])

            # Compute cosine similarity
            cos_sim = np.dot(query_array, emb_array) / (
                np.linalg.norm(query_array) * np.linalg.norm(emb_array)
            )
            similarities.append((entity_id, float(cos_sim)))
        except Exception as e:
            print(f"Failed to process embedding for {entity_id}: {e}")
            continue

    # Sort by similarity and return top k
    similarities.sort(key=lambda x: x[1], reverse=True)
    top_results = similarities[:k]

    # Return as IRIS result set
    result_set = iris.sql.create_result_set()
    for entity_id, score in top_results:
        result_set.add_row([entity_id, score])

    return result_set
}

/// Text search in RDF qualifiers and object IDs
ClassMethod kg_TXT(queryText As %String, k As %Integer = 50) As %SQL.StatementResult [ Language = python ]
{
    # Simple text search using SQL LIKE
    search_pattern = f'%{queryText}%'

    sql = f"""
        SELECT TOP {k}
            e.s AS entity_id,
            (
                CASE WHEN e.qualifiers LIKE ? THEN 1.0 ELSE 0.0 END +
                CASE WHEN e.o_id LIKE ? THEN 0.5 ELSE 0.0 END
            ) AS bm25_score
        FROM rdf_edges e
        WHERE e.qualifiers LIKE ?
           OR e.o_id LIKE ?
        ORDER BY bm25_score DESC
    """

    statement = iris.sql.prepare(sql)
    result = statement.execute(search_pattern, search_pattern, search_pattern, search_pattern)

    # Convert to result set
    result_set = iris.sql.create_result_set()
    while result.fetch():
        entity_id = result.get_data(1)
        score = result.get_data(2)
        result_set.add_row([entity_id, score])

    return result_set
}

/// Reciprocal Rank Fusion of vector and text search results
ClassMethod kg_RRF_FUSE(k As %Integer = 50, k1 As %Integer = 200, k2 As %Integer = 200, c As %Integer = 60, queryVector As %String = "", queryText As %String = "") As %SQL.StatementResult [ Language = python ]
{
    # Get vector search results
    vector_results = []
    if queryVector:
        vector_rs = iris.vector.graph.GraphOperators.kg_KNN_VEC(queryVector, k1, "")
        while vector_rs.fetch():
            entity_id = vector_rs.get_data(1)
            score = vector_rs.get_data(2)
            vector_results.append((entity_id, score))

    # Get text search results
    text_results = []
    if queryText:
        text_rs = iris.vector.graph.GraphOperators.kg_TXT(queryText, k2)
        while text_rs.fetch():
            entity_id = text_rs.get_data(1)
            score = text_rs.get_data(2)
            text_results.append((entity_id, score))

    # Create ranking dictionaries
    vector_ranks = {entity_id: rank + 1 for rank, (entity_id, _) in enumerate(vector_results)}
    text_ranks = {entity_id: rank + 1 for rank, (entity_id, _) in enumerate(text_results)}

    # Get all unique entity IDs
    all_entities = set(vector_ranks.keys()) | set(text_ranks.keys())

    # Calculate RRF scores
    rrf_scores = []
    for entity_id in all_entities:
        # Get original scores
        vector_score = next((score for eid, score in vector_results if eid == entity_id), 0.0)
        text_score = next((score for eid, score in text_results if eid == entity_id), 0.0)

        # Calculate RRF score
        vector_rank = vector_ranks.get(entity_id, 1000000)
        text_rank = text_ranks.get(entity_id, 1000000)
        rrf_score = (1.0 / (c + vector_rank)) + (1.0 / (c + text_rank))

        rrf_scores.append((entity_id, rrf_score, vector_score, text_score))

    # Sort by RRF score and return top k
    rrf_scores.sort(key=lambda x: x[1], reverse=True)
    top_results = rrf_scores[:k]

    # Return as IRIS result set
    result_set = iris.sql.create_result_set()
    for entity_id, rrf_score, vector_score, text_score in top_results:
        result_set.add_row([entity_id, rrf_score, vector_score, text_score])

    return result_set
}

/// Simple graph path traversal
ClassMethod kg_GRAPH_PATH(srcId As %String, pred1 As %String, pred2 As %String, maxHops As %Integer = 2) As %SQL.StatementResult [ Language = objectscript ]
{
    // Use ObjectScript for simple SQL operations
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

    // Create result set
    Set resultSet = ##class(%SQL.StatementResult).%New()

    For {
        &sql(FETCH path_cursor INTO :pathId, :step, :s, :p, :o)
        Quit:SQLCODE'=0
        Do resultSet.%AddRow($LB(pathId, step, s, p, o))
    }

    &sql(CLOSE path_cursor)

    Return resultSet
}

/// Rerank using RRF fusion (passthrough)
ClassMethod kg_RERANK(topN As %Integer, queryVector As %String, queryText As %String) As %SQL.StatementResult [ Language = python ]
{
    # Call RRF fusion and return only entity_id and rrf_score
    rrf_rs = iris.vector.graph.GraphOperators.kg_RRF_FUSE(topN, 200, 200, 60, queryVector, queryText)

    result_set = iris.sql.create_result_set()
    while rrf_rs.fetch():
        entity_id = rrf_rs.get_data(1)
        rrf_score = rrf_rs.get_data(2)
        result_set.add_row([entity_id, rrf_score])

    return result_set
}

}