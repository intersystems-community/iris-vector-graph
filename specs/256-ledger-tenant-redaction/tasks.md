<!-- markdownlint-disable MD013 -->

# Tasks: spec 256 ledger tenant redaction

Tests come first in every phase. A phase is done when its E2E test passes.

## Phase 0: Docs

- [x] T001 `plan.md`, `tasks.md`
- [x] T002 ADR-0013 "Redaction removes content from history"; link from ADR-0004

## Phase 1: Tests (fail first)

- [x] T010 Unit: `MutationRecord` decodes tombstone rows (14 columns) and legacy rows (12)
- [x] T011 Unit: `GraphState.apply`, `reconstruct`, `compute_diff` skip tombstones and `revision` records; `Diff.redacted`
- [x] T012 Unit: `ledger.redact` arguments, `RedactionReceipt`, error-code mapping
- [x] T013 Unit: readers raise and `verify()` reports `redaction_in_progress` while the marker exists
- [x] T014 Unit: the trigger exemption is narrow and `%IVGLedgerRedaction` is set only in `LedgerRedact.cls`
- [x] T020 E2E US1: two tenants, a spanning revision, a whole-store scan, tenant B untouched, idempotent, default-graph prefix with separator
- [x] T021 E2E US2: `verify()` diverges after erase alone, is `equal` after redaction; digest equals SHA-256 of `CanonicalRecord`; replay to every seq
- [x] T022 E2E US3: refusal while live, disabled or another redaction is in progress, each with a before/after snapshot of every store
- [x] T023 E2E: paused batches; commits that touch the target are refused, others commit; resuming completes
- [x] T024 E2E: the redaction revision is content-free; an idempotency-key retry is refused
- [x] T025 Perf (`-m perf`): SC-002 and SC-003

## Phase 2: Implementation

- [x] T030 `Graph.KG.LedgerRedact` (target, liveness, batches, tombstones, metadata, final revision, receipt)
- [x] T031 `LedgerRevision.RejectUpdate` exemption
- [x] T032 `Ledger.Records` tombstone columns; `CanonicalRecord` for tombstones; `CommitInTxn` marker and `idemRedacted` checks
- [x] T033 Python: `replay.py`, `client.py` (`redact`, marker checks), `errors.py`, exports

## Phase 3: Gate

- [x] T040 Deploy to IVGSEC; `tests/unit`; ledger and eraser E2E; spec 256 E2E; perf measurements
