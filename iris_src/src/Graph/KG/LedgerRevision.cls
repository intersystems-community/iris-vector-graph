/// Spec 213 — one immutable row per committed revision (Graph_KG.ledger_revisions).
/// Written only by Graph.KG.Ledger.Commit inside the commit transaction.
/// UPDATE and DELETE are rejected by triggers and %OnBeforeSave (FR-023), with the one
/// spec 256 exception documented on RejectUpdate (ADR-0013).
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
///
/// One exception, and only one (spec 256, ADR-0013): Graph.KG.LedgerRedact.ClearRevision
/// may empty message, source, correlation_id and idempotency_key of a revision that has a
/// redacted record. It is let through only when all of these hold: %IVGLedgerRedaction
/// names this row's seq (LedgerRedact sets it around that one UPDATE and nothing else
/// sets it), ^IVG.Ledger("redactedRev", seq) exists (written in the same transaction,
/// after a record of the revision became a tombstone), no other column changes, and all
/// four new values are empty. The exception records itself in redactedRev and in the
/// redaction revision that names the seq. The object path and DELETE stay closed.
Trigger RejectUpdate [ Event = UPDATE, Foreach = row/object ]
{
    // Field references use the SQL field names.
    Set tRedaction = ($Get(%IVGLedgerRedaction) '= "") && ($Get(%IVGLedgerRedaction) = {seq}) && ($Data(^IVG.Ledger("redactedRev", {seq})) > 0)
    If tRedaction {
        Set tRedaction = ('{seq*C}) && ('{revision_id*C}) && ('{parent_id*C}) && ('{kind*C}) && ('{actor*C}) && ('{actor_type*C}) && ('{conn_user*C}) && ('{committed_ms*C}) && ('{op_count*C}) && ('{stmt_first*C}) && ('{stmt_last*C})
    }
    If tRedaction {
        Set tRedaction = ({message*N} = "") && ({source*N} = "") && ({correlation_id*N} = "") && ({idempotency_key*N} = "")
    }
    If 'tRedaction {
        Set %ok = 0
        Set %msg = "Graph_KG.ledger_revisions is immutable (spec 213 FR-023)"
    }
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
