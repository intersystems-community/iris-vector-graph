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

/// The HNSW indexes on the table pSchema.pTable, as a JSON array of {"name": SqlName, "properties": Properties};
/// "[]" when the table has none or does not exist. It reads the class dictionary as objects, so a user IRIS refuses
/// %Dictionary in SQL (ADMIN_GUIDE section 3's reading role, SQLCODE -99) gets the answer status() reports (4.6.0 R247).
ClassMethod HnswIndexes(pSchema As %String, pTable As %String) As %String
{
	Set tOut = ##class(%DynamicArray).%New()
	Set tExists = $SYSTEM.SQL.Schema.TableExists(pSchema_"."_pTable, .tMeta)
	Quit:'tExists tOut.%ToJSON()
	Set tClass = $ListGet(tMeta, 3)
	Quit:tClass="" tOut.%ToJSON()
	Set tDef = ##class(%Dictionary.CompiledClass).%OpenId(tClass)
	Quit:'$IsObject(tDef) tOut.%ToJSON()
	For i = 1:1:tDef.Indices.Count() {
		Set tIndex = tDef.Indices.GetAt(i)
		Continue:'$IsObject(tIndex)
		Set tType = $ZConvert(tIndex.TypeClass, "U")
		Continue:tType'["HNSW"
		Set tRow = ##class(%DynamicObject).%New()
		Do tRow.%Set("name", tIndex.SqlName)
		Do tRow.%Set("properties", tIndex.Properties)
		Do tOut.%Push(tRow)
	}
	Quit tOut.%ToJSON()
}

/// Clear all metadata (for testing/reset).
ClassMethod PurgeAll() As %Status
{
    Kill ^Graph.KG.Meta
    Quit $$$OK
}

ClassMethod GetNKG(s1 As %String = "", s2 As %String = "", s3 As %String = "", s4 As %String = "") As %String
{
	If s4 '= "" { Return $Get(^NKG(s1, s2, s3, s4), "") }
	If s3 '= "" { Return $Get(^NKG(s1, s2, s3), "") }
	If s2 '= "" { Return $Get(^NKG(s1, s2), "") }
	If s1 '= "" { Return $Get(^NKG(s1), "") }
	Return ""
}

ClassMethod GetKG(s1 As %String = "", s2 As %String = "", s3 As %String = "", s4 As %String = "", s5 As %String = "", s6 As %String = "") As %String
{
	If s6 '= "" { Return $Get(^KG(s1, s2, s3, s4, s5, s6), "") }
	If s5 '= "" { Return $Get(^KG(s1, s2, s3, s4, s5), "") }
	If s4 '= "" { Return $Get(^KG(s1, s2, s3, s4), "") }
	If s3 '= "" { Return $Get(^KG(s1, s2, s3), "") }
	If s2 '= "" { Return $Get(^KG(s1, s2), "") }
	If s1 '= "" { Return $Get(^KG(s1), "") }
	Return ""
}

}
