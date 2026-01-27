/// Fast CSV loader for Graph data
Class Graph.KG.Loader Extends %RegisteredObject
{

ClassMethod Load(path As %String) As %Status
{
    Set file = ##class(%Stream.FileCharacter).%New()
    Set sc = file.LinkToFile(path)
    If $$$ISERR(sc) Return sc
    
    Kill ^KG
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
        
        Set ^KG("out", s, p, o) = 1.0
        Set ^KG("in", o, p, s) = 1.0
        Set tmp = $Increment(^KG("deg", s))
        Set tmp = $Increment(^KG("degp", s, p))
        
        Set count = count + 1
        If count # 50000 = 0 Write "Loaded ", count, " edges...", !
    }
    
    Set end = $ZTimestamp
    Set duration = $Piece(end, ",", 2) - $Piece(start, ",", 2)
    If duration < 0 Set duration = duration + 86400
    
    Write "Finished loading ", count, " edges in ", duration, "s", !
    Return $$$OK
}

}
