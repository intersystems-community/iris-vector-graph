# Feature Specification: Opt-in multigraph (parallel edges)

**Feature Branch**: `234-multigraph-parallel-edges`
**Created**: 2026-09-26
**Status**: Draft
**Input**: Spec 234. Option B for the parallel-edge blocker found in spec 203:
parallel edges as an opt-in, with existing single-edge data and layout left
byte-identical when the mode is off.

## Context

IVG identifies an edge by `(s, p, o_id, graph_id)`:

- `rdf_edges` enforces that tuple as `u_spo_graph`.
- `^KG("out"/"in")` keys adjacency on it.
- The ledger's `tuple` index maps it to one statement.
- The translator guards every relationship insert with a NOT EXISTS on it.
- Bolt derives the relationship id from a hash of it.

[research.md](research.md) lists 78 touchpoints (T01–T78).

openCypher is a multigraph: `CREATE (a)-[:T]->(b)` run twice makes two
relationships. Six TCK scenarios fail on this today.

- **Match6 [14]**, **Merge5 [3]**, **Merge5 [5]**, **Merge5 [21]** need two
  parallel edges.
- **Match4 [8]** and **Match9 [6]** need a relationship value that carries a
  real identity through `WITH`. Today one carries only its triple (research
  R6).

The single-edge model is also a feature. FHIR sync, RDF import, the ledger's
tuple refs and every production caller rely on "write the same triple twice →
one edge". So the multigraph is opt-in, and it is off for every graph that
exists today.

## Clarifications

### Session 2026-09-26

- Q: Multigraph as the default, opt-in, or never? → A: Opt-in (option B).
- Q: Scope of the mode: per graph or per namespace? → A: per graph, including
  the default graph, stored in `^IVG.GraphMode(gKey)`.
- Q: Layout of parallel edges in `^KG`? → A: children
  `^KG("out", g, s, p, o, ekey)` under the existing leaf, present only while two
  or more edges share a triple (research R3, option 1).
- Q: What do `deg`/`degp` count in a multigraph? → A: live edges, not triples.
- Q: How do PPR/PageRank/Arno treat parallel edges? → A: set semantics, one
  adjacency entry per triple with the representative weight; `^NKG` unchanged.
- Q: How are edge embeddings keyed? → A: per triple, unchanged; parallel edges
  share one embedding.
- Q: What does `create_edge` return in a multigraph? → A: unchanged
  (True/False). A new `create_edge_returning_id()` returns the new `edge_id`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Parallel edges in a multigraph (Priority: P1)

A developer turns multigraph mode on for a graph and runs
`CREATE (a)-[:T]->(b)` twice. They see two relationships in SQL MATCH, in
`^KG` traversal, in `count(r)` and in Bolt, each with its own id. Deleting one
leaves the other.

**Why this priority**: this is the capability itself, and four of the six target
TCK scenarios depend on it.

**Independent Test**: enable the mode on an empty graph, CREATE two parallel
edges, then read them back through each path.

**Acceptance Scenarios**:

1. **Given** a graph with multigraph on, **When** `CREATE (a)-[:T]->(b)` runs
   twice, **Then** `MATCH (a)-[r:T]->(b) RETURN count(r)` returns 2. The two
   rows have distinct `edge_id`s and `ekey` values 0 and 1.
2. **Given** two parallel edges, **When** one is deleted by
   `MATCH … WHERE r.name = 'r1' DELETE r`, **Then** exactly one edge remains in
   `rdf_edges`, under `^KG("out", g, a, "T", b)`, with `deg`/`degp` equal to 1.
3. **Given** two parallel edges, **When** the ^KG out-neighbours of `a` are read,
   **Then** `b` appears once as a neighbour. The edge-level readers (GraphVerify,
   Subgraph, path-returning traversals) report two edges.
4. **Given** two parallel edges returned over Bolt, **Then** the two
   relationship structs carry different ids, equal to their `edge_id`s.
5. **Given** a single CREATE that writes the same triple twice (Match6 [14]
   setup), **Then** both edges exist.

---

### User Story 2 - MERGE over parallel edges (Priority: P1)

A developer MERGEs a relationship into a multigraph that already holds parallel
edges.

- MERGE matches every existing parallel edge that fits the pattern, properties
  included, and binds one row per match.
- MERGE creates exactly one new edge, with a new `ekey`, only when none fits.

**Why this priority**: Merge5 [3], [5] and [21]. MERGE is also where multigraph
semantics are easiest to get wrong.

**Independent Test**: the three Merge5 scenarios, run in a multigraph graph.

**Acceptance Scenarios**:

1. **Given** two parallel `:TYPE` edges, **When**
   `MERGE (a)-[r:TYPE]->(b) RETURN count(r)`, **Then** the result is 2 and nothing is created
   (Merge5 [3]).
2. **Given** parallel edges `{name: 'r1'}` and `{name: 'r2'}`, **When**
   `MERGE (a)-[r:TYPE {name: 'r2'}]->(b)`, **Then** the result is 1 and nothing is
   created (Merge5 [5]).
3. **Given** two parallel edges deleted earlier in the same query, **When**
   `MERGE (a)-[t2:T {name: 'rel3'}]->(b)` runs for each of the two rows,
   **Then** one edge is created, and both rows bind it (Merge5 [21]).
4. **Given** multigraph off, **When** the same MERGE runs, **Then** behaviour and
   SQL are unchanged from `92b078e`.

---

### User Story 3 - Relationship values carry identity (Priority: P1)

A developer passes relationships through `WITH`, collects them in a list and
matches a variable-length pattern over the list (`MATCH (a)-[rs*]->(b)`). Each
relationship keeps its `edge_id` identity through every stage. This is
independent of multigraph mode.

**Why this priority**: Match4 [8] and Match9 [6]. Also, every identity
comparison (`r1 = r2`, isomorphism, `count(DISTINCT r)`, GROUP BY on `r`) is
only correct for parallel edges if it compares `edge_id`.

**Independent Test**: Match4 [8] and Match9 [6] on a single-edge graph, with
multigraph off.

**Acceptance Scenarios**:

1. **Given**
   `MATCH ()-[r1]->()-[r2]->() WITH [r1, r2] AS rs LIMIT 1 MATCH (first)-[rs*]->(second)`,
   **Then** the result is `(:A), (:C)` (Match4 [8]).
2. **Given** the bound-endpoint variant, **Then** the result is `(:A), (:C)`
   (Match9 [6]).
3. **Given** two parallel edges with equal properties, **When**
   `MATCH (a)-[r]->(b) WITH r, count(*) AS c RETURN count(r)`, **Then** the result is 2.
   They do not collapse into one group.

---

### User Story 4 - Upgrade and mode switch (Priority: P2)

An operator upgrades an existing install. Nothing changes until they opt a
graph in:

- `rdf_edges` gains `ekey = 0` on every row;
- `^KG` is byte-identical;
- ledger fingerprints are unchanged.

Turning the mode on for a graph is an admin call. Turning it off is refused
while the graph holds any triple with more than one edge.

**Why this priority**: required to ship, but it follows the core model.

**Independent Test**: upgrade a pre-234 snapshot, then compare `^KG`, ledger
fingerprints and row counts with the pre-upgrade values.

**Acceptance Scenarios**:

1. **Given** a 4.x install with data and an enabled ledger, **When** it upgrades,
   **Then** every `rdf_edges` row has `ekey = 0`. `u_spo_graph` is replaced by
   `u_spo_graph_ekey`. A `$Query` dump of `^KG` is byte-identical to the dump
   taken before the upgrade. The ledger head fingerprint is unchanged.
2. **Given** the upgrade runs twice, **Then** the second run changes nothing and
   reports "already at 234".
3. **Given** a graph with parallel edges, **When** the operator turns multigraph
   off, **Then** the call fails with `parallel_edges_present` and a count.
4. **Given** a pre-234 snapshot archive, **When** it is restored into a 234
   install, **Then** every edge row lands with `ekey = 0`.

---

### Edge Cases

- **A single-graph write of an existing triple**: behaviour is unchanged.
  `create_edge` returns False; the translator's NOT EXISTS skips; the ledger
  `create_rel` returns `relationship_exists`.
- **`CREATE` after `MATCH` that yields N rows for the same `(a, b)`** in a
  multigraph: N edges are created. Today's `SELECT DISTINCT` and NOT EXISTS
  collapse them (T59).
- **Two writers racing** on the same triple in a multigraph: both compute the
  same next `ekey`. The loser gets `-119` on `u_spo_graph_ekey` and retries once
  with a new `MAX + 1`. After a second collision it fails loudly.
- **Deleting the lowest-`ekey` edge while others remain**: the leaf weight
  becomes the weight of the new lowest live `ekey`. `ekey` values are never
  renumbered.
- **Going from two parallel edges to one**: the child nodes are killed, and the
  layout returns to exactly the single-edge form.
- **Temporal edges** (`^KG("tout", g, ts, s, p, o)`): these are keyed by
  timestamp already. Two temporal edges with the same `(ts, s, p, o)` are still
  one edge (non-goal).
- **A ledger tuple ref to a triple with several live edges**: the call fails with
  `ambiguous_tuple` unless the ref carries `ekey` or a `stmt_id`.
- **Edge embeddings**: they stay keyed by triple and attach to the triple, not to
  one parallel edge (non-goal; settled 2026-09-26).
- **`BuildKG` rebuilding a multigraph**: it rebuilds children and counters from
  `rdf_edges` rows. It must produce the same `^KG` as incremental writes.

## Requirements *(mandatory)*

### Functional Requirements

#### Mode

- **FR-001**: Multigraph mode MUST be a per-graph setting that is off by default.
  It covers the default graph too, keyed by `GraphKey.ForIndex` (0 for the
  default graph). The setting MUST be stored in a namespace-local global
  `^IVG.GraphMode(gKey) = "multi"`. It MUST be listed in `STORE_PLAN`
  (`_engine/snapshot.py:46`) and in `GraphStores`, and killed by `Eraser` when
  its graph is dropped.
- **FR-002**: The engine MUST expose `set_multigraph(graph, enabled)` and
  `is_multigraph(graph)`. Enabling is always allowed. Disabling MUST fail with
  `parallel_edges_present` while any triple in the graph has more than one live
  row.

#### Storage

- **FR-003**: `rdf_edges` MUST gain `ekey INTEGER NOT NULL DEFAULT 0`.
  `u_spo_graph` MUST be replaced by
  `u_spo_graph_ekey UNIQUE (s, p, o_id, graph_id, ekey)`.
  - In single-edge graphs every writer writes `ekey = 0`, so the constraint
    still enforces one edge per triple.
  - `edge_id` stays the primary key and becomes the public relationship
    identity.
- **FR-004**: `^KG("out", g, s, p, o)` and `^KG("in", g, o, p, s)` MUST keep
  their current value and layout for a triple with one live edge.
  - While a triple has two or more live edges, each one MUST also appear as
    `^KG("out", g, s, p, o, ekey) = w` and `^KG("in", g, o, p, s, ekey) = w`.
  - The leaf value MUST be the weight of the live edge with the lowest `ekey`.
- **FR-005**: `EdgeScan` MUST gain `WriteAdjacencyKeyed(s, p, o, w, g, ekey)` and
  `DeleteAdjacencyKeyed(s, p, o, g, ekey)`. They own the transitions between
  one and many edges. `deg` and `degp` MUST count live edges, not triples.
  `^NKG` MUST stay a set of triples: it is written on the first edge and
  removed with the last. The existing `WriteAdjacency` and `DeleteAdjacency`
  MUST keep their signatures and their behaviour for `ekey = 0` callers.
- **FR-006**: `BuildKG` (`TraversalBuild.cls`) MUST rebuild the FR-004 layout
  and counters from `rdf_edges`, with parallel rows included. The result MUST
  equal the result of incremental writes.

#### Writers

- **FR-007**: In a multigraph graph, `create_edge` MUST always create a new edge.
  It allocates `ekey = COALESCE(MAX(ekey) + 1, 0)` over the triple. Its return
  contract (True/False) is unchanged in both modes. A new method
  `create_edge_returning_id()` MUST create the edge the same way and return the
  new `edge_id` in both modes.
- **FR-008**: In a multigraph graph, the translator's CREATE MUST emit one edge
  per input row.
  - This covers CREATE with VALUES (T58) and CREATE after MATCH (T59).
  - It drops the `DISTINCT` and the triple NOT EXISTS guard.
  - `ekey` comes from the current `MAX(ekey)` plus
    `ROW_NUMBER() OVER (PARTITION BY s, p, o_id)`.
  - If `PARTITION BY` is not accepted inside `INSERT … SELECT` on the target
    image (research R8), the translator MUST fall back to per-row inserts
    through the engine.
- **FR-009**: MERGE of a relationship in a multigraph MUST match every existing
  edge that fits the pattern's type and inline properties, binding one row per
  match. It MUST create one edge with a fresh `ekey` only when nothing fits, and
  re-evaluate per input row in openCypher order. With the mode off, the MERGE
  SQL MUST be unchanged.

#### Identity

- **FR-010**: A relationship value MUST carry `edge_id` through every stage.
  - `__edge_{v}_id` goes next to `__edge_{v}_s/_p/_o` (T60, T65, T68, T69).
  - A list of relationships holds `edge_id`s.
  - `(a)-[rs*]->(b)` over a list MUST match edge by edge on `edge_id`.
- **FR-011**: Relationship equality, isomorphism, GROUP BY on a relationship and
  the var-length path key MUST compare `edge_id`, not the triple.
  - This covers T63, T64, T67, T70, and T73/T74 (`k` built from `edge_id`).
  - It applies in both modes. In a single-edge graph, `edge_id` equality and
    triple equality give the same answer, so no result changes.
- **FR-012**: Bolt MUST use `edge_id` as the relationship id (T77, T78). It MUST
  fall back to a stable hash (sha256-derived, not `hash()`) only for values with
  no `edge_id`. Relationship values the translator emits MUST include `edge_id`.

#### Ledger

- **FR-013**: The ledger's `tuple` index MUST stay
  `^IVG.Ledger("tuple", s, p, o, gk) = stmtId` for `ekey = 0`. Parallel edges MUST be indexed at
  `^IVG.Ledger("tuple", s, p, o, gk, ekey)`.
  - `TupleJson` and `CanonicalRecord` MUST add an `ekey` field only when it is
    not 0, so every existing record and fingerprint is byte-identical.
  - Genesis MUST order by `s, p, o_id, graph_id, ekey`.
  - In a multigraph graph, `create_rel` MUST create a new edge. `upsert` MUST
    fail with `ambiguous_tuple` when more than one edge fits.
  - A tuple ref MAY carry `ekey`.

#### Migration and restore

- **FR-014**: A migration step `multigraph_ekey` MUST do the following, and MUST
  be idempotent:
  1. add `ekey`;
  2. backfill it to 0 explicitly;
  3. swap the constraint;
  4. record the step in the upgrade report.

  The 4.0.0 re-key (T08) MUST carry `ekey`. Restoring an archive without `ekey`
  MUST land every edge at 0 (T52).

#### Scope

- **FR-015**: Arno and `^NKG` MUST keep set semantics, with one edge per triple.
  PPR and BFS results in a multigraph graph MUST equal the results of the same
  graph with its parallel edges collapsed.
- **FR-016**: With multigraph off everywhere, the full TCK run MUST show zero
  regressions against `/tmp/tck_r4_base.tsv` (3835/3897; best full run 3855, r5). Every unit and E2E
  suite MUST keep its `92b078e` pass rate.

### Non-goals

- **Multigraph as the default**, and any per-namespace switch.
- **Parallel temporal edges at one timestamp**: `^KG("tout")` keeps
  `(ts, s, p, o)` as its key.
- **Per-edge embeddings for parallel edges**: `kg_EdgeEmbeddings` stays keyed by
  triple.
- **Multiplicity-weighted PPR or centrality**: FR-015.
- **FHIR sync, RDF import and the bulk loader emitting parallel edges**: they
  keep single-edge semantics in every mode, because a triple is exactly what
  they mean.
- **Renumbering or compacting `ekey`s.**

### Key Entities

- **Edge key (`ekey`)**: a small integer, unique within
  `(s, p, o_id, graph_id)`. It is 0 for the first or only edge. It is never reused while its
  edge lives, and never renumbered.
- **Graph mode**: `^IVG.GraphMode(gKey)`. Absent means single-edge, and `"multi"`
  means multigraph.
- **Relationship identity**: `edge_id`, used in Cypher values, Bolt and
  comparisons. The ledger statement id stays the ledger's identity, mapped to
  one `(s, p, o, g, ekey)`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: TCK Merge5 [3], Merge5 [5], Merge5 [21], Match6 [14], Match4 [8]
  and Match9 [6] pass. The harness enables multigraph on the default graph of
  the TCK namespace.
- **SC-002**: Zero TCK regressions against the best-ever baseline, checked with
  `/tmp/tck_cmp.sh` per area over all 37 areas. The whole run is done twice:
  once with the TCK default graph in multigraph mode and once with it off.
- **SC-003**: After the upgrade, a `^KG` dump and the ledger head fingerprint of
  an unchanged install are byte-identical to the values before the upgrade.
- **SC-004**: With the mode off, the median `^KG("out")` scan and the median
  `create_edge` latency on a 100k-edge graph are within 3% of `92b078e`.
  - With the mode on and no parallel edges, they are also within 3%.
  - With the mode on and 10% of triples doubled, the scan cost is measured and
    published, not asserted.
- **SC-005**: GraphVerify reports zero drift on a multigraph graph after 1,000
  random create and delete operations over 50 triples.

## Assumptions

- No install holds a parallel edge today: every write path fails or skips on the
  triple (research R2). So the backfill to `ekey = 0` cannot violate the new
  constraint.
- `edge_id` is stable across restore. Today NDJSON restore strips only `id`
  (`snapshot.py:888`). If `edge_id` is re-issued on restore, Bolt ids change
  across a restore. That is acceptable, and it is documented.
- Tests run against `ivg-iris-enterprise` (port 31972) only.

## Test-first tasks note

`/speckit.tasks` MUST order each phase as tests, then code, then gate.

1. **Phase 1 (storage)**:
   - Tests: unit tests for `WriteAdjacencyKeyed` and `DeleteAdjacencyKeyed`
     layout transitions (one→two→one, delete lowest `ekey`, counters); an
     E2E test that the upgrade is byte-identical (SC-003).
   - Gate: those tests pass.
2. **Phase 2 (identity, US3)**:
   - Tests: translator unit tests asserting `__edge_{v}_id` in stage SQL and
     `edge_id` comparisons, plus Match4 [8] and Match9 [6] as E2E.
   - Gate: those tests pass, and the TCK match and with areas show zero
     regressions.
3. **Phase 3 (US1)**:
   - Tests: the parallel CREATE, DELETE and Bolt id E2E, plus Match6 [14].
   - Gate: those tests pass.
4. **Phase 4 (US2)**:
   - Tests: Merge5 [3], [5] and [21] as E2E, plus a unit test that the mode-off
     MERGE SQL is unchanged.
   - Gate: those tests pass.
5. **Phase 5 (US4, ledger)**:
   - Tests: fingerprint byte-identity, the `ambiguous_tuple` error, and genesis
     ordering.
   - Gate: those tests pass, then SC-002 (full TCK, twice) and SC-004.

## Open Questions

None. All six were settled on 2026-09-26 (see Clarifications).
