<!-- markdownlint-disable MD013 -->

# Feature Specification: Read-only Cypher executed natively over the Adjacency Index

**Feature Branch**: `alt-parity`
**Created**: 2026-10-20
**Status**: Draft
**Input**: Decided 2026-10-20 after the SF0.1 comparison with Neo4j. With the spec 252 index, `^KG("out"/"in"/"label")`
equals the rows in the same transaction as every write. The SQL path still pays one index probe per joined row (~30 µs
measured): IC1 is 150 ms against Neo4j's ~4 ms, IC3 116 ms against ~3 ms, and the median IVG/Neo4j over the 40 SNB
queries is 6.1. A hand-built native IC1 (the `^KG` walk, a label check in `^KG("label")` and the property read from
`rdf_props`' own index globals) measured 2 ms for the per-end work that costs ~140 ms through SQL joins.

Decisions (2026-10-20, in order): the executor is native; properties are read from `rdf_props`' index globals, with
the layout probed and prepared SQL as the fallback; Python plans and ObjectScript runs, shapes the planner does not
recognise stay on SQL unchanged; v1 covers broad read Cypher; the native path is on by default where the index is
declared, opt-out per engine, and the default flips only after a shadow run equals the SQL path on the full TCK and
the 40 SNB queries.

## User Scenarios & Testing

### User Story 1 - The same answers, natively (Priority: P1)

A caller runs a read-only Cypher query on a namespace with the adjacency index declared. The engine runs it natively
and returns exactly the rows, columns and value types the SQL path returns, including IVG's own typing and ordering
rules.

**Why this priority**: a faster wrong answer is worse than a slow right one; every later story stands on this one.

**Independent Test**: a shadow mode runs both paths for each query and fails on any difference; the TCK and the SNB
queries run under it.

**Acceptance Scenarios**:

1. **Given** the index is declared and a query the planner supports, **When** it runs in shadow mode, **Then** the
   native and SQL results are equal (columns, row order where the query orders, row multiset otherwise, value types).
2. **Given** a query the planner does not support (any write clause, a procedure call, an unsupported function),
   **When** it runs, **Then** it takes the SQL path exactly as today.
3. **Given** the index is not declared, or the engine was built with `native_cypher=False`, **When** any query runs,
   **Then** it takes the SQL path.

### User Story 2 - The SNB reads get fast (Priority: P1)

The SNB interactive and BI reads that the SF0.1 report shows furthest behind Neo4j (IC1, IC3, IC2, IC5, IC9, BI11 and
the BI aggregates) run natively.

**Why this priority**: it is the measured gap the decision was made to close.

**Independent Test**: the SNB harness at SF0.1 on the same machine as the earlier report, native against SQL against
Neo4j, same parameter sets.

**Acceptance Scenarios**:

1. **Given** SF0.1 loaded with the index declared, **When** the 40 queries run natively, **Then** every answer equals
   the SQL path's and the per-query times are recorded next to Neo4j's and Memgraph's.

### User Story 3 - Broad read Cypher (Priority: P2)

Beyond SNB: multiple `MATCH` clauses, `OPTIONAL MATCH`, `WITH` pipelines (projection, aggregation, `WHERE` after
`WITH`), `UNWIND` of a list, unanchored label scans, and the expression set the TCK read scenarios use.

**Why this priority**: SNB is the benchmark, but a native path that covers only it leaves most real queries on SQL.

**Independent Test**: the TCK in shadow mode; the share of scenarios planned natively is reported and must not fall.

**Acceptance Scenarios**:

1. **Given** the TCK, **When** it runs in shadow mode, **Then** 3896/3896 pass and no natively planned scenario
   differs from SQL.

### User Story 4 - Safe to turn off and to fall back (Priority: P2)

An operator can turn the native path off per engine, and any native failure falls back to SQL rather than failing
the query, with the reason logged.

**Why this priority**: a new executor will have defects; a caller must never see one as a wrong answer.

**Acceptance Scenarios**:

1. **Given** the native executor raises (an unexpected storage layout, an unsupported value), **When** the query
   runs, **Then** the SQL path answers it and a warning names the reason, once per reason per engine.

### Edge Cases

- A node in two graphs: identity is `(node_id, graph_id)`; a read with no `USE GRAPH` sees every graph.
- Multigraph graphs: parallel edges are distinct relationships (ekey children in `^KG`).
- Temporal edges' marked rows are relationships like any other (ADR-0009).
- Values are stored untyped in `rdf_props.val`; comparisons and ordering follow the SQL path's rules, not Neo4j's.
- Missing properties are null; null comparison and ordering follow the SQL path.
- An `rdf_props` storage layout other than the probed one (renamed globals, a rebuilt table) uses the SQL fallback.
- A query planned natively inside an open transaction sees that transaction's own writes (it runs in the same
  process).

## Requirements

### Functional Requirements

- **FR-001**: The planner MUST produce a native plan only for read-only queries it fully supports; anything else MUST
  take today's SQL path unchanged.
- **FR-002**: A native result MUST equal the SQL path's: column names, value rendering (nodes, relationships, paths,
  lists, maps), value types, row order where the query orders, row multiset otherwise.
- **FR-003**: The executor MUST read adjacency and labels from `^KG` only where `Graph.KG.Adjacency.Indexed()` holds.
- **FR-004**: The executor MUST read properties from `rdf_props`' index and data globals, resolved from
  `%Dictionary` at run time, after checking the layout; on any mismatch it MUST read them with prepared SQL.
- **FR-005**: The native path MUST be on by default where the index is declared and off with
  `IRISGraphEngine(..., native_cypher=False)`.
- **FR-006**: A shadow mode MUST run both paths and raise on any difference, with the query, parameters and the first
  differing row in the message.
- **FR-007**: Any exception in the native executor MUST fall back to the SQL path and log a warning once per reason per
  engine.
- **FR-008**: v1 MUST cover: `MATCH` patterns (chains, stars, several comma-separated parts), fixed and variable-length
  relationships in every direction with type alternatives, multiple `MATCH` clauses, `OPTIONAL MATCH`, `WHERE` on
  labels, properties and pattern predicates, `WITH` (projection, `DISTINCT`, aggregation, `WHERE`), `UNWIND`,
  `RETURN` with `DISTINCT`, `count`/`count(DISTINCT)`/`sum`/`min`/`max`/`avg`/`collect`, `ORDER BY`, `SKIP`, `LIMIT`,
  parameters, and the scalar functions the SNB queries and TCK read scenarios use (listed in research.md).
- **FR-009**: Graph scope: `USE GRAPH g` reads `g`; no `USE GRAPH` reads every graph, where a node is
  `(node_id, graph_id)`, its labels and properties are its own graph's, and an edge meets only nodes of its own graph
  (decided 2026-10-20). Today's SQL path joins on `node_id` alone across graphs (research.md §E); a prerequisite spec
  fixes the SQL path to this rule first (CHANGELOG: results change where a node id lives in several graphs), so the
  shadow gate compares two paths that mean the same thing. Single-graph installs (SNB, the TCK) are unchanged.
- **FR-010**: The default MUST NOT be turned on until the shadow run is green on the full TCK and the 40 SNB queries
  at SF0.1.

- **FR-011**: Before reading natively, the executor MUST check that the caller holds SELECT on `Graph_KG.nodes`,
  `rdf_labels`, `rdf_props` and `rdf_edges` (`$SYSTEM.SQL.Security.CheckPrivilege`, cached per process and user); if
  any is missing the query MUST take the SQL path, so SQL table privileges keep deciding what a caller can read
  (decided 2026-10-20: docs/OPERATIONS.md and docs/FHIR_GRAPH.md promise exactly that).

- **FR-012**: A defect the native work finds on the SQL path (an answer that is wrong by the spec's semantics, not a
  deliberate IVG rule) MUST be fixed on the SQL path first, as its own tested change with a CHANGELOG entry and the
  TCK green, and native implements the fixed behaviour; the shadow gate stays plain equality (decided 2026-10-20).
  Known at the start: the cross-graph join (FR-009), `min`/`max` over a property rounded to an int, the k-hop fast
  path reading only the default graph. Deliberate rules stay: untyped values, numeric-looking text ordering as a
  number.
- **FR-013**: Where the query does not totally order the rows a `LIMIT` or `SKIP` keeps, the shadow gate MUST compare
  row counts and check that each native row is a row of the SQL result without the `LIMIT`/`SKIP`; elsewhere it
  compares exactly (ordered where the query orders, as a multiset otherwise).
- **FR-014**: The native path MUST NOT run inside `bulk_load_session` (index maintenance is deferred there).
- **FR-015**: Results MUST be returned in pages (a process-private global read in chunks), never as one string: an
  ObjectScript string stops at ~3.6 MB.

### Key Entities

- **Plan**: a JSON pipeline the planner emits and the executor runs: operators (anchor, scan, expand, var-expand,
  filter, optional, project, aggregate, distinct, unwind, sort, skip/limit) over rows of bound variables.
- **Executor**: `Graph.KG.Cypher*` ObjectScript classes that run a plan in one call and return the result rows.
- **Property reader**: the layout-checked reader of `rdf_props`' globals, with the prepared-SQL fallback.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Shadow mode: TCK 3896/3896, zero native/SQL differences.
- **SC-002**: SNB SF0.1, 40 queries: zero native/SQL differences.
- **SC-003**: SNB SF0.1 same machine: IC1 and IC3 within 5x Neo4j; median IVG/Neo4j over the 40 queries at or below 2.
- **SC-004**: Planner coverage: the share of TCK read scenarios planned natively is reported; at least 80% at v1.

## Assumptions

- The spec 252 index is declared wherever the native path is expected to run.
- `rdf_props` keeps its DDL-generated storage; a layout change shows up in the probe, not as a wrong answer.
- The Python parser is the one source of the AST; the executor never parses Cypher.
