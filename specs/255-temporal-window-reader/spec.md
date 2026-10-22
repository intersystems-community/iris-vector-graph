<!-- markdownlint-disable MD013 -->

# Feature Specification: A paged temporal window reader with edge attributes

**Feature Branch**: not created (draft)
**Created**: 2026-10-21
**Status**: Draft, not filed
**Input**: opsreview's Event Store evaluation (2026-10-20, opsreview
`docs/wayfinder/mvp-gaps-se-and-mso/research/event-store-flat-sql-vs-ivg.md` § 4.3 and § 5.3, go/no-go G2–G4), sent over
the agent bus as request 4 of 5. The hero query (one tenant, a 5-minute window, every entity type, with payload) took
20–45 ms through IVG's public API against about 1 ms on the flat table. The cause is that `QueryWindow` returns keys only,
so each event's payload costs a further `GetEdgeAttrs` round trip. A custom one-call server join took 1.6–6.8 ms.
`QueryWindow` builds its answer as one `%String`: a fleet-wide 1-hour window at 80 tenants raised `<MAXSTRING>`.
There is no "latest event per entity" read; opsreview widened `QueryWindow` until it found one (2.0–22.6 ms).

**Update 2026-10-22** (Tom's go-ahead, relayed from opsreview): this is opsreview's priority 2, needed now. Its
explorer's window graph, live on its EKS cluster, calls `get_edges_in_window` and then `get_edge_attrs` once per action
or finding. A paged reader returning the attributes in the same call, on the pattern of `Graph.KG.Cypher.Exec`'s
`ReadPage`, is what it asks for, and a fleet-wide window must not hit `<MAXSTRING>`. opsreview now keeps one graph per
tenant (IVG is a derived index over a flat SQL event table), so Story 1's shared-graph prefix filter (FR-003's prefix,
FR-004, SC-002) moves to P3: still worth having for callers who share a graph, not blocking opsreview.

## What the code does today

Read from `Graph.KG.TemporalIndex` and `iris_vector_graph/stores/iris_sql_store.py`, 2026-10-21. These are claims to
prove with the first tests:

- **`QueryWindow` concatenates every edge into one string.** IRIS's string limit is 3,641,144 characters, about 35 k
  edges at roughly 100 characters each. The opsreview run fit 11.6 k edges in a 5-minute fleet window and failed at
  1 hour.
- **The failure is invisible from Python.** `execute_temporal_window_query` catches the exception, logs a warning and
  returns no rows without setting `error`, so `get_edges_in_window` returns `[]`. A full window and an empty one look
  the same. That breaks the repo's rule against silent wrong answers.
- **A source filter does not narrow the walk.** With `source` given, `QueryWindow` still `$Order`s every source at
  every timestamp in the window and skips the rest. A tenant's read therefore costs as much as the fleet's activity in
  the window, which is the 3–5.6× growth opsreview measured from 10 to 80 tenants in a shared graph.
- **Attributes need one call per edge** (`GetEdgeAttrs(ts, s, p, o)`).

## User Scenarios & Testing

### User Story 1 - The hero query in one call (Priority: P1)

A caller asks for one tenant's events across its entities in a 5-minute window, with each event's attributes. One
call returns them all, in timestamp order, each with source, predicate, target, timestamp, weight, attributes and
event id (spec 254) when there is one.

**Why this priority**: it is G2, and the largest measured gap: 20–45 ms against about 1 ms.

**Independent Test**: load opsreview's synthetic mix for 10 and 80 tenants and run the hero query through the new
reader. Compare its rows with `QueryWindow` plus `GetEdgeAttrs` for every edge.

**Acceptance Scenarios**:

1. **Given** a window holding 150 events of one tenant, **When** the reader is asked for that tenant's sources with
   attributes, **Then** it returns the same 150 events and attributes as `QueryWindow` plus `GetEdgeAttrs`, in one
   round trip.
2. **Given** sources named `<tenant>|<entity>` in a shared graph, **When** the reader is given the prefix
   `<tenant>|`, **Then** it returns only that tenant's events, and its cost does not depend on other tenants' sources
   at those timestamps (see SC-002).
3. **Given** a list of sources, **Then** the result is the union of their events, each once.

---

### User Story 2 - Any window size, without a cap (Priority: P1)

A caller reads a window of any size, up to the fleet-wide hour of request (c) and beyond. The reader returns the
first page and a handle, and further pages on request, with memory bounded by the page size. A failure raises; it
never returns a short or empty answer as if complete.

**Why this priority**: `<MAXSTRING>` makes cross-tenant windows impossible (G4). The silent `[]` turns a capacity limit
into a wrong answer.

**Independent Test**: write 200 k edges in one hour, read them all through the reader in pages, and compare the count
and contents with a walk of `^KG("tout")`. Separately, make the existing `get_edges_in_window` exceed the string limit
and assert that it raises.

**Acceptance Scenarios**:

1. **Given** 200 k edges in a window, **When** read with a page size of 1,000, **Then** every edge comes back once, in
   order, and the server holds at most one page's rows plus the cursor between calls.
2. **Given** a reader that is abandoned before its last page, **Then** its server state is freed when the connection
   ends, and `close()` frees it at once.
3. **Given** `get_edges_in_window` on a window past the string limit, **Then** it raises instead of returning `[]`
   (FR-006).

---

### User Story 3 - The latest event per entity (Priority: P2)

A caller asks for the most recent event of each of a tenant's entities, optionally of one predicate and before a
given time.

**Why this priority**: it is G3. opsreview measured 2.0–22.6 ms by widening windows. SQL answers it with
`TOP 1 ... ORDER BY EventTime DESC` per entity in about 1 ms.

**Independent Test**: for 80 tenants, compare the reader's answer with a brute-force walk for every entity, with and
without a predicate and a cutoff.

**Acceptance Scenarios**:

1. **Given** five entities, **When** asked for their latest events, **Then** each entity's newest event (by timestamp,
   then a stable order on ties) is returned, with attributes.
2. **Given** an entity with no event before the cutoff, **Then** it is absent from the answer, not returned as null.

---

### Edge Cases

- `tsStart > tsEnd`: an empty answer, not an error, the same as `QueryWindow`.
- Both ends are inclusive, the same as `QueryWindow`. The reader's documentation says so, and a test pins it.
- Edges written with `suppressReverseIndex`: an inbound read cannot see them. The reader says so, as
  `get_edges_in_window` does.
- A page read after the window's events were purged: either a snapshot or a documented live read. See Open question 2.
- Ms and second-precision graphs (`TemporalIndexMS` and `TemporalIndex`) read through the same reader.
- Attribute values that are numbers, strings or JSON-unsafe text: they come back as the caller wrote them (the
  `jsonEsc`/`jsonNum` rules).
- A graph in the 4.x layout, and a graph written with event ids (spec 254): both read.

## Requirements

### Functional Requirements

- **FR-001**: A window read MUST return any number of edges, in pages, through a cursor. There MUST be no limit tied to
  a single string.
- **FR-002**: The read MUST be able to include each edge's attributes, and its event id where it has one, in the same
  page as the edge.
- **FR-003**: Filters: a source, a list of sources, a source prefix, a set of predicates, and a direction (out or in).
- **FR-004**: With a source, a list or a prefix given, the work per timestamp MUST be a lookup of those sources, not a
  walk over every source active at that timestamp.
- **FR-005**: Order: ascending timestamp, then source, predicate, target and event id. The order is stable across pages.
- **FR-006**: `get_edges_in_window` and `execute_temporal_window_query` MUST raise, or return a result carrying the
  error, when the server call fails. They MUST NOT return an empty list as if the window were empty.
- **FR-007**: A "latest per source" read returns each named source's newest event, optionally of a predicate and before
  a timestamp, with attributes.
- **FR-008**: `QueryWindow`, `QueryWindowInbound` and `GetEdgeAttrs` keep their signatures and output. Their
  documentation names the string cap and points to the new reader.
- **FR-009**: The Python API exposes the reader as an iterator of events, and the cursor is closed when the iteration
  ends or is abandoned.

### Key Entities

- **Window reader**: a server-side cursor over `^KG("tout")` or `("tin")` for one graph, a time range and filters. Its
  state lives in the calling process, the same way the native Cypher executor holds its rows in `^||IVG.CypherOut`
  between `Run` and `ReadPage` (spec 253, FR-015).
- **Page**: up to N events, each with coordinates, weight, attributes and event id.

## Success Criteria

### Measurable Outcomes

These use opsreview's bench (opsreview `research/event_store_bench/`) on the same laptop VM, so the numbers are
comparable with § 4.3.

- **SC-001**: Hero query p50 at most 2× the custom server join: one graph per tenant ≤ 3.6 ms; shared graph at 80
  tenants ≤ 13.6 ms. The target is well under G2's 100 ms p95.
- **SC-002**: Hero query in a shared graph grows by at most 1.5× from 10 to 80 tenants at a fixed answer size. Today it
  grows 3× to 5.6×.
- **SC-003**: A fleet-wide 1-hour window at 80 tenants (about 144 k events) returns every event, with no `<MAXSTRING>`,
  within G4's 1 s p95.
- **SC-004**: Latest event per entity for one tenant: p50 ≤ 3 ms in a shared graph at 80 tenants (today 22.6 ms).

## Open questions

Each is for Tom, one at a time.

1. **"Latest per source": walk or index.** (a) Walk `tout` backwards from the cutoff until every requested source is
   found. There is no new store, but a quiet entity costs a walk back to its last event. (b) Maintain
   `^KG("tlast", g, s, p) = ts` on every write. That is O(1) per source, but costs one more global set per event (journal,
   SC-003 of spec 254), and purge and erase must maintain it. Recommendation: (b) if spec 254's journal budget allows
   it, otherwise (a) with a bounded look-back the caller sets.
2. **Snapshot or live paging.** Collect the whole window into the process-private global on the first call (a
   snapshot, the same as the native executor), or walk lazily from a resume key (live, with constant server memory).
   Recommendation: lazy from a resume key `(ts, s, p, o, eid)`. A fleet window can be large, and the resume key is
   also what makes FR-005's stable order free.
3. **A source-first index for FR-004.** A prefix lookup per timestamp still pays one step per timestamp in the window,
   across the whole fleet. Making cost track the answer needs `^KG("tsrc", g, s, ts, p, o)`, one more write per event.
   Is the lookup per timestamp enough for SC-002? Recommendation: build the lookup first, measure SC-002, and add the
   index only if it fails.

## Not in this spec

Event identity (spec 254) is a prerequisite only for FR-002's event id field. The reader can ship first and return the
id once it exists. opsreview's requests 2, 3 and 5 are outside both specs (see spec 254, "Not in this spec").
