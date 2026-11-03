Class IVGSpike.Bench
{

/// n edges over `nodes` nodes, `batch` per transaction. mode "plain": EdgePlain only. "index": Edge, whose
/// functional index maintains the adjacency. "noindex": Edge with %NOINDEX, then %BuildIndices of Adj.
/// Returns "<insert seconds>|<build seconds>".
ClassMethod Run(mode As %String, n As %Integer, nodes As %Integer = 5000, batch As %Integer = 500, prefix As %String = "n") As %String
{
	Set t0 = $ZHorolog
	Set i = 0, fail = 0, lastcode = 0
	While i < n {
		TSTART
		For j = 1:1:batch {
			Quit:i>=n
			Set s = prefix _ (i # nodes), o = prefix _ ((i * 7 + 1) # nodes), p = "R" _ (i # 3)
			Set i = i + 1
			Continue:s=o
			If mode = "plain" {
				&sql(INSERT INTO IVGSpike.EdgePlain (GraphId, S, P, O) VALUES ('', :s, :p, :o))
			} ElseIf mode = "manual" {
				&sql(INSERT INTO IVGSpike.EdgePlain (GraphId, S, P, O) VALUES ('', :s, :p, :o))
				If SQLCODE = 0 { Do ##class(Graph.KG.EdgeScan).WriteAdjacency(s, p, o, "1.0", 0) }
			} ElseIf mode = "index" {
				&sql(INSERT INTO IVGSpike.Edge (GraphId, S, P, O) VALUES ('', :s, :p, :o))
			} Else {
				&sql(INSERT %NOINDEX %NOCHECK INTO IVGSpike.Edge (GraphId, S, P, O) VALUES ('', :s, :p, :o))
			}
			If SQLCODE '= 0 { Set fail = fail + 1, lastcode = SQLCODE_" "_$Get(%msg) }
		}
		TCOMMIT
	}
	Set t1 = $ZHorolog
	If mode = "noindex" {
		Kill ^IVGSpike
		Set sc = ##class(IVGSpike.Edge).%BuildIndices()
	}
	Set t2 = $ZHorolog
	Quit $FNumber(t1 - t0, "", 2) _ "|" _ $FNumber(t2 - t1, "", 2) _ "|failed=" _ fail _ " " _ lastcode
}

/// DML the verification needs from compiled code: a transaction that is rolled back, a delete, an update.
ClassMethod Dml(what As %String) As %String
{
	If what = "rollback" {
		TSTART
		&sql(INSERT INTO IVGSpike.Edge (GraphId, S, P, O) VALUES ('', 'rb1', 'R', 'rb2'))
		Set inside = $Data(^IVGSpike("out", 0, "rb1", "R", "rb2"))
		TROLLBACK
		Quit "inside tx indexed=" _ inside _ ", after rollback indexed=" _ $Data(^IVGSpike("out", 0, "rb1", "R", "rb2")) _ ", SQLCODE=" _ SQLCODE
	}
	If what = "delete" {
		&sql(DELETE FROM IVGSpike.Edge WHERE S = 'n1')
		Quit "SQLCODE=" _ SQLCODE _ " rows=" _ %ROWCOUNT _ " deg(n1)=" _ +$Get(^IVGSpike("deg", 0, "n1"))
	}
	If what = "update" {
		&sql(UPDATE IVGSpike.Edge SET O = 'moved' WHERE S = 'n2' AND P = 'R2')
		Quit "SQLCODE=" _ SQLCODE _ " rows=" _ %ROWCOUNT
	}
	Quit "?"
}

/// Today's real bulk path (registers both endpoints, checks for the edge, inserts, writes ^KG), n edges.
ClassMethod Today(n As %Integer, nodes As %Integer = 5000) As %String
{
	Set arr = []
	For i = 0:1:n - 1 {
		Do arr.%Push({"s": ("m" _ (i # nodes)), "p": ("R" _ (i # 3)), "o": ("m" _ ((i * 7 + 1) # nodes))})
	}
	Set json = arr.%ToJSON()
	Set t0 = $ZHorolog
	Set landed = ##class(Graph.KG.EdgeScan).BulkIngestEdgesSQL(json)
	Quit $FNumber($ZHorolog - t0, "", 2) _ "s for " _ landed _ " edges landed"
}

/// `jobs` concurrent writers on disjoint node ranges; returns wall seconds and then the Verify line.
ClassMethod Parallel(mode As %String, jobs As %Integer, n As %Integer) As %String
{
	Kill ^IVGSpikeDone
	Set t0 = $ZHorolog
	For j = 1:1:jobs {
		Job ##class(IVGSpike.Bench).Worker(mode, n, "j" _ j _ "_", j)
	}
	For wait = 1:1:600 {
		Quit:(+$Get(^IVGSpikeDone) '< jobs)
		Hang 0.1
	}
	Set wall = $ZHorolog - t0
	Quit $FNumber(wall, "", 2) _ "s wall, writers finished=" _ +$Get(^IVGSpikeDone)
}

ClassMethod Worker(mode As %String, n As %Integer, prefix As %String, slot As %Integer)
{
	Set r = ..Run(mode, n, 20000, 500, prefix)
	Set tmp = $Increment(^IVGSpikeDone)
}

ClassMethod Reset()
{
	&sql(DELETE FROM IVGSpike.EdgePlain)
	Do ##class(IVGSpike.Edge).%KillExtent()
	Kill ^IVGSpike
}

/// The adjacency stores compared with a recount from the table.
ClassMethod Verify() As %String
{
	Set bad = 0, rows = 0
	Kill tDeg, tDegp
	Set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT GraphId, S, P, O FROM IVGSpike.Edge")
	While rs.%Next() {
		Set g = rs.%Get("GraphId"), k = $Select((g = "") || (g = $Char(0)): 0, 1: g), s = rs.%Get("S"), p = rs.%Get("P"), o = rs.%Get("O")
		Set rows = rows + 1
		If '$Data(^IVGSpike("out", k, s, p, o)) { Set bad = bad + 1 }
		If '$Data(^IVGSpike("in", k, o, p, s)) { Set bad = bad + 1 }
		Set tDeg(k, s) = $Get(tDeg(k, s)) + 1
		Set tDegp(k, s, p) = $Get(tDegp(k, s, p)) + 1
	}
	Set k = "" For { Set k = $Order(tDeg(k)) Quit:k=""  Set s = "" For { Set s = $Order(tDeg(k, s)) Quit:s=""  If +$Get(^IVGSpike("deg", k, s)) '= tDeg(k, s) { Set bad = bad + 1 } } }
	Set n = 0, k = "" For { Set k = $Order(^IVGSpike("out", k)) Quit:k=""  Set s = "" For { Set s = $Order(^IVGSpike("out", k, s)) Quit:s=""  Set p = "" For { Set p = $Order(^IVGSpike("out", k, s, p)) Quit:p=""  Set o = "" For { Set o = $Order(^IVGSpike("out", k, s, p, o)) Quit:o=""  Set n = n + 1 } } } }
	If n '= rows { Set bad = bad + 1 }
	Quit rows _ " rows, " _ n _ " out entries, " _ bad _ " mismatches"
}

}
