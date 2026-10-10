/// User.SQL — legacy helper class kept for non-SQL ObjectScript use.
/// SQL UDFs have moved to User.CypherFn (IVG-prefixed names avoid 2026.3 reserved keywords).
Class User.SQL Extends %RegisteredObject
{

/// Regex match — non-SQL helper method (SQL version: SQLUser.CypherFn_IVGREGEXMATCH)
ClassMethod REGEXMATCH(str As %String = "", pattern As %String = "") As %Integer
{
    If (str = "") || (pattern = "") {
        Return 0
    }

    Try {
        Set compiled = ##class(%Regex.Matcher).%New(pattern)
        Set result = compiled.Match(str)
        Return $Select(result: 1, 1: 0)
    } Catch ex {
        Return 0
    }
}

}
