<!-- markdownlint-disable MD013 -->

# Feature Specification: Temporal edges that purge cleanly, collide visibly and rebuild identically

**Feature Branch**: not created (draft)
**Created**: 2026-10-21 (rescoped 2026-10-22)
**Status**: Draft, decisions recorded
**Input**: opsreview's Event Store evaluation (2026-10-20, opsreview
`docs/wayfinder/mvp-gaps-se-and-mso/research/event-store-flat-sql-vs-ivg.md` § 5.2), request 1 of 5 on the agent bus.
Rescoped on Tom's decision of 2026-10-22: an event id is not a graph construct and is not built into every IVG graph.
opsreview makes each action, configuration change and Finding its own node, with an id derived from its `event_id`, so
its temporal edges are `(ts, instance, ACTION_AT, action:<event_id>)`. The target is unique per event, same-millisecond
events cannot collide, and a replay rewrites the same edge. The bench's merges came from pointing edges at a shared
target (`sal_lifecycle.deploy`).

That design moves three costs onto IVG, and this spec is about them. With one node per event, every event leaves a
structural row and a `^KG("out")`/`("in")` entry that `PurgeRawBefore` never removes, so adjacency grows without bound
under retention. A write to coordinates that already hold an edge is silently merged ("insert" mode) and counted twice
in the aggregates. And opsreview's Enrichment process rebuilds a tenant's graph from its event table and must land the
same graph (its acceptance test 5 compares node and edge sets by hash).

## What the code does today

Read from `Graph.KG.TemporalIndex` (`WriteStores`, `BulkInsert`, `PurgeRawBefore`), 2026-10-21. Claims to prove with the
first tests:

- **Mode "insert" (the default)** writes a second edge at existing coordinates over the first in `^KG("tout")` and
  `^KG("tin")`, but `$Increment`s `^KG("bucket")` and the `^KG("tagg")` count and sum again and folds it into the HLL.
  Afterwards one raw edge remains and the bucket aggregates count two. `^KG("edgeprop")` is not cleared, so the stored
  attributes are the union of both writes' keys.
- **Every temporal edge asserts its structural row** (`Graph.KG.Adjacency.AssertTemporalRow`, ADR-0009). Several
  timestamps of one `(s, p, o)` share it.
- **`PurgeRawBefore`** removes `tout`, `tin` and `edgeprop` entries. It never removes structural rows or adjacency, even
  when no surviving timestamp holds that `(s, p, o)`.

## User Scenarios & Testing

### User Story 1 - Retention removes everything an expired edge left (Priority: P1)

Retention purges temporal edges older than a cutoff. Afterwards nothing an expired edge wrote remains: its raw entries,
its attributes, and the structural row and adjacency that only expired timestamps held up. The bucket aggregates stay,
as today.

**Why this priority**: with one node per event, an adjacency entry that outlives retention grows the graph without
bound, and the store fails over two years even if every read passes.

**Independent Test**: write one-node-per-event edges over 72 hours, purge everything older than 48 hours (opsreview's
metric-ingest raw retention), and compare every store with a reference graph that only ever held the last 48 hours,
aggregates excepted.

**Acceptance Scenarios**:

1. **Given** an `(s, p, o)` whose every timestamp was purged, **Then** its structural row and its `^KG("out")`/`("in")`
   entries are gone, unless a non-temporal write also asserted that edge.
2. **Given** an `(s, p, o)` with timestamps on both sides of the cutoff, **Then** its structural row stays.
3. **Given** a node whose every edge was purged, **Then** the node itself stays (purge removes edges, not nodes), and
   the degree cache reports 0 for it.

---

### User Story 2 - A write to occupied coordinates is a reported collision (Priority: P1)

A caller writes an edge at `(ts, s, p, o)` that already holds one, in mode "insert". The second write is not stored and
not counted, and the call's result reports it as a collision.

**Why this priority**: today the aggregates count an edge the raw store no longer holds, and nothing says so.

**Independent Test**: write the same coordinates twice with different weights and attributes, in one batch and in two
calls. Read the window, the aggregates and the attributes.

**Acceptance Scenarios**:

1. **Given** a second "insert" at existing coordinates, **Then** the raw store, the attributes and the aggregates hold
   the first write only, and the batch reports 1 written and 1 collision.
2. **Given** mode "update", **Then** behaviour is unchanged (replace weight and attributes).

---

### User Story 3 - A rebuild from zero lands the same graph (Priority: P1)

A process drops a graph and replays the same writes into it, in batches whose boundaries differ from the original
run's, or in another order. Every store for the graph is equal to the original's.

**Why this priority**: it is what a derived, rebuildable index rests on, and opsreview's acceptance test 5.

**Independent Test**: write 100,000 temporal edges (one node per event, plus some colliding writes) in batches of
1,000; snapshot every store for the graph (canonical, sorted, hashed). Erase the graph, replay in batches of 777, then
in a shuffled order. Compare the hashes.

**Acceptance Scenarios**:

1. **Given** the same writes in any batch size, **Then** the hashes are equal.
2. **Given** the same writes in another order, **Then** the hashes are equal, aggregates included. With Story 2 the
   first write at a coordinate wins, so this holds when colliding writes are identical or the caller orders them; the
   test pins which.

---

### User Story 4 - Existing callers keep working (Priority: P2)

Existing temporal readers and writers see nothing new, apart from Story 1's purge and Story 2's collision count.

**Independent Test**: the existing temporal unit and E2E suites pass, updated only where they pinned the double count or
the leftover adjacency.

---

### Edge Cases

- `suppressReverseIndex`: an edge with no `tin` entry is still purged, and its structural row with it.
- A structural edge also written by `create_edge`: purge leaves it (Story 1, scenario 1). Telling the two apart needs
  a marker or a reference count; see Decision 1.
- `EraseGraph`, `EraseNodes` and `DeleteTemporalEdge` keep their behaviour, with a case per path for Story 1's new
  removals (TESTING_POLICY: a new dimension needs a case per existing write and delete path).
- Millisecond and second-precision indexes (`TemporalIndexMS`, `TemporalIndex`) both.

## Requirements

### Functional Requirements

- **FR-001**: `PurgeRawBefore` MUST remove a structural row and its `^KG("out")`/`("in")` entries that only purged
  temporal edges asserted, in every graph.
- **FR-002**: A structural edge asserted by a non-temporal write MUST outlive the purge of its timestamps.
- **FR-003**: A mode "insert" write at existing coordinates MUST change no store and MUST be reported as a collision.
  `BulkInsert` and `bulk_create_edges_temporal` report written and collision counts separately; the existing integer
  result keeps meaning "written".
- **FR-004**: Writing the same set of temporal edges in any batch size MUST leave every store identical.
- **FR-005**: Data written before this feature MUST read unchanged, with no migration step.

## Success Criteria

### Measurable Outcomes

- **SC-001**: After a 48-hour raw purge of a 72-hour one-node-per-event load, the structural rows and adjacency hold
  nothing from the purged 24 hours.
- **SC-002**: The rebuild hashes are equal in every batch size and order the test uses.
- **SC-003**: Bulk ingest rate and journal bytes per edge within 15% of today's on the same box (opsreview bench:
  29.5–33.2 k ev/s at 80 tenants; 2.25–2.28 kB per event).
- **SC-004**: `PurgeRawBefore` over a day of opsreview's load costs at most 2× today's (it now also checks each purged
  `(s, p, o)` for surviving timestamps).

## Decisions (Tom, 2026-10-22)

1. **Purge removes structural rows held up only by temporal edges, for every graph** (formerly open question 5). The
   row means "this edge exists at some time"; after the purge it no longer does. Telling a temporal assertion from a
   non-temporal one (FR-002) needs a marker on the row or a reference count; the plan picks one. ADR-0009 ("a temporal
   edge implies a structural edge") gets an amendment: the implication ends when the last timestamp goes.
2. **"insert" reports a collision, not a second count** (formerly open question 2).
3. **Rebuild determinism is in scope** (formerly User Story 4).

## Not in this spec (future)

- **An event id in the temporal key, a dedup store, a dedup window and delete-by-event-id** (formerly open questions 1,
  3 and 4). Moved out: an event id is not a graph construct. The general concept is edge identity: parallel edges, a
  multigraph edge key. If IVG takes it up, Tom's preferred design is a generic optional edge key as a real level in the
  temporal store, with a one-time migration, not a side store.
- opsreview's other requests: the window reader with payloads (spec 255), ledger redaction (spec 256), a tenant-scoped
  temporal erase in a shared graph (request 2, lower now that opsreview keeps one graph per tenant), and the journal and
  write stalls (request 5, diagnosis first).
