/// The Cypher sort key the native executor orders by (spec 253, 257): SQLUser.CY_SORT_KEY's code, as a plain class
/// method. Through the UDF's generated class (##class(User.funcCYSORTKEY).CYSORTKEY) one key cost 44.7 us, here 1.8 us,
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
	If $Extract(v) = "P" {
		Quit "k" _ v
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
