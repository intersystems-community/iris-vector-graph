<!-- markdownlint-disable MD013 MD060 -->

# Feature Specification: The Adjacency Index kept current by the edge table

**Feature Branch**: `alt-parity`
**Created**: 2026-10-19
**Status**: Draft
**Input**: ADR-0008 step 2, scope decided 2026-10-19 ("Scope of step 2" in the ADR). After a Cypher `CREATE` a one-hop
`MATCH` from a bound node answered nothing, and after a Cypher `DELETE` it answered the deleted edge: every reader of the
Adjacency Index (the k-hop fast path, `shortestPath`, weighted shortest path, BFS, the graph algorithms) answers from an
index that SQL-only writes, Cypher DML among them, leave stale. The index is to be kept current inside the transaction of
the row it derives from, for every writer, so that no caller has to remember it.

## User Scenarios & Testing

### User Story 1 - Read after a Cypher write (Priority: P1)

A developer writes with Cypher and reads with Cypher, in the same session or the next one, and gets the graph they wrote.

**Why this priority**: today the answer is wrong with no error, on ordinary queries (a one-hop `MATCH` from a bound node,
`shortestPath`), and nothing in the result says so.

**Independent Test**: on a scratch namespace, create an edge with Cypher and read it back through every reader of the index;
delete it with Cypher and read again.

**Acceptance Scenarios**:

1. **Given** two nodes, **When** `CREATE (a)-[:R]->(b)` runs through `execute_cypher`, **Then** `MATCH (a {node_id: $a})-[:R]->(y) RETURN y.node_id`,
   `shortestPath`, weighted shortest path, BFS and the k-hop counts all see the edge, with no `sync()`.
2. **Given** an edge, **When** a Cypher `DELETE r` or `DETACH DELETE` removes it, **Then** none of those readers returns it.
3. **Given** a Cypher `MERGE` that creates an edge, or `SET r.weight = 5`, **Then** the readers see the edge and the weight.
4. **Given** `SET n:Label` / `REMOVE n:Label`, **Then** label-driven readers see the change.

---

### User Story 2 - Every other writer (Priority: P1)

An application that writes through raw SQL, the engine's edge methods, the ledger, an erase, a snapshot restore, RDF import,
inference or the FHIR graph sync finds the index current afterwards, and a rolled-back write leaves it unchanged.

**Why this priority**: a writer that bypasses the index is the defect class itself; one left out reopens it.

**Independent Test**: one E2E case per writer path (the list in FR-005), each asserting the index against a recount from the
tables after the write, and that the recount has no index entry without a row behind it.

**Acceptance Scenarios**:

1. **Given** a SQL `INSERT`, `DELETE`, or an `UPDATE` that changes `s`, `p`, `o_id`, `graph_id` or `qualifiers` on `rdf_edges`,
   **When** it commits, **Then** the index equals a recount.
2. **Given** any write inside a transaction, **When** it rolls back, **Then** the index is as it was before.
3. **Given** a graph in multigraph mode with two parallel edges, **When** one is deleted, **Then** the other remains in the
   index with its own key and the parallel count is right.

---

### User Story 3 - Bulk loads stay fast (Priority: P2)

A user loading millions of edges (`bulk_load_session`, the SNB loaders) gets the load rate of today and a current index at the
end, built once, not row by row.

**Why this priority**: load time is benchmarked against Neo4j and Memgraph; a per-row index write would cost 14-40%.

**Independent Test**: load SNB SF0.1 through `bulk_load_session`, compare load time with today's, and recount the index.

**Acceptance Scenarios**:

1. **Given** a bulk load, **When** it returns, **Then** the index equals a recount and duplicate triples were not written twice.
2. **Given** a bulk load that fails part-way, **When** it returns the error, **Then** the index still equals a recount of what is
   in the tables (no half-built index left behind).

---

### User Story 4 - Existing installs (Priority: P2)

An existing install upgrades and its index, which may already have drifted, becomes current once and stays current.

**Why this priority**: every install today has had Cypher or SQL writes the index never saw.

**Independent Test**: on a namespace with deliberate drift (an index entry with no row, a row with no entry), run
`ensure_indexes`; then check the index against a recount, and that a second `ensure_indexes` does no rebuild.

**Acceptance Scenarios**:

1. **Given** a drifted install, **When** `ensure_indexes` runs, **Then** it announces a one-time rebuild, performs it, and the
   index equals a recount afterwards.
2. **Given** an install already rebuilt, **When** `ensure_indexes` runs again, **Then** it does no rebuild.
3. **Given** a caller of `sync()`, **When** it runs after this change, **Then** it still succeeds and changes nothing.

---

### Edge Cases

- The default graph's id arrives as `$Char(0)` in index callbacks and must map to the default graph key (`0`).
- A row whose `graph_id` is not a legal subscript (ADR-0003) fails its insert, not the index silently.
- Two writers insert the same triple concurrently: the unique index refuses one; the index has one entry.
- A temporal edge: its structural entry follows from its row (ADR-0009); a temporal-only write without a row is out of scope.
- `INSERT %NOINDEX` skips this index and the unique index: any writer using it must dedupe and finish with `%BuildIndices`.
- The degree cache (ADR-0012) and the Graph-Version Token must move on every index change, or cached answers outlive the data.
- The table class must compile after its index class (the cause of the first functional index's removal).
- Erasing one graph must not touch another graph's index entries.

## Requirements

### Functional Requirements

- **FR-001**: Every insert, delete and update of an `rdf_edges` row MUST change `^KG("out")` and `^KG("in")` in the same
  transaction, so that a rollback undoes both.
- **FR-002**: Every insert and delete of an `rdf_labels` row MUST change `^KG("label")` in the same transaction.
- **FR-003**: In a graph in multigraph mode the index MUST keep one child per edge key and the `"#"` count; outside it, one
  entry per triple.
- **FR-004**: Every index change MUST move the source's degree generation (ADR-0012) and the Graph-Version Token.
- **FR-005**: Each of these writer paths MUST leave the index equal to a recount, proven by one E2E test each: Cypher `CREATE`,
  `MERGE`, `DELETE`, `DETACH DELETE`, `SET` on a relationship property, `SET`/`REMOVE` label; raw SQL `INSERT`, `DELETE`,
  `UPDATE`; `create_edge`, `delete_edge`, `bulk_ingest_edges`, `bulk_create_edges` with and without `auto_sync`,
  `bulk_load_session`; ledger commit and rollback; `erase_graph`, `erase_all`; snapshot restore; `import_rdf`; inference
  materialize and retract; temporal edge insert; the FHIR graph sync.
- **FR-006**: An edge's weight in the index MUST come from its row's `qualifiers` on insert and on update.
- **FR-007**: The `^KG("prop")` store MUST be removed and its one reader moved to SQL.
- **FR-008**: Bulk paths that write `%NOINDEX` MUST dedupe and MUST end in `%BuildIndices` before returning, success or failure.
- **FR-009**: `ensure_indexes` MUST declare the index and, on an install whose index was not built by it, run one announced
  `%BuildIndices`; it MUST NOT rebuild an install it has already rebuilt.
- **FR-010**: `sync()` MUST stay callable and MUST NOT be needed for `out`, `in` or `label` afterwards.
- **FR-011**: The single-writer guard (ADR-0011, `test_adjacency_single_writer.py`) MUST keep passing: the index callbacks
  reach the stores through `Graph.KG.Adjacency`.
- **FR-012**: No reader changes its interface; answers change only where they were stale.
- **FR-013**: `CONTEXT.md` (Adjacency Index, Drift) MUST be updated to the new behaviour when this lands.
- **FR-014**: Every temporal edge MUST have a structural row carrying a temporal-origin marker (ADR-0009), on every write path
  including the ledger; deleting the last timestamp removes the row only while the marker is set; existing installs get marked
  rows backfilled from the temporal globals at upgrade.
- **FR-015**: An edge's weight MUST live in its row's `qualifiers`: `create_edge(weight=)` writes it there and `set_edge_weight`
  updates the row.
- **FR-016**: No path MAY write adjacency without a row: `Loader` is removed, `BenchFormat` seeds rows, `bulk_delete_adjacency`
  becomes a deprecated alias that deletes the edge rows (with a deprecation warning), `Snapshot.ImportGlobals` restores rows only.

### Key Entities

- **Adjacency Index**: `^KG("out")`, `^KG("in")`, `^KG("label")`, derived from `rdf_edges` and `rdf_labels`, now current with them.
- **Edge row**: a row of `rdf_edges`; the authority the index derives from.
- **Rebuilt store**: `^NKG`, two-hop statistics, the Arno snapshot, cached algorithm results; unchanged, still token-validated.

## Success Criteria

### Measurable Outcomes

- **SC-001**: For every writer path in FR-005, the index equals a recount from the tables after the write, with no entry lacking
  a row (0 mismatches).
- **SC-002**: The Cypher read-after-write scenarios of User Story 1 answer as Neo4j does (the edge after `CREATE`, nothing after
  `DELETE`).
- **SC-003**: Non-bulk edge writes cost at most 40% more than a plain SQL insert (the spike measured 14-40%).
- **SC-004**: Bulk load time for SNB SF0.1 through `bulk_load_session` stays within 10% of today's.
- **SC-005**: TCK 3896/3896, the full unit suite, and all 40 SNB SF0.1 queries with the same answers as before.
- **SC-006**: After the change, SNB SF1 and SF10 run on AWS against Neo4j and Memgraph (ADR-0008, benchmark order).

## Assumptions

- No code rewrites an edge's endpoints in place except through `UPDATE`, which the index handles (FR-001).
- The spike's correctness and cost results (`docs/research/spikes/functional-index-adjacency/`) hold on the alternative path.
- `^NKG` and the other Rebuilt stores stay as they are; making them current is out of scope.
