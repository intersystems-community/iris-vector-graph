/// Percentile aggregate helpers for IVG Cypher engine.
Class IVG.Percentile Extends %RegisteredObject [ SqlSchemaName = IVG ]
{

/// percentileDisc(vals, p) — discrete Nth percentile of sorted JSON array.
ClassMethod PDISC(vals As %String, p As %Double) As %String [ SqlProc ]
{
	If (p < 0) || (p > 1) { Throw ##class(%Exception.General).%New("ArgumentError","5030",,"ArgumentError: NumberOutOfRange: percentile must be between 0.0 and 1.0") }
	Set arr = ##class(%Library.DynamicArray).%FromJSON(vals)
	Set n = arr.%Size()
	If n = 0 { Quit "" }
	Set sorted = ..SortArray(arr, n)
	Set i = ((n - 1) * p) \ 1
	If i < 0 { Set i = 0 }
	If i >= n { Set i = n - 1 }
	Quit sorted.%Get(i)
}

/// percentileCont(vals, p) — continuous Nth percentile (linear interpolation).
ClassMethod PCONT(vals As %String, p As %Double) As %Double [ SqlProc ]
{
	If (p < 0) || (p > 1) { Throw ##class(%Exception.General).%New("ArgumentError","5030",,"ArgumentError: NumberOutOfRange: percentile must be between 0.0 and 1.0") }
	Set arr = ##class(%Library.DynamicArray).%FromJSON(vals)
	Set n = arr.%Size()
	If n = 0 { Quit 0 }
	Set sorted = ..SortArray(arr, n)
	Set pos = (n - 1) * p
	Set lower = pos \ 1
	If lower < 0 { Set lower = 0 }
	If lower >= (n - 1) { Quit sorted.%Get(n - 1) + 0 }
	Set frac = pos - lower
	Set lo = sorted.%Get(lower) + 0
	Set hi = sorted.%Get(lower + 1) + 0
	Quit lo + (frac * (hi - lo))
}

/// Sort a DynamicArray of numbers ascending (insertion sort, in-place).
ClassMethod SortArray(arr As %Library.DynamicArray, n As %Integer) As %Library.DynamicArray
{
	Set i = 1
	While i < n {
		Set key = arr.%Get(i) + 0
		Set j = i - 1
		While j >= 0 && (arr.%Get(j) + 0 > key) {
			Do arr.%Set(j + 1, arr.%Get(j) + 0)
			Set j = j - 1
		}
		Do arr.%Set(j + 1, key)
		Set i = i + 1
	}
	Quit arr
}

}
