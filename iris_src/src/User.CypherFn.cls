/// User.CypherFn — SQL UDFs for Cypher expression translation
/// All method names use IVG prefix to avoid IRIS 2026.3 reserved keyword conflicts.
/// SQL projection names: SQLUser.CypherFn_IVG<Name>
Class User.CypherFn Extends %RegisteredObject
{

/// Regex match operator (=~ in Cypher). Returns 1 if string matches pattern, 0 otherwise.
ClassMethod IVGREGEXMATCH(s As %String = "", p As %String = "") As %Integer [ SqlProc ]
{
    If (s = "") || (p = "") { Return 0 }
    Try {
        Set m = ##class(%Regex.Matcher).%New(p)
        Set r = m.Match(s)
        Return $Select(r: 1, 1: 0)
    } Catch { Return 0 }
}

/// JSON array length. Returns 0 for null/empty.
ClassMethod IVGJSONARRAYLENGTH(json As %String = "") As %Integer [ SqlProc ]
{
    If (json = "") || (json = "null") { Return 0 }
    Try {
        Set arr = ##class(%DynamicArray).%FromJSON(json)
        If arr = "" { Return 0 }
        Return arr.%Size()
    } Catch { Return 0 }
}

/// JSON value extraction via JSONPath (supports $.key and $[N] forms).
ClassMethod IVGJSONVALUE(json As %String = "", path As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "" }
    Try {
        Set obj = ##class(%DynamicObject).%FromJSON(json)
        If obj = "" { Return "" }
        If $Extract(path, 1, 2) = "$." {
            Return obj.%Get($Extract(path, 3, *))
        } ElseIf $Extract(path, 1, 2) = "$[" {
            Set idx = +$Extract(path, 3, $Length(path) - 1)
            If obj.%IsA("%DynamicArray") { Return obj.%Get(idx) }
        }
        Return ""
    } Catch { Return "" }
}

/// Return keys of a JSON object as a JSON array string.
ClassMethod IVGJSONKEYS(json As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "[]" }
    Try {
        Set obj = ##class(%DynamicObject).%FromJSON(json)
        If obj = "" { Return "[]" }
        Set keys = ##class(%DynamicArray).%New()
        Set iter = obj.%GetIterator()
        While iter.%GetNext(.key, .val) { Do keys.%Push(key) }
        Return keys.%ToJSON()
    } Catch { Return "[]" }
}

/// Reverse a JSON array.
ClassMethod IVGLISTREVERSE(json As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "[]" }
    Try {
        Set arr = ##class(%DynamicArray).%FromJSON(json)
        If arr = "" { Return "[]" }
        Set rev = ##class(%DynamicArray).%New()
        Set sz = arr.%Size()
        For i = sz:-1:1 { Do rev.%Push(arr.%Get(i - 1)) }
        Return rev.%ToJSON()
    } Catch { Return "[]" }
}

/// Split string by delimiter and return as JSON array.
ClassMethod IVGSTRSPLIT(s As %String = "", delim As %String = "") As %String [ SqlProc ]
{
    If s = "" { Return "[]" }
    Set result = ##class(%DynamicArray).%New()
    Set pieces = $Length(s, delim)
    For i = 1:1:pieces { Do result.%Push($Piece(s, delim, i)) }
    Return result.%ToJSON()
}

/// Generate a random UUID.
ClassMethod IVGNEWID() As %String [ SqlProc ]
{
    Return ##class(%SYS.Util).CreateGUID()
}

/// Generate a random float in [0, 1).
ClassMethod IVGRAND() As %Decimal [ SqlProc ]
{
    Return ##class(%PopulationStatistics).RandomDouble()
}

/// Get first element of a JSON array.
ClassMethod IVGLISTHEAD(json As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "" }
    Try {
        Set arr = ##class(%DynamicArray).%FromJSON(json)
        If arr = "" || arr.%Size() = 0 { Return "" }
        Return arr.%Get(0)
    } Catch { Return "" }
}

/// Get last element of a JSON array.
ClassMethod IVGLISTLAST(json As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "" }
    Try {
        Set arr = ##class(%DynamicArray).%FromJSON(json)
        If arr = "" || arr.%Size() = 0 { Return "" }
        Return arr.%Get(arr.%Size() - 1)
    } Catch { Return "" }
}

/// Get all but the first element of a JSON array.
ClassMethod IVGLISTTAIL(json As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "[]" }
    Try {
        Set arr = ##class(%DynamicArray).%FromJSON(json)
        If arr = "" || arr.%Size() = 0 { Return "[]" }
        Set tail = ##class(%DynamicArray).%New()
        For i = 1:1:arr.%Size() - 1 { Do tail.%Push(arr.%Get(i)) }
        Return tail.%ToJSON()
    } Catch { Return "[]" }
}

/// List element access with strict integer type checking (Cypher TCK compliance).
/// idx must be a string representation of an integer (no decimals, no leading zeros beyond "0").
/// Raises an error for non-integer indices (boolean, float, string, list, map).
/// Returns the array element at 0-based index idx, or NULL for out-of-bounds.
ClassMethod IVGLISTGET(json As %String = "", idx As %String = "") As %String [ SqlProc ]
{
    // Reject empty/null list
    If (json = "") || (json = "null") { Return "" }
    // Type-check: idx must parse as an exact integer (no decimal point)
    // IRIS $IsValidNum returns 1 for integers and floats; use $Find for dot check
    If $Find(idx, ".") > 0 {
        // Float index: TypeError
        Throw ##class(%Exception.General).%New("TypeError","5001",,"Non-integer index for list subscript: "_idx)
    }
    // Check if idx looks like a JSON array or object (starts with [ or {)
    Set firstChar = $Extract($ZStrip(idx,"<W"), 1)
    If firstChar = "[" || (firstChar = "{") {
        Throw ##class(%Exception.General).%New("TypeError","5001",,"Non-integer index for list subscript: "_idx)
    }
    // Must be a valid integer
    If '$IsValidNum(idx, 0) {
        Throw ##class(%Exception.General).%New("TypeError","5001",,"Non-integer index for list subscript: "_idx)
    }
    Set intIdx = +idx
    Try {
        Set arr = ##class(%DynamicArray).%FromJSON(json)
        If arr = "" { Return "" }
        If arr.%IsA("%DynamicArray") = 0 {
            // Not an array (it's an object): TypeError
            Throw ##class(%Exception.General).%New("TypeError","5001",,"Cannot use list subscript on non-list value")
        }
        // Negative index: wrap around
        If intIdx < 0 {
            Set intIdx = arr.%Size() + intIdx
        }
        If intIdx < 0 || intIdx >= arr.%Size() { Return "" }
        Return arr.%Get(intIdx)
    } Catch e {
        Throw e
    }
}

/// Always raises a TypeError. Used to signal type errors at SQL execution time.
ClassMethod IVGTYPEERROR(msg As %String = "TypeError") As %String [ SqlProc ]
{
    Throw ##class(%Exception.General).%New("TypeError","5001",,msg)
}

/// Map property access by dynamic key. Returns NULL for missing keys.
ClassMethod IVGMAPGET(json As %String = "", key As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "" }
    Try {
        Set obj = ##class(%DynamicObject).%FromJSON(json)
        If obj = "" { Return "" }
        Return obj.%Get(key)
    } Catch { Return "" }
}

/// Set a key in a JSON object, returning the updated JSON string.
/// Creates a new empty object if json is null/empty.
/// Numeric values are stored as numbers; others as strings.
ClassMethod IVGJSONSET(json As %String = "", key As %String = "", val As %String = "") As %String [ SqlProc ]
{
    Try {
        If (json = "") || (json = "null") {
            Set obj = ##class(%DynamicObject).%New()
        } Else {
            Set obj = ##class(%DynamicObject).%FromJSON(json)
            If '$IsObject(obj) { Set obj = ##class(%DynamicObject).%New() }
        }
        If $IsValidNum(val) {
            Set obj = obj.%Set(key, +val)
        } Else {
            Set obj = obj.%Set(key, val)
        }
        Return obj.%ToJSON()
    } Catch { Return json }
}

/// Remove a key from a JSON object, returning the updated JSON string.
ClassMethod IVGJSONREMOVE(json As %String = "", key As %String = "") As %String [ SqlProc ]
{
    If (json = "") || (json = "null") { Return "{}" }
    Try {
        Set obj = ##class(%DynamicObject).%FromJSON(json)
        If obj = "" { Return "{}" }
        Do obj.%Remove(key)
        Return obj.%ToJSON()
    } Catch { Return json }
}

/// Generate a Cypher range [start, end] as a JSON array. Step defaults to 1.
ClassMethod IVGRANGE(startVal As %Integer = 0, endVal As %Integer = 0, stepVal As %Integer = 1) As %String [ SqlProc ]
{
    If stepVal = 0 { Return "[]" }
    Set arr = ##class(%DynamicArray).%New()
    If stepVal > 0 {
        For i = startVal:stepVal:endVal { Do arr.%Push(i) }
    } Else {
        Set i = startVal
        While i >= endVal { Do arr.%Push(i)  Set i = i + stepVal }
    }
    Return arr.%ToJSON()
}

}
