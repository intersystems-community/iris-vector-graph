Class Graph.KG.PyOps Extends %RegisteredObject
{

/// Get default embedding dimension (768 for BioBERT/PubMedBERT)
/// Common values: 384 (all-MiniLM-L6-v2), 768 (BioBERT), 1536 (ada-002)
/// Note: Using ClassMethod instead of Parameter due to IRIS Python-method compatibility issue
ClassMethod getDefaultDimension() As %Integer
{
    Return 768
}

/// Get table name containing vector embeddings for dimension inference
ClassMethod getVectorTable() As %String
{
    Return "kg_NodeEmbeddings"
}

/// Get column name containing vector data for dimension inference  
ClassMethod getVectorColumn() As %String
{
    Return "embedding"
}

/// Get expected embedding dimension from database schema
/// Uses iris-vector-rag compatible formula: round(CHARACTER_MAXIMUM_LENGTH / 346)
/// Falls back to getDefaultDimension() if schema lookup fails
ClassMethod getExpectedDimension() As %Integer [ Language = python ]
{
    import iris
    
    table = iris.cls("Graph.KG.PyOps").getVectorTable()
    column = iris.cls("Graph.KG.PyOps").getVectorColumn()
    default_dim = iris.cls("Graph.KG.PyOps").getDefaultDimension()
    
    sql = "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ? AND COLUMN_NAME = ?"
    rs = iris.sql.exec(sql, table, column)
    
    for row in rs:
        if row[0]:
            char_length = int(row[0])
            dimension = round(char_length / 346)
            return dimension
    
    return default_dim
}

/// Convert DynamicArray vector to JSON string with validation
/// Validates dimension against database schema for consistency with indexed vectors
ClassMethod vectorToJson(vec As %DynamicArray, expectedDim As %Integer = 0) As %String [ Internal, Language = python ]
{
    import iris
    import json
    
    if vec is None or not hasattr(vec, '_Size'):
        raise ValueError("vector required")
    
    if expectedDim and expectedDim > 0:
        dim = int(expectedDim)
    else:
        dim = iris.cls("Graph.KG.PyOps").getExpectedDimension()
    
    actual_dim = vec._Size()
    if actual_dim != dim:
        raise ValueError("Query embedding dimension " + str(actual_dim) + " does not match expected " + str(dim))
    
    vector_list = []
    for i in range(vec._Size()):
        val = vec._Get(i)
        vector_list.append(float(val))
    
    return json.dumps(vector_list)
}

ClassMethod VectorSearch(vec As %DynamicArray, k As %Integer = 50, label As %String = "") As %DynamicArray [ Language = python ]
{
    import iris

    vector_json = iris.cls("Graph.KG.PyOps").vectorToJson(vec)
    k = int(k) if k else 50
    label = str(label) if label else None

    sql = "CALL kg_KNN_VEC(?, ?, ?)"
    rs = iris.sql.exec(sql, vector_json, k, label)
    rows = rs.fetchall()
    out = iris.cls('%DynamicArray')._New()
    for r in rows:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("id", r[0])
        obj._Set("score", float(r[1]))
        out._Push(obj)
    return out
}

ClassMethod HybridSearch(vec As %DynamicArray, text As %String, k As %Integer = 50, c As %Integer = 60) As %DynamicArray [ Language = python ]
{
    import iris

    vector_json = iris.cls("Graph.KG.PyOps").vectorToJson(vec)
    k = int(k) if k else 50
    c = int(c) if c else 60
    text = str(text) if text else ""

    sql = "CALL kg_RRF_FUSE(?, ?, ?, ?, ?, ?)"
    rs = iris.sql.exec(sql, k, 200, 200, c, vector_json, text)
    rows = rs.fetchall()
    out = iris.cls('%DynamicArray')._New()
    for r in rows:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("id", r[0])
        obj._Set("score", float(r[1]))
        extras = iris.cls('%DynamicObject')._New()
        extras._Set("vs", float(r[2]) if r[2] is not None else None)
        extras._Set("bm25", float(r[3]) if r[3] is not None else None)
        obj._Set("extras", extras)
        out._Push(obj)
    return out
}

ClassMethod MetaPath(srcId As %String, preds As %DynamicArray, maxHops As %Integer = 2, dstLabel As %String = "") As %DynamicArray [ Language = python ]
{
    import iris
    res = iris.cls("Graph.KG.Traversal").BFS_JSON(srcId, preds, int(maxHops), str(dstLabel) if dstLabel else "")
    return res
}

}
