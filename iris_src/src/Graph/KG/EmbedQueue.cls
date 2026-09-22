Class Graph.KG.EmbedQueue Extends %RegisteredObject
{

/// Enhanced embedding queue (spec 199). Entries are subscripted:
///   ^EmbedQueue(reqId, "status")    = "PENDING" | "DONE" | "ERROR"
///   ^EmbedQueue(reqId, "text")      = text to embed
///   ^EmbedQueue(reqId, "node_id")   = node id (node-keyed entries) or ""
///   ^EmbedQueue(reqId, "result")    = "<f1>,<f2>,...,<fN>" comma-joined vector (on DONE)
///   ^EmbedQueue(reqId, "error")     = message (on ERROR)
///   ^EmbedQueue(reqId, "config")    = embedding config the entry asked for ("" = the
///                                     worker's own model, whatever that is)
///   ^EmbedQueue(reqId, "graph")     = graph the vector belongs to ("" = the default
///                                     graph). Spec 227: the worker runs in another
///                                     process, so an entry that does not carry its
///                                     graph has lost it, and every vector lands in
///                                     the default graph.
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
ClassMethod Enqueue(nodeId As %String, embeddingConfig As %String = "", text As %String = "", graph As %String = "") As %Status
{
    If text = "" { Set text = nodeId }
    // reqId is the node id, so one node holds one pending request across every graph.
    // Spec 227 lets the same node id exist in two graphs, each wanting its own vector,
    // and an overwrite here would silently discard the other graph's request. Refused
    // instead, naming both graphs. BulkEnqueue counts only successes, so the caller
    // gets a smaller count rather than a wrong one. The reqId is deliberately NOT
    // graph-qualified: a graph name may contain any character a subscript may, so no
    // delimiter would be unambiguous, and an ambiguous reqId is worse than a refusal.
    If ..NormStatus($Get(^EmbedQueue(nodeId, "status"))) = "PENDING" {
        Set pending = $Get(^EmbedQueue(nodeId, "graph"))
        If pending '= graph {
            Quit $$$ERROR($$$GeneralError, "node "_nodeId_" already has a PENDING embed request for graph '"_pending_"'; refusing to overwrite it with one for graph '"_graph_"'")
        }
    }
    Set ^EmbedQueue(nodeId, "status") = "PENDING"
    Set ^EmbedQueue(nodeId, "text") = text
    Set ^EmbedQueue(nodeId, "node_id") = nodeId
    Set ^EmbedQueue(nodeId, "result") = ""
    Set ^EmbedQueue(nodeId, "error") = ""
    Set ^EmbedQueue(nodeId, "config") = embeddingConfig
    Set ^EmbedQueue(nodeId, "graph") = graph
    Set ^EmbedQueue(nodeId, "queued_at") = $ZDateTime($Horolog, 3)
    Quit $$$OK
}

/// nodeIds may be a JSON array string (as sent by the Python client) or a
/// %DynamicArray. Returns count enqueued.
ClassMethod BulkEnqueue(nodeIds As %String, embeddingConfig As %String = "", graph As %String = "") As %Integer
{
    Set arr = nodeIds
    If '$IsObject(arr) { Set arr = ##class(%DynamicArray).%FromJSON(nodeIds) }
    Set count = 0
    Set iter = arr.%GetIterator()
    While iter.%GetNext(.idx, .nodeId) {
        // One graph for the whole call. The count returned is successes only, so a
        // node already pending for another graph is refused by Enqueue and simply
        // does not appear in the count — the caller sees a shortfall, not a silent
        // overwrite of somebody else's request.
        Set sc = ..Enqueue(nodeId, embeddingConfig, "", graph)
        If $$$ISOK(sc) { Set count = count + 1 }
    }
    Quit count
}

/// Free-text enqueue (always-new entry; reqId = "txt:<seq>"). texts may be a JSON
/// array string (Python client) or a %DynamicArray. Returns count enqueued.
ClassMethod BulkEnqueueText(texts As %String, embeddingConfig As %String = "", graph As %String = "") As %Integer
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
        Set ^EmbedQueue(reqId, "graph") = graph
        Set ^EmbedQueue(reqId, "queued_at") = $ZDateTime($Horolog, 3)
        Set count = count + 1
    }
    Quit count
}

/// Return up to batchSize PENDING entries as JSON [{reqId,text,node_id,config}, ...].
/// Plain ordered scan (single-processor; no claim state).
///
/// ``config`` is what the entry was enqueued with (spec 226). Enqueue has always stored
/// it; without it on the wire a worker cannot tell which model an entry asked for, so the
/// queue would be the one write path that escapes the embedding identity registry.
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
        Set obj.config = $Get(^EmbedQueue(reqId, "config"))
        Set obj.graph = $Get(^EmbedQueue(reqId, "graph"))
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

/// One entry's own state as JSON {reqId,status,error,config,node_id}. A claim only ever
/// returns PENDING entries, so before this there was nothing that could read back why a
/// single entry failed — the queue's only Python-visible state was aggregate counts
/// (spec 226). Returns a record with an empty status when reqId is unknown.
ClassMethod GetEntry(reqId As %String) As %String
{
    Set obj = {}
    Set obj.reqId = reqId
    Set obj.status = ..NormStatus($Get(^EmbedQueue(reqId, "status")))
    Set obj.error = $Get(^EmbedQueue(reqId, "error"))
    Set obj.config = $Get(^EmbedQueue(reqId, "config"))
    Set obj.graph = $Get(^EmbedQueue(reqId, "graph"))
    Set obj."node_id" = $Get(^EmbedQueue(reqId, "node_id"))
    Quit obj.%ToJSON()
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

/// Is ``nodeId`` present in ``graph``? Returns 1 (present), 0 (absent) or "" (the
/// question could not be asked — the column or table is not there).
///
/// COALESCE on both sides: an empty host variable binds as SQL NULL in IRIS, so a bare
/// ``graph_id = ?`` for the default graph compiles to ``= NULL`` and matches nothing,
/// which would fail every default-graph entry.
ClassMethod NodeInGraph(nodeId As %String, graph As %String = "") As %String [ Private ]
{
    Set sql = "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, '')"
    Set stmt = ##class(%SQL.Statement).%New()
    Set sc = stmt.%Prepare(sql)
    If $$$ISERR(sc) { Quit "" }
    Set rs = stmt.%Execute(nodeId, graph)
    If rs.%SQLCODE '= 0 { Quit "" }
    If 'rs.%Next() { Quit "" }
    Quit $Select(rs.%GetData(1) > 0:1, 1:0)
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
        // Spec 227: an entry queued before the upgrade has no graph subscript, so it
        // reads as the default graph. Its node may well not be there — under
        // UNIQUE (graph_id, node_id) the same id can live in another graph entirely —
        // and writing the vector anyway would leave a row pointing at nothing.
        Set entryGraph = $Get(^EmbedQueue(reqId, "graph"))
        Set nodeOk = 1
        If nodeId '= "" { Set nodeOk = ..NodeInGraph(nodeId, entryGraph) }

        If nodeOk = 0 {
            // The graph is named. "node not found" would send the reader looking for a
            // missing node when the node is usually present, just in another graph.
            Set ^EmbedQueue(reqId, "status") = "ERROR"
            Set ^EmbedQueue(reqId, "error") = "node "_nodeId_" is not in graph '"_entryGraph_"'; the entry was queued for a graph this node does not belong to, so no vector was written"
            Set errors = errors + 1
        } ElseIf nodeOk = "" {
            Set ^EmbedQueue(reqId, "status") = "ERROR"
            Set ^EmbedQueue(reqId, "error") = "could not check whether node "_nodeId_" is in graph '"_entryGraph_"' (Graph_KG.nodes is missing its graph_id column?); refusing to write an unscoped vector"
            Set errors = errors + 1
        } Else {
            Try {
                Set sql = "SELECT EMBEDDING(?, ?) FROM (SELECT 1)"
                Set stmt = ##class(%SQL.Statement).%New()
                Set sc = stmt.%Prepare(sql)
                If $$$ISOK(sc) {
                    Set rs = stmt.%Execute(text, embConfig)
                    If rs.%SQLCODE = 0 && rs.%Next() {
                        Set embVal = rs.%GetData(1)
                        If (embVal '= "") && (nodeId '= "") {
                            Set insertSql = "INSERT OR REPLACE INTO Graph_KG.kg_NodeEmbeddings (graph_id, node_id, emb) VALUES (?, ?, TO_VECTOR(?, DOUBLE))"
                            Set stmt2 = ##class(%SQL.Statement).%New()
                            Do stmt2.%Prepare(insertSql)
                            Do stmt2.%Execute(entryGraph, nodeId, embVal)
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
