Class Spike.Stress Extends %RegisteredObject
{

ClassMethod BigTxn()
{
	Set g = 987400
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkW(g)
	Set t0 = $ZHorolog
	TStart
	For i = 1:1:200000 {
		Do ##class(Spike.Deg).PutGL(g, "s" _ (i # 5000), "p", "o" _ i)
	}
	Set mid = $ZHorolog - t0
	TCommit
	Write "RES big_txn_200k_writes_s=", $FNumber(mid, "", 2), " commit_ok tlevel=", $TLevel, !
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkW(g)
}

ClassMethod Worker(g, dir, id)
{
	Set t0 = $ZHorolog, ok = 1
	For batch = 1:1:40 {
		TStart
		For k = 1:1:50 {
			Set j = $Select(dir = 1: k, 1: 51 - k)
			Do ##class(Spike.Deg).PutGL(g, "shared" _ j, "p", "w" _ id _ "_" _ batch _ "_" _ k)
		}
		TCommit
	}
	Set ^SpkDone(id) = $ZHorolog - t0
}

ClassMethod Concurrent()
{
	Set g = 987500
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkW(g), ^SpkDone
	Job ..Worker(g, 1, "a")
	Job ..Worker(g, -1, "b")
	Job ..Worker(g, 1, "c")
	Job ..Worker(g, -1, "d")
	Set t0 = $ZHorolog
	For {
		Quit:$Data(^SpkDone("a"))&&$Data(^SpkDone("b"))&&$Data(^SpkDone("c"))&&$Data(^SpkDone("d"))
		Quit:(($ZHorolog - t0) > 60)
		Hang 0.1
	}
	Set n = 0, s = ""
	For {
		Set s = $Order(^KG("out", g, s))
		Quit:s=""
		Set n = n + ##class(Spike.Deg).Count(g, s)
	}
	Write "RES opposite_order_writers_4 done=", ($Data(^SpkDone("a")) + $Data(^SpkDone("b")) + $Data(^SpkDone("c")) + $Data(^SpkDone("d"))) \ 1, "/4 wall_s=", $FNumber($ZHorolog - t0, "", 2), " edges=", n, " expected=", 4 * 40 * 50, !
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkW(g), ^SpkDone
}

}
