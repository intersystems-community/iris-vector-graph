Class Spike.Bench Extends %RegisteredObject
{

ClassMethod Writes()
{
	Set N = 200000
	For round = 1:1:3 {
		For m = "D", "G", "GL" {
			Set g = 987300
			Kill ^KG("out", g), ^KG("in", g), ^KG("deg", g), ^KG("degp", g), ^KG("gen", g), ^SpkW(g)
			Set t0 = $ZHorolog
			For i = 1:1:N {
				Set s = "s" _ (i # 5000), o = "o" _ i
				If m = "D" {
					Do ##class(Spike.Deg).PutD(g, s, "p", o)
				} ElseIf m = "G" {
					Do ##class(Spike.Deg).PutG(g, s, "p", o)
				} Else {
					Do ##class(Spike.Deg).PutGL(g, s, "p", o)
				}
			}
			Set el = $ZHorolog - t0
			Write "RES write_", m, " round", round, " us_per_edge=", $FNumber(el * 1000000 / N, "", 3), !
		}
	}
	Kill ^KG("out", 987300), ^KG("in", 987300), ^KG("deg", 987300), ^KG("degp", 987300), ^KG("gen", 987300), ^SpkW(987300)
}

ClassMethod Pattern(label, mode, n, node, rounds)
{
	Set t0 = $ZHorolog, sum = 0
	For i = 1:1:n {
		If mode = "stored" {
			Set sum = sum + $Get(^KG("deg", 0, node))
		} ElseIf mode = "counted" {
			Set sum = sum + ##class(Spike.Deg).Count(0, node)
		} ElseIf mode = "G" {
			Set sum = sum + ##class(Spike.Deg).DegG(0, node)
		} Else {
			Set sum = sum + ##class(Spike.Deg).DegGL(0, node)
		}
	}
	Set el = $ZHorolog - t0
	Write "RES ", label, " ", mode, " us_per_call=", $FNumber(el * 1000000 / n, "", 3), " sum=", sum, !
}

ClassMethod Reads()
{
	Do ##class(Perf.Bench).Seeds(.seeds)
	For mode = "stored", "counted", "G", "GL" {
		Kill ^SpkC
		For pass = "cold", "warm" {
			If (pass = "warm") && ((mode = "stored") || (mode = "counted")) {
				Continue
			}
			Set lab = pass
			Set t0 = $ZHorolog, sum = 0
			For rep = 1:1:5 {
				For i = 1:1:20 {
					Set s = seeds(i)
					If mode = "stored" {
						Set sum = sum + $Get(^KG("deg", 0, s))
					} ElseIf mode = "counted" {
						Set sum = sum + ##class(Spike.Deg).Count(0, s)
					} ElseIf mode = "G" {
						Set sum = sum + ##class(Spike.Deg).DegG(0, s)
					} Else {
						Set sum = sum + ##class(Spike.Deg).DegGL(0, s)
					}
				}
			}
			Set el = $ZHorolog - t0
			Write "RES khop20x5 ", mode, " ", lab, " ms=", $FNumber(el * 1000, "", 3), " sum=", sum, !
			Do ..Pattern("khop_hub_x200 " _ lab, mode, 200, "hub", 1)
			Do ..Pattern("khop_d200_x20000 " _ lab, mode, 20000, "d200", 1)
		}
	}
	// degree-centrality style loop over every source
	For mode = "stored", "counted", "G", "GL" {
		Kill ^SpkC
		For pass = "cold", "warm" {
			If (pass = "warm") && ((mode = "stored") || (mode = "counted")) {
				Continue
			}
			Set t0 = $ZHorolog, sum = 0, ns = 0, s = ""
			For {
				Set s = $Order(^KG("out", 0, s))
				Quit:s=""
				Set ns = ns + 1
				If mode = "stored" {
					Set sum = sum + $Get(^KG("deg", 0, s))
				} ElseIf mode = "counted" {
					Set sum = sum + ##class(Spike.Deg).Count(0, s)
				} ElseIf mode = "G" {
					Set sum = sum + ##class(Spike.Deg).DegG(0, s)
				} Else {
					Set sum = sum + ##class(Spike.Deg).DegGL(0, s)
				}
			}
			Set el = $ZHorolog - t0
			Write "RES centrality_loop ", mode, " ", pass, " ms=", $FNumber(el * 1000, "", 3), " sources=", ns, " sum=", sum, !
		}
	}
	Kill ^SpkC
}

}
