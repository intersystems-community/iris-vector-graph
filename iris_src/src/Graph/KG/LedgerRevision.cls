/// Spec 213 — one immutable row per committed revision (Graph_KG.ledger_revisions).
/// Written only by Graph.KG.Ledger.Commit inside the commit transaction.
/// UPDATE and DELETE are rejected by triggers and %OnBeforeSave (FR-023).
/// Test/admin reset goes through Graph.KG.Ledger.PurgeAll (%KillExtent bypasses triggers).
Class Graph.KG.LedgerRevision Extends %Persistent [ DdlAllowed, Final, SqlTableName = ledger_revisions ]
{

Property Seq As %Integer [ Required, SqlFieldName = seq ];

Property RevisionId As %String(MAXLEN = 32) [ Required, SqlFieldName = revision_id ];

Property ParentId As %String(MAXLEN = 32) [ SqlFieldName = parent_id ];

Property Kind As %String(MAXLEN = 16) [ Required, SqlFieldName = kind ];

Property Actor As %String(MAXLEN = 256) [ Required, SqlFieldName = actor ];

Property ActorType As %String(MAXLEN = 32) [ Required, SqlFieldName = actor_type ];

Property ConnUser As %String(MAXLEN = 128) [ SqlFieldName = conn_user ];

Property CommittedMs As %Integer [ Required, SqlFieldName = committed_ms ];

Property Message As %String(MAXLEN = 4000) [ SqlFieldName = message ];

Property Source As %String(MAXLEN = 4000) [ SqlFieldName = source ];

Property CorrelationId As %String(MAXLEN = 256) [ SqlFieldName = correlation_id ];

Property IdempotencyKey As %String(MAXLEN = 256) [ SqlFieldName = idempotency_key ];

Property OpCount As %Integer [ SqlFieldName = op_count ];

Property StmtFirst As %Integer [ SqlFieldName = stmt_first ];

Property StmtLast As %Integer [ SqlFieldName = stmt_last ];

Index SeqIdx On Seq [ IdKey, Unique ];

Index RevisionIdIdx On RevisionId [ Unique ];

Index ActorIdx On Actor;

Index ActorTypeIdx On ActorType;

Index CommittedIdx On CommittedMs;

/// Reject in-place modification through the SQL path (FR-023).
Trigger RejectUpdate [ Event = UPDATE, Foreach = row/object ]
{
    Set %ok = 0
    Set %msg = "Graph_KG.ledger_revisions is immutable (spec 213 FR-023)"
}

/// Reject deletion through the SQL path (FR-023).
Trigger RejectDelete [ Event = DELETE, Foreach = row/object ]
{
    Set %ok = 0
    Set %msg = "Graph_KG.ledger_revisions is immutable (spec 213 FR-023)"
}

/// Reject re-save of an existing object through the object path (FR-023).
Method %OnBeforeSave(insert As %Boolean) As %Status [ Private, ServerOnly = 1 ]
{
    If 'insert {
        Return $$$ERROR($$$GeneralError, "Graph_KG.ledger_revisions is immutable (spec 213 FR-023)")
    }
    Return $$$OK
}

/// Reject object deletion (FR-023).
ClassMethod %OnDelete(oid As %ObjectIdentity) As %Status [ Private, ServerOnly = 1 ]
{
    Return $$$ERROR($$$GeneralError, "Graph_KG.ledger_revisions is immutable (spec 213 FR-023)")
}

}
