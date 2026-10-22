<!-- markdownlint-disable MD013 -->

# Plan: Redacting a tenant from the revision ledger (spec 256)

**Branch**: worktree off `alt-parity` | **ADR**: [ADR-0013](../../docs/adr/0013-redaction-removes-content-from-history.md)

## What the code showed (claims checked before design)

- **Node records carry no graph.** `LedgerApply.OpCreateNode` inserts into the default graph and every node record (`create_node`, `add_label`, `set_prop`, `remove_*`, `delete_node`) has graph `""`. Only relationship, qualifier and temporal-edge records carry a named graph. A tenant's node records are therefore reachable only by entity-id prefix in the default graph, never by its graph name. The full tenant erasure is two targets: `redact(tenantGraph)` and `redact(None, prefix=tenant)`.
- **`verify()` compares nodes graph-blind** (`read_canonical` collapses `node_id` across graphs). The node-liveness check for a default-graph target is graph-blind for the same reason.
- **`verify()` after `EraseGraph` alone diverges**: replay rebuilds the erased relationships. Redaction is what makes erase-then-verify `equal` again.
- `Eraser.EraseGraph` already kills every `("stmt")` entry of the graph (live and deleted); `EraseNodes` kills only the live ones it reaches.
- `idx_ledger_correlation` indexes `correlation_id`, so metadata is cleared through SQL `UPDATE` (index maintained), not a storage-global write.

## Design

### Server: `Graph.KG.LedgerRedact`

`Redact(pGraph, pPrefix, pSeparator="|", pActor, pBatch=2000, pMaxBatches=0)` returns the receipt JSON (or `Ledger.Err` JSON).

1. **Refusals, before anything is written**: `transaction_open`, `not_enabled`, `disabled`, `invalid_target` (bad graph name, empty separator), `redaction_in_progress` (a marker for a different target), `target_live` (live rows of the target in `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`).
2. **Target**: graph name (`GraphKey.ForName`) plus an optional match string `prefix _ separator` (not doubled when the prefix already ends with it). A record matches when its graph is the target graph and, with a prefix, a node record's id, or a relationship's source or target, starts with the match string. Relationship endpoints come from the tuple JSON of `create_rel`/`delete_rel`/`create_temporal_edge` and, for qualifier records, from a pre-pass over those records, the live statement map, and the statements tombstoned by this redaction.
3. **Batches**: each batch is `TSTART` + head lock, at most `pBatch` tombstones (and a scan cap), then `TCOMMIT` and unlock. The first tombstone sets the marker `^IVG.Ledger("redacting")`.
4. **Tombstone**: `^IVG.Ledger("rec", seq, ordinal) = $LB("redacted", sha256(CanonicalRecord), flags, op, entityKind)`. No id, attribute, value or graph.
5. **First tombstone in a revision** (Decision 2): remove `^IVG.Ledger("idem", key)` and keep `^IVG.Ledger("idemRedacted", sha256(key)) = seq`; set `^IVG.Ledger("redactedRev", seq)`; clear `message`, `source`, `correlation_id`, `idempotency_key` by SQL `UPDATE`.
6. **Final step** (own transaction, under the lock): re-check liveness, kill the target's `("stmt")`/`("tuple")` entries, write the `redaction` revision, kill the marker.
7. **Commit while a marker exists**: `CommitInTxn` refuses a changeset with a record that matches the marker's target (`redaction_in_progress`). A commit whose key is in `idemRedacted` is refused (`idempotency_redacted`), so a retry never replays into, or re-creates, an erased graph.

### The one trigger exemption

`LedgerRevision.RejectUpdate` lets an `UPDATE` through only when all hold: `%IVGLedgerRedaction = {Seq}` (set by `LedgerRedact` around its one statement), `^IVG.Ledger("redactedRev", {Seq})` exists, no column other than the four changed, and all four new values are empty. `%OnBeforeSave`, `%OnDelete` and `RejectDelete` are unchanged. The exemption records itself in `redactedRev` and in the `redaction` revision.

### The `redaction` revision is content-free

- kind `redaction`, actor (default `system:ledger-redact`), actor type `system`.
- message `redaction: N record(s) in M revision(s)` (counts only).
- source `{"target_sha256": sha256(graph NUL match), "records": N, "revisions": M, "statements": S}`.
- one record per redacted revision: `$LB("redact", "revision", seq, "", "N", "S" _ count, "", "redaction")`.

Nothing from the target appears: no graph name, prefix, id, attribute, value or free text. A test scans the revision for the tenant's strings.

### Readers (FR-003, Decision 3)

- `ledger_records` gains `digest` and `redacted_op` columns. A tombstone row is `op = "redacted"`, empty id, attribute and graph, both values null.
- `MutationRecord.redacted`; `GraphState.apply`, `compute_diff` and `reconstruct` skip tombstones and `revision` records. `Diff.redacted` lists the `(seq, ordinal)` of tombstones in range.
- While a marker exists, `get_revision`, `diff`, `reconstruct` and `export_reconstruction` raise `RedactionInProgressError`, and `verify()` returns `result="redaction_in_progress"`, with no adoption.

### Python

`ledger.redact(graph, prefix=None, *, separator="|", actor=..., batch_size=2000, max_batches=0) -> RedactionReceipt`. New errors: `RedactionTargetLiveError`, `RedactionInProgressError`, `InvalidRedactionTargetError`, and `IdempotencyRedactedError` (a subclass of `IdempotencyConflictError`).

## Tests (written first)

- **Unit** (`tests/unit/test_256_ledger_redaction.py`): row decoding (12 and 14 columns), replay/diff skip tombstones, receipt and error mapping, readers honour the marker, and a source check that the exemption is narrow and set only by `LedgerRedact`.
- **E2E** (`tests/integration/test_256_ledger_redaction_e2e.py`), IVGSEC on `ivg-iris-enterprise`:
  - US1: two tenants and a spanning revision; a full scan of `^IVG.Ledger` and the `ledger_*` tables finds no `tenantA` strings; `tenantB` is untouched; a second run is a no-op; the default graph is addressed by prefix (`acme|` is not `acme-health|`).
  - US2: `verify()` is `equal`; digests match; replay to every seq works.
  - US3: refusals, each proven by a whole-store before/after snapshot; paused batches with the marker.
- **Perf** (`-m perf`): SC-002 (10,000 revisions over 10 tenants, 5% spanning) and SC-003 (100,000 records, total time and longest batch).
