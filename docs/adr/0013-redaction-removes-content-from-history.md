<!-- markdownlint-disable MD013 -->

# ADR-0013 — Redaction removes content from history

**Status**: Accepted
**Date**: 2026-10-22
**Specs**: 256 (ledger tenant redaction), 213 (revision ledger)
**Refines**: ADR-0004 (erasure removes content, not history)

## Decision

`Graph.KG.LedgerRedact.Redact(graph, prefix)` (`ledger.redact(graph, prefix=None)` in Python) removes a target's content from `^IVG.Ledger("rec")` and from `Graph_KG.ledger_revisions`. It keeps the history's shape. Every seq, revision id, parent, kind, commit time and record ordinal stays where it was.

- **A record becomes a tombstone** in its own `(seq, ordinal)`: `$LB("redacted", digest, flags, op, entityKind)`. The digest is SHA-256 of the record's `Ledger.CanonicalRecord` JSON. A tombstone holds no entity id, attribute, value or graph. Replay, diff and reconstruction skip it. The record readers show it as `op = "redacted"` rather than hiding the ordinal.
- **A revision with any redacted record loses its free-text metadata**: `message`, `source`, `correlation_id`, `idempotency_key`. Its idempotency binding moves to `^IVG.Ledger("idemRedacted", sha256(key))`, and a later commit with that key is refused (`idempotency_redacted`). A retry cannot replay into, or re-create, an erased tenant.
- **The redaction is itself a ledger revision**, of kind `redaction`. It names the redacted revisions by seq, one `redact` record each, and holds counts and `sha256(graph NUL match)` instead of the target's name. Nothing in it comes from the target.
- **Redaction only follows erasure.** It is refused while any row of the target is live, because replay without the target's records would no longer match the store. The order is `EraseGraph` (or `EraseNodes`) first, then `Redact`. After both, `verify()` reports `equal`. After the erase alone it does not, since replay rebuilds the erased relationships.
- **Batches.** Each batch is one transaction under the head lock. While a redaction runs, `^IVG.Ledger("redacting")` marks it. Record readers raise `RedactionInProgressError`, `verify()` returns `redaction_in_progress`, and a commit with a record in the target is refused. Re-running the same redaction resumes it. A redaction of a different target is refused until the first one finishes.

## The one exception to immutability

Spec 213 FR-023 makes `ledger_revisions` immutable through triggers and `%OnBeforeSave`. There is now exactly one exception. `RejectUpdate` lets an `UPDATE` through only when all of these hold:

1. `%IVGLedgerRedaction` equals the row's seq. `LedgerRedact` sets it around its single `UPDATE` statement, and nothing else sets it.
2. `^IVG.Ledger("redactedRev", seq)` exists. It is written in the same transaction, after a record of that revision has become a tombstone.
3. No column other than the four changes.
4. All four new values are empty.

The object path (`%OnBeforeSave`, `%OnDelete`) and `RejectDelete` are unchanged. The exception records itself twice: in `redactedRev` and in the `redaction` revision that lists the seq.

## Context

ADR-0004 kept `rec` on erasure because one revision's ordinals can belong to several graphs, and pruning by graph would punch holes in other graphs' history. A tombstone keeps that property. The ordinal stays and only its content goes, so other graphs' records in the same revision are untouched.

There is no hash chain over records (spec 213). "Verifiable" means replay equals the store. A tombstone contributes nothing to replay, and its target is gone from the store, so both sides lose the same content.

The ledger's node operations are graph-blind. `LedgerApply.OpCreateNode` writes into the default graph, and every node record has graph `""`. A tenant's node records can therefore only be addressed by entity-id prefix in the default graph. The prefix is matched with its separator (`acme|`), so `acme` never matches `acme-health|`. The full redaction of a tenant whose relationships live in its own graph is `redact(tenantGraph)` plus `redact(None, prefix=tenant)`.

## Alternatives considered

- **Delete the records and renumber.** Rejected. It breaks every stored seq and revision id reference, and every external copy of the history.
- **Keep the audit outside the ledger** (a separate table or log). Rejected by Decision 1 of spec 256: an audit outside the ledger is one more store to keep consistent.
- **A salted digest.** Rejected for now. An auditor holding the original record must be able to recompute the digest, so the digest has to be unsalted. See Consequences.

## Consequences

- Redaction is a privacy primitive and erasure is not (ADR-0004). Together they cover everything IVG can reach.
- **Out of reach**: `ledger.export_reconstruction` files written before the redaction, IRIS journals, and backups. Every receipt says so.
- An unsalted SHA-256 of a low-entropy record (a short id, a known schema) can be confirmed by guessing. The digest proves which record was redacted. It does not hide a guessable one.
- `actor`, `actor_type` and `conn_user` are kept. They identify who wrote the revision, not the tenant. A deployment that puts tenant names in the actor has to choose another actor.
- During a run, the marker holds the target in plain text, so that a concurrent commit can be checked against it. It is removed in the final transaction (journals still hold it).
