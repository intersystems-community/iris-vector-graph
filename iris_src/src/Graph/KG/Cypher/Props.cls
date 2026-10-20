/// Property reads for the native Cypher executor (spec 253, FR-004).
///
/// A property is a Graph_KG.rdf_props row, found through the table's own unique index pkprops (graph_id, s, key, ID)
/// and read from its data global: one $Order and one $ListGet, where a prepared SQL statement per lookup costs about six
/// times as much (research.md section A). The globals are DDL-generated and hashed per table creation, so they are
/// resolved from %Dictionary once per process, keyed by the class's compile time, and checked: the index must be on
/// (graphid, s, key) and the data must hold `val` in a known slot. A layout that is not the expected one is read with
/// prepared SQL instead, never guessed at.
Class Graph.KG.Cypher.Props Extends %RegisteredObject
{

/// 1 when rdf_props' layout is the one this class reads directly; pData / pIndex / pSlot are then its data global,
/// pkprops index global and the $List slot of `val`.
ClassMethod Layout(Output pData As %String, Output pIndex As %String, Output pSlot As %Integer) As %Boolean
{
	Set tStamp = ..Stamp()
	If (tStamp = "") || ($Get(^||IVG.CypherProps("stamp")) '= tStamp) {
		Kill ^||IVG.CypherProps
		Set ^||IVG.CypherProps("stamp") = tStamp
		Set (^||IVG.CypherProps("data"), ^||IVG.CypherProps("index"), ^||IVG.CypherProps("slot")) = ""
		Do:tStamp'="" ..Resolve()
	}
	Set pData = ^||IVG.CypherProps("data"), pIndex = ^||IVG.CypherProps("index"), pSlot = ^||IVG.CypherProps("slot")
	Quit (pData '= "")
}

/// Reads rdf_props' layout from %Dictionary into ^||IVG.CypherProps, leaving it empty when the layout is not the
/// expected one.
ClassMethod Resolve() [ Private ]
{
	Set tData = ..Scalar("SELECT DataLocation FROM %Dictionary.CompiledStorage WHERE parent = 'Graph.KG.rdfprops'")
	Set tIndex = ..Scalar("SELECT Location FROM %Dictionary.CompiledStorageIndex WHERE parent %STARTSWITH 'Graph.KG.rdfprops||' AND Name = 'pkprops'")
	Set tProps = ..Scalar("SELECT Properties FROM %Dictionary.CompiledIndex WHERE parent = 'Graph.KG.rdfprops' AND Name = 'pkprops'")
	Set tSlot = ..Scalar("SELECT Name FROM %Dictionary.CompiledStorageDataValue WHERE parent %STARTSWITH 'Graph.KG.rdfprops||' AND Value = 'val'")
	If (tData = "") || (tIndex = "") || ($ZStrip(tProps, "*W") '= "graphid,s,key") || (tSlot '= +tSlot) {
		Quit
	}
	Set ^||IVG.CypherProps("data") = tData, ^||IVG.CypherProps("index") = tIndex, ^||IVG.CypherProps("slot") = +tSlot
}

/// The value of property pKey of node pS in graph pGraph ("" the default graph); pFound says whether the row exists.
ClassMethod Get(pGraph As %String, pS As %String, pKey As %String, Output pFound As %Boolean, pForceSQL As %Boolean = 0) As %String
{
	Set pFound = 0
	Set tGraph = $Select(pGraph = "": $Char(0), 1: pGraph)
	If 'pForceSQL && ..Layout(.tData, .tIndex, .tSlot) {
		Set tId = $Order(@tIndex@(tGraph, pS, pKey, ""))
		Quit:tId="" ""
		Set pFound = 1
		Quit $ListGet($Get(@tData@(tId)), tSlot)
	}
	If '$IsObject($Get(%ivgCypherPropsStmt)) {
		Set %ivgCypherPropsStmt = ##class(%SQL.Statement).%New()
		$$$ThrowOnError(%ivgCypherPropsStmt.%Prepare("SELECT val FROM Graph_KG.rdf_props WHERE COALESCE(graph_id, '') = COALESCE(?, '') AND s = ? AND ""key"" = ?"))
	}
	Set tRS = %ivgCypherPropsStmt.%Execute(tGraph, pS, pKey)
	Quit:'tRS.%Next() ""
	Set pFound = 1
	Quit tRS.%GetData(1)
}

/// Get as JSON {"found": bool, "val": value} (tests and callers over the Native API).
ClassMethod GetJson(pGraph As %String, pS As %String, pKey As %String, pForceSQL As %Boolean = 0) As %String
{
	Set tVal = ..Get(pGraph, pS, pKey, .tFound, pForceSQL)
	Set tOut = ##class(%Library.DynamicObject).%New()
	Do tOut.%Set("found", tFound, "boolean")
	If tFound {
		Do tOut.%Set("val", tVal)
	}
	Quit tOut.%ToJSON()
}

/// 1 when the direct reader is in use on this install.
ClassMethod LayoutOK() As %Integer
{
	Quit ..Layout()
}

/// The compile time of the rdf_props class, "" when it does not exist.
ClassMethod Stamp() As %String [ Private ]
{
	Quit ..Scalar("SELECT TimeChanged FROM %Dictionary.CompiledClass WHERE ID = 'Graph.KG.rdfprops'")
}

/// The first column of the first row of pSQL, "" on an SQL error or no row.
ClassMethod Scalar(pSQL As %String) As %String [ Private ]
{
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, pSQL)
	Quit:tRS.%SQLCODE<0 ""
	Quit:'tRS.%Next() ""
	Quit tRS.%GetData(1)
}

}
