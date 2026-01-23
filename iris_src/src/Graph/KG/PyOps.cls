Class Graph.KG.PyOps Extends %RegisteredObject
{

ClassMethod VectorSearch(vec As %DynamicArray, k As %Integer = 50, label As %String = "") As %DynamicArray [ Language = python ]
{
    import iris
    import json

    if vec is None or vec._Size() == 0:
        raise ValueError("vector required")
    if vec._Size() != 768:
        raise ValueError("Expected 768-dimensional vector (biomedical embeddings like BioBERT/PubMedBERT)")

    # Convert DynamicArray to JSON string for SQL procedure
    vector_list = []
    for i in range(vec._Size()):
        vector_list.append(float(vec._Get(i)))

    vector_json = json.dumps(vector_list)
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
    import json

    if vec is None or vec._Size() != 768:
        raise ValueError("Expected 768-dimensional vector (biomedical embeddings like BioBERT/PubMedBERT)")

    # Convert DynamicArray to JSON string for SQL procedure
    vector_list = []
    for i in range(vec._Size()):
        vector_list.append(float(vec._Get(i)))

    vector_json = json.dumps(vector_list)
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
