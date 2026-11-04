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
/// A namespace with no registry (one from before spec 226) does not route, so it answers
/// with the legacy table, and NoteNoRegistry says so. Any other failure to read the
/// registry raises (4.6.0 R146): it fell back too, so a user who may not read the
/// registry got the default pair's table for a named graph without a word.
ClassMethod getVectorTable(graph As %String = "", modelKey As %String = "") As %String [ Language = python ]
{
    import iris

    pyops = iris.cls("Graph.KG.PyOps")
    legacy = pyops.getLegacyVectorTable()
    graph_id = str(graph) if graph else ""
    model = str(modelKey) if modelKey else ""

    sql = (
        "SELECT table_name FROM Graph_KG.embedding_registry "
        "WHERE COALESCE(graph_id, '') = ? AND COALESCE(model_key, '') = ? "
        "AND COALESCE(kind, 'node') = 'node'"
    )
    rows = []
    try:
        rs = iris.sql.exec(sql, graph_id, model)
        rows = list(rs)
        sqlcode, message = rs.ResultSet._SQLCODE, rs.ResultSet._Message
    except Exception as exc:
        sqlcode = getattr(exc, "sqlcode", None)
        if sqlcode == -30:
            # No registry: this database does not route, so 3.2.0's table is still
            # where its vectors are.
            pyops.NoteNoRegistry("getVectorTable")
            return legacy
        if sqlcode is None:
            raise
        message = str(exc)
    failure = pyops.ReadFailure("getVectorTable", sqlcode, message, "Graph_KG.embedding_registry", "")
    if failure:
        raise (PermissionError if sqlcode == -99 else RuntimeError)(failure)
    for row in rows:
        if row[0]:
            name = str(row[0])
            return name if "." in name else "Graph_KG." + name

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
/// registry nor INFORMATION_SCHEMA can answer. A namespace with no registry (pre-226)
/// goes on to the column's width; any other failure to read the registry raises
/// (4.6.0 R146), as in getVectorTable.
ClassMethod getExpectedDimension(graph As %String = "", modelKey As %String = "") As %Integer [ Language = python ]
{
    import iris

    pyops = iris.cls("Graph.KG.PyOps")
    column = pyops.getVectorColumn()
    default_dim = pyops.getDefaultDimension()
    graph_id = str(graph) if graph else ""
    model = str(modelKey) if modelKey else ""

    declared = (
        "SELECT dimension FROM Graph_KG.embedding_registry "
        "WHERE COALESCE(graph_id, '') = ? AND COALESCE(model_key, '') = ? "
        "AND COALESCE(kind, 'node') = 'node'"
    )
    rows = []
    try:
        rs = iris.sql.exec(declared, graph_id, model)
        rows = list(rs)
        sqlcode, message = rs.ResultSet._SQLCODE, rs.ResultSet._Message
    except Exception as exc:
        sqlcode = getattr(exc, "sqlcode", None)
        if sqlcode is None:
            raise
        message = str(exc)
        if sqlcode == -30:
            pyops.NoteNoRegistry("getExpectedDimension")
            sqlcode = 100
    failure = pyops.ReadFailure("getExpectedDimension", sqlcode, message, "Graph_KG.embedding_registry", "")
    if failure:
        raise (PermissionError if sqlcode == -99 else RuntimeError)(failure)
    for row in rows:
        if row[0]:
            return int(row[0])

    table_full = iris.cls("Graph.KG.PyOps").getVectorTable(graph_id, model)
    if not table_full:
        # The pair has no route and is not the default pair, so there is no width to
        # report. The default is the only honest answer left, and a write to this
        # pair will declare its own width when it creates the route.
        return default_dim

    if "." in table_full:
        schema, table = table_full.split(".")
    else:
        schema, table = "Graph_KG", table_full

    sql = "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?"
    rs = iris.sql.exec(sql, schema, table, column)

    for row in rs:
        if row[0]:
            char_length = int(row[0])
            dimension = round(char_length / 346)
            return dimension

    # INFORMATION_SCHEMA shows a table's columns only to a user with a privilege on it. A user without SELECT
    # on the vector table got the default width here, and its search failed on "dimension 8 does not match
    # expected 768" instead of naming the table it may not read (4.6.0 R143).
    lacks = iris.cls("Graph.KG.PyOps").Lacks(table_full, "")
    if lacks:
        raise PermissionError(
            "getExpectedDimension: " + lacks + ", so the width of its column " + column + " cannot be read"
        )
    return default_dim
}

/// What the current user may not use: SELECT on each of pTables and EXECUTE on each of pProcedures (comma-separated
/// SQL names), as "USER lacks SELECT on S.t, EXECUTE on S.p"; "" when it may use them all (4.6.0 R143).
ClassMethod Lacks(pTables As %String = "", pProcedures As %String = "") As %String [ Internal ]
{
	Set tDenied = ""
	For i = 1:1:$Length(pProcedures, ",") {
		Set tName = $Piece(pProcedures, ",", i)
		Continue:tName=""
		If '$SYSTEM.SQL.Security.CheckPrivilege($Username, 9, tName, "e") Set tDenied = tDenied_$ListBuild("EXECUTE on "_tName)
	}
	For i = 1:1:$Length(pTables, ",") {
		Set tName = $Piece(pTables, ",", i)
		Continue:tName=""
		If '$SYSTEM.SQL.Security.CheckPrivilege($Username, 1, tName, "s") Set tDenied = tDenied_$ListBuild("SELECT on "_tName)
	}
	If tDenied = "" Quit ""
	Quit $Username_" lacks "_$ListToString(tDenied, ", ")
}

/// The failure a read through iris.sql reported, as text; "" when it reported none (pSQLCODE 0, 100 or "").
///
/// A procedure checks the tables it reads when the first row is fetched, and iris.sql's iteration stops at a
/// failed fetch as if no row were left: the -99 is only on ResultSet.%SQLCODE, so VectorSearch and HybridSearch
/// answered [] to a user who may not read a table (4.6.0 R143). IRIS's message names neither the table nor the
/// privilege; a -99 here says which of pTables and pProcedures the user lacks.
ClassMethod ReadFailure(pWhat As %String, pSQLCODE As %String, pMessage As %String, pTables As %String = "", pProcedures As %String = "") As %String [ Internal ]
{
	If (pSQLCODE = "") || (pSQLCODE '< 0) Quit ""
	Set tText = pWhat_" failed: SQLCODE "_pSQLCODE_" "_pMessage
	If pSQLCODE = -99 {
		Set tLacks = ..Lacks(pTables, pProcedures)
		If tLacks '= "" Set tText = tText_"; "_tLacks
	}
	Quit tText
}

/// Note in messages.log, at information severity and once per process, that this namespace has no
/// embedding_registry (one from before spec 226), so pWhat answered from the legacy vector table (4.6.0 R146).
ClassMethod NoteNoRegistry(pWhat As %String) [ Internal ]
{
	Quit:$Data(^||ivgPyOpsNoRegistry)
	Set ^||ivgPyOpsNoRegistry = 1
	Do ##class(%SYS.System).WriteToConsoleLog("IVG Graph.KG.PyOps."_pWhat_": no Graph_KG.embedding_registry in namespace "_$Namespace_" (before spec 226), so the legacy vector table answers; initialize_schema() creates it", 0, 0)
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
    # "", not None: iris.sql.exec binds None so that `:labelFilter IS NULL` is false and the label join
    # matches nothing, so a search with no label answered [] (4.6.0 R143).
    label = str(label) if label else ""

    # Qualified, and read as a table: an unqualified CALL resolves in SQLUser, where IVG defines no
    # procedure, and a CALL through iris.sql.exec iterates no rows even when it does resolve, so
    # this answered [] for every query (4.6.0, R078). Graph_KG is the deploy's token for the
    # namespace's IVG Schema.
    sql = "SELECT node_id, score FROM Graph_KG.kg_KNN_VEC(?, ?, ?, ?)"
    try:
        rs = iris.sql.exec(sql, vector_json, k, label, graph)
        rows = list(rs)  # iris.sql results are iterated; they have no fetchall()
        sqlcode, message = rs.ResultSet._SQLCODE, rs.ResultSet._Message
    except Exception as exc:
        if getattr(exc, "sqlcode", None) is None:
            raise
        sqlcode, message = exc.sqlcode, str(exc)
    # The iteration stops at a failed fetch as if no row were left (4.6.0 R143): read the result's SQLCODE.
    failure = iris.cls("Graph.KG.PyOps").ReadFailure(
        "kg_KNN_VEC", sqlcode, message, "Graph_KG.kg_NodeEmbeddings,Graph_KG.rdf_labels", "Graph_KG.kg_KNN_VEC"
    )
    if failure:
        raise (PermissionError if sqlcode == -99 else RuntimeError)(failure)
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

    # Qualified and read as a table, as in VectorSearch (4.6.0, R078)
    sql = "SELECT id, rrf, vs, ts FROM Graph_KG.kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)"
    try:
        rs = iris.sql.exec(sql, k, 200, 200, c, vector_json, text, graph)
        rows = list(rs)  # iris.sql results are iterated; they have no fetchall()
        sqlcode, message = rs.ResultSet._SQLCODE, rs.ResultSet._Message
    except Exception as exc:
        if getattr(exc, "sqlcode", None) is None:
            raise
        sqlcode, message = exc.sqlcode, str(exc)
    # As in VectorSearch (4.6.0 R143); kg_RRF_FUSE reads through kg_KNN_VEC and kg_TXT.
    failure = iris.cls("Graph.KG.PyOps").ReadFailure(
        "kg_RRF_FUSE", sqlcode, message,
        "Graph_KG.kg_NodeEmbeddings,Graph_KG.rdf_labels,Graph_KG.docs",
        "Graph_KG.kg_RRF_FUSE,Graph_KG.kg_KNN_VEC,Graph_KG.kg_TXT",
    )
    if failure:
        raise (PermissionError if sqlcode == -99 else RuntimeError)(failure)
    out = iris.cls('%DynamicArray')._New()
    for r in rows:
        obj = iris.cls('%DynamicObject')._New()
        obj._Set("id", r[0])
        obj._Set("score", float(r[1]))
        extras = iris.cls('%DynamicObject')._New()
        # A leg that did not find the row answers SQL NULL, which iris.sql hands over as "". It is
        # set as JSON null: a Python None set into a %DynamicObject is a %SYS.Python reference,
        # and %ToJSON of the result then fails (METHOD NOT SUPPORTED).
        for key, value in (("vs", r[2]), ("bm25", r[3])):
            if value in (None, ""):
                extras._Set(key, "", "null")
            else:
                extras._Set(key, float(value))
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
