/// Spec 213 — Immutable graph revision ledger: server-side commit engine.
///
/// Storage (research.md R2):
///   ^IVG.Ledger("meta","state"|"strict"|"maxOps"|"reconBound")
///   ^IVG.Ledger("head") = seq            ^IVG.Ledger("stmtNext") = next statement id
///   ^IVG.Ledger("revById", id) = seq     ^IVG.Ledger("idBySeq", seq) = id
///   ^IVG.Ledger("rec", seq, ordinal) = $LB(op, entityKind, entityId, attr, priorEnc, newEnc, graph, flags)
///   ^IVG.Ledger("stmt", stmtId) = $LB(s, p, o, graph, createdSeq, deletedSeq)
///   ^IVG.Ledger("tuple", s, p, o, graphKey) = stmtId          (live statements only)
///   ^IVG.Ledger("idem", key) = $LB(seq, fingerprint)
///   ^||IVG.LedgerStage(token, i) / ^||IVG.LedgerOut(tag, i)   (process-private transport buffers)
/// Value encoding inside records: "N" = null, "S"_value = string (FR-014a exact representation).
///
/// Commit() owns TSTART/TCOMMIT/TROLLBACK and the head lock (R3, R5). Check order (FR-019
/// precedence): lock → state → idempotency → expected_head → size → apply.
Class Graph.KG.Ledger Extends %RegisteredObject
{

Parameter LOCKTIMEOUT = 30;

Parameter CHUNKCHARS = 1000000;

/// ---------------------------------------------------------------- helpers

ClassMethod NowMs() As %Integer
{
    Set tH = $ZTimestamp
    Set tDays = +$Piece(tH, ",", 1)
    Set tSecs = +$Piece(tH, ",", 2)
    Return ((((tDays - 47117) * 86400) + tSecs) * 1000) \ 1
}

ClassMethod NewId() As %String
{
    Return $ZConvert($Translate($System.Util.CreateGUID(), "-", ""), "L")
}

ClassMethod State() As %String
{
    Return $Get(^IVG.Ledger("meta", "state"), "never-enabled")
}

ClassMethod GKey(pGraph As %String) As %String
{
    Return $Select(pGraph = "": $Char(1), 1: pGraph)
}

ClassMethod MetaGet(pObj As %DynamicObject, pKey As %String, pDefault As %String = "") As %String
{
    If '$IsObject(pObj) Return pDefault
    If 'pObj.%IsDefined(pKey) Return pDefault
    Set tType = pObj.%GetTypeOf(pKey)
    If (tType = "null") || (tType = "unassigned") Return pDefault
    Return pObj.%Get(pKey)
}

ClassMethod Err(pCode As %String, pReason As %String, pHead As %String = "", pOpIndex As %String = "") As %String
{
    Set tObj = {}
    Do tObj.%Set("ok", 0, "boolean")
    Do tObj.%Set("error", pCode)
    Do tObj.%Set("reason", pReason)
    If pHead = "" { Do tObj.%Set("head", "", "null") } Else { Do tObj.%Set("head", pHead) }
    If pOpIndex = "" { Do tObj.%Set("op_index", "", "null") } Else { Do tObj.%Set("op_index", +pOpIndex, "number") }
    Return tObj.%ToJSON()
}

ClassMethod ErrLimit(pMax As %Integer, pCount As %Integer) As %String
{
    Set tObj = {}
    Do tObj.%Set("ok", 0, "boolean")
    Do tObj.%Set("error", "too_large")
    Do tObj.%Set("reason", "changeset has " _ pCount _ " operations; max_ops is " _ pMax)
    Do tObj.%Set("limit", pMax, "number")
    Do tObj.%Set("head", "", "null")
    Do tObj.%Set("op_index", "", "null")
    Return tObj.%ToJSON()
}

/// Build the RevisionInfo wire object for a sequence position ("" when absent).
ClassMethod RevisionObj(pSeq As %Integer) As %DynamicObject
{
    Set tRev = ##class(Graph.KG.LedgerRevision).%OpenId(pSeq)
    If '$IsObject(tRev) Return ""
    Set tObj = {}
    Do tObj.%Set("seq", tRev.Seq, "number")
    Do tObj.%Set("revision_id", tRev.RevisionId)
    If tRev.ParentId = "" { Do tObj.%Set("parent_id", "", "null") } Else { Do tObj.%Set("parent_id", tRev.ParentId) }
    Do tObj.%Set("kind", tRev.Kind)
    Do tObj.%Set("actor", tRev.Actor)
    Do tObj.%Set("actor_type", tRev.ActorType)
    Do tObj.%Set("conn_user", tRev.ConnUser)
    Do tObj.%Set("committed_ms", tRev.CommittedMs, "number")
    If tRev.Message = "" { Do tObj.%Set("message", "", "null") } Else { Do tObj.%Set("message", tRev.Message) }
    If tRev.Source = "" {
        Do tObj.%Set("source", "", "null")
    } Else {
        Try { Do tObj.%Set("source", {}.%FromJSON(tRev.Source)) } Catch { Do tObj.%Set("source", tRev.Source) }
    }
    If tRev.CorrelationId = "" { Do tObj.%Set("correlation_id", "", "null") } Else { Do tObj.%Set("correlation_id", tRev.CorrelationId) }
    If tRev.IdempotencyKey = "" { Do tObj.%Set("idempotency_key", "", "null") } Else { Do tObj.%Set("idempotency_key", tRev.IdempotencyKey) }
    Do tObj.%Set("op_count", +tRev.OpCount, "number")
    Return tObj
}

ClassMethod RevisionResponse(pSeq As %Integer, pReplayed As %Boolean, pStmtIds As %DynamicObject) As %String
{
    Set tObj = {}
    Do tObj.%Set("ok", 1, "boolean")
    Set tRev = ..RevisionObj(pSeq)
    If '$IsObject(tRev) Return ..Err("inconsistent", "revision row missing for seq " _ pSeq)
    Do tObj.%Set("revision", tRev)
    Do tObj.%Set("replayed", pReplayed, "boolean")
    If $IsObject(pStmtIds) { Do tObj.%Set("stmt_ids", pStmtIds) } Else { Do tObj.%Set("stmt_ids", {}) }
    Return tObj.%ToJSON()
}

/// Return pJson inline, or stage it in ^||IVG.LedgerOut and return "CHUNKED:tag:n" (R6).
ClassMethod LargeOut(pJson As %String) As %String
{
    If $Length(pJson) <= ..#CHUNKCHARS Return pJson
    Set tTag = "L" _ $Increment(^||IVG.LedgerOutSeq)
    Kill ^||IVG.LedgerOut(tTag)
    Set tN = 0
    For tPos = 1:..#CHUNKCHARS:$Length(pJson) {
        Set tN = tN + 1
        Set ^||IVG.LedgerOut(tTag, tN) = $Extract(pJson, tPos, (tPos + ..#CHUNKCHARS) - 1)
    }
    Return "CHUNKED:" _ tTag _ ":" _ tN
}

ClassMethod ReadLargeOutChunk(pTag As %String, pChunk As %Integer) As %String
{
    Return $Get(^||IVG.LedgerOut(pTag, pChunk), "")
}

/// ---------------------------------------------------------------- lifecycle

ClassMethod Meta() As %String
{
    Set tObj = {}
    Do tObj.%Set("ok", 1, "boolean")
    Do tObj.%Set("state", ..State())
    Do tObj.%Set("strict", (+$Get(^IVG.Ledger("meta", "strict"), 0) = 1), "boolean")
    Do tObj.%Set("max_ops", +$Get(^IVG.Ledger("meta", "maxOps"), 50000), "number")
    Do tObj.%Set("recon_bound", +$Get(^IVG.Ledger("meta", "reconBound"), 250000), "number")
    Set tHead = +$Get(^IVG.Ledger("head"), 0)
    Do tObj.%Set("head_seq", tHead, "number")
    Set tHeadId = $Get(^IVG.Ledger("idBySeq", tHead), "")
    If tHeadId = "" { Do tObj.%Set("head_id", "", "null") } Else { Do tObj.%Set("head_id", tHeadId) }
    Return tObj.%ToJSON()
}

ClassMethod Head() As %String
{
    If ..State() = "never-enabled" Return ..Err("not_enabled", "ledger has not been enabled")
    Set tSeq = +$Get(^IVG.Ledger("head"), 0)
    If tSeq < 1 Return ..Err("inconsistent", "ledger enabled but head is 0")
    Return ..RevisionResponse(tSeq, 0, {})
}

ClassMethod SeqOf(pId As %String) As %String
{
    Set tObj = {}
    If '$Data(^IVG.Ledger("revById", pId)) Return ..Err("unknown_head", "unknown revision " _ pId)
    Do tObj.%Set("ok", 1, "boolean")
    Do tObj.%Set("seq", +^IVG.Ledger("revById", pId), "number")
    Return tObj.%ToJSON()
}

/// Live statement identity for a tuple ("" when unrecorded). Used by verification.
ClassMethod StmtForTuple(pS As %String, pP As %String, pO As %String, pGraph As %String = "") As %String
{
    Return $Get(^IVG.Ledger("tuple", pS, pP, pO, ..GKey(pGraph)), "")
}

ClassMethod StageChunk(pToken As %String, pIndex As %Integer, pChunk As %String) As %Status
{
    Set ^||IVG.LedgerStage(pToken, pIndex) = pChunk
    Quit $$$OK
}

ClassMethod Count(pKind As %String) As %Status
{
    Quit ##class(Graph.KG.LedgerStats).Bump(pKind)
}

ClassMethod RecordVerify(pResult As %String) As %Status
{
    Set tSC = $$$OK
    Try {
        TSTART
        Set tStats = ##class(Graph.KG.LedgerStats).Ensure()
        If $IsObject(tStats) {
            Set tStats.LastVerifyMs = ..NowMs()
            Set tStats.LastVerifyResult = pResult
            Set tSC = tStats.%Save()
        }
        If $$$ISERR(tSC) { TROLLBACK } Else { TCOMMIT }
    } Catch ex {
        If $TLevel > 0 { TROLLBACK }
        Set tSC = ex.AsStatus()
    }
    Quit tSC
}

/// Admin utility: rebuild indices of the structural tables so phantom rows (left by
/// %NOINDEX loads) become visible to DELETE, COUNT and ORDER BY scans.
/// rdf_edges is handled through %BuildIndices on the named B-tree indices ONLY when the
/// Graph.KG.Edge class is compiled: BUILD INDEX FOR TABLE would invoke the functional
/// index's PurgeIndex, which kills ^KG and ^NKG (the spec 206 class of data loss).
ClassMethod RebuildEdgeIndices(pSchema As %String = "Graph_KG") As %Integer [ SqlName = rebuild_edge_indices, SqlProc ]
{
    Set tN = 0
    For tTable = "nodes", "rdf_labels", "rdf_props" {
        Try {
            Set tRS = ##class(%SQL.Statement).%ExecDirect(, "BUILD INDEX FOR TABLE " _ pSchema _ "." _ tTable)
            If tRS.%SQLCODE >= 0 Set tN = tN + 1
        } Catch {
        }
    }
    Try {
        If ##class(%Dictionary.CompiledClass).%ExistsId("Graph.KG.Edge") {
            Set tSC = ##class(Graph.KG.Edge).%BuildIndices($ListBuild("uspo", "idxS", "idxP", "idxSP", "idxPOid"), 1, 0)
            If $$$ISOK(tSC) Set tN = tN + 1
        } ElseIf pSchema = "Graph_KG" {
            Set tRS = ##class(%SQL.Statement).%ExecDirect(, "BUILD INDEX FOR TABLE " _ pSchema _ ".rdf_edges")
            If tRS.%SQLCODE >= 0 Set tN = tN + 1
        }
    } Catch {
    }
    Return tN
}

/// Test/admin reset: removes all ledger state, bypassing the immutability triggers.
ClassMethod PurgeAll() As %Status
{
    Kill ^IVG.Ledger
    Do ##class(Graph.KG.LedgerRevision).%KillExtent()
    Do ##class(Graph.KG.LedgerStats).%KillExtent()
    Quit $$$OK
}

/// Enable the ledger. never-enabled → genesis revision (seq 1); disabled → enabled with
/// adoption_needed=true; enabled → idempotent no-op. Returns JSON.
ClassMethod Enable(pStrict As %Boolean = 0, pMaxOps As %Integer = 50000, pReconBound As %Integer = 250000, pSchema As %String = "Graph_KG") As %String
{
    Set tState = ..State()
    If tState = "enabled" {
        Set tJson = ..RevisionResponse(+$Get(^IVG.Ledger("head"), 0), 0, {})
        Set tObj = {}.%FromJSON(tJson)
        Do tObj.%Set("adoption_needed", 0, "boolean")
        Return tObj.%ToJSON()
    }
    If $TLevel > 0 Return ..Err("transaction_open", "a transaction is already open ($TLEVEL=" _ $TLevel _ ")")
    Set tResult = ""
    Try {
        TSTART
        Lock +^IVG.Ledger("head"):..#LOCKTIMEOUT
        If '$Test {
            TROLLBACK
            Return ..Err("lock_timeout", "could not acquire ledger head lock")
        }
        If tState = "disabled" {
            Set ^IVG.Ledger("meta", "state") = "enabled"
            Set ^IVG.Ledger("meta", "strict") = +pStrict
            Set tStats = ##class(Graph.KG.LedgerStats).Ensure()
            Set tStats.State = "enabled", tStats.Strict = +pStrict, tStats.EnabledMs = ..NowMs()
            Set tSC = tStats.%Save()
            If $$$ISERR(tSC) {
                Lock -^IVG.Ledger("head")
                TROLLBACK
                Return ..Err("inconsistent", $System.Status.GetErrorText(tSC))
            }
            Lock -^IVG.Ledger("head")
            TCOMMIT
            Set tObj = {}.%FromJSON(..RevisionResponse(+$Get(^IVG.Ledger("head"), 0), 0, {}))
            Do tObj.%Set("adoption_needed", 1, "boolean")
            Return tObj.%ToJSON()
        }
        // never-enabled → genesis
        Set ^IVG.Ledger("meta", "state") = "enabled"
        Set ^IVG.Ledger("meta", "strict") = +pStrict
        Set ^IVG.Ledger("meta", "maxOps") = +pMaxOps
        Set ^IVG.Ledger("meta", "reconBound") = +pReconBound
        Set ^IVG.Ledger("head") = 0
        Set ^IVG.Ledger("stmtNext") = 1
        Set tSeq = 1
        Kill tRecords
        Set tRecords = 0, tFirst = "", tLast = ""
        Set tErr = ##class(Graph.KG.LedgerGenesis).Capture(tSeq, pSchema, .tRecords, .tFirst, .tLast)
        If tErr '= "" {
            Lock -^IVG.Ledger("head")
            TROLLBACK
            Return tErr
        }
        Set tErr = ..WriteRevision(tSeq, "genesis", "system:ledger", "system", "genesis", "", "", "", "", .tRecords, tFirst, tLast)
        If tErr '= "" {
            Lock -^IVG.Ledger("head")
            TROLLBACK
            Return tErr
        }
        Set tStats = ##class(Graph.KG.LedgerStats).Ensure()
        Set tStats.State = "enabled", tStats.Strict = +pStrict, tStats.EnabledMs = ..NowMs()
        Set tSC = tStats.%Save()
        If $$$ISERR(tSC) {
            Lock -^IVG.Ledger("head")
            TROLLBACK
            Return ..Err("inconsistent", $System.Status.GetErrorText(tSC))
        }
        Lock -^IVG.Ledger("head")
        TCOMMIT
        Set tObj = {}.%FromJSON(..RevisionResponse(tSeq, 0, {}))
        Do tObj.%Set("adoption_needed", 0, "boolean")
        Set tResult = tObj.%ToJSON()
    } Catch ex {
        If $TLevel > 0 { TROLLBACK }
        Lock -^IVG.Ledger("head")
        Set tResult = ..Err("inconsistent", ex.DisplayString())
    }
    Return tResult
}

ClassMethod Disable() As %String
{
    Set tObj = {}
    Do tObj.%Set("ok", 1, "boolean")
    Set tState = ..State()
    If tState '= "enabled" {
        Do tObj.%Set("state", tState)
        Return tObj.%ToJSON()
    }
    Try {
        TSTART
        Set ^IVG.Ledger("meta", "state") = "disabled"
        Set tStats = ##class(Graph.KG.LedgerStats).Ensure()
        Set tStats.State = "disabled", tStats.DisabledMs = ..NowMs()
        Set tSC = tStats.%Save()
        If $$$ISERR(tSC) {
            TROLLBACK
            Return ..Err("inconsistent", $System.Status.GetErrorText(tSC))
        }
        TCOMMIT
    } Catch ex {
        If $TLevel > 0 { TROLLBACK }
        Return ..Err("inconsistent", ex.DisplayString())
    }
    Do tObj.%Set("state", "disabled")
    Return tObj.%ToJSON()
}

ClassMethod SetStrict(pFlag As %Boolean) As %String
{
    If ..State() = "never-enabled" Return ..Err("not_enabled", "ledger has not been enabled")
    Try {
        TSTART
        Set ^IVG.Ledger("meta", "strict") = +pFlag
        Set tStats = ##class(Graph.KG.LedgerStats).Ensure()
        Set tStats.Strict = +pFlag
        Set tSC = tStats.%Save()
        If $$$ISERR(tSC) {
            TROLLBACK
            Return ..Err("inconsistent", $System.Status.GetErrorText(tSC))
        }
        TCOMMIT
    } Catch ex {
        If $TLevel > 0 { TROLLBACK }
        Return ..Err("inconsistent", ex.DisplayString())
    }
    Set tObj = {}
    Do tObj.%Set("ok", 1, "boolean")
    Do tObj.%Set("strict", (+pFlag = 1), "boolean")
    Return tObj.%ToJSON()
}

/// ---------------------------------------------------------------- commit

/// pPayload: canonical changeset JSON, or a stage token when meta.staged is true.
/// pMetaJson: {"fingerprint","expected_head","idempotency_key","kind","schema","staged"}.
ClassMethod Commit(pPayload As %String, pMetaJson As %String) As %String
{
    Set tResult = "", tBump = "", tOk = 0, tToken = ""
    If $TLevel > 0 {
        Set tResult = ..Err("transaction_open", "a transaction is already open ($TLEVEL=" _ $TLevel _ ")")
        Return ..LargeOut(tResult)
    }
    Try {
        TSTART
        Set tResult = ..CommitInTxn(pPayload, pMetaJson, .tBump, .tOk, .tToken)
        Lock -^IVG.Ledger("head")
        If tOk { TCOMMIT } Else { TROLLBACK }
    } Catch ex {
        If $TLevel > 0 { TROLLBACK }
        Lock -^IVG.Ledger("head")
        Set tResult = ..Err("inconsistent", ex.DisplayString())
        Set tBump = ""
    }
    If tToken '= "" Kill ^||IVG.LedgerStage(tToken)
    If tBump '= "" Do ##class(Graph.KG.LedgerStats).Bump(tBump)
    Return ..LargeOut(tResult)
}

/// Body of Commit, executed inside TSTART. Sets pOk=1 only when everything was applied
/// and written. Returns the response JSON (success, replay, or error).
ClassMethod CommitInTxn(pPayload As %String, pMetaJson As %String, ByRef pBump As %String, ByRef pOk As %Boolean, ByRef pToken As %String) As %String
{
    Set pOk = 0, pBump = ""
    Set tMeta = {}.%FromJSON(pMetaJson)
    Set tSchema = ..MetaGet(tMeta, "schema", "Graph_KG")
    Set tKind = ..MetaGet(tMeta, "kind", "changeset")
    Set tStaged = (..MetaGet(tMeta, "staged", 0) = 1) || (..MetaGet(tMeta, "staged", "") = "true")
    If tStaged {
        Set pToken = pPayload
        Set tStream = ##class(%Stream.TmpCharacter).%New()
        Set tI = ""
        For {
            Set tI = $Order(^||IVG.LedgerStage(pToken, tI))
            Quit:tI=""
            Do tStream.Write(^||IVG.LedgerStage(pToken, tI))
        }
        Set tCs = {}.%FromJSON(tStream)
    } Else {
        Set tCs = {}.%FromJSON(pPayload)
    }

    Lock +^IVG.Ledger("head"):..#LOCKTIMEOUT
    If '$Test {
        Set pBump = "rej_lock_timeout"
        Return ..Err("lock_timeout", "could not acquire ledger head lock within " _ ..#LOCKTIMEOUT _ "s")
    }
    Set tState = ..State()
    If tState = "never-enabled" Return ..Err("not_enabled", "ledger has not been enabled")
    If tState = "disabled" {
        Set pBump = "rej_disabled"
        Return ..Err("disabled", "ledger is disabled; re-enable before committing")
    }
    Set tHeadSeq = +$Get(^IVG.Ledger("head"), 0)
    Set tHeadId = $Get(^IVG.Ledger("idBySeq", tHeadSeq), "")

    // 1. idempotency (FR-019 precedence over expected_head)
    Set tKey = ..MetaGet(tMeta, "idempotency_key", "")
    Set tFp = ..MetaGet(tMeta, "fingerprint", "")
    If (tKey '= "") && $Data(^IVG.Ledger("idem", tKey)) {
        Set tBound = ^IVG.Ledger("idem", tKey)
        If $ListGet(tBound, 2) = tFp {
            Set pBump = "replays_idem"
            Return ..RevisionResponse(+$ListGet(tBound, 1), 1, {})
        }
        Set pBump = "rej_idem_conflict"
        Return ..Err("idempotency_conflict", "idempotency key '" _ tKey _ "' already bound to a different changeset", tHeadId)
    }

    // 2. expected_head
    Set tExp = ..MetaGet(tMeta, "expected_head", "")
    If tExp '= "" {
        If '$Data(^IVG.Ledger("revById", tExp)) {
            Set pBump = "rej_unknown_head"
            Return ..Err("unknown_head", "expected_head " _ tExp _ " is not a revision of this ledger", tHeadId)
        }
        If tExp '= tHeadId {
            Set pBump = "rej_stale_head"
            Return ..Err("stale_head", "expected_head " _ tExp _ " but head is " _ tHeadId, tHeadId)
        }
    }

    // 3. size / empty (caller changesets only)
    Set tOps = tCs.%Get("ops")
    Set tCount = $Select($IsObject(tOps): tOps.%Size(), 1: 0)
    If tKind = "changeset" {
        If tCount = 0 Return ..Err("empty", "changeset has no operations", tHeadId)
        Set tMax = +$Get(^IVG.Ledger("meta", "maxOps"), 50000)
        If tCount > tMax {
            Set pBump = "rej_size_limit"
            Return ..ErrLimit(tMax, tCount)
        }
    }

    // 4. apply
    Set tSeq = tHeadSeq + 1
    Kill tRecords
    Set tRecords = 0, tFirst = "", tLast = ""
    Set tStmtIds = {}
    If tKind = "adoption" {
        Set tErr = ##class(Graph.KG.LedgerGenesis).Adopt(tOps, tSeq, tSchema, .tRecords, .tFirst, .tLast)
    } Else {
        Set tErr = ##class(Graph.KG.LedgerApply).ApplyAll(tOps, tSeq, tSchema, .tRecords, .tStmtIds, .tFirst, .tLast)
    }
    If tErr '= "" {
        Set pBump = "rej_failed_op"
        Return tErr
    }

    // 5. revision row, head, bindings, stats
    Set tSource = ""
    If tCs.%IsDefined("source") && (tCs.%GetTypeOf("source") = "object") Set tSource = tCs.%Get("source").%ToJSON()
    Set tErr = ..WriteRevision(tSeq, tKind, ..MetaGet(tCs, "actor", ""), ..MetaGet(tCs, "actor_type", ""), ..MetaGet(tCs, "message", ""), tSource, ..MetaGet(tCs, "correlation_id", ""), tKey, tHeadId, .tRecords, tFirst, tLast)
    If tErr '= "" Return tErr
    If tKey '= "" Set ^IVG.Ledger("idem", tKey) = $ListBuild(tSeq, tFp)
    Set pOk = 1
    Return ..RevisionResponse(tSeq, 0, tStmtIds)
}

/// Write mutation records, the LedgerRevision row, head pointer and id maps, and bump
/// the success counters. Caller holds the lock and the transaction. Returns "" or error JSON.
ClassMethod WriteRevision(pSeq As %Integer, pKind As %String, pActor As %String, pActorType As %String, pMessage As %String, pSource As %String, pCorrelation As %String, pIdemKey As %String, pParentId As %String, ByRef pRecords, pStmtFirst As %String, pStmtLast As %String) As %String
{
    For tI = 1:1:pRecords {
        Set ^IVG.Ledger("rec", pSeq, tI) = pRecords(tI)
    }
    Set tId = ..NewId()
    Set tRev = ##class(Graph.KG.LedgerRevision).%New()
    Set tRev.Seq = pSeq
    Set tRev.RevisionId = tId
    Set tRev.ParentId = pParentId
    Set tRev.Kind = pKind
    Set tRev.Actor = $Select(pActor = "": "system:ledger", 1: pActor)
    Set tRev.ActorType = $Select(pActorType = "": "system", 1: pActorType)
    Set tRev.ConnUser = $Username
    Set tRev.CommittedMs = ..NowMs()
    Set tRev.Message = pMessage
    Set tRev.Source = pSource
    Set tRev.CorrelationId = pCorrelation
    Set tRev.IdempotencyKey = pIdemKey
    Set tRev.OpCount = +pRecords
    If pStmtFirst '= "" Set tRev.StmtFirst = +pStmtFirst
    If pStmtLast '= "" Set tRev.StmtLast = +pStmtLast
    Set tSC = tRev.%Save()
    If $$$ISERR(tSC) Return ..Err("inconsistent", "revision row save failed: " _ $System.Status.GetErrorText(tSC), pParentId)
    Set ^IVG.Ledger("head") = pSeq
    Set ^IVG.Ledger("revById", tId) = pSeq
    Set ^IVG.Ledger("idBySeq", pSeq) = tId
    Set tStats = ##class(Graph.KG.LedgerStats).Ensure()
    If $IsObject(tStats) {
        Set tStats.HeadSeq = pSeq
        Set tStats.CommitsOk = tStats.CommitsOk + 1
        Set tSC = tStats.%Save()
        If $$$ISERR(tSC) Return ..Err("inconsistent", "stats save failed: " _ $System.Status.GetErrorText(tSC), pParentId)
    }
    Return ""
}

/// ---------------------------------------------------------------- records

/// Canonical JSON of one mutation record (R14): keys in alphabetical order.
ClassMethod CanonicalRecord(pSeq As %Integer, pOrdinal As %Integer) As %String
{
    Set tRec = $Get(^IVG.Ledger("rec", pSeq, pOrdinal), "")
    If tRec = "" Return ""
    Set tObj = {}
    Do tObj.%Set("attr", $ListGet(tRec, 4))
    Do tObj.%Set("entity_id", $ListGet(tRec, 3))
    Do tObj.%Set("entity_kind", $ListGet(tRec, 2))
    Set tFlags = [], tF = $ListGet(tRec, 8)
    For tI = 1:1:$Length(tF, ",") { If $Piece(tF, ",", tI) '= "" Do tFlags.%Push($Piece(tF, ",", tI)) }
    Do tObj.%Set("flags", tFlags)
    Set tG = $ListGet(tRec, 7)
    If tG = "" { Do tObj.%Set("graph", "", "null") } Else { Do tObj.%Set("graph", tG) }
    Set tNew = $ListGet(tRec, 6)
    If $Extract(tNew) = "N" { Do tObj.%Set("new", "", "null") } Else { Do tObj.%Set("new", $Extract(tNew, 2, *)) }
    Do tObj.%Set("op", $ListGet(tRec, 1))
    Do tObj.%Set("ordinal", pOrdinal, "number")
    Set tPrior = $ListGet(tRec, 5)
    If $Extract(tPrior) = "N" { Do tObj.%Set("prior", "", "null") } Else { Do tObj.%Set("prior", $Extract(tPrior, 2, *)) }
    Do tObj.%Set("seq", pSeq, "number")
    Return tObj.%ToJSON()
}

/// Paged record reader used by the Python client (R9). Returns at most pPageSize rows
/// starting at (pSeqFrom, pOrdinalFrom) and never beyond pSeqTo.
Query Records(pSeqFrom As %Integer, pSeqTo As %Integer, pOrdinalFrom As %Integer = 1, pPageSize As %Integer = 5000) As %Query(ROWSPEC = "seq:%Integer,ordinal:%Integer,op:%String,entity_kind:%String,entity_id:%String,attr:%String,prior:%String,prior_null:%Integer,new:%String,new_null:%Integer,graph:%String,flags:%String") [ SqlName = ledger_records, SqlProc ]
{
}

ClassMethod RecordsExecute(ByRef qHandle As %Binary, pSeqFrom As %Integer, pSeqTo As %Integer, pOrdinalFrom As %Integer = 1, pPageSize As %Integer = 5000) As %Status
{
    Set tOrd = +pOrdinalFrom - 1
    If tOrd < 0 Set tOrd = 0
    Set qHandle = $ListBuild(+pSeqFrom, tOrd, +pSeqTo, +pPageSize)
    Quit $$$OK
}

ClassMethod RecordsFetch(ByRef qHandle As %Binary, ByRef Row As %List, ByRef AtEnd As %Integer = 0) As %Status [ PlaceAfter = RecordsExecute ]
{
    Set Row = "", AtEnd = 0
    Set tSeq = $ListGet(qHandle, 1), tOrd = $ListGet(qHandle, 2), tSeqTo = $ListGet(qHandle, 3), tLeft = $ListGet(qHandle, 4)
    If tLeft <= 0 { Set AtEnd = 1 Quit $$$OK }
    For {
        If (tSeq = "") || (tSeq > tSeqTo) { Set AtEnd = 1 Quit }
        Set tOrd = $Order(^IVG.Ledger("rec", tSeq, tOrd))
        If tOrd = "" {
            Set tSeq = $Order(^IVG.Ledger("rec", tSeq))
            Set tOrd = 0
            Continue
        }
        Set tRec = ^IVG.Ledger("rec", tSeq, tOrd)
        Set tPrior = $ListGet(tRec, 5), tNew = $ListGet(tRec, 6)
        Set Row = $ListBuild(tSeq, tOrd, $ListGet(tRec, 1), $ListGet(tRec, 2), $ListGet(tRec, 3), $ListGet(tRec, 4), $Extract(tPrior, 2, *), ($Extract(tPrior) = "N"), $Extract(tNew, 2, *), ($Extract(tNew) = "N"), $ListGet(tRec, 7), $ListGet(tRec, 8))
        Set tLeft = tLeft - 1
        Quit
    }
    Set qHandle = $ListBuild(tSeq, tOrd, tSeqTo, tLeft)
    Quit $$$OK
}

ClassMethod RecordsClose(ByRef qHandle As %Binary) As %Status [ PlaceAfter = RecordsExecute ]
{
    Set qHandle = ""
    Quit $$$OK
}

}
