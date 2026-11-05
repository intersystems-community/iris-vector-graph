/// The SQL privilege check of IVG's SQL-callable functions (4.6.0 R280). Reads no global and no table.
///
/// A function that walks a global (^KG, the interned adjacency, the BM25 and IVF indexes, the ledger) reads around
/// SQL privileges: IRIS checks only the database resource there. Each such function's SQL entry point calls
/// RequireSelect first, with the table whose rows the global stands for, so a caller holding EXECUTE but no SELECT is
/// refused as SQL would refuse it. A function that writes around them (embedded SQL, which IRIS does not check for
/// the caller) calls RequireWrite first, with the privileges the write needs on the tables it writes (4.6.0 R289).
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
		Continue:..Holds(tTable, tRoles, "s")
		Throw ##class(%Exception.SQL).CreateFromSQLCODE(-99, "User "_$Username_" lacks SELECT on "_tTable)
	}
	Quit tSC
}

/// Throw SQLCODE -99 naming the first privilege and table of pTables (comma-separated, each schema-qualified) the
/// caller does not hold; return $$$OK when it holds them all. pActions is the privileges each table needs, as letters:
/// "i" INSERT, "u" UPDATE, "d" DELETE. The SQL-callable routines that write around SQL privileges (embedded SQL, or a
/// global the tables stand for) call it before they write (4.6.0 R289): backfill_temporal_rows wrote node and
/// relationship rows for a caller holding SELECT only. The caller is $Username with the roles in $Roles; %All passes.
ClassMethod RequireWrite(pTables As %String, pActions As %String = "i") As %Status
{
	Set tSC = $$$OK
	Set tRoles = $ListFromString($Roles)
	Quit:$ListFind(tRoles, "%All") tSC
	For tI = 1:1:$Length(pTables, ",") {
		Set tTable = $ZStrip($Piece(pTables, ",", tI), "<>W")
		Continue:tTable=""
		For tJ = 1:1:$Length(pActions) {
			Set tAction = $ZConvert($Extract(pActions, tJ), "L")
			Set tName = $Case(tAction, "i": "INSERT", "u": "UPDATE", "d": "DELETE", : "")
			Throw:tName="" ##class(%Exception.SQL).CreateFromSQLCODE(-99, "RequireWrite: unknown privilege "_tAction)
			Continue:..Holds(tTable, tRoles, tAction)
			Throw ##class(%Exception.SQL).CreateFromSQLCODE(-99, "User "_$Username_" lacks "_tName_" on "_tTable)
		}
	}
	Quit tSC
}

/// Throw what a dynamic SQL read of pTable left on its result: %ExecDirect returns a refused or failed statement as a
/// result whose %Next() answers 0, its SQLCODE (pSQLCODE, < 0) and message (pMessage) on the result, so a caller that
/// reads on as if no row were left answers {} or [] (4.6.0 R279). A refusal (-99, whose message names neither table
/// nor privilege) is thrown as RequireSelect throws it, naming the table. $$$OK when the read failed in nothing.
ClassMethod RaiseIfRefused(pSQLCODE As %Integer, pMessage As %String, pTable As %String) As %Status
{
	Set tSC = $$$OK
	Quit:(+pSQLCODE '< 0) tSC
	Do:(+pSQLCODE = -99) ..RequireSelect(pTable)
	Throw ##class(%Exception.SQL).CreateFromSQLCODE(pSQLCODE, pMessage_" (reading "_pTable_")")
	Quit tSC
}

/// Whether $Username, or one of pRoles, holds privilege pAction ("s", "i", "u", "d") on pTable (on the table, or on
/// its schema).
ClassMethod Holds(pTable As %String, pRoles As %List, pAction As %String) As %Boolean [ Private ]
{
	Quit:$SYSTEM.SQL.Security.CheckPrivilege($Username, 1, pTable, pAction) 1
	Set tOK = 0
	For tI = 1:1:$ListLength(pRoles) {
		Set tRole = $ListGet(pRoles, tI)
		Continue:tRole=""
		If $SYSTEM.SQL.Security.CheckPrivilege(tRole, 1, pTable, pAction) {
			Set tOK = 1
			Quit
		}
	}
	Quit tOK
}

}
