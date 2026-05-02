/// Percentile aggregate helpers for IVG Cypher engine.
Class IVG.Percentile Extends %RegisteredObject [ SqlSchemaName = IVG ]
{

/// percentileDisc(vals, p) — discrete Nth percentile of JSON array.
ClassMethod PDISC(vals As %String, p As %Double) As %String [ SqlProc ]
{
	Set arr = ##class(%Library.DynamicArray).%FromJSON(vals)
	Set n = arr.%Size()
	If n = 0 { Quit "" }
	Set i = n * p \ 1
	If i >= n { Set i = n - 1 }
	Quit arr.%Get(i)
}

/// percentileCont(vals, p) — continuous Nth percentile (linear interpolation).
ClassMethod PCONT(vals As %String, p As %Double) As %Double [ SqlProc ]
{
	Set arr = ##class(%Library.DynamicArray).%FromJSON(vals)
	Set n = arr.%Size()
	If n = 0 { Quit 0 }
	Set lower = n * p \ 1
	If lower >= n - 1 { Quit arr.%Get(n-1) + 0 }
	Set frac = n * p - lower
	Quit arr.%Get(lower) + frac * (arr.%Get(lower+1) - arr.%Get(lower))
}

}
