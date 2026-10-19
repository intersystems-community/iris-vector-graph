/// ^KG("label") as a functional index on Graph_KG.rdf_labels (spec 252, ADR-0008):
///
///   CREATE INDEX idx_labels_adj ON Graph_KG.rdf_labels (graph_id, s, label) AS Graph.KG.LabelIndex
///
/// Every label row written or removed, by any writer, changes the store in the row's transaction,
/// through Graph.KG.Adjacency (ADR-0011).
Class Graph.KG.LabelIndex Extends %Library.FunctionalIndex
{

ClassMethod InsertIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Quit ##class(Graph.KG.Adjacency).PutLabel($Get(pArg(1)), $Get(pArg(2)), $Get(pArg(3)))
}

/// New values in pArg(1..3), the old ones in pArg(4..6).
ClassMethod UpdateIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Set tSC = ##class(Graph.KG.Adjacency).RemoveLabel($Get(pArg(4)), $Get(pArg(5)), $Get(pArg(6)))
	If $$$ISERR(tSC) {
		Quit tSC
	}
	Quit ##class(Graph.KG.Adjacency).PutLabel($Get(pArg(1)), $Get(pArg(2)), $Get(pArg(3)))
}

ClassMethod DeleteIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Quit ##class(Graph.KG.Adjacency).RemoveLabel($Get(pArg(1)), $Get(pArg(2)), $Get(pArg(3)))
}

ClassMethod PurgeIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
	Quit ##class(Graph.KG.Adjacency).RemoveAllLabels()
}

ClassMethod SortBeginIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
	Quit $$$OK
}

ClassMethod SortEndIndex(pCommit As %Integer = 1) As %Status [ ServerOnly = 1 ]
{
	Quit $$$OK
}

}
