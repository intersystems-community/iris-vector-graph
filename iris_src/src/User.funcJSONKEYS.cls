/// User.funcJSONKEYS — SQL UDF for Cypher keys() function
/// SQL projection name: SQLUser.JSON_KEYS
Class User.funcJSONKEYS Extends %RegisteredObject
{

/// Return keys of a JSON object as a JSON array string.
ClassMethod JSONKEYS(json As %String = "") As %String [ SqlName = JSON_KEYS, SqlProc ]
{
    If (json = "") || (json = "null") { Return "[]" }
    Try {
        Set obj = ##class(%DynamicObject).%FromJSON(json)
        If obj = "" { Return "[]" }
        Set keys = ##class(%DynamicArray).%New()
        Set iter = obj.%GetIterator()
        While iter.%GetNext(.key, .val) { Do keys.%Push(key) }
        Return keys.%ToJSON()
    } Catch { Return "[]" }
}

}
