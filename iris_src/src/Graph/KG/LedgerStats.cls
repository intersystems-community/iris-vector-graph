/// Spec 213 — per-ledger counters as a queryable table (Graph_KG.ledger_stats, FR-051).
/// Exactly one row (ID = 1). Successful-commit counters are updated inside the commit
/// transaction; rejection counters are bumped immediately after rollback via Bump().
Class Graph.KG.LedgerStats Extends %Persistent [ DdlAllowed, Final, SqlTableName = ledger_stats ]
{

Property LedgerName As %String(MAXLEN = 32) [ InitialExpression = "default", Required, SqlFieldName = ledger ];

Property State As %String(MAXLEN = 16) [ InitialExpression = "never-enabled", SqlFieldName = state ];

Property Strict As %Boolean [ InitialExpression = 0, SqlFieldName = strict ];

Property HeadSeq As %Integer [ InitialExpression = 0, SqlFieldName = head_seq ];

Property CommitsOk As %Integer [ InitialExpression = 0, SqlFieldName = commits_ok ];

Property RejStaleHead As %Integer [ InitialExpression = 0, SqlFieldName = rej_stale_head ];

Property RejUnknownHead As %Integer [ InitialExpression = 0, SqlFieldName = rej_unknown_head ];

Property RejIdemConflict As %Integer [ InitialExpression = 0, SqlFieldName = rej_idem_conflict ];

Property ReplaysIdem As %Integer [ InitialExpression = 0, SqlFieldName = replays_idem ];

Property RejFailedOp As %Integer [ InitialExpression = 0, SqlFieldName = rej_failed_op ];

Property RejStrictBlock As %Integer [ InitialExpression = 0, SqlFieldName = rej_strict_block ];

Property RejSizeLimit As %Integer [ InitialExpression = 0, SqlFieldName = rej_size_limit ];

Property RejDisabled As %Integer [ InitialExpression = 0, SqlFieldName = rej_disabled ];

Property RejLockTimeout As %Integer [ InitialExpression = 0, SqlFieldName = rej_lock_timeout ];

Property LastVerifyMs As %Integer [ SqlFieldName = last_verify_ms ];

Property LastVerifyResult As %String(MAXLEN = 32) [ SqlFieldName = last_verify_result ];

Property EnabledMs As %Integer [ SqlFieldName = enabled_ms ];

Property DisabledMs As %Integer [ SqlFieldName = disabled_ms ];

Index LedgerIdx On LedgerName [ IdKey, Unique ];

/// Return the singleton row, creating it if absent. Caller decides transaction scope.
ClassMethod Ensure() As Graph.KG.LedgerStats
{
    Set tObj = ..%OpenId("default")
    If '$IsObject(tObj) {
        Set tObj = ..%New()
        Set tObj.LedgerName = "default"
        Set tSC = tObj.%Save()
        Set tObj = ..%OpenId("default")
    }
    Return tObj
}

/// Increment one counter in its own small transaction. pKind is a Python-side
/// column name (e.g. "rej_stale_head"); unknown names are ignored.
ClassMethod Bump(pKind As %String) As %Status
{
    Set tSC = $$$OK
    Set tProp = ..PropertyFor(pKind)
    If tProp = "" Quit tSC
    Try {
        TSTART
        Set tObj = ..Ensure()
        If $IsObject(tObj) {
            Set $Property(tObj, tProp) = $Property(tObj, tProp) + 1
            Set tSC = tObj.%Save()
        }
        If $$$ISERR(tSC) {
            TROLLBACK
        } Else {
            TCOMMIT
        }
    } Catch ex {
        If $TLevel > 0 { TROLLBACK }
        Set tSC = ex.AsStatus()
    }
    Quit tSC
}

/// Map a snake_case column name to the property name; "" when unknown.
ClassMethod PropertyFor(pKind As %String) As %String
{
    Set tMap("commits_ok") = "CommitsOk"
    Set tMap("rej_stale_head") = "RejStaleHead"
    Set tMap("rej_unknown_head") = "RejUnknownHead"
    Set tMap("rej_idem_conflict") = "RejIdemConflict"
    Set tMap("replays_idem") = "ReplaysIdem"
    Set tMap("rej_failed_op") = "RejFailedOp"
    Set tMap("rej_strict_block") = "RejStrictBlock"
    Set tMap("rej_size_limit") = "RejSizeLimit"
    Set tMap("rej_disabled") = "RejDisabled"
    Set tMap("rej_lock_timeout") = "RejLockTimeout"
    Return $Get(tMap(pKind), "")
}

}
