/// Graph.KG.GraphMode - which graphs are multigraphs (spec 234).
///
/// A multigraph may hold several edges with the same (s, p, o), told apart by
/// Graph_KG.rdf_edges.ekey. Every other graph keeps one edge per triple and
/// behaves exactly as it did before spec 234.
///
///   ^IVG.GraphMode(gKey) = "multi"     gKey from GraphKey.ForIndex (default 0)
///
/// Absent means off. Every method takes the graph NAME and derives the key
/// itself, so no caller spells the default graph on its own (ADR-0003).
/// Graph.KG.Eraser kills the node with the graph.
Class Graph.KG.GraphMode Extends %RegisteredObject
{

/// 1 when pGraph is in multigraph mode, else 0.
ClassMethod IsMulti(pGraph As %String = "") As %Integer
{
	Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	Quit ($Get(^IVG.GraphMode(tKey)) = "multi")
}

/// Turn multigraph mode on or off for pGraph.
///
/// Turning it off is refused while any triple of the graph still has two edges:
/// the graph would then hold rows its own mode forbids.
ClassMethod Set(pGraph As %String = "", pOn As %Boolean = 1) As %Status
{
	Set tSC = $$$OK
	Try {
		Set tKey = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
		If pOn {
			Set ^IVG.GraphMode(tKey) = "multi"
			Quit
		}
		Set tCount = ..ParallelCount(pGraph)
		If tCount > 0 {
			Set tSC = $$$ERROR($$$GeneralError, "parallel_edges_present: "_tCount)
			Quit
		}
		Kill ^IVG.GraphMode(tKey)
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

/// The number of (s, p, o) triples of pGraph that carry more than one edge.
ClassMethod ParallelCount(pGraph As %String = "") As %Integer
{
	Set tName = ##class(Graph.KG.GraphKey).ForName(pGraph)
	Set tSQL = "SELECT COUNT(*) FROM (SELECT s FROM Graph_KG.rdf_edges"
	Set tSQL = tSQL_" WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
	Set tSQL = tSQL_" GROUP BY s, p, o_id HAVING COUNT(*) > 1)"
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, tSQL, tName)
	If tRS.%SQLCODE < 0 {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "ParallelCount SQLCODE "_tRS.%SQLCODE_": "_tRS.%Message))
	}
	Set tCount = 0
	If tRS.%Next() { Set tCount = +tRS.%GetData(1) }
	Quit tCount
}

/// Set(), in the shape the Python engine reads: "ok", or
/// "parallel_edges_present:N" when turning the mode off was refused, or
/// "error:<text>" for anything else.
ClassMethod Apply(pGraph As %String = "", pOn As %Boolean = 1) As %String
{
	Set tSC = ..Set(pGraph, pOn)
	If $$$ISOK(tSC) { Quit "ok" }
	Set tText = $System.Status.GetErrorText(tSC)
	If tText [ "parallel_edges_present: " {
		Quit "parallel_edges_present:"_+$Piece(tText, "parallel_edges_present: ", 2)
	}
	Quit "error:"_tText
}

}
