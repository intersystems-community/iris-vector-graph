<!-- markdownlint-disable MD013 -->

# Feature Specification: Redacting a tenant from the revision ledger

**Feature Branch**: not created (draft)
**Created**: 2026-10-22
**Status**: Draft, decisions recorded
**Input**: opsreview's request 3 of 5 (agent bus, 2026-10-20; priorities restated 2026-10-22 on Tom's go-ahead). opsreview's
tenant erasure (opsreview spec 055, `research.md` "Gap found 2026-10-12") has to report `^IVG.Ledger("rec")` and
`Graph_KG.ledger_revisions` as outside its reach. `Graph.KG.Eraser` removes a graph's live content and its live
statement and tuple indexes, but leaves the operation records by design (ADR-0004), so the erased tenant's node ids,
property values and commit messages can still be read, and replayed, from the history. opsreview asked for "a ledger
operation that redacts or removes a Tenant's revisions and keeps the chain verifiable (a tombstoned revision with its
hash kept)".

Context that changed the priority: opsreview now keeps its Event Store in a flat SQL table (its ADR 0006 / spec 056).
IVG becomes a derived, rebuildable correlation index with one named graph per tenant. Tenant erasure is then
`EraseGraph` plus this redaction.

## What the code does today

Read 2026-10-22, from `Graph.KG.Ledger`, `Graph.KG.LedgerRevision`, `Graph.KG.Eraser`, `iris_vector_graph/ledger/`
and ADR-0004. These are claims to prove with the first tests:

- **Records**: `^IVG.Ledger("rec", seq, ordinal) = $LB(op, entityKind, entityId, attr, priorEnc, newEnc, graph,
  flags)`. The graph is per record, so one revision can touch several graphs (ADR-0004's reason for keeping `rec`).
- **Revision metadata**: `Graph_KG.ledger_revisions` holds actor, message, source, correlation id and idempotency key
  per revision. A trigger refuses any UPDATE or DELETE (spec 213 FR-023).
- **"Verifiable"** means `ledger.verify()`: replay every record from seq 1 to the head and compare the reconstructed
  state with the live store. There is no hash chain over records. The per-commit `fingerprint` in
  `^IVG.Ledger("idem")` is for idempotent retries, not integrity.
- **After `EraseGraph(g)`**: the live rows and `stmt`/`tuple` entries for `g` are gone, and the records remain. Replay
  rebuilds `g`'s content, so whether `verify()` still reports `equal` after an erase is a claim to test first. ADR-0004
  says "no live statement outlives its content", not "replay equals live".
- **Other copies**: `export.py` writes records out, and journals and backups hold them. Those are out of reach here,
  as opsreview's receipt already says for backups.

## User Scenarios & Testing

### User Story 1 - A tenant's history can be redacted (Priority: P1)

An operator erases a tenant (`EraseGraph(tenantGraph)`) and then redacts the tenant from the ledger. Afterwards no
record, revision field or index reachable through IVG holds the tenant's entity ids, attribute values or messages. A
redaction receipt says what was redacted, from which revisions, and what stayed out of reach.

**Why this priority**: it is what opsreview's spec 055 receipt cannot claim today, and it is the whole of the request.

**Independent Test**: commit revisions for two tenant graphs (`t1`, `t2`) and one revision that touches both. Erase and
redact `t1`. Search every ledger global, every `ledger_*` table and every reader (`history`, `records`, `export`,
`diff`) for `t1`'s ids and values; expect none. Check `t2` is unchanged.

**Acceptance Scenarios**:

1. **Given** revisions wholly in `t1`, **When** `t1` is redacted, **Then** each of their records is a tombstone, and
   their revision metadata (message, source, correlation id, idempotency key) is cleared, while seq, revision id,
   parent, kind and commit time stay.
2. **Given** a revision touching `t1` and `t2`, **Then** only its `t1` records become tombstones. Its `t2` records and
   their ordinals are unchanged. Its metadata is kept which Decision 2 clears.
3. **Given** a redacted tenant, **When** the same redaction runs again, **Then** it changes nothing and reports 0.

---

### User Story 2 - The ledger stays verifiable after a redaction (Priority: P1)

After erase and redaction, `ledger.verify()` reports `equal`, and replay never fails on a tombstone. Each tombstone
keeps a digest of the record it replaced. An auditor holding a copy of the original record can prove it was the one
redacted, and nobody can recover the content from the digest.

**Why this priority**: a redaction that breaks `verify()` would make every later verification report a divergence,
which makes verification useless for every tenant.

**Independent Test**: after Story 1's redaction, run `verify()`. Replay to every seq. Check each tombstone's digest
against the canonical form of the original record captured before the redaction.

**Acceptance Scenarios**:

1. **Given** a redacted and erased tenant, **Then** `verify()` reports `equal`, with no differences.
2. **Given** a tombstone, **When** replay meets it, **Then** it contributes nothing to the reconstructed state.
3. **Given** the original record's canonical JSON (`Ledger.CanonicalRecord`), **Then** its digest equals the
   tombstone's.

---

### User Story 3 - Redaction refuses to leave the ledger and the store inconsistent (Priority: P2)

Redaction is refused while the tenant's content is still live, because replay without the tenant's records would no
longer match the store. A clear error names the live graph. It is also refused while the ledger is disabled or a commit
is in flight; it takes the head lock like a commit.

**Why this priority**: a redaction of live content is the inconsistency ADR-0004 exists to prevent.

**Independent Test**: redact a graph that still holds nodes. Expect a refusal and no change to any store, proven by a
before/after snapshot.

---

### Edge Cases

- A tenant in the default graph (opsreview's spec 042 revision store wrote `Revision:<tenant>|...` nodes there): redact
  by entity-id prefix within a graph as well as by graph. Prefixes must not over-match (`acme` versus `acme-health`), so
  a prefix is matched with its separator.
- A statement created in `t1` and deleted later: both its create and delete records are redacted. `stmt` entries for
  deleted statements must be redacted too.
- An idempotency key derived from tenant data: `^IVG.Ledger("idem", key)` is keyed by it, so the entry is removed or
  re-keyed by digest. A retried commit with that key after redaction must not replay into an erased graph.
- `LedgerStats` counters stay right (records, revisions), and the revision count does not change.
- Very large tenants: redaction runs in bounded batches, with the in-progress marker of Decision 3.
- `ledger.export()` taken before the redaction still holds the content. The receipt says so.

## Requirements

### Functional Requirements

- **FR-001**: A redaction operation (`Graph.KG.LedgerRedact.Redact(graph, prefix)`, and `ledger.redact(graph,
  prefix=None)` in Python) MUST replace every record whose graph is `graph` (and whose entity id starts with
  `prefix + separator`, when a prefix is given) with a tombstone.
- **FR-002**: A tombstone keeps seq, ordinal and op kind, and holds a digest (SHA-256) of the replaced record's
  canonical JSON. It holds no entity id, attribute name or value.
- **FR-003**: Replay and every reader (`history`, `records`, `diff`, `export`) MUST treat a tombstone as contributing
  nothing, and MUST mark it as redacted in their output rather than hiding the ordinal.
- **FR-004**: For a revision whose every record is redacted, `message`, `source`, `correlation_id` and
  `idempotency_key` MUST be cleared, through a path that is the only exception to the immutability trigger and that
  records itself (Decision 1).
- **FR-005**: Redaction MUST refuse while any non-tombstoned live statement or node of the target remains (Story 3).
- **FR-006**: Redaction MUST take the ledger head lock, run in one transaction per batch, and be idempotent.
- **FR-007**: `verify()` MUST report `equal` after an erase followed by a redaction of the same target.
- **FR-008**: The operation returns a receipt: revisions touched, records tombstoned, metadata cleared, and what stays
  out of reach (exports, journals, backups).
- **FR-009**: The redaction itself is recorded: who, when, what target, which revisions, with no tenant content. This
  is a content-free ledger revision of kind `redaction` (Decision 1).

### Key Entities

- **Tombstone**: a record replaced by redaction: `$LB("redacted", digest, flags)` in the original `(seq, ordinal)`.
- **Redaction**: one run of FR-001 over a target (graph, optional prefix), with its receipt and audit entry.
- **Receipt**: the counts and reach statement of FR-008, in the shape of opsreview's erasure receipt.

## Success Criteria

### Measurable Outcomes

- **SC-001**: After erase and redaction of one of two tenants, a scan of every `^IVG.Ledger` subscript and value and
  every `Graph_KG.ledger_*` row finds none of the tenant's ids or values. Zero matches is the bar.
- **SC-002**: `verify()` reports `equal` after the redaction, with 10,000 revisions over 10 tenants and 5% of
  revisions spanning two tenants.
- **SC-003**: Redacting a tenant with 100,000 records takes under 60 s on the laptop's enterprise container and blocks
  commits for under 1 s at a time (batching).
- **SC-004**: The other tenants' records, revision rows and `verify()` result are byte-identical before and after.

## Decisions (Tom, 2026-10-22)

1. **The redaction's audit** (formerly open question 1): (b), a ledger revision of kind `redaction` that names the
   redacted revisions by seq and holds no content, if it can be kept content-free. The plan must show it is: no entity
   id, attribute, value or free text from the target.
2. **A revision spanning tenants** (formerly 2): its metadata is cleared when any of its records is redacted.
3. **Batches** (formerly 3): with a redaction-in-progress marker that readers and `verify()` honour.
4. **ADR** (formerly 4): a new ADR-0013, "redaction removes content from history", that ADR-0004 links to.

## Not in this spec

Exports, journals and backups (outside IVG's reach; the receipt names them). Event identity is spec 254, and the window
reader spec 255. A tenant-scoped erase of the temporal tiers inside a shared graph (request 2) is now lower priority:
with one graph per tenant, `EraseGraph` covers it. Journal volume and write stalls (request 5) need diagnosis first.
