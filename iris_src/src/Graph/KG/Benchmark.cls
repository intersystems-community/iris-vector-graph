/// Benchmark runner for BFS
Class Graph.KG.Benchmark Extends %RegisteredObject
{

ClassMethod Run(srcId As %String = "alice", maxHops As %Integer = 2)
{
    // 1. Standard BFS (Object Bottleneck)
    Set start = $Piece($ZTimestamp, ",", 2)
    Set results = ##class(Graph.KG.Traversal).BFS(srcId, "", maxHops)
    Set end = $Piece($ZTimestamp, ",", 2)
    Set duration = end - start
    If duration < 0 Set duration = duration + 86400
    Write "Standard BFS (Objects): ", duration, "s | Count: ", results.%Size(), !

    // 2. Fast BFS (PPG + ListBuild)
    Set start = $Piece($ZTimestamp, ",", 2)
    Set count = ##class(Graph.KG.Traversal).BFSFast(srcId, "", maxHops)
    Set end = $Piece($ZTimestamp, ",", 2)
    Set durationF = end - start
    If durationF < 0 Set durationF = durationF + 86400
    Write "Fast BFS (PPG):      ", durationF, "s | Count: ", count, !
    
    If durationF > 0 Write "Improvement: ", $Fnumber((duration - durationF) / duration * 100, "", 2), "%", !
    
    Return durationF
}

}
