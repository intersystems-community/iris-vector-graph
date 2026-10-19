/// The Adjacency Index ^KG("out"/"in") as a functional index on Graph_KG.rdf_edges (spec 252, ADR-0008):
///
///   CREATE INDEX idx_edges_adj ON Graph_KG.rdf_edges (graph_id, s, p, o_id, qualifiers, ekey) AS Graph.KG.AdjIndex
///
/// Every insert, update and delete of a row, from any writer (Cypher DML, raw SQL, the engine, a
/// rollback), changes its entry inside the row's transaction. The callbacks write no global: they
/// call Graph.KG.Adjacency, the stores' one writer (ADR-0011). %BuildIndices rebuilds the stores
/// from the rows (PurgeIndex, then InsertIndex for every row).
Class Graph.KG.AdjIndex Extends %Library.FunctionalIndex
{

ClassMethod InsertIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Quit ##class(Graph.KG.Adjacency).RowPut($Get(pArg(1)), $Get(pArg(2)), $Get(pArg(3)), $Get(pArg(4)), $Get(pArg(5)), $Get(pArg(6)))
}

/// New values in pArg(1..6), the old ones in pArg(7..12).
ClassMethod UpdateIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Quit ##class(Graph.KG.Adjacency).RowUpdate($Get(pArg(1)), $Get(pArg(2)), $Get(pArg(3)), $Get(pArg(4)), $Get(pArg(5)), $Get(pArg(6)), $Get(pArg(7)), $Get(pArg(8)), $Get(pArg(9)), $Get(pArg(10)), $Get(pArg(11)), $Get(pArg(12)))
}

ClassMethod DeleteIndex(pID As %String, pArg... As %Binary) As %Status [ ServerOnly = 1 ]
{
	Quit ##class(Graph.KG.Adjacency).RowRemove($Get(pArg(1)), $Get(pArg(2)), $Get(pArg(3)), $Get(pArg(4)), $Get(pArg(6)))
}

/// Only the stores this index derives (out, in and their degree cache), never the ^KG root.
ClassMethod PurgeIndex(pClassName As %String, pIndexName As %String) As %Status [ ServerOnly = 1 ]
{
	Quit ##class(Graph.KG.Adjacency).RemoveAll()
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
