/// Graph.KG.JsonKeys — SQL UDF for Cypher keys() function (User.funcJSONKEYS until 4.6.0, spec 262)
/// SQL projection name: <IVG Schema>.JSON_KEYS, from its forwarder in Graph.KG.SqlProcs (4.6.0 R084)
Class Graph.KG.JsonKeys Extends %RegisteredObject
{

/// Return keys of a JSON object as a JSON array string.
ClassMethod JSONKEYS(json As %String = "") As %String
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
