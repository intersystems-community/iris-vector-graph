/// IVG's own part of a write, nested in whatever transaction the process already has (4.6.4).
///
/// Python's iris_vector_graph._write_txn calls these through the IVG Schema's SQL functions Txn_Begin, Txn_End and
/// Txn_Undo (Graph.KG.SqlProcs), on the cursor that writes: a SQL function runs in the connection's own server
/// process, where the caller's transaction is. The Native API may be on another connection.
///
/// Measured on IRIS 2026.3 over DB-API: SQL START TRANSACTION inside a transaction does nothing, and COMMIT and
/// ROLLBACK end every level; SAVEPOINT raises $TLEVEL and ROLLBACK TO SAVEPOINT takes it back, but SQL has no
/// RELEASE SAVEPOINT and a SAVEPOINT that is never rolled back to cost ~2.5 ms a call. TSTART, TCOMMIT and
/// TROLLBACK 1 nest: a TCOMMIT inside another level only lowers $TLEVEL, and TROLLBACK 1 takes back that level's SQL
/// rows and globals alone. These three are those.
Class Graph.KG.Txn Extends %RegisteredObject [ Final ]
{

/// Open IVG's level and answer its $TLEVEL: 1 when no transaction was open (IVG's write is its own and commits at
/// Txn_End), more when the caller's transaction is under it. pHold is 1 for a connection with the driver's
/// autocommit off, whose transaction starts only at its first write: a level is opened for the caller first, which
/// the driver's commit or rollback ends, so IVG's write is the caller's even when it is the first one.
ClassMethod Begin(pHold As %Integer = 0) As %Integer
{
    If pHold, $TLevel = 0 {
        TSTART
    }
    TSTART
    Quit $TLevel
}

/// Commit IVG's level pLevel (and any level a class it called left open above it) and answer $TLEVEL after. At
/// level 1 that commits the write; above, it only lowers $TLEVEL to the caller's. A $TLEVEL already below pLevel
/// (something ended IVG's level) is left as it is, and the caller reads that from the answer.
ClassMethod End(pLevel As %Integer = 0) As %Integer
{
    If pLevel < 1 {
        Quit $TLevel
    }
    While $TLevel >= pLevel {
        TCOMMIT
    }
    Quit $TLevel
}

/// Take back IVG's level pLevel and anything above it, never a level below: the caller's work stays. Answers
/// $TLEVEL after.
ClassMethod Undo(pLevel As %Integer = 0) As %Integer
{
    If pLevel < 1 {
        Quit $TLevel
    }
    While $TLevel >= pLevel {
        TROLLBACK 1
    }
    Quit $TLevel
}

}
