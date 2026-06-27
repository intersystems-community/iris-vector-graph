Class Graph.KG.EmbedQueue Extends %RegisteredObject
{

/// Enhanced embedding queue (spec 199). Entries are subscripted:
///   ^EmbedQueue(reqId, "status")    = "PENDING" | "DONE" | "ERROR"
///   ^EmbedQueue(reqId, "text")      = text to embed
///   ^EmbedQueue(reqId, "node_id")   = node id (node-keyed entries) or ""
///   ^EmbedQueue(reqId, "result")    = "<f1>,<f2>,...,<fN>" comma-joined vector (on DONE)
///   ^EmbedQueue(reqId, "error")     = message (on ERROR)
///   ^EmbedQueue(reqId, "queued_at") = $ZDateTime timestamp
///
/// reqId = node_id (node-keyed enqueue; re-enqueue overwrites — one entry per node)
///       or "txt:<n>" (free-text enqueue; always-new, never deduplicated).
///
/// Single-processor: ClaimPendingBatch is a plain ordered scan (no atomic claim state).
/// Statuses are written UPPERCASE; reads tolerate legacy lowercase pending/done/error.
/// The actual batched embedding is performed Python-side (process_embed_queue), which
/// claims a batch, encodes all texts in one model call, and writes results via SetResult.

/// Normalize a stored status value to UPPERCASE canonical form (back-compat).
ClassMethod NormStatus(s As %String) As %String [ Private ]
{
    Quit $ZConvert(s, "U")
}

/// Node-keyed enqueue (one entry per node id; overwrite). The text to embed defaults
/// to the node id; pass an explicit ``text`` to embed something else for the node.
ClassMethod Enqueue(nodeId As %String, embeddingConfig As %String = "", text As %String = "") As %Status
{
    If text = "" { Set text = nodeId }
    Set ^EmbedQueue(nodeId, "status") = "PENDING"
    Set ^EmbedQueue(nodeId, "text") = text
    Set ^EmbedQueue(nodeId, "node_id") = nodeId
    Set ^EmbedQueue(nodeId, "result") = ""
    Set ^EmbedQueue(nodeId, "error") = ""
    Set ^EmbedQueue(nodeId, "config") = embeddingConfig
    Set ^EmbedQueue(nodeId, "queued_at") = $ZDateTime($Horolog, 3)
    Quit $$$OK
}

/// nodeIds may be a JSON array string (as sent by the Python client) or a
/// %DynamicArray. Returns count enqueued.
ClassMethod BulkEnqueue(nodeIds As %String, embeddingConfig As %String = "") As %Integer
{
    Set arr = nodeIds
    If '$IsObject(arr) { Set arr = ##class(%DynamicArray).%FromJSON(nodeIds) }
    Set count = 0
    Set iter = arr.%GetIterator()
    While iter.%GetNext(.idx, .nodeId) {
        Set sc = ..Enqueue(nodeId, embeddingConfig)
        If $$$ISOK(sc) { Set count = count + 1 }
    }
    Quit count
}

/// Free-text enqueue (always-new entry; reqId = "txt:<seq>"). texts may be a JSON
/// array string (Python client) or a %DynamicArray. Returns count enqueued.
ClassMethod BulkEnqueueText(texts As %String, embeddingConfig As %String = "") As %Integer
{
    Set arr = texts
    If '$IsObject(arr) { Set arr = ##class(%DynamicArray).%FromJSON(texts) }
    Set count = 0
    Set iter = arr.%GetIterator()
    While iter.%GetNext(.idx, .text) {
        Continue:text=""
        Set seq = $Increment(^EmbedQueue("$seq"))
        Set reqId = "txt:" _ seq
        Set ^EmbedQueue(reqId, "status") = "PENDING"
        Set ^EmbedQueue(reqId, "text") = text
        Set ^EmbedQueue(reqId, "node_id") = ""
        Set ^EmbedQueue(reqId, "result") = ""
        Set ^EmbedQueue(reqId, "error") = ""
        Set ^EmbedQueue(reqId, "config") = embeddingConfig
        Set ^EmbedQueue(reqId, "queued_at") = $ZDateTime($Horolog, 3)
        Set count = count + 1
    }
    Quit count
}

/// Return up to batchSize PENDING entries as JSON [{reqId,text,node_id}, ...].
/// Plain ordered scan (single-processor; no claim state).
ClassMethod ClaimPendingBatch(batchSize As %Integer = 100) As %String
{
    Set arr = []
    Set reqId = ""
    Set n = 0
    For {
        Set reqId = $Order(^EmbedQueue(reqId))
        Quit:reqId=""
        Continue:reqId="$seq"
        Set st = ..NormStatus($Get(^EmbedQueue(reqId, "status")))
        Continue:st'="PENDING"
        Set obj = {}
        Set obj.reqId = reqId
        Set obj.text = $Get(^EmbedQueue(reqId, "text"))
        Set obj."node_id" = $Get(^EmbedQueue(reqId, "node_id"))
        Do arr.%Push(obj)
        Set n = n + 1
        Quit:n>=batchSize
    }
    Quit arr.%ToJSON()
}

/// Set an entry's result. status="DONE" → payload is the result vector string;
/// status="ERROR" → payload is the error message.
ClassMethod SetResult(reqId As %String, status As %String, payload As %String = "") As %Status
{
    If '$Data(^EmbedQueue(reqId, "status")) { Quit $$$ERROR($$$GeneralError, "unknown reqId") }
    Set up = $ZConvert(status, "U")
    Set ^EmbedQueue(reqId, "status") = up
    If up = "DONE" {
        Set ^EmbedQueue(reqId, "result") = payload
        Set ^EmbedQueue(reqId, "error") = ""
    } ElseIf up = "ERROR" {
        Set ^EmbedQueue(reqId, "error") = payload
    }
    Quit $$$OK
}

ClassMethod PendingCount() As %Integer
{
    Set count = 0
    Set reqId = ""
    For {
        Set reqId = $Order(^EmbedQueue(reqId))
        Quit:reqId=""
        Continue:reqId="$seq"
        If ..NormStatus($Get(^EmbedQueue(reqId, "status"))) = "PENDING" { Set count = count + 1 }
    }
    Quit count
}

/// Remove DONE entries; leave PENDING and ERROR intact. Returns number cleared.
ClassMethod ClearDone() As %Integer
{
    Set cleared = 0
    Set reqId = ""
    For {
        Set reqId = $Order(^EmbedQueue(reqId))
        Quit:reqId=""
        Continue:reqId="$seq"
        If ..NormStatus($Get(^EmbedQueue(reqId, "status"))) = "DONE" {
            Kill ^EmbedQueue(reqId)
            Set cleared = cleared + 1
        }
    }
    Quit cleared
}

/// Legacy native-EMBEDDING() per-row processing path, retained for callers that do not
/// use the Python batched path. Reads the subscripted schema.
ClassMethod ProcessBatch(batchSize As %Integer = 100, timeout As %Integer = 30) As %String
{
    Set processed = 0
    Set errors = 0
    Set reqId = ""
    For {
        Set reqId = $Order(^EmbedQueue(reqId))
        Quit:reqId=""
        Continue:reqId="$seq"
        Quit:processed>=batchSize
        Continue:..NormStatus($Get(^EmbedQueue(reqId, "status")))'="PENDING"

        Set nodeId = $Get(^EmbedQueue(reqId, "node_id"))
        Set embConfig = $Get(^EmbedQueue(reqId, "config"))
        Set text = $Get(^EmbedQueue(reqId, "text"))
        Try {
            Set sql = "SELECT EMBEDDING(?, ?) FROM (SELECT 1)"
            Set stmt = ##class(%SQL.Statement).%New()
            Set sc = stmt.%Prepare(sql)
            If $$$ISOK(sc) {
                Set rs = stmt.%Execute(text, embConfig)
                If rs.%SQLCODE = 0 && rs.%Next() {
                    Set embVal = rs.%GetData(1)
                    If (embVal '= "") && (nodeId '= "") {
                        Set insertSql = "INSERT OR REPLACE INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE))"
                        Set stmt2 = ##class(%SQL.Statement).%New()
                        Do stmt2.%Prepare(insertSql)
                        Do stmt2.%Execute(nodeId, embVal)
                    }
                    If embVal '= "" { Set ^EmbedQueue(reqId, "result") = embVal }
                }
            }
            Set ^EmbedQueue(reqId, "status") = "DONE"
            Set processed = processed + 1
        } Catch ex {
            Set ^EmbedQueue(reqId, "status") = "ERROR"
            Set ^EmbedQueue(reqId, "error") = ex.DisplayString()
            Set errors = errors + 1
        }
    }
    Set result = {"processed": (processed), "errors": (errors)}
    Quit result.%ToJSON()
}

/// Start a background IRIS Task to process the queue.
ClassMethod StartBackgroundTask(batchSize As %Integer = 100) As %String
{
    Set task = ##class(%SYSTEM.Task).%New()
    Set task.Name = "IVG EmbedQueue Worker"
    Set task.NameSpace = $ZNamespace
    Set task.TaskClass = "Graph.KG.EmbedQueueTask"
    Set sc = task.%Save()
    If $$$ISERR(sc) { Quit "error: "_$System.Status.GetErrorText(sc) }
    Set taskId = task.%Id()
    Do ##class(%SYSTEM.Task).RunNow(taskId)
    Quit taskId
}

}
