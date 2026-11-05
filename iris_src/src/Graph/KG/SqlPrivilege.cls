/// The SQL privilege check of IVG's SQL-callable functions (4.6.0 R280). Reads no global and no table.
///
/// A function that walks a global (^KG, the interned adjacency, the BM25 and IVF indexes, the ledger) reads around
/// SQL privileges: IRIS checks only the database resource there. Each such function's SQL entry point calls
/// RequireSelect first, with the table whose rows the global stands for, so a caller holding EXECUTE but no SELECT is
/// refused as SQL would refuse it.
///
/// IVG's class methods called over the Native API (the native Cypher executor, Arno, the algorithms) do not check:
/// the database resource and %Native_ClassExecution are their boundary (ADMIN_GUIDE section 3).
Class Graph.KG.SqlPrivilege Extends %RegisteredObject
{

/// Throw SQLCODE -99 naming the first table of pTables (comma-separated, each schema-qualified) the caller may not
/// SELECT from; return $$$OK when it may read them all. The caller is $Username with the roles the process holds
/// ($Roles: a role an application adds counts, as it does for SQL); %All passes. One CheckPrivilege per table and
/// user or role, once per call of the function: about 30 us each for a user without %All (2026.3.0AI).
ClassMethod RequireSelect(pTables As %String) As %Status
{
	Set tSC = $$$OK
	Set tRoles = $ListFromString($Roles)
	Quit:$ListFind(tRoles, "%All") tSC
	For tI = 1:1:$Length(pTables, ",") {
		Set tTable = $ZStrip($Piece(pTables, ",", tI), "<>W")
		Continue:tTable=""
		Continue:..MaySelect(tTable, tRoles)
		Throw ##class(%Exception.SQL).CreateFromSQLCODE(-99, "User "_$Username_" lacks SELECT on "_tTable)
	}
	Quit tSC
}

/// Whether $Username, or one of pRoles, holds SELECT on pTable (on the table, or on its schema).
ClassMethod MaySelect(pTable As %String, pRoles As %List) As %Boolean [ Private ]
{
	Quit:$SYSTEM.SQL.Security.CheckPrivilege($Username, 1, pTable, "s") 1
	Set tOK = 0
	For tI = 1:1:$ListLength(pRoles) {
		Set tRole = $ListGet(pRoles, tI)
		Continue:tRole=""
		If $SYSTEM.SQL.Security.CheckPrivilege(tRole, 1, pTable, "s") {
			Set tOK = 1
			Quit
		}
	}
	Quit tOK
}

}
