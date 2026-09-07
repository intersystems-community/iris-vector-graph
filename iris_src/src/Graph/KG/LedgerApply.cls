/// Spec 213 — applies changeset operations inside Graph.KG.Ledger.Commit's transaction (R7).
///
/// One method per operation kind. Each: resolves references, captures prior values, applies
/// SQL through dynamic statements against the caller's schema, maintains adjacency via
/// Graph.KG.EdgeScan, and appends mutation records. Any failure returns an error string
/// (reason); ApplyAll wraps it into a CommitError JSON carrying the 0-based op index.
/// Record layout: $LB(op, entityKind, entityId, attr, priorEnc, newEnc, graph, flags)
/// with "N" = null and "S"_value = string.
Class Graph.KG.LedgerApply Extends %RegisteredObject
{

/// Apply every op in pOps for revision pSeq. Returns "" on success, else CommitError JSON.
ClassMethod ApplyAll(pOps As %DynamicArray, pSeq As %Integer, pSchema As %String, ByRef pRecords, ByRef pStmtIds As %DynamicObject, ByRef pFirst As %String, ByRef pLast As %String) As %String
{
    If '$IsObject(pStmtIds) Set pStmtIds = {}
    Kill tCtx
    Set tCtx("seq") = pSeq, tCtx("schema") = pSchema
    Set tN = pOps.%Size()
    For tI = 0:1:(tN - 1) {
        Set tOp = pOps.%Get(tI)
        If '$IsObject(tOp) Return ##class(Graph.KG.Ledger).Err("failed_op", "operation is not an object", "", tI)
        Set tKind = ##class(Graph.KG.Ledger).MetaGet(tOp, "op", "")
        Set tCtx("opIndex") = tI
        Set tReason = ""
        If tKind = "create_node"      { Set tReason = ..OpCreateNode(tOp, .tCtx, .pRecords, 0) }
        ElseIf tKind = "upsert_node"  { Set tReason = ..OpCreateNode(tOp, .tCtx, .pRecords, 1) }
        ElseIf tKind = "delete_node"  { Set tReason = ..OpDeleteNode(tOp, .tCtx, .pRecords, .pFirst, .pLast) }
        ElseIf tKind = "add_label"    { Set tReason = ..OpAddLabel(tOp, .tCtx, .pRecords) }
        ElseIf tKind = "remove_label" { Set tReason = ..OpRemoveLabel(tOp, .tCtx, .pRecords) }
        ElseIf tKind = "set_prop"     { Set tReason = ..OpSetProp(tOp, .tCtx, .pRecords) }
        ElseIf tKind = "remove_prop"  { Set tReason = ..OpRemoveProp(tOp, .tCtx, .pRecords) }
        ElseIf tKind = "create_rel"   { Set tReason = ..OpCreateRel(tOp, .tCtx, .pRecords, .pStmtIds, .pFirst, .pLast, 0) }
        ElseIf tKind = "upsert_rel"   { Set tReason = ..OpCreateRel(tOp, .tCtx, .pRecords, .pStmtIds, .pFirst, .pLast, 1) }
        ElseIf tKind = "delete_rel"   { Set tReason = ..OpDeleteRel(tOp, .tCtx, .pRecords, .pFirst, .pLast) }
        ElseIf tKind = "set_qual"     { Set tReason = ..OpQual(tOp, .tCtx, .pRecords, .pFirst, .pLast, "set") }
        ElseIf tKind = "replace_quals" { Set tReason = ..OpQual(tOp, .tCtx, .pRecords, .pFirst, .pLast, "replace") }
        ElseIf tKind = "remove_qual"  { Set tReason = ..OpQual(tOp, .tCtx, .pRecords, .pFirst, .pLast, "remove") }
        Else { Set tReason = "unknown_operation: " _ tKind }
        If tReason '= "" Return ##class(Graph.KG.Ledger).Err("failed_op", tReason, "", tI)
    }
    Return ""
}

/// ---------------------------------------------------------------- primitives

ClassMethod Enc(pValue As %String) As %String
{
    Return "S" _ pValue
}

ClassMethod EncNull() As %String
{
    Return "N"
}

ClassMethod Rec(ByRef pRecords, pOp As %String, pKind As %String, pId As %String, pAttr As %String, pPrior As %String, pNew As %String, pGraph As %String = "", pFlags As %String = "")
{
    Set pRecords = $Get(pRecords, 0) + 1
    Set pRecords(pRecords) = $ListBuild(pOp, pKind, pId, pAttr, pPrior, pNew, pGraph, pFlags)
}

ClassMethod IsReserved(pKey As %String) As %Boolean
{
    Return (pKey = "id") || (pKey = "__graph")
}

/// Run dynamic SQL; returns the result set. Caller checks %SQLCODE.
ClassMethod Run(pSql As %String, pArgs... As %String) As %SQL.StatementResult
{
    Return ##class(%SQL.Statement).%ExecDirect(, pSql, pArgs...)
}

ClassMethod NodeExists(pSchema As %String, pId As %String) As %Boolean
{
    Set tRS = ..Run("SELECT 1 FROM " _ pSchema _ ".nodes WHERE node_id = ?", pId)
    If tRS.%SQLCODE < 0 Return 0
    Return tRS.%Next()
}

ClassMethod TupleJson(pS As %String, pP As %String, pO As %String, pGraph As %String) As %String
{
    Set tObj = {}
    If pGraph = "" { Do tObj.%Set("graph", "", "null") } Else { Do tObj.%Set("graph", pGraph) }
    Do tObj.%Set("o", pO)
    Do tObj.%Set("p", pP)
    Do tObj.%Set("s", pS)
    Return tObj.%ToJSON()
}

/// Parse a qualifiers column value into a %DynamicObject of string values.
ClassMethod ParseQuals(pRaw As %String) As %DynamicObject
{
    Set tOut = {}
    If pRaw = "" Return tOut
    Try {
        Set tIn = {}.%FromJSON(pRaw)
        Set tIter = tIn.%GetIterator()
        While tIter.%GetNext(.tK, .tV) {
            Set tType = tIn.%GetTypeOf(tK)
            If tType = "null" Continue
            If tType = "boolean" { Do tOut.%Set(tK, $Select(tV: "true", 1: "false")) }
            ElseIf (tType = "object") || (tType = "array") { Do tOut.%Set(tK, tV.%ToJSON()) }
            Else { Do tOut.%Set(tK, "" _ tV) }
        }
    } Catch {
        Set tOut = {}
    }
    Return tOut
}

/// Find a live edge by tuple. Returns edgeId ("" when absent); pQuals gets the parsed qualifiers.
ClassMethod FindEdge(pSchema As %String, pS As %String, pP As %String, pO As %String, pGraph As %String, ByRef pQuals As %DynamicObject) As %String
{
    Set pQuals = {}
    If pGraph = "" {
        Set tRS = ..Run("SELECT %ID, qualifiers FROM " _ pSchema _ ".rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id IS NULL", pS, pP, pO)
    } Else {
        Set tRS = ..Run("SELECT %ID, qualifiers FROM " _ pSchema _ ".rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ?", pS, pP, pO, pGraph)
    }
    If tRS.%SQLCODE < 0 Return ""
    If 'tRS.%Next() Return ""
    Set tId = tRS.%GetData(1)
    Set pQuals = ..ParseQuals(tRS.%GetData(2))
    Return tId
}

/// Allocate a statement identity for a live tuple and register it (R4).
ClassMethod AllocStmt(pSeq As %Integer, pS As %String, pP As %String, pO As %String, pGraph As %String, ByRef pFirst As %String, ByRef pLast As %String) As %String
{
    Set tId = +$Get(^IVG.Ledger("stmtNext"), 1)
    Set ^IVG.Ledger("stmtNext") = tId + 1
    Set ^IVG.Ledger("stmt", tId) = $ListBuild(pS, pP, pO, pGraph, pSeq, "")
    Set ^IVG.Ledger("tuple", pS, pP, pO, ##class(Graph.KG.Ledger).GKey(pGraph)) = tId
    If pFirst = "" Set pFirst = tId
    Set pLast = tId
    Return tId
}

/// Resolve a RelRef. Fills pStmt, pEdgeId, pS, pP, pO, pGraph, pQuals, pFlags ("assigned_stmt" when
/// an unrecorded live edge received an identity here). Returns "" or a reason.
ClassMethod ResolveRef(pRef As %DynamicObject, ByRef pCtx, ByRef pStmt As %String, ByRef pEdgeId As %String, ByRef pS, ByRef pP, ByRef pO, ByRef pGraph, ByRef pQuals As %DynamicObject, ByRef pFlags As %String, ByRef pFirst As %String, ByRef pLast As %String) As %String
{
    Set pFlags = "", pStmt = "", pEdgeId = "", pS = "", pP = "", pO = "", pGraph = "", pQuals = {}
    If '$IsObject(pRef) Return "relationship reference missing"
    Set tSchema = pCtx("schema"), tSeq = pCtx("seq")
    If pRef.%IsDefined("op_ref") {
        Set tIdx = +pRef.%Get("op_ref")
        If '$Data(pCtx("opStmt", tIdx)) Return "op_ref " _ tIdx _ " does not name an earlier relationship operation"
        Set pStmt = pCtx("opStmt", tIdx)
    } ElseIf pRef.%IsDefined("stmt_id") {
        Set pStmt = "" _ pRef.%Get("stmt_id")
        If '$Data(^IVG.Ledger("stmt", pStmt)) Return "statement " _ pStmt _ " is unknown"
    } ElseIf pRef.%IsDefined("tuple") {
        Set tT = pRef.%Get("tuple")
        If '$IsObject(tT) Return "tuple reference is not an object"
        Set pS = ##class(Graph.KG.Ledger).MetaGet(tT, "s", ""), pP = ##class(Graph.KG.Ledger).MetaGet(tT, "p", "")
        Set pO = ##class(Graph.KG.Ledger).MetaGet(tT, "o", ""), pGraph = ##class(Graph.KG.Ledger).MetaGet(tT, "graph", "")
        Set pEdgeId = ..FindEdge(tSchema, pS, pP, pO, pGraph, .pQuals)
        If pEdgeId = "" Return "relationship not found: " _ pS _ " -[" _ pP _ "]-> " _ pO
        Set pStmt = $Get(^IVG.Ledger("tuple", pS, pP, pO, ##class(Graph.KG.Ledger).GKey(pGraph)), "")
        If pStmt = "" {
            Set pStmt = ..AllocStmt(tSeq, pS, pP, pO, pGraph, .pFirst, .pLast)
            Set pFlags = "assigned_stmt"
        }
        If $Data(pCtx("deletedStmt", pStmt)) Return "statement " _ pStmt _ " was deleted earlier in this changeset"
        Return ""
    } Else {
        Return "relationship reference must contain stmt_id, tuple, or op_ref"
    }
    // stmt_id / op_ref path: load tuple from the statement map
    If $Data(pCtx("deletedStmt", pStmt)) Return "statement " _ pStmt _ " was deleted earlier in this changeset"
    Set tRow = $Get(^IVG.Ledger("stmt", pStmt), "")
    If tRow = "" Return "statement " _ pStmt _ " is unknown"
    If $ListGet(tRow, 6) '= "" Return "statement " _ pStmt _ " was deleted in revision " _ $ListGet(tRow, 6)
    Set pS = $ListGet(tRow, 1), pP = $ListGet(tRow, 2), pO = $ListGet(tRow, 3), pGraph = $ListGet(tRow, 4)
    Set pEdgeId = ..FindEdge(tSchema, pS, pP, pO, pGraph, .pQuals)
    If pEdgeId = "" Return "statement " _ pStmt _ " has no row in the store (ledger inconsistency)"
    Return ""
}

/// ---------------------------------------------------------------- node ops

ClassMethod OpCreateNode(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords, pUpsert As %Boolean) As %String
{
    Set tSchema = pCtx("schema")
    Set tId = ##class(Graph.KG.Ledger).MetaGet(pOp, "id", "")
    If tId = "" Return "node id is required"
    If $Data(pCtx("deletedNode", tId)) Return "node '" _ tId _ "' was deleted earlier in this changeset"
    Set tExists = ..NodeExists(tSchema, tId)
    If tExists && 'pUpsert Return "node_exists: '" _ tId _ "'"
    // reserved names first so nothing is applied
    Set tProps = pOp.%Get("props")
    If $IsObject(tProps) {
        Set tIter = tProps.%GetIterator()
        While tIter.%GetNext(.tK, .tV) { If ..IsReserved(tK) Return "reserved_property: '" _ tK _ "'" }
    }
    If 'tExists {
        Set tRS = ..Run("INSERT INTO " _ tSchema _ ".nodes (node_id) VALUES (?)", tId)
        If tRS.%SQLCODE < 0 Return "insert node failed: " _ tRS.%Message
        Do ..Rec(.pRecords, "create_node", "node", tId, "", ..EncNull(), ..Enc(""))
    }
    Set tLabels = pOp.%Get("labels")
    If $IsObject(tLabels) {
        For tI = 0:1:(tLabels.%Size() - 1) {
            Set tL = "" _ tLabels.%Get(tI)
            Set tReason = ..AddLabel(tSchema, tId, tL, .pRecords)
            If tReason '= "" Return tReason
        }
    }
    If $IsObject(tProps) {
        Set tIter = tProps.%GetIterator()
        While tIter.%GetNext(.tK, .tV) {
            Set tReason = ..SetProp(tSchema, tId, tK, "" _ tV, .pRecords)
            If tReason '= "" Return tReason
        }
    }
    Return ""
}

/// FR-009c: adding an existing label succeeds with no record.
ClassMethod AddLabel(pSchema As %String, pId As %String, pLabel As %String, ByRef pRecords) As %String
{
    If pLabel = "" Return "label is required"
    Set tRS = ..Run("SELECT 1 FROM " _ pSchema _ ".rdf_labels WHERE s = ? AND label = ?", pId, pLabel)
    If tRS.%SQLCODE < 0 Return "label lookup failed: " _ tRS.%Message
    If tRS.%Next() Return ""
    Set tRS = ..Run("INSERT INTO " _ pSchema _ ".rdf_labels (s, label) VALUES (?, ?)", pId, pLabel)
    If tRS.%SQLCODE < 0 Return "insert label failed: " _ tRS.%Message
    Do ..Rec(.pRecords, "add_label", "node", pId, pLabel, ..EncNull(), ..Enc(""))
    Return ""
}

/// FR-009c: setting an identical value succeeds with no record.
ClassMethod SetProp(pSchema As %String, pId As %String, pKey As %String, pValue As %String, ByRef pRecords) As %String
{
    If pKey = "" Return "property key is required"
    If ..IsReserved(pKey) Return "reserved_property: '" _ pKey _ "'"
    Set tRS = ..Run("SELECT val FROM " _ pSchema _ ".rdf_props WHERE s = ? AND ""key"" = ?", pId, pKey)
    If tRS.%SQLCODE < 0 Return "property lookup failed: " _ tRS.%Message
    If tRS.%Next() {
        Set tCur = tRS.%GetData(1)
        If tCur = pValue Return ""
        Set tRS2 = ..Run("UPDATE " _ pSchema _ ".rdf_props SET val = ? WHERE s = ? AND ""key"" = ?", pValue, pId, pKey)
        If tRS2.%SQLCODE < 0 Return "update property failed: " _ tRS2.%Message
        Do ..Rec(.pRecords, "set_prop", "node", pId, pKey, ..Enc(tCur), ..Enc(pValue))
        Return ""
    }
    Set tRS2 = ..Run("INSERT INTO " _ pSchema _ ".rdf_props (s, ""key"", val) VALUES (?, ?, ?)", pId, pKey, pValue)
    If tRS2.%SQLCODE < 0 Return "insert property failed: " _ tRS2.%Message
    Do ..Rec(.pRecords, "set_prop", "node", pId, pKey, ..EncNull(), ..Enc(pValue))
    Return ""
}

ClassMethod OpAddLabel(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords) As %String
{
    Set tId = ##class(Graph.KG.Ledger).MetaGet(pOp, "id", "")
    If $Data(pCtx("deletedNode", tId)) Return "node '" _ tId _ "' was deleted earlier in this changeset"
    If '..NodeExists(pCtx("schema"), tId) Return "node_not_found: '" _ tId _ "'"
    Return ..AddLabel(pCtx("schema"), tId, ##class(Graph.KG.Ledger).MetaGet(pOp, "label", ""), .pRecords)
}

ClassMethod OpRemoveLabel(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords) As %String
{
    Set tSchema = pCtx("schema")
    Set tId = ##class(Graph.KG.Ledger).MetaGet(pOp, "id", ""), tL = ##class(Graph.KG.Ledger).MetaGet(pOp, "label", "")
    If $Data(pCtx("deletedNode", tId)) Return "node '" _ tId _ "' was deleted earlier in this changeset"
    If '..NodeExists(tSchema, tId) Return "node_not_found: '" _ tId _ "'"
    Set tRS = ..Run("SELECT 1 FROM " _ tSchema _ ".rdf_labels WHERE s = ? AND label = ?", tId, tL)
    If tRS.%SQLCODE < 0 Return "label lookup failed: " _ tRS.%Message
    If 'tRS.%Next() Return "label_not_present: '" _ tL _ "' on '" _ tId _ "'"
    Set tRS = ..Run("DELETE FROM " _ tSchema _ ".rdf_labels WHERE s = ? AND label = ?", tId, tL)
    If tRS.%SQLCODE < 0 Return "delete label failed: " _ tRS.%Message
    Do ..Rec(.pRecords, "remove_label", "node", tId, tL, ..Enc(""), ..EncNull())
    Return ""
}

ClassMethod OpSetProp(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords) As %String
{
    Set tId = ##class(Graph.KG.Ledger).MetaGet(pOp, "id", "")
    If $Data(pCtx("deletedNode", tId)) Return "node '" _ tId _ "' was deleted earlier in this changeset"
    Set tKey = ##class(Graph.KG.Ledger).MetaGet(pOp, "key", "")
    If ..IsReserved(tKey) Return "reserved_property: '" _ tKey _ "'"
    If '..NodeExists(pCtx("schema"), tId) Return "node_not_found: '" _ tId _ "'"
    Return ..SetProp(pCtx("schema"), tId, tKey, ##class(Graph.KG.Ledger).MetaGet(pOp, "value", ""), .pRecords)
}

ClassMethod OpRemoveProp(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords) As %String
{
    Set tSchema = pCtx("schema")
    Set tId = ##class(Graph.KG.Ledger).MetaGet(pOp, "id", ""), tKey = ##class(Graph.KG.Ledger).MetaGet(pOp, "key", "")
    If $Data(pCtx("deletedNode", tId)) Return "node '" _ tId _ "' was deleted earlier in this changeset"
    If ..IsReserved(tKey) Return "reserved_property: '" _ tKey _ "'"
    If '..NodeExists(tSchema, tId) Return "node_not_found: '" _ tId _ "'"
    Set tRS = ..Run("SELECT val FROM " _ tSchema _ ".rdf_props WHERE s = ? AND ""key"" = ?", tId, tKey)
    If tRS.%SQLCODE < 0 Return "property lookup failed: " _ tRS.%Message
    If 'tRS.%Next() Return "property_not_present: '" _ tKey _ "' on '" _ tId _ "'"
    Set tCur = tRS.%GetData(1)
    Set tRS = ..Run("DELETE FROM " _ tSchema _ ".rdf_props WHERE s = ? AND ""key"" = ?", tId, tKey)
    If tRS.%SQLCODE < 0 Return "delete property failed: " _ tRS.%Message
    Do ..Rec(.pRecords, "remove_prop", "node", tId, tKey, ..Enc(tCur), ..EncNull())
    Return ""
}

/// Snapshot of a node's labels and properties as canonical JSON (prior value for delete_node).
ClassMethod NodeSnapshotJson(pSchema As %String, pId As %String) As %String
{
    Set tLabels = []
    Set tRS = ..Run("SELECT label FROM " _ pSchema _ ".rdf_labels WHERE s = ? ORDER BY label", pId)
    While (tRS.%SQLCODE >= 0) && tRS.%Next() { Do tLabels.%Push(tRS.%GetData(1)) }
    Set tProps = {}
    Set tRS = ..Run("SELECT ""key"", val FROM " _ pSchema _ ".rdf_props WHERE s = ? ORDER BY ""key""", pId)
    While (tRS.%SQLCODE >= 0) && tRS.%Next() {
        If '..IsReserved(tRS.%GetData(1)) Do tProps.%Set(tRS.%GetData(1), tRS.%GetData(2))
    }
    Set tObj = {}
    Do tObj.%Set("labels", tLabels)
    Do tObj.%Set("props", tProps)
    Return tObj.%ToJSON()
}

/// Delete a node row with its labels, props and embeddings, appending one delete_node record.
ClassMethod DeleteNodeRows(pSchema As %String, pId As %String, ByRef pRecords, pFlags As %String = "") As %String
{
    Set tPrior = ..NodeSnapshotJson(pSchema, pId)
    Do ..Run("DELETE FROM " _ pSchema _ ".kg_NodeEmbeddings WHERE id = ?", pId)
    Do ..Run("DELETE FROM " _ pSchema _ ".kg_NodeEmbeddings_optimized WHERE id = ?", pId)
    Set tRS = ..Run("DELETE FROM " _ pSchema _ ".rdf_labels WHERE s = ?", pId)
    If tRS.%SQLCODE < 0 Return "delete labels failed: " _ tRS.%Message
    Set tRS = ..Run("DELETE FROM " _ pSchema _ ".rdf_props WHERE s = ?", pId)
    If tRS.%SQLCODE < 0 Return "delete props failed: " _ tRS.%Message
    Set tRS = ..Run("DELETE FROM " _ pSchema _ ".nodes WHERE node_id = ?", pId)
    If tRS.%SQLCODE < 0 Return "delete node failed: " _ tRS.%Message
    Do ..Rec(.pRecords, "delete_node", "node", pId, "", ..Enc(tPrior), ..EncNull(), "", pFlags)
    Return ""
}

/// FR-011: strict fails on incident live relationships; detach cascades them in statement-id order.
ClassMethod OpDeleteNode(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords, ByRef pFirst As %String, ByRef pLast As %String) As %String
{
    Set tSchema = pCtx("schema"), tSeq = pCtx("seq")
    Set tId = ##class(Graph.KG.Ledger).MetaGet(pOp, "id", "")
    Set tMode = ##class(Graph.KG.Ledger).MetaGet(pOp, "mode", "strict")
    If $Data(pCtx("deletedNode", tId)) Return "node '" _ tId _ "' was deleted earlier in this changeset"
    If '..NodeExists(tSchema, tId) Return "node_not_found: '" _ tId _ "'"
    // incident live relationships
    Kill tSort
    Set tRS = ..Run("SELECT %ID, s, p, o_id, graph_id, qualifiers FROM " _ tSchema _ ".rdf_edges WHERE s = ? OR o_id = ?", tId, tId)
    If tRS.%SQLCODE < 0 Return "incident lookup failed: " _ tRS.%Message
    Set tCount = 0
    While tRS.%Next() {
        Set tCount = tCount + 1
        Set tS = tRS.%GetData(2), tP = tRS.%GetData(3), tO = tRS.%GetData(4), tG = tRS.%GetData(5)
        Set tStmt = $Get(^IVG.Ledger("tuple", tS, tP, tO, ##class(Graph.KG.Ledger).GKey(tG)), "")
        Set tFlags = "cascade"
        If tMode = "detach" {
            If tStmt = "" {
                Set tStmt = ..AllocStmt(tSeq, tS, tP, tO, tG, .pFirst, .pLast)
                Set tFlags = "cascade,assigned_stmt"
            }
            Set tSort(+tStmt) = $ListBuild(tRS.%GetData(1), tS, tP, tO, tG, tRS.%GetData(6), tFlags)
        }
    }
    If tCount > 0 {
        If tMode '= "detach" Return "node_has_relationships: '" _ tId _ "' has " _ tCount _ " live relationship(s); use mode=detach"
        Set tK = ""
        For {
            Set tK = $Order(tSort(tK))
            Quit:tK=""
            Set tRow = tSort(tK)
            Set tReason = ..DeleteRelInternal(tSchema, tSeq, tK, $ListGet(tRow, 1), $ListGet(tRow, 2), $ListGet(tRow, 3), $ListGet(tRow, 4), $ListGet(tRow, 5), ..ParseQuals($ListGet(tRow, 6)), $ListGet(tRow, 7), .pRecords, .pCtx)
            If tReason '= "" Return tReason
        }
    }
    Set tReason = ..DeleteNodeRows(tSchema, tId, .pRecords)
    If tReason '= "" Return tReason
    Set pCtx("deletedNode", tId) = 1
    Return ""
}

/// ---------------------------------------------------------------- relationship ops

ClassMethod QualsJson(pQuals As %DynamicObject) As %String
{
    // canonical: sorted keys
    Kill tKeys
    Set tIter = pQuals.%GetIterator()
    While tIter.%GetNext(.tK, .tV) { Set tKeys(tK) = "" _ tV }
    Set tOut = {}
    Set tK = ""
    For {
        Set tK = $Order(tKeys(tK))
        Quit:tK=""
        Do tOut.%Set(tK, tKeys(tK))
    }
    Return tOut.%ToJSON()
}

ClassMethod WeightOf(pQuals As %DynamicObject) As %String
{
    If '$IsObject(pQuals) Return "1.0"
    If 'pQuals.%IsDefined("weight") Return "1.0"
    Set tW = pQuals.%Get("weight")
    If tW = "" Return "1.0"
    Return tW
}

ClassMethod IsNumeric(pValue As %String) As %Boolean
{
    Return (pValue '= "") && $IsValidNum(pValue)
}

ClassMethod OpCreateRel(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords, ByRef pStmtIds As %DynamicObject, ByRef pFirst As %String, ByRef pLast As %String, pUpsert As %Boolean) As %String
{
    Set tSchema = pCtx("schema"), tSeq = pCtx("seq"), tIdx = pCtx("opIndex")
    Set tT = pOp.%Get("tuple")
    If '$IsObject(tT) Return "tuple is required"
    Set tS = ##class(Graph.KG.Ledger).MetaGet(tT, "s", ""), tP = ##class(Graph.KG.Ledger).MetaGet(tT, "p", "")
    Set tO = ##class(Graph.KG.Ledger).MetaGet(tT, "o", ""), tG = ##class(Graph.KG.Ledger).MetaGet(tT, "graph", "")
    If (tS = "") || (tP = "") || (tO = "") Return "tuple requires s, p and o"
    If $Data(pCtx("deletedNode", tS)) || $Data(pCtx("deletedNode", tO)) Return "endpoint was deleted earlier in this changeset"
    If '..NodeExists(tSchema, tS) Return "node_not_found: '" _ tS _ "'"
    If '..NodeExists(tSchema, tO) Return "node_not_found: '" _ tO _ "'"
    Set tQuals = pOp.%Get("quals")
    If '$IsObject(tQuals) Set tQuals = {}
    If tQuals.%IsDefined("weight") && '..IsNumeric("" _ tQuals.%Get("weight")) Return "qualifier 'weight' must be numeric"
    Set tEdgeId = ..FindEdge(tSchema, tS, tP, tO, tG, .tCur)
    If tEdgeId '= "" {
        If 'pUpsert Return "relationship_exists: " _ tS _ " -[" _ tP _ "]-> " _ tO
        // merge qualifiers onto the live relationship
        Set tStmt = $Get(^IVG.Ledger("tuple", tS, tP, tO, ##class(Graph.KG.Ledger).GKey(tG)), "")
        Set tFlags = ""
        If tStmt = "" { Set tStmt = ..AllocStmt(tSeq, tS, tP, tO, tG, .pFirst, .pLast)  Set tFlags = "assigned_stmt" }
        If $Data(pCtx("deletedStmt", tStmt)) Return "statement " _ tStmt _ " was deleted earlier in this changeset"
        Set tReason = ..MergeQuals(tSchema, tSeq, tStmt, tEdgeId, tS, tP, tO, tG, tCur, tQuals, tFlags, .pRecords)
        If tReason '= "" Return tReason
        Set pCtx("opStmt", tIdx) = tStmt
        Do pStmtIds.%Set("" _ tIdx, "" _ tStmt)
        Return ""
    }
    // create
    Set tQJson = ..QualsJson(tQuals)
    If tG = "" {
        Set tRS = ..Run("INSERT INTO " _ tSchema _ ".rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)", tS, tP, tO, tQJson)
    } Else {
        Set tRS = ..Run("INSERT INTO " _ tSchema _ ".rdf_edges (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)", tS, tP, tO, tQJson, tG)
    }
    If tRS.%SQLCODE < 0 Return "insert relationship failed: " _ tRS.%Message
    Set tStmt = ..AllocStmt(tSeq, tS, tP, tO, tG, .pFirst, .pLast)
    // Adjacency: when the Graph.KG.GraphIndex functional index is compiled it already
    // wrote ^KG("out"/"in") for this row; only set the weight then. Otherwise maintain it.
    If $Data(^KG("out", 0, tS, tP, tO)) {
        Set ^KG("out", 0, tS, tP, tO) = ..WeightOf(tQuals)
        Set ^KG("in", 0, tO, tP, tS) = ..WeightOf(tQuals)
    } Else {
        Do ##class(Graph.KG.EdgeScan).WriteAdjacency(tS, tP, tO, ..WeightOf(tQuals), ""
    }
    Do ..Rec(.pRecords, "create_rel", "rel", tStmt, "", ..EncNull(), ..Enc(..TupleJson(tS, tP, tO, tG)), tG)
    Kill tKeys
    Set tIter = tQuals.%GetIterator()
    While tIter.%GetNext(.tK, .tV) { Set tKeys(tK) = "" _ tV }
    Set tK = ""
    For {
        Set tK = $Order(tKeys(tK))
        Quit:tK=""
        Do ..Rec(.pRecords, "set_qual", "rel", tStmt, tK, ..EncNull(), ..Enc(tKeys(tK)), tG)
    }
    Set pCtx("opStmt", tIdx) = tStmt
    Do pStmtIds.%Set("" _ tIdx, "" _ tStmt)
    Return ""
}

/// Merge pNew into pCur for a live relationship (FR-009a/FR-009c) and persist.
ClassMethod MergeQuals(pSchema As %String, pSeq As %Integer, pStmt As %String, pEdgeId As %String, pS, pP, pO, pG, pCur As %DynamicObject, pNew As %DynamicObject, pFlags As %String, ByRef pRecords) As %String
{
    Set tChanged = 0
    Kill tKeys
    Set tIter = pNew.%GetIterator()
    While tIter.%GetNext(.tK, .tV) { Set tKeys(tK) = "" _ tV }
    Set tK = ""
    For {
        Set tK = $Order(tKeys(tK))
        Quit:tK=""
        Set tNewV = tKeys(tK)
        If pCur.%IsDefined(tK) && (("" _ pCur.%Get(tK)) = tNewV) Continue
        Set tPrior = $Select(pCur.%IsDefined(tK): ..Enc("" _ pCur.%Get(tK)), 1: ..EncNull())
        Do pCur.%Set(tK, tNewV)
        Do ..Rec(.pRecords, "set_qual", "rel", pStmt, tK, tPrior, ..Enc(tNewV), pG, pFlags)
        Set tChanged = 1
    }
    If 'tChanged Return ""
    Return ..PersistQuals(pSchema, pEdgeId, pS, pP, pO, pCur)
}

ClassMethod PersistQuals(pSchema As %String, pEdgeId As %String, pS, pP, pO, pQuals As %DynamicObject) As %String
{
    Set tRS = ..Run("UPDATE " _ pSchema _ ".rdf_edges SET qualifiers = ? WHERE %ID = ?", ..QualsJson(pQuals), pEdgeId)
    If tRS.%SQLCODE < 0 Return "update qualifiers failed: " _ tRS.%Message
    // weight projection (FR-009b): update adjacency value without touching degree counters
    Set tW = ..WeightOf(pQuals)
    If $Data(^KG("out", 0, pS, pP, pO)) Set ^KG("out", 0, pS, pP, pO) = tW
    If $Data(^KG("in", 0, pO, pP, pS)) Set ^KG("in", 0, pO, pP, pS) = tW
    Return ""
}

ClassMethod OpQual(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords, ByRef pFirst As %String, ByRef pLast As %String, pMode As %String) As %String
{
    Set tSchema = pCtx("schema"), tSeq = pCtx("seq")
    Set tReason = ..ResolveRef(pOp.%Get("ref"), .pCtx, .tStmt, .tEdgeId, .tS, .tP, .tO, .tG, .tCur, .tFlags, .pFirst, .pLast)
    If tReason '= "" Return tReason
    If pMode = "set" {
        Set tKey = ##class(Graph.KG.Ledger).MetaGet(pOp, "key", "")
        If tKey = "" Return "qualifier key is required"
        Set tVal = "" _ ##class(Graph.KG.Ledger).MetaGet(pOp, "value", "")
        If (tKey = "weight") && '..IsNumeric(tVal) Return "qualifier 'weight' must be numeric"
        Set tNew = {}
        Do tNew.%Set(tKey, tVal)
        Return ..MergeQuals(tSchema, tSeq, tStmt, tEdgeId, tS, tP, tO, tG, tCur, tNew, tFlags, .pRecords)
    }
    If pMode = "remove" {
        Set tKey = ##class(Graph.KG.Ledger).MetaGet(pOp, "key", "")
        If tKey = "" Return "qualifier key is required"
        If 'tCur.%IsDefined(tKey) Return "qualifier_not_present: '" _ tKey _ "' on statement " _ tStmt
        Set tPrior = "" _ tCur.%Get(tKey)
        Do tCur.%Remove(tKey)
        Do ..Rec(.pRecords, "remove_qual", "rel", tStmt, tKey, ..Enc(tPrior), ..EncNull(), tG, tFlags)
        Return ..PersistQuals(tSchema, tEdgeId, tS, tP, tO, tCur)
    }
    // replace: remove keys not supplied, then merge supplied
    Set tNew = pOp.%Get("quals")
    If '$IsObject(tNew) Set tNew = {}
    If tNew.%IsDefined("weight") && '..IsNumeric("" _ tNew.%Get("weight")) Return "qualifier 'weight' must be numeric"
    Kill tExisting
    Set tIter = tCur.%GetIterator()
    While tIter.%GetNext(.tK, .tV) { Set tExisting(tK) = "" _ tV }
    Set tChanged = 0
    Set tK = ""
    For {
        Set tK = $Order(tExisting(tK))
        Quit:tK=""
        If tNew.%IsDefined(tK) Continue
        Do tCur.%Remove(tK)
        Do ..Rec(.pRecords, "remove_qual", "rel", tStmt, tK, ..Enc(tExisting(tK)), ..EncNull(), tG, tFlags)
        Set tChanged = 1
    }
    Set tReason = ..MergeQuals(tSchema, tSeq, tStmt, tEdgeId, tS, tP, tO, tG, tCur, tNew, tFlags, .pRecords)
    If tReason '= "" Return tReason
    If tChanged Return ..PersistQuals(tSchema, tEdgeId, tS, tP, tO, tCur)
    Return ""
}

ClassMethod OpDeleteRel(pOp As %DynamicObject, ByRef pCtx, ByRef pRecords, ByRef pFirst As %String, ByRef pLast As %String) As %String
{
    Set tReason = ..ResolveRef(pOp.%Get("ref"), .pCtx, .tStmt, .tEdgeId, .tS, .tP, .tO, .tG, .tCur, .tFlags, .pFirst, .pLast)
    If tReason '= "" Return tReason
    Return ..DeleteRelInternal(pCtx("schema"), pCtx("seq"), tStmt, tEdgeId, tS, tP, tO, tG, tCur, tFlags, .pRecords, .pCtx)
}

/// Delete one live relationship: cascade reifications, delete the row, close the statement,
/// maintain adjacency, and record delete_rel (prior = tuple+qualifiers JSON).
ClassMethod DeleteRelInternal(pSchema As %String, pSeq As %Integer, pStmt As %String, pEdgeId As %String, pS, pP, pO, pG, pQuals As %DynamicObject, pFlags As %String, ByRef pRecords, ByRef pCtx) As %String
{
    // reification cascade (FR-012)
    Set tRS = ..Run("SELECT reifier_id FROM " _ pSchema _ ".rdf_reifications WHERE %ID = ?", pEdgeId)
    If tRS.%SQLCODE >= 0 {
        Kill tReifiers
        Set tN = 0
        While tRS.%Next() { Set tN = tN + 1, tReifiers(tN) = tRS.%GetData(1) }
        For tI = 1:1:tN {
            Set tR = tReifiers(tI)
            Do ..Run("DELETE FROM " _ pSchema _ ".rdf_reifications WHERE reifier_id = ?", tR)
            If ..NodeExists(pSchema, tR) {
                Set tReason = ..DeleteNodeRows(pSchema, tR, .pRecords, "cascade")
                If tReason '= "" Return tReason
                Set pCtx("deletedNode", tR) = 1
            }
        }
    }
    Set tPriorObj = {}
    Do tPriorObj.%Set("graph", $Select(pG = "": "", 1: pG), $Select(pG = "": "null", 1: "string"))
    Do tPriorObj.%Set("o", pO)
    Do tPriorObj.%Set("p", pP)
    Do tPriorObj.%Set("quals", {}.%FromJSON(..QualsJson(pQuals)))
    Do tPriorObj.%Set("s", pS)
    Set tRS = ..Run("DELETE FROM " _ pSchema _ ".rdf_edges WHERE %ID = ?", pEdgeId)
    If tRS.%SQLCODE < 0 Return "delete relationship failed: " _ tRS.%Message
    Kill ^IVG.Ledger("tuple", pS, pP, pO, ##class(Graph.KG.Ledger).GKey(pG))
    Set tRow = $Get(^IVG.Ledger("stmt", pStmt), "")
    If tRow '= "" Set ^IVG.Ledger("stmt", pStmt) = $ListBuild($ListGet(tRow, 1), $ListGet(tRow, 2), $ListGet(tRow, 3), $ListGet(tRow, 4), $ListGet(tRow, 5), pSeq)
    // Adjacency: the functional index (when compiled) already removed the entries.
    If $Data(^KG("out", 0, pS, pP, pO)) Do ##class(Graph.KG.EdgeScan).DeleteAdjacency(pS, pP, pO, 0)
    Do ..Rec(.pRecords, "delete_rel", "rel", pStmt, "", ..Enc(tPriorObj.%ToJSON()), ..EncNull(), pG, pFlags)
    Set pCtx("deletedStmt", pStmt) = 1
    Return ""
}

}
