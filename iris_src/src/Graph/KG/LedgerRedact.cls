/// Spec 256 — redacting a target (a tenant) from the revision ledger. See ADR-0013.
///
/// A target is a graph name, optionally narrowed to the entities whose id starts with
/// prefix _ separator. Every record of the target becomes a tombstone in its own
/// (seq, ordinal):
///
///   ^IVG.Ledger("rec", seq, ordinal) = $LB("redacted", sha256(CanonicalRecord), flags, op, entityKind)
///
/// so the history keeps its shape and loses its content. A revision with any tombstone
/// loses its message, source, correlation id and idempotency key (spec 256 Decision 2),
/// through the one exemption from the immutability trigger on Graph_KG.ledger_revisions.
/// The run ends with a content-free revision of kind "redaction" naming the redacted
/// revisions by seq (Decision 1).
///
/// The ledger's node operations are graph-blind: every node record has graph "". A
/// tenant's node records are therefore addressed by prefix in the default graph, and
/// a tenant whose relationships live in its own graph takes two targets.
///
/// Storage added by this class:
///   ^IVG.Ledger("redacting") = $LB(targetDigest, graph, match, startedMs, actor)   (while a run is unfinished)
///   ^IVG.Ledger("redacting", "revs", seq) = records tombstoned in that revision
///   ^IVG.Ledger("redacting", "stmt", stmtId) = ""   statements whose create/delete was tombstoned
///   ^IVG.Ledger("redacting", "n", "records"|"cleared"|"idem") = counters
///   ^IVG.Ledger("redactedRev", seq) = ""             revisions whose metadata was cleared
///   ^IVG.Ledger("idemRedacted", sha256(key)) = seq   idempotency bindings of redacted revisions
///
/// Batches (Decision 3): each batch is one transaction under the head lock. While the
/// marker exists the Python readers refuse, verify() reports redaction_in_progress, and
/// Graph.KG.Ledger.CommitInTxn refuses a changeset with a record in the target.
Class Graph.KG.LedgerRedact Extends %RegisteredObject
{

/// A batch scans at most this many records per tombstone it may write, so a batch over
/// a ledger where the target is sparse still releases the head lock quickly.
Parameter SCANFACTOR = 20;

/// ---------------------------------------------------------------- helpers

ClassMethod Sha256Hex(pText As %String) As %String
{
    Set tBin = $System.Encryption.SHAHash(256, $ZConvert(pText, "O", "UTF8"))
    Return $ZConvert(##class(%xsd.hexBinary).LogicalToXSD(tBin), "L")
}

/// The string an entity id must start with: the prefix and its separator, so "acme"
/// never matches "acme-health|". A prefix that already ends with the separator is used
/// as it is. "" when there is no prefix (the whole graph).
ClassMethod MatchString(pPrefix As %String, pSeparator As %String) As %String
{
    If pPrefix = "" Return ""
    If $Extract(pPrefix, ($Length(pPrefix) - $Length(pSeparator)) + 1, *) = pSeparator Return pPrefix
    Return pPrefix _ pSeparator
}

/// What the audit holds instead of the target's name.
ClassMethod TargetDigest(pName As %String, pMatch As %String) As %String
{
    Return ..Sha256Hex(pName _ $Char(0) _ pMatch)
}

ClassMethod HasPrefix(pId As %String, pMatch As %String) As %Boolean
{
    Return ($Extract(pId, 1, $Length(pMatch)) = pMatch)
}

/// For an error message only (never stored).
ClassMethod TargetText(pName As %String, pMatch As %String) As %String
{
    Set tText = $Select(pName = "": "the default graph", 1: "graph '" _ pName _ "'")
    If pMatch '= "" Set tText = tText _ " (ids starting '" _ pMatch _ "')"
    Return tText
}

/// $LB(source, target) from an encoded tuple value ("S" _ tuple JSON), "" when absent.
ClassMethod TupleEnds(pEnc As %String) As %List
{
    If $Extract(pEnc) '= "S" Return ""
    Try {
        Set tT = {}.%FromJSON($Extract(pEnc, 2, *))
    } Catch {
        Return ""
    }
    If '$IsObject(tT) Return ""
    Return $ListBuild(##class(Graph.KG.Ledger).MetaGet(tT, "s", ""), ##class(Graph.KG.Ledger).MetaGet(tT, "o", ""))
}

/// 1 when record pRec belongs to the target, 0 when not, -1 when it is a relationship
/// record of the target graph whose endpoints cannot be determined (prefix targets only).
ClassMethod Matches(pRec As %List, pName As %String, pMatch As %String) As %Integer
{
    Set tOp = $ListGet(pRec, 1)
    If (tOp = "redacted") || (tOp = "redact") Return 0
    If $Translate($ListGet(pRec, 7), $Char(0), "") '= pName Return 0
    If pMatch = "" Return 1
    Set tKind = $ListGet(pRec, 2), tId = $ListGet(pRec, 3)
    If tKind = "node" Return ..HasPrefix(tId, pMatch)
    If (tOp = "create_rel") || (tKind = "temporal_edge") {
        Set tEnds = ..TupleEnds($ListGet(pRec, 6))
    } ElseIf tOp = "delete_rel" {
        Set tEnds = ..TupleEnds($ListGet(pRec, 5))
    } Else {
        If tId = "" Return -1
        If $Data(^IVG.Ledger("redacting", "stmt", tId)) Return 1
        Set tEnds = $Get(^||IVG.RedactStmt(tId))
        If tEnds = "" {
            Set tRow = $Get(^IVG.Ledger("stmt", tId))
            If tRow '= "" Set tEnds = $ListBuild($ListGet(tRow, 1), $ListGet(tRow, 3))
        }
    }
    If tEnds = "" Return -1
    Return (..HasPrefix($ListGet(tEnds, 1), pMatch) || ..HasPrefix($ListGet(tEnds, 2), pMatch))
}

/// Statement endpoints for the qualifier records of a prefix target, from the create_rel
/// and delete_rel records of the target graph. Read-only; outside any lock.
ClassMethod PrePass(pName As %String)
{
    Kill ^||IVG.RedactStmt
    Set tSeq = ""
    For {
        Set tSeq = $Order(^IVG.Ledger("rec", tSeq))
        Quit:tSeq=""
        Set tOrd = ""
        For {
            Set tOrd = $Order(^IVG.Ledger("rec", tSeq, tOrd), 1, tRec)
            Quit:tOrd=""
            Continue:$ListGet(tRec, 2)'="rel"
            Continue:$Translate($ListGet(tRec, 7), $Char(0), "")'=pName
            Set tId = $ListGet(tRec, 3)
            Continue:tId=""
            If $ListGet(tRec, 1) = "create_rel" {
                Set tEnds = ..TupleEnds($ListGet(tRec, 6))
            } ElseIf $ListGet(tRec, 1) = "delete_rel" {
                Set tEnds = ..TupleEnds($ListGet(tRec, 5))
            } Else {
                Continue
            }
            If tEnds '= "" Set ^||IVG.RedactStmt(tId) = tEnds
        }
    }
}

/// Live rows of the target in the content tables. -1 on a SQL failure (pWhat says which).
///
/// A prefix target in the default graph counts node, label and property rows in every
/// graph: the ledger's node records are graph-blind, and so is verify(), so a node id
/// live in any graph would still be replayed against. Relationships are counted in the
/// target graph only, because that is where their records are.
ClassMethod LiveRows(pName As %String, pMatch As %String, pSchema As %String, Output pWhat As %String) As %Integer
{
    Set pWhat = "", tTotal = 0
    For tTable = "nodes", "rdf_labels", "rdf_props", "rdf_edges" {
        Set tIdCol = $Select(tTable = "nodes": "node_id", 1: "s")
        Set tFrom = "SELECT COUNT(*) FROM " _ pSchema _ "." _ tTable _ " WHERE "
        // COALESCE on both sides: graph_id is nullable, and an empty bound value arrives as NULL.
        Set tInGraph = "COALESCE(graph_id, '') = COALESCE(?, '')"
        If pMatch = "" {
            Set tRS = ##class(%SQL.Statement).%ExecDirect(, tFrom _ tInGraph, pName)
        } ElseIf tTable = "rdf_edges" {
            Set tRS = ##class(%SQL.Statement).%ExecDirect(, tFrom _ tInGraph _ " AND (%EXACT(s) %STARTSWITH ? OR %EXACT(o_id) %STARTSWITH ?)", pName, pMatch, pMatch)
        } ElseIf pName = "" {
            Set tRS = ##class(%SQL.Statement).%ExecDirect(, tFrom _ "%EXACT(" _ tIdCol _ ") %STARTSWITH ?", pMatch)
        } Else {
            Set tRS = ##class(%SQL.Statement).%ExecDirect(, tFrom _ tInGraph _ " AND %EXACT(" _ tIdCol _ ") %STARTSWITH ?", pName, pMatch)
        }
        If tRS.%SQLCODE < 0 {
            Set pWhat = tTable _ ": " _ tRS.%Message
            Return -1
        }
        Do tRS.%Next()
        Set tN = +tRS.%GetData(1)
        If tN > 0 {
            Set tTotal = tTotal + tN
            Set pWhat = pWhat _ $Select(pWhat = "": "", 1: ", ") _ tTable _ " " _ tN
        }
    }
    Return tTotal
}

/// ---------------------------------------------------------------- entry points

/// Redact a target. Returns the receipt JSON, or an error object in the shape of
/// Graph.KG.Ledger.Err. pMaxBatches > 0 stops after that many batches and leaves the
/// run in progress; running the same target again resumes it.
ClassMethod Redact(pGraph As %String = "", pPrefix As %String = "", pSeparator As %String = "|", pActor As %String = "", pBatch As %Integer = 2000, pMaxBatches As %Integer = 0, pSchema As %String = "Graph_KG") As %String
{
    If $TLevel > 0 Return ##class(Graph.KG.Ledger).Err("transaction_open", "a transaction is already open ($TLEVEL=" _ $TLevel _ ")")
    Set tState = ##class(Graph.KG.Ledger).State()
    If tState = "never-enabled" Return ##class(Graph.KG.Ledger).Err("not_enabled", "ledger has not been enabled")
    If tState = "disabled" Return ##class(Graph.KG.Ledger).Err("disabled", "ledger is disabled; re-enable before redacting")
    If pSeparator = "" Return ##class(Graph.KG.Ledger).Err("invalid_target", "the separator must not be empty: without it a prefix 'acme' would also match 'acme-health|'")
    Try {
        Set tName = ##class(Graph.KG.GraphKey).ForName(pGraph)
    } Catch ex {
        Return ##class(Graph.KG.Ledger).Err("invalid_target", ex.DisplayString())
    }
    If pActor = "" Set pActor = "system:ledger-redact"
    If +pBatch < 1 Set pBatch = 1
    Set tMatch = ..MatchString(pPrefix, pSeparator)
    Set tDigest = ..TargetDigest(tName, tMatch)

    Set tMarker = $Get(^IVG.Ledger("redacting"))
    If (tMarker '= "") && ($ListGet(tMarker, 1) '= tDigest) {
        Return ##class(Graph.KG.Ledger).Err("redaction_in_progress", "another redaction (target_sha256 " _ $ListGet(tMarker, 1) _ ") has not finished; run it again to finish it first")
    }
    Set tLive = ..LiveRows(tName, tMatch, pSchema, .tWhat)
    If tLive < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "liveness check failed: " _ tWhat)
    If tLive > 0 {
        Return ##class(Graph.KG.Ledger).Err("target_live", ..TargetText(tName, tMatch) _ " still holds " _ tLive _ " live row(s) (" _ tWhat _ "); erase it before redacting")
    }

    If tMatch '= "" Do ..PrePass(tName)
    Set tCap = +pBatch * ..#SCANFACTOR
    Set tSeq = $Order(^IVG.Ledger("rec", "")), tOrd = ""
    Set tBatches = 0, tMaxMs = 0, tUnknown = 0, tDone = 0, tRedaction = "", tStmts = 0, tErr = ""
    For {
        If (+pMaxBatches > 0) && (tBatches >= +pMaxBatches) Quit
        Set tErr = ..Batch(tName, tMatch, tDigest, pActor, +pBatch, tCap, pSchema, .tSeq, .tOrd, .tUnknown, .tMs)
        If tErr '= "" Quit
        Set tBatches = tBatches + 1
        If tMs > tMaxMs Set tMaxMs = tMs
        If tSeq = "" {
            Set tDone = 1
            Quit
        }
    }
    Kill ^||IVG.RedactStmt
    If tErr '= "" Return ##class(Graph.KG.Ledger).LargeOut(tErr)
    Kill tCounts
    If tDone {
        Set tErr = ..Final(tName, tMatch, tDigest, pActor, pSchema, .tRedaction, .tStmts, .tCounts, .tMs)
        If tErr '= "" Return ##class(Graph.KG.Ledger).LargeOut(tErr)
        Set tBatches = tBatches + 1
        If tMs > tMaxMs Set tMaxMs = tMs
    } Else {
        Do ..ReadCounts(.tCounts)
    }
    Return ##class(Graph.KG.Ledger).LargeOut(..Receipt(tDone, tDigest, .tCounts, tStmts, tUnknown, tBatches, tMaxMs, tRedaction))
}

/// {"ok":true,"in_progress":bool[,"target_sha256","started_ms","actor"]}
ClassMethod InProgress() As %String
{
    Set tObj = {}
    Do tObj.%Set("ok", 1, "boolean")
    Set tM = $Get(^IVG.Ledger("redacting"))
    Do tObj.%Set("in_progress", (tM '= ""), "boolean")
    If tM '= "" {
        Do tObj.%Set("target_sha256", $ListGet(tM, 1))
        Do tObj.%Set("started_ms", +$ListGet(tM, 4), "number")
        Do tObj.%Set("actor", $ListGet(tM, 5))
    }
    Return tObj.%ToJSON()
}

/// Called by Graph.KG.Ledger.CommitInTxn, inside its transaction and lock, when a
/// redaction is in progress: "" when no record of the changeset is in the target.
ClassMethod BlocksCommit(ByRef pRecords) As %String
{
    Set tM = $Get(^IVG.Ledger("redacting"))
    If tM = "" Return ""
    Set tName = $ListGet(tM, 2), tMatch = $ListGet(tM, 3)
    For tI = 1:1:+$Get(pRecords) {
        If ..Matches(pRecords(tI), tName, tMatch) > 0 {
            Return ##class(Graph.KG.Ledger).Err("redaction_in_progress", "a redaction is in progress and this changeset writes into its target; commit again when it has finished")
        }
    }
    Return ""
}

/// ---------------------------------------------------------------- batches

/// One transaction under the head lock. Advances the cursor (pSeq, pOrd); pSeq = ""
/// when the scan has passed the last record. Returns "" or error JSON.
ClassMethod Batch(pName As %String, pMatch As %String, pDigest As %String, pActor As %String, pBatch As %Integer, pCap As %Integer, pSchema As %String, ByRef pSeq As %String, ByRef pOrd As %String, ByRef pUnknown As %Integer, Output pMs As %Integer) As %String
{
    Set pMs = 0
    Set tStart = $ZHorolog, tLevel = $TLevel, tResult = ""
    Set tSeq0 = pSeq, tOrd0 = pOrd, tUnknown0 = pUnknown
    Try {
        TSTART
        Lock +^IVG.Ledger("head"):##class(Graph.KG.Ledger).#LOCKTIMEOUT
        If '$Test {
            TROLLBACK 1
            Set tResult = ##class(Graph.KG.Ledger).Err("lock_timeout", "could not acquire ledger head lock")
        } Else {
            Set tResult = ..BatchInLock(pName, pMatch, pDigest, pActor, pBatch, pCap, pSchema, .pSeq, .pOrd, .pUnknown)
            If tResult = "" { TCOMMIT } Else { TROLLBACK 1 }
            Lock -^IVG.Ledger("head")
        }
    } Catch ex {
        If $TLevel > tLevel { TROLLBACK 1 }
        Lock -^IVG.Ledger("head")
        Kill %IVGLedgerRedaction
        Set tResult = ##class(Graph.KG.Ledger).Err("inconsistent", ex.DisplayString())
    }
    If tResult '= "" Set pSeq = tSeq0, pOrd = tOrd0, pUnknown = tUnknown0
    Set pMs = (($ZHorolog - tStart) * 1000) \ 1
    Return tResult
}

ClassMethod BatchInLock(pName As %String, pMatch As %String, pDigest As %String, pActor As %String, pBatch As %Integer, pCap As %Integer, pSchema As %String, ByRef pSeq As %String, ByRef pOrd As %String, ByRef pUnknown As %Integer) As %String
{
    // The ledger can be disabled between two batches; the run resumes once it is re-enabled.
    If ##class(Graph.KG.Ledger).State() '= "enabled" Return ##class(Graph.KG.Ledger).Err("disabled", "ledger is disabled; re-enable it and run the redaction again to finish it")
    Set tMatched = 0, tScanned = 0
    For {
        If pSeq = "" Quit
        If (tMatched >= pBatch) || (tScanned >= pCap) Quit
        Set pOrd = $Order(^IVG.Ledger("rec", pSeq, pOrd), 1, tRec)
        If pOrd = "" {
            Set pSeq = $Order(^IVG.Ledger("rec", pSeq))
            Continue
        }
        Set tScanned = tScanned + 1
        Set tHit = ..Matches(tRec, pName, pMatch)
        If tHit < 0 {
            Set pUnknown = pUnknown + 1
            Continue
        }
        If tHit = 0 Continue
        If '$Data(^IVG.Ledger("redacting")) {
            Set ^IVG.Ledger("redacting") = $ListBuild(pDigest, pName, pMatch, ##class(Graph.KG.Ledger).NowMs(), pActor)
        }
        Set tErr = ..Tombstone(pSeq, pOrd, tRec)
        If tErr '= "" Return tErr
        Set tMatched = tMatched + 1
    }
    Return ""
}

/// Replace one record with its tombstone; on the revision's first, clear its metadata.
ClassMethod Tombstone(pSeq As %Integer, pOrd As %Integer, pRec As %List) As %String
{
    Set tDigest = ..Sha256Hex(##class(Graph.KG.Ledger).CanonicalRecord(pSeq, pOrd))
    Set tOp = $ListGet(pRec, 1), tKind = $ListGet(pRec, 2), tId = $ListGet(pRec, 3)
    Set ^IVG.Ledger("rec", pSeq, pOrd) = $ListBuild("redacted", tDigest, $ListGet(pRec, 8), tOp, tKind)
    // A tombstoned create/delete hides the statement's endpoints from a resumed run.
    If (tKind = "rel") && (tId '= "") && ((tOp = "create_rel") || (tOp = "delete_rel")) {
        Set ^IVG.Ledger("redacting", "stmt", tId) = ""
    }
    // Set, not $Increment: these counters must roll back with the batch.
    Set ^IVG.Ledger("redacting", "revs", pSeq) = $Get(^IVG.Ledger("redacting", "revs", pSeq)) + 1
    Set ^IVG.Ledger("redacting", "n", "records") = $Get(^IVG.Ledger("redacting", "n", "records")) + 1
    If $Data(^IVG.Ledger("redactedRev", pSeq)) Return ""
    Return ..ClearRevision(pSeq)
}

/// Spec 256 Decision 2: clear message, source, correlation id and idempotency key of a
/// revision with a redacted record. The UPDATE below is the only statement the
/// immutability trigger on Graph_KG.ledger_revisions lets through (ADR-0013), and only
/// while %IVGLedgerRedaction names this seq and ^IVG.Ledger("redactedRev", seq) exists.
ClassMethod ClearRevision(pSeq As %Integer) As %String
{
    Set tRS = ##class(%SQL.Statement).%ExecDirect(, "SELECT idempotency_key FROM Graph_KG.ledger_revisions WHERE seq = ?", pSeq)
    If tRS.%SQLCODE < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "revision " _ pSeq _ " could not be read: " _ tRS.%Message)
    If 'tRS.%Next() Return ##class(Graph.KG.Ledger).Err("inconsistent", "revision row missing for seq " _ pSeq)
    Set tKey = tRS.%GetData(1)
    If tKey '= "" {
        // A retry with this key must neither replay a redacted revision nor re-create
        // the erased content (Ledger.CommitInTxn refuses it as idempotency_redacted).
        Kill ^IVG.Ledger("idem", tKey)
        Set ^IVG.Ledger("idemRedacted", ..Sha256Hex(tKey)) = pSeq
        Set ^IVG.Ledger("redacting", "n", "idem") = $Get(^IVG.Ledger("redacting", "n", "idem")) + 1
    }
    Set ^IVG.Ledger("redactedRev", pSeq) = ""
    Set %IVGLedgerRedaction = pSeq
    Try {
        Set tRS = ##class(%SQL.Statement).%ExecDirect(, "UPDATE Graph_KG.ledger_revisions SET message = NULL, source = NULL, correlation_id = NULL, idempotency_key = NULL WHERE seq = ?", pSeq)
    } Catch ex {
        Kill %IVGLedgerRedaction
        Throw ex
    }
    Kill %IVGLedgerRedaction
    If tRS.%SQLCODE < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "clearing the metadata of revision " _ pSeq _ " failed: " _ tRS.%Message)
    Set ^IVG.Ledger("redacting", "n", "cleared") = $Get(^IVG.Ledger("redacting", "n", "cleared")) + 1
    Return ""
}

/// ---------------------------------------------------------------- final step

/// Own transaction under the head lock: re-check liveness, drop the target's statement
/// entries, write the redaction revision and remove the marker.
ClassMethod Final(pName As %String, pMatch As %String, pDigest As %String, pActor As %String, pSchema As %String, Output pRedaction As %String, Output pStmts As %Integer, ByRef pCounts, Output pMs As %Integer) As %String
{
    Set pRedaction = "", pStmts = 0, pMs = 0
    Set tStart = $ZHorolog, tLevel = $TLevel, tResult = ""
    Try {
        TSTART
        Lock +^IVG.Ledger("head"):##class(Graph.KG.Ledger).#LOCKTIMEOUT
        If '$Test {
            TROLLBACK 1
            Set tResult = ##class(Graph.KG.Ledger).Err("lock_timeout", "could not acquire ledger head lock")
        } Else {
            Set tResult = ..FinalInLock(pName, pMatch, pDigest, pActor, pSchema, .pRedaction, .pStmts, .pCounts)
            If tResult = "" { TCOMMIT } Else { TROLLBACK 1 }
            Lock -^IVG.Ledger("head")
        }
    } Catch ex {
        If $TLevel > tLevel { TROLLBACK 1 }
        Lock -^IVG.Ledger("head")
        Set tResult = ##class(Graph.KG.Ledger).Err("inconsistent", ex.DisplayString())
    }
    If tResult '= "" Set pRedaction = "", pStmts = 0
    Set pMs = (($ZHorolog - tStart) * 1000) \ 1
    Return tResult
}

ClassMethod FinalInLock(pName As %String, pMatch As %String, pDigest As %String, pActor As %String, pSchema As %String, ByRef pRedaction As %String, ByRef pStmts As %Integer, ByRef pCounts) As %String
{
    If ##class(Graph.KG.Ledger).State() '= "enabled" Return ##class(Graph.KG.Ledger).Err("disabled", "ledger is disabled; re-enable it and run the redaction again to finish it")
    // Under the lock this time: nothing can have made the target live since the check.
    Set tLive = ..LiveRows(pName, pMatch, pSchema, .tWhat)
    If tLive < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "liveness check failed: " _ tWhat)
    If tLive > 0 Return ##class(Graph.KG.Ledger).Err("target_live", ..TargetText(pName, pMatch) _ " became live again (" _ tWhat _ "); erase it and run the redaction again")
    Set pStmts = ..DropStatements(pName, pMatch)
    Do ..ReadCounts(.pCounts)
    If '$Data(^IVG.Ledger("redacting")) && (pStmts = 0) Return ""

    // Decision 1: a content-free revision naming the redacted revisions by seq.
    Kill tRecords
    Set tRecords = 0, tRevs = 0, tSeq = ""
    For {
        Set tSeq = $Order(^IVG.Ledger("redacting", "revs", tSeq), 1, tN)
        Quit:tSeq=""
        Set tRevs = tRevs + 1
        Do ##class(Graph.KG.LedgerApply).Rec(.tRecords, "redact", "revision", tSeq, "", "N", "S" _ tN, "", "redaction")
    }
    Set tSource = {}
    Do tSource.%Set("records", +pCounts("records"), "number")
    Do tSource.%Set("revisions", tRevs, "number")
    Do tSource.%Set("statements", pStmts, "number")
    Do tSource.%Set("target_sha256", pDigest)
    Set tMessage = "redaction: " _ (+pCounts("records")) _ " record(s) in " _ tRevs _ " revision(s)"
    Set tHead = +$Get(^IVG.Ledger("head"), 0)
    Set tParent = $Get(^IVG.Ledger("idBySeq", tHead), "")
    Set tErr = ##class(Graph.KG.Ledger).WriteRevision(tHead + 1, "redaction", pActor, "system", tMessage, tSource.%ToJSON(), "", "", tParent, .tRecords, "", "")
    If tErr '= "" Return tErr
    Kill ^IVG.Ledger("redacting")
    Set pRedaction = tHead + 1
    Return ""
}

/// Remove the target's ("stmt") entries, live or closed, and the tuple entries that
/// still point at them. Collect first, kill after (Eraser.EraseLedgerFor's reason).
ClassMethod DropStatements(pName As %String, pMatch As %String) As %Integer
{
    Kill tDoomed
    Set tId = ""
    For {
        Set tId = $Order(^IVG.Ledger("stmt", tId), 1, tRow)
        Quit:tId=""
        Continue:$Translate($ListGet(tRow, 4), $Char(0), "")'=pName
        If pMatch '= "" {
            Continue:'(..HasPrefix($ListGet(tRow, 1), pMatch)||..HasPrefix($ListGet(tRow, 3), pMatch))
        }
        Set tDoomed(tId) = tRow
    }
    Set tCount = 0, tId = ""
    For {
        Set tId = $Order(tDoomed(tId), 1, tRow)
        Quit:tId=""
        Set tKey = ##class(Graph.KG.GraphKey).ForLedger($ListGet(tRow, 4))
        If $Get(^IVG.Ledger("tuple", $ListGet(tRow, 1), $ListGet(tRow, 2), $ListGet(tRow, 3), tKey)) = tId {
            Kill ^IVG.Ledger("tuple", $ListGet(tRow, 1), $ListGet(tRow, 2), $ListGet(tRow, 3), tKey)
        }
        Kill ^IVG.Ledger("stmt", tId)
        Set tCount = tCount + 1
    }
    Return tCount
}

ClassMethod ReadCounts(ByRef pCounts)
{
    Set pCounts("records") = +$Get(^IVG.Ledger("redacting", "n", "records"))
    Set pCounts("cleared") = +$Get(^IVG.Ledger("redacting", "n", "cleared"))
    Set pCounts("idem") = +$Get(^IVG.Ledger("redacting", "n", "idem"))
    Kill pCounts("rev")
    Merge pCounts("rev") = ^IVG.Ledger("redacting", "revs")
}

ClassMethod Receipt(pComplete As %Boolean, pDigest As %String, ByRef pCounts, pStmts As %Integer, pUnknown As %Integer, pBatches As %Integer, pMaxMs As %Integer, pRedaction As %String) As %String
{
    Set tObj = {}
    Do tObj.%Set("ok", 1, "boolean")
    Do tObj.%Set("complete", +pComplete, "boolean")
    Do tObj.%Set("target_sha256", pDigest)
    Set tSeqs = [], tSeq = ""
    For {
        Set tSeq = $Order(pCounts("rev", tSeq))
        Quit:tSeq=""
        Do tSeqs.%Push(+tSeq, "number")
    }
    Do tObj.%Set("revision_seqs", tSeqs)
    Do tObj.%Set("records_tombstoned", +$Get(pCounts("records")), "number")
    Do tObj.%Set("metadata_cleared", +$Get(pCounts("cleared")), "number")
    Do tObj.%Set("idempotency_keys_removed", +$Get(pCounts("idem")), "number")
    Do tObj.%Set("statements_removed", +pStmts, "number")
    Do tObj.%Set("unattributed_records", +pUnknown, "number")
    Do tObj.%Set("batches", +pBatches, "number")
    Do tObj.%Set("max_batch_ms", +pMaxMs, "number")
    Set tRev = ""
    If pRedaction '= "" Set tRev = ##class(Graph.KG.Ledger).RevisionObj(pRedaction)
    If $IsObject(tRev) { Do tObj.%Set("redaction", tRev) } Else { Do tObj.%Set("redaction", "", "null") }
    Set tOut = []
    Do tOut.%Push("ledger exports (export_reconstruction files) written before this redaction still hold the content")
    Do tOut.%Push("IRIS journal files written before this redaction still hold the content")
    Do tOut.%Push("backups taken before this redaction still hold the content")
    Do tObj.%Set("out_of_reach", tOut)
    Return tObj.%ToJSON()
}

}
