Class Graph.KG.EmbedQueue Extends %RegisteredObject
{

/// Global layout: ^EmbedQueue(node_id) = $LB(embedding_config, status, queued_at)
/// status: "pending" | "done" | "error"

ClassMethod Enqueue(nodeId As %String, embeddingConfig As %String = "") As %Status
{
    Set status = "pending"
    Set queuedAt = $ZDateTime($ZDateTimeH($ZDateTime(,3),3),3)
    Set ^EmbedQueue(nodeId) = $LB(embeddingConfig, status, queuedAt)
    Quit $$$OK
}

ClassMethod BulkEnqueue(nodeIds As %DynamicArray, embeddingConfig As %String = "") As %Integer
{
    Set count = 0
    Set iter = nodeIds.%GetIterator()
    While iter.%GetNext(.idx, .nodeId) {
        Set sc = ..Enqueue(nodeId, embeddingConfig)
        If $$$ISOK(sc) { Set count = count + 1 }
    }
    Quit count
}

ClassMethod ProcessBatch(batchSize As %Integer = 100, timeout As %Integer = 30) As %String
{
    Set processed = 0
    Set errors = 0
    Set nodeId = ""

    For {
        Set nodeId = $Order(^EmbedQueue(nodeId))
        Quit:nodeId=""
        Quit:processed>=batchSize

        Set entry = ^EmbedQueue(nodeId)
        Set embConfig = $List(entry, 1)
        Set status = $List(entry, 2)

        Continue:status'="pending"

        Try {
            Set sql = "SELECT EMBEDDING(?, ?) FROM (SELECT 1)"
            Set stmt = ##class(%SQL.Statement).%New()
            Set sc = stmt.%Prepare(sql)
            If $$$ISOK(sc) {
                Set displayName = ##class(Graph.KG.Traversal).GetNodeDisplayText(nodeId)
                If displayName = "" { Set displayName = nodeId }
                Set rs = stmt.%Execute(displayName, embConfig)
                If rs.%SQLCODE = 0 && rs.%Next() {
                    Set embVal = rs.%GetData(1)
                    If embVal '= "" {
                        Set insertSql = "INSERT OR REPLACE INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE))"
                        Set stmt2 = ##class(%SQL.Statement).%New()
                        Do stmt2.%Prepare(insertSql)
                        Do stmt2.%Execute(nodeId, embVal)
                    }
                }
            }
            Set $List(^EmbedQueue(nodeId), 2) = "done"
            Set processed = processed + 1
        } Catch ex {
            Set $List(^EmbedQueue(nodeId), 2) = "error"
            Set errors = errors + 1
        }
    }

    Set result = {"processed": (processed), "errors": (errors)}
    Quit result.%ToJSON()
}

ClassMethod PendingCount() As %Integer
{
    Set count = 0
    Set nodeId = ""
    For {
        Set nodeId = $Order(^EmbedQueue(nodeId))
        Quit:nodeId=""
        If $List(^EmbedQueue(nodeId), 2) = "pending" { Set count = count + 1 }
    }
    Quit count
}

ClassMethod ClearDone() As %Integer
{
    Set cleared = 0
    Set nodeId = ""
    For {
        Set nodeId = $Order(^EmbedQueue(nodeId))
        Quit:nodeId=""
        If $List(^EmbedQueue(nodeId), 2) = "done" {
            Kill ^EmbedQueue(nodeId)
            Set cleared = cleared + 1
        }
    }
    Quit cleared
}

/// Start a background IRIS Task to process the queue
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
