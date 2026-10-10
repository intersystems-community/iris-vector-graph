Class IVG236Tmp.Cov
{

ClassMethod Run(routines As %String, stopFile As %String, outFile As %String)
{
	Set list = $ListFromString(routines, ",")
	Do ##class(%Monitor.System.LineByLine).Stop()
	Set sc = ##class(%Monitor.System.LineByLine).Start(list, "", "")
	Set f = ##class(%File).%New(outFile)
	Do f.Open("WN")
	If $$$ISERR(sc) {
		Do f.WriteLine("START_FAILED|"_$System.Status.GetErrorText(sc))
		Do f.Close()
		Quit
	}
	Do f.WriteLine("STARTED")
	Do f.Close()
	For i = 1:1:3000 {
		Quit:##class(%File).Exists(stopFile)
		Hang 2
	}
	// Counters are read BEFORE Stop: Stop clears them.
	Set f = ##class(%File).%New(outFile)
	Do f.Open("WN")
	For k = 1:1:$ListLength(list) {
		Set rn = $List(list, k)
		Kill cnt
		Set rs = ##class(%ResultSet).%New("%Monitor.System.LineByLine:Result")
		Set sc2 = rs.Execute(rn)
		If $$$ISERR(sc2) { Do f.WriteLine("ERR|"_rn_"|"_$System.Status.GetErrorText(sc2))  Continue }
		Set n = 0
		While rs.Next() { Set n = n + 1, cnt(n) = +$ListGet(rs.GetData(1), 1) }
		Set rtn = ##class(%Routine).%OpenId(rn_".INT")
		If '$IsObject(rtn) { Do f.WriteLine("NOINT|"_rn)  Continue }
		Set ln = 0
		While 'rtn.AtEnd {
			Set text = rtn.ReadLine()
			Set ln = ln + 1
			Do f.WriteLine("L|"_rn_"|"_ln_"|"_$Get(cnt(ln), -9)_"|"_text)
		}
	}
	Do f.WriteLine("DONE")
	Do f.Close()
	Do ##class(%Monitor.System.LineByLine).Stop()
}

}
