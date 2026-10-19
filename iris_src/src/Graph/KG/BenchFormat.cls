/// Benchmark: Compare adjacency walk strategies for ^KG.
Class Graph.KG.BenchFormat Extends %RegisteredObject
{

ClassMethod BuildAdjacencyTSV() As %String
{
	Set result = ""
	Set src = ""
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", 0, src, pred, dst))
				Quit:dst=""
				Set result = result _ src _ $Char(9) _ dst _ $Char(10)
			}
		}
	}
	Return result
}

/// Seed n nodes N1..Nn with 1..branching REL edges each, as rows (spec 252, ADR-0008): the adjacency index
/// derives ^KG from them; without the index, Put writes the mirror as before. The namespace is erased first
/// through Graph.KG.Eraser (which keeps the Graph-Version Token rising), not by a bare Kill of ^KG, which also
/// destroyed the temporal stores and left rows behind.
ClassMethod Seed(n As %Integer = 5000, branching As %Integer = 4) As %Status
{
	Set tmp = ##class(Graph.KG.Eraser).EraseAll()
	Do ##class(Graph.KG.GraphIndex).DropNKG()
	Kill ^ArnoKG
	Set tIndexed = ##class(Graph.KG.Adjacency).Indexed()
	For i = 1:1:n {
		Set s = "N" _ i
		&sql(INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (:s, ''))
	}
	For i = 1:1:n {
		Set s = "N" _ i
		Set deg = $Random(branching) + 1
		For j = 1:1:deg {
			Set d = "N" _ ($Random(n) + 1)
			// A repeated (s, REL, d) is refused by the unique constraint, as the mirror held it once before.
			&sql(INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (:s, 'REL', :d, ''))
			If (SQLCODE = 0) && ('tIndexed) {
				Do ##class(Graph.KG.Adjacency).Put(0, s, "REL", d, 1)
			}
		}
	}
	Return $$$OK
}

/// Pure $ORDER walk — counts edges, returns edge count + time.
ClassMethod TimeOrderWalk() As %String
{
	Set t1 = $ZHorolog
	Set edgeCount = 0
	Set src = ""
	For {
		Set src = $Order(^KG("out", 0, src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", 0, src, pred))
			Quit:pred=""
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", 0, src, pred, dst))
				Quit:dst=""
				Set edgeCount = edgeCount + 1
			}
		}
	}
	Set t2 = $ZHorolog
	Return $FNumber((t2 - t1) * 1000, "", 1) _ "ms (" _ edgeCount _ " edges)"
}

/// Full comparison at a given scale.
ClassMethod Compare(n As %Integer = 5000) As %String
{
	Do ..Seed(n)

	Set orderResult = ..TimeOrderWalk()

	Set t1 = $ZHorolog
	Set json = ##class(Graph.KG.ArnoAccel).BuildGraphJson()
	Set t2 = $ZHorolog
	Set jsonMs = $FNumber((t2 - t1) * 1000, "", 1)
	Set jsonKB = $Length(json) \ 1024

	Set t1 = $ZHorolog
	Set tsv = ..BuildAdjacencyTSV()
	Set t2 = $ZHorolog
	Set tsvMs = $FNumber((t2 - t1) * 1000, "", 1)
	Set tsvKB = $Length(tsv) \ 1024

	Set tmp = ##class(Graph.KG.Eraser).EraseAll()
	Do ##class(Graph.KG.GraphIndex).DropNKG()
	Kill ^ArnoKG
	Return "n=" _ n _ " | $ORDER: " _ orderResult _ " | JSON: " _ jsonMs _ "ms (" _ jsonKB _ "KB) | TSV: " _ tsvMs _ "ms (" _ tsvKB _ "KB)"
}

}
