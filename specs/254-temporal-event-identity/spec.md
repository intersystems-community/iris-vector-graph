<!-- markdownlint-disable MD013 -->

# Feature Specification: Temporal event identity and idempotent writes

**Feature Branch**: not created (draft)
**Created**: 2026-10-21
**Status**: Draft, not filed
**Input**: opsreview's Event Store evaluation (2026-10-20, opsreview
`docs/wayfinder/mvp-gaps-se-and-mso/research/event-store-flat-sql-vs-ivg.md` § 5.2, go/no-go G1 and G8), sent over the
agent bus as request 1 of 5. IVG keys a temporal edge by `(graph, ts, source, predicate, target)`. Two events of the same
type on the same entity in the same millisecond become one, with no error: 22 of 3.46 M synthetic events at 80 tenants.
Putting the event id in the target avoids the collapse but writes one structural edge row and one `^KG("out")` entry
per event, and `PurgeRawBefore` never removes either. The flat SQL table gets `event_id` idempotency from its primary
key; IVG has nothing equivalent, so opsreview kept a separate dedup global (`^OpsReview.Monitor.EventDedup`, spec 033).

**Update 2026-10-22** (Tom's go-ahead, relayed from opsreview): opsreview keeps its Event Store in a flat SQL table
(`IrisEvents.Events`, append-only; its ADR 0006 / spec 056). IVG becomes a derived, rebuildable correlation index: one
named graph per tenant, written by an "Enrichment" process that reads the table from a RowID watermark and is idempotent
on `event_id`. It writes `ACTION_AT` / `FINDING_AT` temporal edges per event. Enrichment must be able to rebuild a graph
from zero and land exactly the same graph (opsreview's acceptance test 5 compares node and edge sets by hash), so a
same-millisecond collision breaks the rebuild. This is opsreview's priority 1, needed now. Retention (Story 3) matters
less, since the table is the store of record, but the temporal edges still age out.

## What the code does today

Read from `Graph.KG.TemporalIndex` (`WriteStores`, `BulkInsert`, `PurgeRawBefore`), 2026-10-21. These are claims to prove
with the first tests, not measurements:

- **Mode "insert" (the default)** writes a second event at existing coordinates over the first in `^KG("tout")` and
  `^KG("tin")`, but `$Increment`s `^KG("bucket")` and the `^KG("tagg")` count and sum again and folds it into the HLL
  sketch. Afterwards one raw edge remains and the bucket aggregates count two. `^KG("edgeprop")` is not cleared in this
  mode, so the stored attributes are the union of both events' keys, with the second event's values winning.
- **Mode "skip"** keeps the first write and leaves out the second. That makes a retry safe, but it also drops a
  distinct event at the same coordinates. The batch's returned count is the only sign.
- **Mode "update"** replaces the weight and attributes. The aggregates keep the first weight (spec 221 FR-004).
- **Every temporal edge asserts its structural row** (`Graph.KG.Adjacency.AssertTemporalRow`). Several timestamps of
  one `(s, p, o)` share that row. An event id in the target makes every event a new row in `rdf_edges` and a new
  `^KG("out")`/`("in")` entry.
- **`PurgeRawBefore`** removes `tout`, `tin` and `edgeprop` entries. It never removes structural rows or adjacency, even
  when no surviving timestamp holds that `(s, p, o)`.

## User Scenarios & Testing

### User Story 1 - Distinct events never merge (Priority: P1)

An event pipeline writes two events with different ids to the same entity, with the same type, in the same
millisecond. Both are stored. A window read returns both, each with its own weight and attributes, and the bucket
aggregates count two.

**Why this priority**: an event store that loses events silently fails G1 and G8. Nothing else in this spec matters
without it.

**Independent Test**: write two events at identical `(ts, s, p, o)` with ids `e1` and `e2` and different weights and
attributes. Read the window, the aggregate and each event's attributes.

**Acceptance Scenarios**:

1. **Given** two events with ids `e1` and `e2` at the same `(ts, s, p, o)`, **When** both are written in one batch,
   **Then** the batch reports 2 written, the window read returns 2 events, and `GetAggregate(..., "count")` is 2.
2. **Given** the same two events written in separate calls, **Then** the result is the same as in one batch.
3. **Given** `e1` with attributes `{a: 1}` and `e2` with `{b: 2}`, **Then** each event's attributes are its own, not
   the union.

---

### User Story 2 - A re-sent event is stored once (Priority: P1)

A consumer redelivers a batch after a timeout (at-least-once delivery). Events whose ids the graph already holds are
not written again. The batch's result says how many were new and how many were duplicates. Whatever the delivery
order, count in = count stored, by event id.

**Why this priority**: idempotency is G8. Today it needs a second store kept beside IVG, with its own retention.

**Independent Test**: write a batch of 1,000 events, then the same batch again, then a batch of 500 old and 500 new
events. Count the stored events, the aggregates and the duplicates reported.

**Acceptance Scenarios**:

1. **Given** a batch already written, **When** it is written again, **Then** the call reports 0 written and 1,000
   duplicates, and no store (`tout`, `tin`, `edgeprop`, `bucket`, `tagg`, HLL, structural rows) changes. Prove this with a
   before/after snapshot of every store.
2. **Given** an event id re-sent with a different timestamp or payload, **Then** it is reported as a duplicate, and
   the stored event is unchanged.
3. **Given** two graphs (two tenants), **Then** the same event id in each is two events. Event ids are unique within a
   graph.

---

### User Story 3 - Retention removes everything an event left (Priority: P1)

Retention purges events older than a cutoff. Afterwards nothing an expired event wrote remains: its raw entries, its
attributes, its dedup entry, and any structural row and adjacency that only expired events held up. The bucket
aggregates stay, as today.

**Why this priority**: a dedup index or a per-event structural row that outlives retention grows without bound, so
the store fails G7 over two years even if every read passes.

**Independent Test**: write events over two hours, purge the first hour, and compare every store with a reference
graph that only ever held the second hour, aggregates excepted.

**Acceptance Scenarios**:

1. **Given** events with ids, **When** `PurgeRawBefore` removes them, **Then** their ids are no longer known: a
   re-sent id is written as new. The re-sent case is a decision, see Open question 3.
2. **Given** an `(s, p, o)` whose every timestamp was purged, **Then** its structural row and its `^KG("out")`/`("in")`
   entries are gone, unless a non-temporal write also asserted that edge.
3. **Given** an `(s, p, o)` with timestamps on both sides of the cutoff, **Then** its structural row stays.

---

### User Story 4 - A rebuild from zero lands the same graph (Priority: P1)

Enrichment drops a tenant's graph and replays the event table into it, in RowID order, in batches whose boundaries
differ from the original run's. The rebuilt graph's nodes, edges, temporal entries, attributes and aggregates equal the
original's.

**Why this priority**: it is the property the derived-index design rests on, and opsreview's acceptance test 5.

**Independent Test**: write 100,000 events with ids (including same-millisecond, same-type pairs and re-sent ids) in
batches of 1,000; snapshot every store for the graph (canonical, sorted, hashed). Erase the graph, replay the same
events in batches of 777 and then in a shuffled order. Compare the hashes.

**Acceptance Scenarios**:

1. **Given** the same events in any batch size, **Then** the hashes are equal.
2. **Given** the same events in another order, **Then** the hashes are equal, aggregates included (an aggregate must
   not depend on arrival order; Open question 2 decides what "insert" does without ids).

---

### User Story 5 - Writes without an event id keep working (Priority: P2)

Existing callers write temporal edges without event ids and read them with the current readers. Nothing they see
changes, apart from the double-count defect (FR-008).

**Why this priority**: The temporal API has users today, opsreview's metric ingest among them. A
stored layout change must not break them, and a namespace with old data must stay readable without a migration.

**Independent Test**: the existing temporal unit and E2E suites pass unchanged. Old data written by the 4.x release
reads the same after the upgrade.

---

### Edge Cases

- An event id that is empty or absent: the edge is keyed by coordinates, as today.
- Two events with the same id in one batch: the first is written and the second counts as a duplicate.
- `DeleteTemporalEdge` by coordinates where several events share them: either remove all, or require the event id.
  See Open question 4.
- `EraseGraph`, `EraseNodes` and the Eraser's other paths must remove the dedup entries they reach, with a case per
  write and delete path (TESTING_POLICY: a new dimension needs a case per existing path).
- `suppressReverseIndex`: an event with an id and no `tin` entry is still found by id and still purged.
- Very long event ids: subscript limits apply. State the maximum, and refuse longer ids rather than truncating.

## Requirements

### Functional Requirements

- **FR-001**: A temporal write (`InsertEdge`, `BulkInsert`, `create_edge_temporal`, `bulk_create_edges_temporal`) MAY
  carry an event id. Two events with different ids are always stored as two, whatever their coordinates.
- **FR-002**: A write whose event id the graph already holds MUST change no store, and MUST be reported as a duplicate.
- **FR-003**: `BulkInsert` and `bulk_create_edges_temporal` MUST report written and duplicate counts separately. The
  existing integer result keeps meaning "written".
- **FR-004**: Window, inbound, attribute and export readers MUST return each event separately, with its event id when
  it has one.
- **FR-005**: Events with ids MUST NOT add structural rows or adjacency entries beyond the one per `(s, p, o)` that
  timestamps already share.
- **FR-006**: `PurgeRawBefore` MUST remove the dedup entries of the events it purges. It MUST also remove a structural
  row and its adjacency that only purged temporal edges asserted.
- **FR-007**: `EraseGraph`, `EraseNodes`, `DeleteTemporalEdge` and `EraseAll` MUST remove the dedup entries of what
  they remove.
- **FR-008**: A write in mode "insert" at coordinates already holding an event without an id MUST NOT leave the
  aggregates counting an event the raw store no longer holds. See Open question 2 for the choice.
- **FR-009**: Data written before this feature MUST read unchanged, with no migration step.
- **FR-010**: An event can be read by its id, at a cost independent of the graph's size.

### Key Entities

- **Event**: one temporal edge write. It has coordinates `(graph, ts, source, predicate, target)`, a weight,
  attributes and an optional event id unique within its graph.
- **Event id**: the caller's identifier, such as the envelope's `EventID`. IVG never generates one.
- **Dedup index**: maps event id to coordinates, per graph. It is bounded by retention and erasure, like the events.

## Success Criteria

### Measurable Outcomes

- **SC-001**: opsreview's 80-tenant load (3.46 M events, ids in the envelope) stores 3.46 M events. Count in = count
  stored by id. The 22 collisions are gone.
- **SC-002**: Re-sending the whole load writes 0 events and reports 3.46 M duplicates, and no store changes.
- **SC-003**: Bulk ingest with ids is within 15% of today's rate on the same box (29.5–33.2 k ev/s at 80 tenants).
  Journal bytes per event stay within 15% of today's 2.25–2.28 kB.
- **SC-004**: After purging the oldest hour, the dedup index, the structural rows and the adjacency hold nothing from
  that hour.

## Open questions

Each is for Tom, one at a time.

1. **Where the event id lives.** (a) As a level below the target in `tout`/`tin`/`edgeprop`. Every reader's depth
   changes, and `edgeprop`'s attribute level collides with it. (b) In a separate per-event store (`^KG("tev", g, ts, s,
   p, o, eid)`, with attributes beneath), while `tout` keeps one entry per coordinate as a summary. Old readers keep
   working, and readers that know about events expand them. (c) A dedup index only (`^KG("teid", g, eid)`), with
   colliding events kept in a per-coordinate sequence. Recommendation: (b). It leaves old data and old readers
   unchanged (FR-009). The cost is a second write per event, which SC-003 must absorb.
2. **The insert-mode double count without ids** (FR-008). Count the second write as a collision, so it is not written
   and not counted, and say so in the result? Or keep "last write wins" and stop counting it in the aggregates?
   Recommendation: report it as a collision. That is what "insert" promises, and the result already counts writes.
3. **The dedup window.** Should a purged event's id be accepted again (dedup bounded by retention), or remembered for
   a separate, longer window? Recommendation: bounded by retention. Redelivery happens within minutes, and a longer
   window is a store of its own.
4. **`DeleteTemporalEdge` by coordinates with several events.** Remove all of them, or require the event id?
   Recommendation: an optional event id. Without one, remove all, and return the count.
5. **Structural rows held up only by temporal edges** (FR-006). This changes what purge means for any caller that relied
   on the structural edge outliving its timestamps. Is that acceptable for every graph, or only those written with
   ids? Recommendation: for every graph. The row means "this edge exists at some time", and after the purge it no
   longer does.

## Not in this spec

opsreview's other requests, triaged on the bus on 2026-10-20: a tenant-scoped erase of the temporal tiers in a shared
graph (request 2, suited to the Eraser), a ledger redact/erase operation (request 3, needs its own design spec, see
spec 055 in opsreview), and the 3.8–4.3 s write stalls (request 5, needs diagnosis first). The window reader with
payloads is spec 255.
