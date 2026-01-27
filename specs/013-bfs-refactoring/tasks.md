# Tasks: BFS Traversal Refactoring & Global Maintenance

**Input**: Design documents from `/specs/013-bfs-refactoring/`
**Prerequisites**: plan.md, spec.md, research.md, quickstart.md

**Tests**: 
- Functional: `pytest` for BFS parity.
- Performance: `tests/benchmarks/bfs_benchmark.py` comparing TEPS and UPS.
- Comparison: Neo4j Cypher benchmark.

---

## Phase 1: Benchmarking Infrastructure & Neo4j Comparison

**Goal**: Establish a performance baseline to beat.

- [X] T001 [P] Implement R-MAT scale-free graph generator in `tests/benchmarks/graph_gen.py`
- [X] T002 Setup Neo4j container and load synthetic graph for baseline comparison
- [X] T003 Measure Neo4j 5-6 hop Cypher traversal performance; save to `specs/013-bfs-refactoring/benchmark_results.md`
- [X] T004 Measure current IRIS `BFS_JSON` performance (requires initial safe-refactor to compile); save to `specs/013-bfs-refactoring/benchmark_results.md`

---

## Phase 2: RDF 1.2 Alignment & Functional Index Facility

**Goal**: Implement real-time maintenance and RDF 1.2 triple identity.

- [X] T005 [P] Create `Graph.KG.GraphIndex` in `iris_src/src/Graph/KG/GraphIndex.cls` extending `%Library.FunctionalIndex`
- [X] T006 [P] Implement `InsertIndex`, `UpdateIndex`, `DeleteIndex` in `Graph.KG.GraphIndex` using `$INCREMENT` for stats
- [X] T007 Create `Graph.KG.Edge` in `iris_src/src/Graph/KG/Edge.cls` to formally map `rdf_edges` with the Functional Index
- [X] T008 Update `rdf_edges` schema to include Unique constraint on `(s, p, o_id)`
- [X] T009 Update `rdf_props` to allow `edge_id` as subject `s` (RDF 1.2 Quoted Triples)
- [X] T010 Implement dynamic weight extraction in `GraphIndex` (default 1.0) from `qualifiers` column

---

## Phase 3: BFS Traversal Optimization (User Story 1 & 2)

**Goal**: Improve readability and instantiation performance.

- [X] T011 [US1] Extract `_traverse_with_predicate` and `_traverse_all_predicates` into internal Python helpers in `Traversal.cls`
- [X] T012 [US1] Refactor `BFS_JSON` main loop to reduce nesting to <= 3 levels
- [X] T013 [US3] Move from JSON serialization to direct `%DynamicObject` instantiation in traversal helpers
- [X] T014 Implement ASQ-inspired Query Signatures (Master Label Set) for $O(1)$ hop rejection

---

## Phase 4: Verification & Stress Testing

**Goal**: Confirm performance targets and functional parity.

- [X] T015 Verify real-time updates: SQL `INSERT` is instantly visible in 6-hop BFS
- [X] T016 Run final stress test (100k nodes, 6 hops) and document results in `benchmark_results.md`
- [X] T017 [P] Verify SC-006: <= 5% performance regression
- [X] T018 [P] Verify SC-007: >= 20% instantiation improvement
- [X] T019 Update requirements checklist in `specs/013-bfs-refactoring/checklists/requirements.md`

---

## Implementation Strategy

### MVP First
1. Establish IRIS vs Neo4j baseline.
2. Implement Functional Index for real-time updates.
3. Optimize BFS for readability and speed.

### Dependencies
- Phase 1 (Baseline) is required to quantify Phase 3/4 improvements.
- Phase 2 (Index) must be completed before Phase 4 (Real-time tests).
