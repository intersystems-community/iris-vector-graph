/// Benchmark: Compare adjacency walk strategies for ^KG.
Class Graph.KG.BenchFormat Extends %RegisteredObject
{

ClassMethod BuildAdjacencyTSV() As %String
{
	Set result = ""
	Set src = ""
	For {
		Set src = $Order(^KG("out", src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", src, pred))
			Quit:pred=""
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", src, pred, dst))
				Quit:dst=""
				Set result = result _ src _ $Char(9) _ dst _ $Char(10)
			}
		}
	}
	Return result
}

ClassMethod Seed(n As %Integer = 5000, branching As %Integer = 4) As %Status
{
	Kill ^KG
	Kill ^NKG
	Kill ^ArnoKG
	For i = 1:1:n {
		Set s = "N" _ i
		Set deg = $Random(branching) + 1
		Set ^KG("deg", s) = deg
		For j = 1:1:deg {
			Set d = "N" _ ($Random(n) + 1)
			Set ^KG("out", s, "REL", d) = 1
			Set ^KG("in", d, "REL", s) = 1
		}
	}
	Set ^KG("__version") = 1
	Return $$$OK
}

/// Pure $ORDER walk — counts edges, returns edge count + time.
ClassMethod TimeOrderWalk() As %String
{
	Set t1 = $ZHorolog
	Set edgeCount = 0
	Set src = ""
	For {
		Set src = $Order(^KG("out", src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", src, pred))
			Quit:pred=""
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", src, pred, dst))
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

	Kill ^KG
	Kill ^NKG
	Kill ^ArnoKG
	Return "n=" _ n _ " | $ORDER: " _ orderResult _ " | JSON: " _ jsonMs _ "ms (" _ jsonKB _ "KB) | TSV: " _ tsvMs _ "ms (" _ tsvKB _ "KB)"
}

}
