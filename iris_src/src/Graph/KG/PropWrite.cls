/// A node property's new value written in place when it has the stored value's text and another type (4.7.0 row 35).
///
/// A node property is rdf_props.val, VARCHAR; a number and a string differ only in how the value is held in the row
/// ($ListBuild type byte: "30" the string, 30 the integer), which CY_JSON_NUM and CY_TYPED read. IRIS's filer writes
/// no value whose text equals the stored one: UPDATE ... SET val = 30 over the string '30' answers one row and leaves
/// the string (measured on 2026.3 for a bound integer, a UDF's number, UPDATE %NOCHECK and %OpenId / %Save alike), so
/// `SET n.p = toInteger(n.p)` could not turn an imported '30' into 30.
///
/// Every Cypher write of a node property goes through SetVal (SQL: Graph_KG.Prop_Set(%ID, val, new), the SET of
/// each UPDATE of rdf_props.val). It answers the new value, which the UPDATE files as usual. Only when the new value
/// has the stored text and another type does it also write the value itself, into the row's val slot of the data
/// global, in the caller's transaction (TSTART levels nest, Graph.KG.Txn) under the row's lock. That write changes
/// no index value: each index of rdf_props reads val's text (val_key is its first 128 characters, the iFind index
/// its words), and the text is checked to be unchanged first. One write, so a reader never sees a marker or a
/// missing property, only the old value or the new.
Class Graph.KG.PropWrite Extends %RegisteredObject [ Final ]
{

/// The slot val is held in, in each row's $ListBuild: the fifth column of rdf_props's storage (graph_id, s, key,
/// val, val_key). Layout reads it and refuses any other layout.
Parameter VALSLOT = 4;

/// The columns of rdf_props's data node, in storage order, as `CREATE TABLE` in schema.py makes them.
Parameter COLUMNS = "graphid,s,key,val,valkey";

/// The table written; the deploy's source rewrite names the IVG Schema's own (IVGALT.rdf_props).
Parameter TABLE = "Graph_KG.rdf_props";

/// pNew, to be filed by the UPDATE whose SET this is; when pNew has pOld's text and another type, row pId of
/// rdf_props gets pNew written into its val slot first. The arguments are %RawString: a %String argument of a SQL
/// procedure is normalized, and the type of the value is what this method is for.
ClassMethod SetVal(pId As %RawString = "", pOld As %RawString = "", pNew As %RawString = "") As %RawString
{
	Set tTable = ..#TABLE
	// a null either side, another text, or the same value: the UPDATE's own write (or no write) is right
	Quit:(pOld="")||(pNew="") pNew
	Quit:pOld'=pNew pNew
	// the encoded lists compared as strings: $ListSame takes "30" and 30 for one value
	Quit:$ListBuild(pOld)=$ListBuild(pNew) pNew
	Do ##class(Graph.KG.SqlPrivilege).RequireWrite(tTable, "u")
	Set tLayout = ..Layout(tTable)
	Set tRef = $Name(@$ListGet(tLayout, 1)@(pId))
	Lock +@tRef:10
	If '$Test {
		Throw ##class(%Exception.General).%New("PropWriteLock", 5001, , "Prop_Set: row "_pId_" of "_tTable_" is locked by another process")
	}
	Try {
		Set tRow = $Get(@tRef)
		// the row as the layout says: the val slot holds pOld exactly (text and type); anything else is a layout
		// this method does not know, and nothing is written
		If ($ListLength(tRow) '= $ListGet(tLayout, 3)) || ($ListBuild($ListGet(tRow, $ListGet(tLayout, 2))) '= $ListBuild(pOld)) {
			Throw ##class(%Exception.General).%New("PropWriteLayout", 5001, , "Prop_Set: row "_pId_" of "_tTable_" in "_$ListGet(tLayout, 1)_" does not hold val in slot "_$ListGet(tLayout, 2)_" of "_$ListGet(tLayout, 3)_"; nothing written")
		}
		Set $List(tRow, $ListGet(tLayout, 2)) = pNew
		Set @tRef = tRow
	} Catch tEx {
		Lock -@tRef
		Throw tEx
	}
	Lock -@tRef
	Quit pNew
}

/// $ListBuild(data global, val slot, column count) of table pTable, from its class's compiled storage, kept for the
/// process (^||IVG.PropWrite). Throws when the storage is not one $ListBuild node per row holding COLUMNS in order.
ClassMethod Layout(pTable As %String) As %List
{
	Set tKey = $ZConvert(pTable, "U")
	Quit:$Data(^||IVG.PropWrite(tKey),tCached) tCached
	Set tSchema = $Piece(pTable, ".", 1), tName = $Piece(pTable, ".", 2)
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, "SELECT Name FROM %Dictionary.CompiledClass WHERE SqlSchemaName=? AND SqlTableName=?", tSchema, tName)
	Set tClass = $Select(tRS.%Next(): tRS.%Get("Name"), 1: "")
	If tClass = "" {
		Throw ##class(%Exception.General).%New("PropWriteLayout", 5001, , "Prop_Set: no class projects "_pTable)
	}
	Set tStorage = ##class(%Dictionary.CompiledStorage).%OpenId(tClass_"||Default")
	If '$IsObject(tStorage) || (tStorage.Type '= "%Storage.Persistent") || (tStorage.DataLocation = "") || (tStorage.Data.Count() '= 1) {
		Throw ##class(%Exception.General).%New("PropWriteLayout", 5001, , "Prop_Set: "_tClass_" is not stored as one data node per row")
	}
	Set tData = tStorage.Data.GetAt(1)
	If (tData.Structure '= "listnode") || (tData.Subscript '= "") {
		Throw ##class(%Exception.General).%New("PropWriteLayout", 5001, , "Prop_Set: "_tClass_"'s data node is "_tData.Structure_" at subscript '"_tData.Subscript_"', not a list node at the row id")
	}
	For tI = 1:1:tData.Values.Count() {
		Set tValue = tData.Values.GetAt(tI)
		Set tAt(+tValue.Name) = tValue.Value
	}
	Set tColumns = "", tI = ""
	For {
		Set tI = $Order(tAt(tI), 1, tCol)
		Quit:tI=""
		Set tColumns = tColumns_$Select(tColumns="": "", 1: ",")_tCol
	}
	If tColumns '= ..#COLUMNS {
		Throw ##class(%Exception.General).%New("PropWriteLayout", 5001, , "Prop_Set: "_tClass_" stores "_tColumns_", not "_..#COLUMNS)
	}
	Set tLayout = $ListBuild(tStorage.DataLocation, ..#VALSLOT, $Length(..#COLUMNS, ","))
	Set ^||IVG.PropWrite(tKey) = tLayout
	Quit tLayout
}

}
