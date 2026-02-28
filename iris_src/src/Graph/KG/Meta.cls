/// Lightweight key/value store backed by a global for tracking bootstrap state.
/// Used by initialize_schema() to record one-time setup steps (e.g. BuildKG completion).
Class Graph.KG.Meta Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

/// Get a metadata value by key.
/// @param key  Metadata key (e.g. "kg_built")
/// @returns    Value string, or "" if not set
ClassMethod Get(key As %String) As %String
{
    Return $Get(^Graph.KG.Meta(key), "")
}

/// Set a metadata value.
/// @param key    Metadata key
/// @param value  Value to store
/// @returns $$$OK
ClassMethod Set(key As %String, value As %String) As %Status
{
    Set ^Graph.KG.Meta(key) = value
    Quit $$$OK
}

/// Delete a metadata key.
ClassMethod Delete(key As %String) As %Status
{
    Kill ^Graph.KG.Meta(key)
    Quit $$$OK
}

/// Check if a key exists and has a truthy value.
ClassMethod IsSet(key As %String) As %Boolean
{
    Set val = $Get(^Graph.KG.Meta(key), "")
    Return (val '= "") && (val '= "0")
}

/// Clear all metadata (for testing/reset).
ClassMethod PurgeAll() As %Status
{
    Kill ^Graph.KG.Meta
    Quit $$$OK
}

}
