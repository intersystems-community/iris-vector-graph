# Feature Specification: Temporal Edge Indexing

**Feature Branch**: `036-temporal-edges`
**Created**: 2026-04-01
**Status**: Draft
**Input**: docs/graph-analytics-detection-roadmap.md — Phase 1 foundational primitive

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Write a Timestamped Edge at Ingest Speed (Priority: P1)

A developer ingesting fraud transactions, security audit logs, or IoT sensor events wants to write edges with timestamps at the maximum speed the IRIS global engine supports — without going through the SQL layer. They call `create_edge_temporal(source, predicate, target, timestamp)` and the edge is written to both the standard `^KG` global (for compatibility) and the new `^KGt` time-indexed global in a single atomic operation.

**Why this priority**: Ingest speed is the first dimension. If writing a temporal edge is slower than writing a regular edge, the feature fails before queries are even relevant. The design must match the speed of a direct global write — sub-millisecond per edge at high throughput.

**Independent Test**: Can be tested by inserting 100K temporal edges and verifying both `^KG` and `^KGt` contain the correct entries, with ingest rate measured and documented.

**Acceptance Scenarios**:

1. **Given** a fraud transaction event, **When** a developer calls `create_edge_temporal("account:A", "SENDS", "account:B", timestamp=1712000000)`, **Then** the edge appears in `^KGt("out", 1712000000, "account:A", "SENDS", "account:B")` AND in `^KG("out", "account:A", "SENDS", "account:B")` — both globals consistent.
2. **Given** 100,000 temporal edges inserted via `bulk_create_edges_temporal()`, **Then** ingest rate is at least 50,000 edges/second on standard IRIS hardware.
3. **Given** a temporal edge, **When** the same (source, predicate, target) pair is inserted again with a different timestamp, **Then** both timestamps exist as separate entries in `^KGt` (multiple timestamps per edge pair are valid).

---

### User Story 2 — Query Edges Within a Time Window (Priority: P1)

A developer wants to find all edges that occurred within a specific time range — e.g., "all transactions from account A in the last 5 minutes" or "all network connections to host X between 14:00 and 14:05." The query uses `$Order` range scans on `^KGt` and returns in sub-millisecond time for typical fraud/security windows.

**Why this priority**: Without queryable time windows, the temporal index has no value. The range scan is the core operation — it must be fast.

**Independent Test**: Can be tested by inserting edges with known timestamps, querying a specific window, and verifying correct results with latency measured.

**Acceptance Scenarios**:

1. **Given** edges spanning 24 hours, **When** querying `get_edges_in_window(source="account:A", start=t0, end=t0+300)`, **Then** only edges with timestamps in [t0, t0+300] are returned, in under 5ms.
2. **Given** a busy node with 10,000 edges in one hour, **When** querying a 1-minute window, **Then** only edges in that minute are returned — the `$Order` range scan skips the rest without scanning all 10K.
3. **Given** an empty time window (no edges), **When** querying, **Then** an empty list is returned (no error).

---

### User Story 3 — Detect High-Velocity Events (Burst Detection) (Priority: P1)

A developer wants to detect when a node's edge rate exceeds a threshold within a time bucket — the "fan-out burst" pattern used in fraud detection (one account suddenly sending to 50+ new accounts in 30 seconds) and cybersecurity (a process spawning 100+ child processes in 10 seconds).

**Why this priority**: This is the first analytics primitive enabled by temporal edges. It doesn't require GNNs or embeddings — just counting edges in a time bucket.

**Independent Test**: Can be tested by inserting a controlled burst of edges for one node, querying the time bucket count, and verifying the burst is detected.

**Acceptance Scenarios**:

1. **Given** account A sends 100 transactions in 60 seconds (burst) and account B sends 5 transactions (normal), **When** calling `get_edge_velocity(node_id, window_seconds=60)`, **Then** account A has velocity=100 and account B has velocity=5.
2. **Given** a velocity threshold of 50 edges/minute, **When** calling `find_burst_nodes(label="Account", predicate="SENDS", window_seconds=60, threshold=50)`, **Then** only account A is returned.
3. **Given** a 5-minute bucket index `^KGt("bucket", floor(ts/300), node_id)`, **Then** bucket queries complete in under 1ms regardless of total edge count.

---

### User Story 4 — Temporal Path Query via Cypher (Priority: P2)

A developer wants to express time-constrained graph patterns in Cypher — "find all attack chains where each hop occurred within 60 seconds of the previous hop." This requires both variable-length paths (Sprint 3) and temporal filtering.

**Why this priority**: P2 because it depends on US1-3 being complete. The Cypher integration is the developer experience layer; the ObjectScript temporal index is the foundation.

**Acceptance Scenarios**:

1. **Given** a temporal graph, **When** executing `MATCH (a)-[:CONNECTS*1..3]->(b) WHERE edge_timestamp > $start AND edge_timestamp < $end RETURN b`, **Then** only paths where the traversal hops fall within the time window are returned.
2. **Given** a temporal path query, **When** the time window excludes all paths, **Then** zero rows are returned.

---

### Edge Cases

- What happens when timestamp is null? Default to current Unix timestamp (`$ZH` in ObjectScript).
- What happens when the same (source, predicate, target, timestamp) tuple is inserted twice? Idempotent — second write is a no-op.
- What happens when `end < start` in a window query? Return empty list (no error).
- What happens when the time window spans billions of edges? The `$Order` scan is O(results) not O(total) — performance degrades gracefully.
- What is the timestamp unit? Unix epoch in seconds (INTEGER). Milliseconds via optional `use_millis=True` parameter.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST maintain `^KGt("out", timestamp, source, predicate, target) = weight` for all temporal edges.
- **FR-002**: System MUST maintain `^KGt("in", timestamp, target, predicate, source) = weight` for reverse lookups.
- **FR-003**: System MUST maintain `^KGt("bucket", floor(timestamp/bucket_size), source) = ""` for fast bucket counting.
- **FR-004**: Writing a temporal edge MUST also write the corresponding `^KG` entry for backward compatibility with all existing operators.
- **FR-005**: `get_edges_in_window(source, predicate, start, end)` MUST use `$Order` range scan and return in O(results) time.
- **FR-006**: `get_edge_velocity(node_id, window_seconds)` MUST return edge count in the most recent window without scanning all edges.
- **FR-007**: `bulk_create_edges_temporal(edges)` MUST accept `[{source, predicate, target, timestamp, weight}]` and achieve ≥50K edges/second on standard hardware.
- **FR-008**: `create_edge_temporal()` MUST be callable with `timestamp=None` to auto-assign current time.
- **FR-009**: All temporal globals MUST be cleaned up by `delete_node()` and `PurgeIndex()`.
- **FR-010**: The `^KGt` global MUST be independent of `^KG` — existing operators continue to work unchanged.

### Key Entities

- **Temporal Edge**: An edge with an associated Unix timestamp. Stored in both `^KGt` (time-indexed) and `^KG` (standard). The timestamp is the first subscript in `^KGt`, enabling `$Order` range scans.
- **Time Bucket**: A coarser time index (`^KGt("bucket", floor(ts/300), node)`) for fast velocity counting without scanning individual edges.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Writing a temporal edge takes no more than 2x the time of writing a non-temporal edge (the overhead of the second global write).
- **SC-002**: `bulk_create_edges_temporal(100K edges)` completes in under 2 seconds.
- **SC-003**: Window queries for 1-minute windows on 1M-edge graphs complete in under 10ms.
- **SC-004**: Burst detection (`find_burst_nodes`) on 10K nodes with 1M edges completes in under 100ms.
- **SC-005**: 100% of existing tests continue to pass (zero regressions — `^KG` unchanged).
- **SC-006**: At least 8 unit tests + 4 e2e tests covering write, window query, velocity, and backward compatibility.

## Assumptions

- Timestamps are Unix epoch integers (seconds). Millisecond precision is opt-in.
- The default bucket size is 300 seconds (5 minutes). Configurable per-index.
- Temporal edges do NOT replace `^KG` — they augment it. All existing operators (PageRank, BFS, PPR, etc.) continue to read from `^KG` unchanged.
- `BuildKG()` is NOT required for temporal edges — they are written directly to `^KGt` at ingest time by `InsertIndex`.
- The `^KGt` global is process-global (not process-private `^||`).

## Scope Boundaries

**In scope (Phase 1)**:
- `^KGt` global structure + `Graph.KG.TemporalIndex.cls` ObjectScript class
- `create_edge_temporal()`, `bulk_create_edges_temporal()` on IRISGraphEngine
- `get_edges_in_window()`, `get_edge_velocity()`, `find_burst_nodes()` on IRISGraphEngine
- `PurgeTemporalIndex()` cleanup
- Unit and e2e tests

**Out of scope (Phase 2 / future)**:
- Cypher `WHERE edge_timestamp >` syntax integration (US4 — depends on US1-3)
- Streaming ingest via Pulsar/Kafka
- Temporal edge TTL / automatic expiry
- Compaction of old time buckets
- `^NKG` integer encoding for temporal edges (Arno acceleration of temporal queries)

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently - e.g., "Can be fully tested by [specific action] and delivers [specific value]"]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]
2. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- What happens when [boundary condition]?
- How does system handle [error scenario]?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST [specific capability, e.g., "allow users to create accounts"]
- **FR-002**: System MUST [specific capability, e.g., "validate email addresses"]  
- **FR-003**: Users MUST be able to [key interaction, e.g., "reset their password"]
- **FR-004**: System MUST [data requirement, e.g., "persist user preferences"]
- **FR-005**: System MUST [behavior, e.g., "log all security events"]

*Example of marking unclear requirements:*

- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified - email/password, SSO, OAuth?]
- **FR-007**: System MUST retain user data for [NEEDS CLARIFICATION: retention period not specified]

### Key Entities *(include if feature involves data)*

- **[Entity 1]**: [What it represents, key attributes without implementation]
- **[Entity 2]**: [What it represents, relationships to other entities]

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: [Measurable metric, e.g., "Users can complete account creation in under 2 minutes"]
- **SC-002**: [Measurable metric, e.g., "System handles 1000 concurrent users without degradation"]
- **SC-003**: [User satisfaction metric, e.g., "90% of users successfully complete primary task on first attempt"]
- **SC-004**: [Business metric, e.g., "Reduce support tickets related to [X] by 50%"]
