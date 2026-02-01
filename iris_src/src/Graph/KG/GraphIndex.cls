Class Graph.KG.GraphIndex Extends %Library.FunctionalIndex
{

Parameter SUPPORTSSHARDING = 1;

/// Insert a relationship into the ^KG global
ClassMethod InsertIndex(pID As %String, s As %Binary, p As %Binary, o As %Binary, qualifiers As %Binary = "") As %Status [ ServerOnly = 1 ]
{
    Set weight = ##class(Graph.KG.GraphIndex).ExtractWeight(qualifiers)
    
    // Maintain adjacency lists with weight
    Set ^KG("out", s, p, o) = weight
    Set ^KG("in", o, p, s) = weight
    
    // Maintain degree statistics (atomic)
    Set tmp = $Increment(^KG("deg", s))
    Set tmp = $Increment(^KG("degp", s, p))
    Quit $$$OK
}

/// Update a relationship in the ^KG global
ClassMethod UpdateIndex(pID As %String, s As %Binary, p As %Binary, o As %Binary, qualifiers As %Binary = "", olds As %Binary, oldp As %Binary, oldo As %Binary, oldqualifiers As %Binary = "") As %Status [ ServerOnly = 1 ]
{
    // Delete old
    Do ##class(Graph.KG.GraphIndex).DeleteIndex(pID, olds, oldp, oldo, oldqualifiers)
    
    // Insert new
    Quit ##class(Graph.KG.GraphIndex).InsertIndex(pID, s, p, o, qualifiers)
}

/// Delete a relationship from the ^KG global
ClassMethod DeleteIndex(pID As %String, s As %Binary, p As %Binary, o As %Binary, qualifiers As %Binary = "") As %Status [ ServerOnly = 1 ]
{
    // Remove from adjacency lists
    Kill ^KG("out", s, p, o)
    Kill ^KG("in", o, p, s)
    
    // Decrement statistics
    Set tmp = $Increment(^KG("deg", s), -1)
    Set tmp = $Increment(^KG("degp", s, p), -1)
    
    // Clean up empty nodes
    If $Get(^KG("deg", s)) <= 0 Kill ^KG("deg", s)
    If $Get(^KG("degp", s, p)) <= 0 Kill ^KG("degp", s, p)
    Quit $$$OK
}

/// Extract weight from qualifiers JSON
ClassMethod ExtractWeight(qualifiers As %String) As %Double
{
    Set result = 1.0
    If qualifiers = "" Return result
    Try {
        Set obj = ##class(%DynamicObject).%FromJSON(qualifiers)
        Set weight = obj.weight
        If weight '= "" Set result = weight
    } Catch ex {
        // Ignore parse errors, use default
    }
    Return result
}

/// Purge the entire ^KG global
ClassMethod PurgeIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
    Kill ^KG
    Quit $$$OK
}

/// Called before bulk index build starts
ClassMethod SortBeginIndex() As %Status [ ServerOnly = 1 ]
{
    Quit $$$OK
}

/// Called after bulk index build completes
ClassMethod SortEndIndex(pCommit As %Integer = 1) As %Status [ ServerOnly = 1 ]
{
    Quit $$$OK
}

}
