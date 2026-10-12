/// Throwaway spike (IVGPERFB only): degree without a stored counter, three ways.
Class Spike.Deg Extends %RegisteredObject
{

/// Today: stored counters via $Increment.
ClassMethod PutD(g, s, p, o)
{
	Set ^KG("out", g, s, p, o) = 1
	Set ^KG("in", g, o, p, s) = 1
	Set t = $Increment(^KG("deg", g, s))
	Set t = $Increment(^KG("degp", g, s, p))
	Set t = $Increment(^KG("__version"))
}

/// G: per-source generation only.
ClassMethod PutG(g, s, p, o)
{
	Set ^KG("out", g, s, p, o) = 1
	Set ^KG("in", g, o, p, s) = 1
	Set t = $Increment(^KG("gen", g, s))
	Set t = $Increment(^KG("__version"))
}

/// GL: generation plus a shared per-graph lock, so a reader can tell a write is in flight.
ClassMethod PutGL(g, s, p, o)
{
	Lock +^SpkW(g)#"S":5
	Set ^KG("out", g, s, p, o) = 1
	Set ^KG("in", g, o, p, s) = 1
	Set t = $Increment(^KG("gen", g, s))
	Set t = $Increment(^KG("__version"))
	Lock -^SpkW(g)#"S"
}

ClassMethod RemoveGL(g, s, p, o)
{
	Lock +^SpkW(g)#"S":5
	Kill ^KG("out", g, s, p, o)
	Kill ^KG("in", g, o, p, s)
	Set t = $Increment(^KG("gen", g, s))
	Set t = $Increment(^KG("__version"))
	Lock -^SpkW(g)#"S"
}

ClassMethod Count(g, s, p = "") As %Integer
{
	Set n = 0
	If p '= "" {
		Set o = ""
		For {
			Set o = $Order(^KG("out", g, s, p, o))
			Quit:o=""
			Set n = n + 1
		}
		Quit n
	}
	Set q = ""
	For {
		Set q = $Order(^KG("out", g, s, q))
		Quit:q=""
		Set o = ""
		For {
			Set o = $Order(^KG("out", g, s, q, o))
			Quit:o=""
			Set n = n + 1
		}
	}
	Quit n
}

/// Cache without the writer probe.
ClassMethod DegG(g, s, p = "") As %Integer
{
	Set k = $Select(p = "": $Char(1), 1: p)
	Set gen = +$Get(^KG("gen", g, s))
	Set c = $Get(^SpkC(g, s, k))
	If (c '= "") && ($List(c, 1) = gen) {
		Quit $List(c, 2)
	}
	Set n = ..Count(g, s, p)
	Set ^SpkC(g, s, k) = $ListBuild(gen, n)
	Quit n
}

/// Cache that is filled only when no writer was in flight and the generation did not move.
ClassMethod DegGL(g, s, p = "") As %Integer
{
	Set k = $Select(p = "": $Char(1), 1: p)
	Set gen = +$Get(^KG("gen", g, s))
	Set c = $Get(^SpkC(g, s, k))
	If (c '= "") && ($List(c, 1) = gen) {
		Quit $List(c, 2)
	}
	Lock +^SpkW(g)#"E":0
	Set got = $Test
	If got {
		Lock -^SpkW(g)#"E"
	}
	Set n = ..Count(g, s, p)
	If got && (+$Get(^KG("gen", g, s)) = gen) {
		Set ^SpkC(g, s, k) = $ListBuild(gen, n)
	}
	Quit n
}

/// The writer of the race: uncommitted write, hold, roll back.
ClassMethod Writer(g, s, o, mode, holdSecs)
{
	TStart
	If mode = "GL" {
		Do ..PutGL(g, s, "p", o)
	} Else {
		Do ..PutG(g, s, "p", o)
	}
	Hang holdSecs
	TRollback
}

ClassMethod Race(mode)
{
	Set g = 987100
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkC(g), ^SpkW(g)
	Do ..PutGL(g, "rz", "p", "a")
	Do ..PutGL(g, "rz", "p", "b")
	Set pre = $Select(mode = "GL": ..DegGL(g, "rz"), 1: ..DegG(g, "rz"))
	Job ..Writer(g, "rz", "c", mode, 3)
	Hang 1
	Set during = $Select(mode = "GL": ..DegGL(g, "rz"), 1: ..DegG(g, "rz"))
	Hang 3
	Set after = $Select(mode = "GL": ..DegGL(g, "rz"), 1: ..DegG(g, "rz"))
	Set truth = ..Count(g, "rz")
	Write "RES race_", mode, " before=", pre, " during_uncommitted=", during, " after_rollback=", after, " truth=", truth, " WRONG=", (after '= truth), !
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkC(g), ^SpkW(g)
}

/// Random ops incl. rolled-back ones; the guarded cache must equal a recount after every step.
ClassMethod Property(steps, seed)
{
	Set g = 987200, bad = 0
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkC(g), ^SpkW(g)
	Set r = seed
	For i = 1:1:steps {
		Set r = (r * 16807) # 2147483647, op = r # 5
		Set r = (r * 16807) # 2147483647, s = "s" _ (r # 4)
		Set r = (r * 16807) # 2147483647, o = "o" _ (r # 6)
		Set r = (r * 16807) # 2147483647, p = "p" _ (r # 2)
		If op < 2 {
			Do ..PutGL(g, s, p, o)
		} ElseIf op = 2 {
			Do ..RemoveGL(g, s, p, o)
		} ElseIf op = 3 {
			TStart
			Do ..PutGL(g, s, p, o)
			TRollback
		} Else {
			TStart
			Do ..RemoveGL(g, s, p, o)
			TRollback
		}
		For j = 0:1:3 {
			Set n = "s" _ j
			If (..DegGL(g, n) '= ..Count(g, n)) || (..DegGL(g, n, "p0") '= ..Count(g, n, "p0")) {
				Set bad = bad + 1
			}
		}
	}
	Write "RES property_steps=", steps, " mismatches=", bad, !
	Kill ^KG("out", g), ^KG("in", g), ^KG("gen", g), ^SpkC(g), ^SpkW(g)
}

}
