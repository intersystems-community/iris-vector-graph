Class Graph.KG.PyOps Extends %RegisteredObject
{

/// Get default embedding dimension (768 for BioBERT/PubMedBERT)
/// Common values: 384 (all-MiniLM-L6-v2), 768 (BioBERT), 1536 (ada-002)
/// Note: Using ClassMethod instead of Parameter due to IRIS Python-method compatibility issue
ClassMethod getDefaultDimension() As %Integer
{
    Return 768
}

/// The legacy node-embedding table: where the default pair's vectors live.
///
/// Spec 227 routes every other pair elsewhere, so this is the answer for the default
/// graph with no model declared and for nothing else. Use getVectorTable() to find
/// where a given pair's vectors actually are.
ClassMethod getLegacyVectorTable() As %String
{
    Return "Graph_KG.kg_NodeEmbeddings"
}

/// The table holding this (graph, model) pair's node vectors (spec 230, FR-014).
///
/// Reads Graph_KG.embedding_registry, the same way `_route_for_read` does in Python.
/// A pair with a route gets its routed table; the default pair — default graph, no
/// model — gets the legacy table whether or not a registry row says so, because
/// that is where 3.2.0 put its vectors (spec 227 FR-015). Any other pair with no
/// route resolves to nothing, and returns "": substituting another pair's table
/// would report a width the write cannot satisfy.
///
/// An unreadable registry (a namespace built by DDL alone, or a pre-226 one) is not
/// a claim that there are no routes, so it answers with the legacy table.
ClassMethod getVectorTable(graph As %String = "", modelKey As %String = "") As %String [ Language = python ]
{
    import iris

    legacy = iris.cls("Graph.KG.PyOps").getLegacyVectorTable()
    graph_id = str(graph) if graph else ""
    model = str(modelKey) if modelKey else ""

    sql = (
        "SELECT table_name FROM Graph_KG.embedding_registry "
        "WHERE COALESCE(graph_id, '') = ? AND COALESCE(model_key, '') = ? "
        "AND COALESCE(kind, 'node') = 'node'"
    )
    try:
        rs = iris.sql.exec(sql, graph_id, model)
        for row in rs:
            if row[0]:
                name = str(row[0])
                return name if "." in name else "Graph_KG." + name
    except Exception:
        # No registry to read: this database does not route, so 3.2.0's table is
        # still where its vectors are.
        return legacy

    if graph_id == "" and model == "":
        return legacy
    return ""
}

/// Get column name containing vector data for dimension inference
/// The column is "emb" — it has never been called "embedding" on any shipped schema,
/// so the INFORMATION_SCHEMA lookup in getExpectedDimension() matched no row and the
/// method fell back to getDefaultDimension() on every call. 768 being both the default
/// and the usual declared width is why that went unnoticed.
ClassMethod getVectorColumn() As %String
{
    Return "emb"
}

/// The width a write to this (graph, model) pair must have (spec 230, FR-014).
///
/// Before 230 this read the literal Graph_KG.kg_NodeEmbeddings on every call, so a
/// routed table narrower or wider than the legacy one was reported at the legacy
/// width — and vectorToJson() rejected every correct write to it on that number.
///
/// Two sources, in this order:
///
/// 1. the route's declared `dimension` in Graph_KG.embedding_registry, which is what
///    the CREATE TABLE declared and therefore what IRIS enforces at INSERT (-104);
/// 2. the column's own declared width, via the iris-vector-rag formula
///    round(CHARACTER_MAXIMUM_LENGTH / 346), for a table with no registry row —
///    which is the default pair's usual state.
///
/// getDefaultDimension() is the last resort, and only reachable when neither the
/// registry nor INFORMATION_SCHEMA can answer.
ClassMethod getExpectedDimension(graph As %String = "", modelKey As %String = "") As %Integer [ Language = python ]
{
    import iris

    column = iris.cls("Graph.KG.PyOps").getVectorColumn()
    default_dim = iris.cls("Graph.KG.PyOps").getDefaultDimension()
    graph_id = str(graph) if graph else ""
    model = str(modelKey) if modelKey else ""

    declared = (
        "SELECT dimension FROM Graph_KG.embedding_registry "
        "WHERE COALESCE(graph_id, '') = ? AND COALESCE(model_key, '') = ? "
        "AND COALESCE(kind, 'node') = 'node'"
    )
    try:
        rs = iris.sql.exec(declared, graph_id, model)
        for row in rs:
            if row[0]:
                return int(row[0])
    except Exception:
        pass

    table_full = iris.cls("Graph.KG.PyOps").getVectorTable(graph_id, model)
    if not table_full:
        # The pair has no route and is not the default pair, so there is no width to
        # report. The default is the only honest answer left, and a write to this
        # pair will declare its own width when it creates the route.
        return default_dim

    if "." in table_full:
        schema, table = table_full.split(".")
    else:
        schema, table = "SQLUser", table_full

    sql = "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?"
    rs = iris.sql.exec(sql, schema, table, column)

    for row in rs:
        if row[0]:
            char_length = int(row[0])
            dimension = round(char_length / 346)
            return dimension

    return default_dim
}

/// Convert DynamicArray vector to JSON string with validation
///
/// The width is the one the write will land in, so the pair has to reach here:
/// validating a graph A vector against the legacy table's width rejected it for
/// being the width graph A's route declares (spec 230, FR-014).
ClassMethod vectorToJson(vec As %DynamicArray, expectedDim As %Integer = 0, graph As %String = "", modelKey As %String = "") As %String [ Internal, Language = python ]
{
    import iris
    import json

    if vec is None or not hasattr(vec, '_Size'):
        raise ValueError("vector required")

    if expectedDim and expectedDim > 0:
        dim = int(expectedDim)
    else:
        dim = iris.cls("Graph.KG.PyOps").getExpectedDimension(
            str(graph) if graph else "", str(modelKey) if modelKey else ""
        )

    actual_dim = vec._Size()
    if actual_dim != dim:
        raise ValueError("Query embedding dimension " + str(actual_dim) + " does not match expected " + str(dim))
    
    vector_list = []
    for i in range(vec._Size()):
        val = vec._Get(i)
        vector_list.append(float(val))
    
    return json.dumps(vector_list)
}

ClassMethod VectorSearch(vec As %DynamicArray, k As %Integer = 50, label As %String = "", graph As %String = "") As %DynamicArray [ Language = python ]
{
    import iris

    # kg_KNN_VEC takes graphId in 4.0.0 (spec 227), so the three-argument CALL this
    # method used to issue was an arity error at runtime — the /vectorSearch endpoint
    # could not answer at all. "" is the default graph, never every graph.
    graph = str(graph) if graph else ""
    vector_json = iris.cls("Graph.KG.PyOps").vectorToJson(vec, 0, graph, "")
    k = int(k) if k else 50
    label = str(label) if label else None

    sql = "CALL kg_KNN_VEC(?, ?, ?, ?)"
    rs = iris.sql.exec(sql, vector_json, k, label, graph)
    rows = rs.fetchall()
    out = iris.cls('%DynamicArray')._New()
    for r in rows:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("id", r[0])
        obj._Set("score", float(r[1]))
        out._Push(obj)
    return out
}

ClassMethod HybridSearch(vec As %DynamicArray, text As %String, k As %Integer = 50, c As %Integer = 60, graph As %String = "") As %DynamicArray [ Language = python ]
{
    import iris

    k = int(k) if k else 50
    c = int(c) if c else 60
    text = str(text) if text else ""
    # kg_RRF_FUSE takes graphId in 4.0.0 (spec 227 FR-023), so a six-argument CALL
    # is now an arity error at runtime. "" is the default graph, never every graph.
    graph = str(graph) if graph else ""
    vector_json = iris.cls("Graph.KG.PyOps").vectorToJson(vec, 0, graph, "")

    sql = "CALL kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)"
    rs = iris.sql.exec(sql, k, 200, 200, c, vector_json, text, graph)
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
    res = iris.cls("Graph.KG.Traversal").BFSJSON(srcId, preds, int(maxHops), str(dstLabel) if dstLabel else "")
    return res
}

}
