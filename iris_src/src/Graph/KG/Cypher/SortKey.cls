/// The Cypher sort key the native executor orders by (spec 253, 257): Graph_KG.CY_SORT_KEY's code, as a plain class
/// method. Through the UDF's generated class (##class(Graph.KG.funcCYSORTKEY).CYSORTKEY) one key cost 44.7 us, here 1.8 us,
/// and an ordered read computes one per match. The SQL path keeps the UDF (a DDL-only install has no classes);
/// tests/unit/test_257_sort_key_class.py holds the two to the same code: change both.
Class Graph.KG.Cypher.SortKey Extends %RegisteredObject
{

/// The sort key of value v of type t (as CY_SORT_KEY: "x" an untyped stored value, or the type of a JSON value).
ClassMethod Key(v As %String, t As %String) As %String
{
	If (v = "") || (t = "null") {
		Quit "z"
	}
	If v = $Char(0) {
		Quit "l"
	}
	// a stored $DOUBLE NaN or infinity (a Cypher write of one): NaN after every number, the infinities at the ends
	// (4.6.0 R307: they sorted as the text NAN, INF, -INF)
	If ((t = "x") || (t = "number")) && ($Length(v) < 40) && ($ASCII($LISTBUILD(v), 2) > 7) {
		Set dt = "" _ v If dt = "NAN" {
			Quit "o"
		}
		If dt = "INF" {
			Quit "n3"
		}
		If dt = "-INF" {
			Quit "n0"
		}
	}
	If t = "string" {
		Quit "l" _ v
	}
	If t = "boolean" {
		Quit "m" _ +v
	}
	If (t = "array") || ((t = "x") && ($Extract(v) = "[")) {
		Set arr = "" Try {
			Set arr = ##class(%Library.DynamicArray).%FromJSON(v)
		} Catch {
			Set arr = ""
		}
		If $IsObject(arr) {
			Set k = "d", it = arr.%GetIterator() While it.%GetNext(.i, .e, .et) {
				If et = "array" {
					Set e = e.%ToJSON()
				} ElseIf et = "object" {
					Set e = e.%ToJSON()
				} ElseIf et = "boolean" {
					Set e = $Select(e: 1, 1: 0)
				}
				Set k = k _ ..Key(e, $Select(et = "string": "x", 1: et)) _ $Char(1)
			}
			Quit k
		}
	}
	If t = "x" {
		Set q = $Char(34) If ($Extract(v, 1, 8) = ("{" _ q _ "_id" _ q _ ":" _ q)) && (v [ ("," _ q _ "_labels" _ q _ ":[")) {
			Quit "b" _ v
		}
		If ($Extract(v, 1, 9) = ("{" _ q _ "type" _ q _ ":" _ q)) && (v [ ("," _ q _ "props" _ q _ ":")) {
			Quit "c" _ v
		}
		If ($Extract(v, 1, 10) = ("{" _ q _ "nodes" _ q _ ":[")) && (v [ ("," _ q _ "rels" _ q _ ":[")) {
			Quit "e" _ v
		}
	}
	If (t = "object") || ((t = "x") && ($Extract(v) = "{")) {
		Quit "a" _ v
	}
	If t = "x" {
		If v = "true" {
			Quit "m1"
		}
		If v = "false" {
			Quit "m0"
		}
		If v = "NaN" {
			Quit "o"
		}
	}
	If (t = "number") || ((t = "x") && $IsValidNum(v)) {
		Set n = +v If n = 0 {
			Quit "n1"
		}
		Set s = "" _ $Select(n < 0: -n, 1: n), ex = 0 If s [ "E" {
			Set ex = +$Piece(s, "E", 2), s = $Piece(s, "E", 1)
		}
		Set ip = $Piece(s, "."), fp = $Piece(s, ".", 2) If +ip {
			Set e = $Length(+ip) - 1, dg = (+ip) _ fp
		} Else {
			Set dg = $ZStrip(fp, "<", "0"), e = $Length(dg) - $Length(fp) - 1
		}
		Set dg = $ZStrip(dg, ">", "0"), e = e + ex + 50000 If n > 0 {
			Quit "n2" _ $Extract(100000 + e, 2, 6) _ dg
		}
		Quit "n0" _ $Extract(100000 + (99999 - e), 2, 6) _ $Translate(dg, "0123456789", "9876543210") _ "~"
	}
	// A duration only when well formed (4.6.0 R050 W2: "Person" and "Pat" sorted as durations, before every string)
	If $Extract(v) = "P" {
		Set dur = ($Length(v) > 1), tp = 0, num = "", p = 1, dl = 0 While dur && (p < $Length(v)) {
			Set p = p + 1, c = $Extract(v, p) If (c = "-") || (c = ".") || ("0123456789" [ c) {
				Set num = num _ c
			} ElseIf (c = "T") && (tp = 0) && ($Length(num) = 0) {
				Set tp = 1
			} ElseIf $Length(num) && $IsValidNum(num) && ($Select(tp: "HMS", 1: "YMWD") [ c) {
				Set dl = dl + (num * $Case($Select(tp: "T", 1: "") _ c, "Y": 31556952, "M": 2629746, "W": 604800, "D": 86400, "TH": 3600, "TM": 60, : 1)), num = ""
			} Else {
				Set dur = 0
			}
		}
		// by length, as openCypher CIP2015-08-06-date-time orders durations (a year 365.2425 days, a month 1/12 of
		// it, 2629746 s, a day 24 h), then by text (4.6.0 R307: by text alone P1D sorted before PT1H)
		If dur && ($Length(num) = 0) && ("YMWDHS" [ $Extract(v, *)) {
			Quit "k" _ $Extract(..Key(dl, "number"), 2, *) _ $Char(1) _ v
		}
	}
	Set d10 = $Extract(v, 1, 10), hasDate = ($Length(d10) = 10) && ($Extract(d10, 5) = "-") && ($Extract(d10, 8) = "-") && ($Translate($Extract(d10, 1, 4) _ $Extract(d10, 6, 7) _ $Extract(d10, 9, 10), "0123456789") = ""), rest = v, h = 0 If hasDate {
		If $Length(v) = 10 {
			Quit "h" _ v
		}
		If $Extract(v, 11) '= "T" {
			Quit "l" _ v
		}
		Set h = $ZDateH(d10, 3, , , , , -672045, , "") If h = "" {
			Quit "l" _ v
		}
		Set rest = $Extract(v, 12, *)
	}
	Set r5 = $Extract(rest, 1, 5) If ($Length(r5) < 5) || ($Length($Translate($Extract(r5, 1, 2) _ $Extract(r5, 4, 5), "0123456789")) > 0) || ($Char(10, 11, 12, 13, 133, 8232, 8233) [ $Extract(r5, 3)) {
		Quit "l" _ v
	}
	Set p = 1 While (p <= $Length(rest)) && ("0123456789:." [ $Extract(rest, p)) {
		Set p = p + 1
	}
	Set tstr = $Extract(rest, 1, p - 1), zone = $Extract(rest, p, *), sf = $Piece(tstr, ":", 3), secs = ($Piece(tstr, ":", 1) * 3600) + ($Piece(tstr, ":", 2) * 60) + $Piece(sf, "."), ns = $Extract($Piece(sf, ".", 2) _ "000000000", 1, 9), offs = 0 If zone [ "[" {
		Set zone = $Piece(zone, "[", 1)
	}
	If zone = "" {
		If hasDate {
			Quit "g" _ d10 _ $Extract(100000 + secs, 2, 6) _ ns
		}
		Quit "j" _ $Extract(100000 + secs, 2, 6) _ ns
	}
	If zone '= "Z" {
		Set offs = $Select($Extract(zone) = "-": -1, 1: 1) * (($Extract(zone, 2, 3) * 3600) + ($Extract(zone, 5, 6) * 60) + $Extract(zone, 8, 9))
	}
	If hasDate {
		Quit "f" _ $Extract(10000000000000 + (((h + 672045) * 86400) + secs - offs), 2, 14) _ ns
	}
	Quit "i" _ $Extract(1000000 + secs - offs + 100000, 2, 7) _ ns
}

}
