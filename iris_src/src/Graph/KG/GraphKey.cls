/// Graph.KG.GraphKey - the single owner of both graph-key derivations.
///
/// A graph name reaches storage as a subscript, and the two stores that hold
/// graph content spell the default graph differently:
///
///   ForIndex()   ^KG / ^NKG                 default graph -> integer 0
///   ForLedger()  ^IVG.Ledger("tuple", ...)  default graph -> $Char(1)
///
/// Both were copied inline: fifteen times in Graph.KG.TemporalIndex, once in
/// Graph.KG.TemporalIndexMS, once as a two-line variant in
/// Graph.KG.TraversalBuild, and once in Graph.KG.Ledger:GKey. One copy had
/// already drifted — GKey never called $ZStrip, so the same graph name keyed one
/// ledger subscript when it arrived from Python and another when it arrived from
/// IRIS SQL carrying $Char(0) padding.
///
/// No caller may re-derive either form. See ADR-0003.
Class Graph.KG.GraphKey Extends %RegisteredObject
{

/// The ledger's sentinel for the default graph, as a character code.
Parameter LEDGERDEFAULT = 1;

/// Reject the graph names that cannot be represented as a subscript.
///
/// "0" collides with the index's default graph: IRIS canonicalizes the subscript
/// "0" to the integer 0, so ^KG("out", "0", ...) and ^KG("out", 0, ...) are the
/// same node (ADR-0001).
///
/// Any other control character is rejected. $Char(1) is the case that matters —
/// it is the ledger's own sentinel for the default graph — but a control
/// character anywhere in a name makes two spellings of that name compare unequal
/// as subscripts, so none of them are allowed.
///
/// $Char(0) is the exception, and is stripped rather than rejected: IRIS SQL
/// returns $Char(0) for an empty-string VARCHAR, so NUL padding is a transport
/// artifact of reading graph_id out of Graph_KG.rdf_edges, not a name a caller
/// chose.
ClassMethod Validate(pGraph As %String = "") As %Status
{
	Set tSC = $$$OK
	Set tGraph = ..Canonical(pGraph)
	If tGraph = "0" {
		Set tSC = $$$ERROR($$$GeneralError, "graph name ""0"" is reserved: IRIS canonicalizes the subscript ""0"" to the integer 0 that keys the default graph")
	} ElseIf tGraph '= $ZStrip(tGraph, "*C") {
		Set tSC = $$$ERROR($$$GeneralError, "graph name contains a control character, which cannot be used as a subscript")
	}
	Quit tSC
}

/// Derive the ^KG / ^NKG subscript for a graph. Default graph is integer 0.
ClassMethod ForIndex(pGraph As %String = "") As %String
{
	$$$ThrowOnError(..Validate(pGraph))
	Set tGraph = ..Canonical(pGraph)
	Return $Select(tGraph = "": 0, 1: tGraph)
}

/// The validated graph name, as it is spelled in a SQL graph_id column.
///
/// The third derivation, and the reason it belongs here rather than at each call
/// site: the SQL stores spell the default graph '' and neither sentinel applies,
/// but the same validation and the same NUL stripping do. Graph.KG.Eraser needs
/// both a subscript and a column value for one erase, and deriving one of them
/// itself is how the two would come to disagree about which graph they mean.
ClassMethod ForName(pGraph As %String = "") As %String
{
	$$$ThrowOnError(..Validate(pGraph))
	Return ..Canonical(pGraph)
}

/// Derive the ^IVG.Ledger subscript for a graph. Default graph is $Char(1).
ClassMethod ForLedger(pGraph As %String = "") As %String
{
	$$$ThrowOnError(..Validate(pGraph))
	Set tGraph = ..Canonical(pGraph)
	Return $Select(tGraph = "": $Char(..#LEDGERDEFAULT), 1: tGraph)
}

/// Remove the NUL padding IRIS SQL adds to an empty VARCHAR. Every method here
/// canonicalizes through this, so Validate() and the two derivations cannot
/// disagree about which string they are judging.
ClassMethod Canonical(pGraph As %String = "") As %String [ Private ]
{
	Return $Translate(pGraph, $Char(0), "")
}

}
