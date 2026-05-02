/// IVG Cypher Engine - submit Cypher queries from ObjectScript.
/// Works against the local IRIS instance or any remote IRIS.
///
/// Local (same IRIS instance):
///   Set engine = ##class(IVG.CypherEngine).Local()
///   Set result = engine.Query("MATCH (n:Person) RETURN n.name LIMIT 5")
///
/// Remote (another IRIS instance):
///   Set engine = ##class(IVG.CypherEngine).Remote("other-host", 1972, "USER", "_SYSTEM", "SYS")
///   Set result = engine.Query("MATCH (n) RETURN count(n) AS cnt")
///
/// result is a %DynamicObject: {columns: %DynamicArray, rows: %DynamicArray, error: ""}
///
Class IVG.CypherEngine Extends %RegisteredObject
{

Property ConnMode As %String  [ Internal, Private ];
Property ConnHost As %String  [ Internal, Private ];
Property ConnPort As %Integer [ Internal, Private ];
Property ConnNS   As %String  [ Internal, Private ];
Property ConnUser As %String  [ Internal, Private ];
Property ConnPass As %String  [ Internal, Private ];
Property EmbedDim As %Integer [ InitialExpression = 768, Internal, Private ];
Property Inited   As %Boolean [ InitialExpression = 0, Internal, Private ];
Property PyEngine As %String  [ Internal, Private ];

ClassMethod Local(embeddingDimension As %Integer = 768) As IVG.CypherEngine
{
    Set obj = ..%New()
    Set obj.ConnMode = "embedded"
    Set obj.EmbedDim = embeddingDimension
    Quit obj
}

ClassMethod Remote(
    host      As %String,
    port      As %Integer = 1972,
    namespace As %String  = "USER",
    username  As %String  = "_SYSTEM",
    password  As %String  = "SYS",
    embeddingDimension As %Integer = 768
) As IVG.CypherEngine
{
    Set obj = ..%New()
    Set obj.ConnMode = "remote"
    Set obj.ConnHost = host
    Set obj.ConnPort = port
    Set obj.ConnNS   = namespace
    Set obj.ConnUser = username
    Set obj.ConnPass = password
    Set obj.EmbedDim = embeddingDimension
    Quit obj
}

/// Initialize Python engine — called lazily by all methods.
Method EnsureEngine() [ Language = python ]
{
    if self.Inited:
        return
    from iris_vector_graph.engine import IRISGraphEngine
    if self.ConnMode == 'remote':
        import sys
        for p in ['/usr/irissys/mgr/python', '/usr/irissys/lib/python']:
            if p in sys.path:
                sys.path.remove(p)
            sys.path.insert(0, p)
        for mod in list(sys.modules.keys()):
            if mod == 'iris' or mod.startswith('iris.'):
                del sys.modules[mod]
        import iris
        conn = iris.connect(
            hostname=str(self.ConnHost),
            port=int(self.ConnPort),
            namespace=str(self.ConnNS),
            username=str(self.ConnUser),
            password=str(self.ConnPass),
        )
        self.PyEngine = IRISGraphEngine(conn, embedding_dimension=int(self.EmbedDim))
    else:
        import sys as _sys, importlib as _il
        for _p in ['/usr/irissys/mgr/python', '/usr/irissys/lib/python']:
            if _p in _sys.path:
                _sys.path.remove(_p)
            _sys.path.insert(0, _p)
        for _m in list(_sys.modules.keys()):
            if _m == 'iris' or _m.startswith('iris.'):
                del _sys.modules[_m]
        _iris_embedded = _il.import_module('iris')
        from iris_vector_graph.embedded import EmbeddedConnection
        self.PyEngine = IRISGraphEngine(
            EmbeddedConnection(_iris_embedded.sql), embedding_dimension=int(self.EmbedDim)
        )
    self.Inited = True
}

/// Execute a Cypher query. Returns JSON string: {"columns":[...],"rows":[[...],...],"error":""}
Method QueryJson(cypher As %String, params As %String = "{}") As %String [ Language = python ]
{
    import json
    self.EnsureEngine()
    p = {}
    if params and params not in ('{}', ''):
        try:
            p = json.loads(params)
        except Exception:
            pass
    r = self.PyEngine.execute_cypher(cypher, parameters=p)
    cols = r.get('columns') or []
    rows = r.get('rows') or []
    safe_rows = []
    for row in rows:
        cells = list(row) if hasattr(row, '__iter__') and not isinstance(row, str) else [row]
        safe_row = []
        for cell in cells:
            if cell is None:
                safe_row.append(None)
            elif isinstance(cell, (list, tuple, dict)):
                safe_row.append(str(cell))
            else:
                try:
                    json.dumps(cell)
                    safe_row.append(cell)
                except Exception:
                    safe_row.append(str(cell))
        safe_rows.append(safe_row)
    return json.dumps({"columns": cols, "rows": safe_rows, "error": r.get('error') or ''})
}

/// Execute a Cypher query. Returns %DynamicObject: {columns, rows, error}
Method Query(cypher As %String, params As %String = "{}") As %DynamicObject
{
    Quit ##class(%DynamicObject).%FromJSON(..QueryJson(cypher, params))
}

/// Return first column of first row as string. Useful for COUNT queries.
Method Scalar(cypher As %String, default As %String = "") As %String [ Language = python ]
{
    self.EnsureEngine()
    r = self.PyEngine.execute_cypher(cypher)
    for row in (r.get('rows') or []):
        cells = list(row) if hasattr(row, '__iter__') and not isinstance(row, str) else [row]
        if cells:
            return '' if cells[0] is None else str(cells[0])
    return default
}

/// Return node labels as JSON array string.
Method GetLabelsJson() As %String [ Language = python ]
{
    import json
    self.EnsureEngine()
    return json.dumps(self.PyEngine.get_labels())
}

/// Return node labels as %DynamicArray.
Method GetLabels() As %DynamicArray
{
    Quit ##class(%DynamicArray).%FromJSON(..GetLabelsJson())
}

/// Return node count, optionally filtered by label.
Method GetNodeCount(label As %String = "") As %Integer [ Language = python ]
{
    self.EnsureEngine()
    return self.PyEngine.get_node_count(label if label else None)
}

/// Return edge count, optionally filtered by predicate.
Method GetEdgeCount(predicate As %String = "") As %Integer [ Language = python ]
{
    self.EnsureEngine()
    return self.PyEngine.get_edge_count(predicate if predicate else None)
}

/// Return 1 if node exists, 0 otherwise.
Method NodeExists(nodeId As %String) As %Boolean [ Language = python ]
{
    self.EnsureEngine()
    return 1 if self.PyEngine.node_exists(str(nodeId)) else 0
}

/// Return label distribution as JSON string: {"Label": count, ...}
Method GetLabelCountsJson() As %String [ Language = python ]
{
    import json
    self.EnsureEngine()
    return json.dumps(self.PyEngine.get_label_distribution())
}

/// Return label distribution as %DynamicObject.
Method GetLabelCounts() As %DynamicObject
{
    Quit ##class(%DynamicObject).%FromJSON(..GetLabelCountsJson())
}

/// Initialize schema. Returns %DynamicObject with status fields.
Method InitSchemaJson() As %String [ Language = python ]
{
    import json
    self.EnsureEngine()
    result = self.PyEngine.initialize_schema()
    return json.dumps({k: str(v) for k, v in result.items()})
}

Method InitSchema() As %DynamicObject
{
    Quit ##class(%DynamicObject).%FromJSON(..InitSchemaJson())
}

/// Rebuild ^KG adjacency index. Returns 1 on success.
Method RebuildKG() As %Boolean [ Language = python ]
{
    self.EnsureEngine()
    return 1 if self.PyEngine.rebuild_kg() else 0
}

/// Return 1 if engine is ready to serve queries.
Method IsReady() As %Boolean [ Language = python ]
{
    try:
        self.EnsureEngine()
        return 1 if self.PyEngine.is_ready() else 0
    except Exception:
        return 0
}

/// Quick smoke test — runs MATCH (n) RETURN count(n) AS cnt and checks result.
ClassMethod SmokeTest()
{
    Set engine = ..Local()
    Set r = engine.Query("MATCH (n) RETURN count(n) AS cnt")
    If r.error '= "" { Write "FAIL: "_r.error,! Quit }
    Set cols = r.columns
    If cols.%Size() = 0 { Write "FAIL: no columns",! Quit }
    Set cnt = engine.GetNodeCount("")
    Write "SmokeTest PASSED: "_cnt_" nodes, "_cols.%Size()_" columns",!
}

}
