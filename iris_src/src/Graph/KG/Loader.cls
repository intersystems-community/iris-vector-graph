/// Fast CSV loader for Graph data.
///
/// Writes the default graph's adjacency in the layout of record (ADR-0001): each row
/// goes through Graph.KG.Adjacency.Put under graph key 0. It used to write the legacy
/// no-shard layout ^KG("out", s, p, o), which no reader walked (spec 245).
///
/// This loader writes adjacency only. It inserts no Graph_KG rows, so the SQL tables
/// do not know about the edges, and a later sync() from SQL would drop them. It has no
/// callers in the engine. Prefer engine.bulk_load_session() / bulk_create_edges().
Class Graph.KG.Loader Extends %RegisteredObject
{

ClassMethod Load(path As %String) As %Status
{
    Set file = ##class(%Stream.FileCharacter).%New()
    Set sc = file.LinkToFile(path)
    If $$$ISERR(sc) Return sc
    
    // The version counter outlives the data: Kill ^KG would reset it to a value readers
    // have already seen as another graph's (spec 236, audit item 5).
    Set tVer = $Get(^KG("__version"), 0)
    Kill ^KG
    Set ^KG("__version") = tVer + 1
    Do ##class(Graph.KG.GraphIndex).DropNKG()
    Set count = 0
    Set start = $ZTimestamp
    
    // Skip header
    Do file.ReadLine()
    
    While 'file.AtEnd {
        Set line = file.ReadLine()
        If line = "" Continue
        
        Set s = $Piece(line, ",", 1)
        Set p = $Piece(line, ",", 2)
        Set o = $Piece(line, ",", 3)
        
        If (s = "") || (p = "") || (o = "") Continue
        
        // Graph.KG.Adjacency writes the mirror and its counters (spec 245).
        Do ##class(Graph.KG.Adjacency).Put(0, s, p, o, 1.0)

        Set count = count + 1
        If count # 50000 = 0 Write "Loaded ", count, " edges...", !
    }
    
    Set end = $ZTimestamp
    Set duration = $Piece(end, ",", 2) - $Piece(start, ",", 2)
    If duration < 0 Set duration = duration + 86400
    
    Set tmp = $Increment(^KG("__version"))
    Write "Finished loading ", count, " edges in ", duration, "s", !
    Return $$$OK
}

}
