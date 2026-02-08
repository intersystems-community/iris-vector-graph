# Detailed Implementation Plan: Performance & Scalability Enhancements

This document outlines the technical steps for implementing and verifying performance optimizations in the IRIS Vector Graph engine.

## 1. Batch Node Retrieval (`get_nodes`)

### Technical Approach
- **SQL Optimization**: Use `IN (?)` clauses to fetch data for multiple `node_ids` in a single round-trip.
- **Data Aggregation**: Efficiently merge results from `rdf_labels` and `rdf_props` into a list of node dictionaries.
- **Fallback Logic**: Maintain individual retrieval as a fallback for complex error cases.

### Tasks
- [x] Modify `IRISGraphEngine.get_nodes` to accept a list of IDs.
- [x] Implement label fetching via `IN` clause.
- [x] Implement property fetching via `IN` clause.
- [x] Implement node existence check via `nodes` table for "empty" nodes (those with no labels/props).

## 2. Advanced Indexing (`iFind` and Functional)

### Technical Approach
- **Substring Search**: Use IRIS `%iFind.Index.Basic` on `rdf_props.val`. This is significantly faster than standard `LIKE` for substring matches.
- **JSON Filtering**: Use a Functional Index on `JSON_VALUE(qualifiers, '$.confidence')` to allow the SQL optimizer to avoid parsing JSON strings during edge filtering.

### Tasks
- [x] Update `GraphSchema.get_base_schema_sql` with new index definitions.
- [x] Update `GraphSchema.ensure_indexes` to include new indexes.
- [x] Implement `GraphSchema.upgrade_val_column` for `VARCHAR(64000)` support.

## 3. Transactional Mutation Batching

### Technical Approach
- **Atomic Operations**: Wrap multi-table inserts (nodes, labels, props) in a single `START TRANSACTION ... COMMIT` block.
- **Batch Binding**: Use `cursor.executemany()` for labels and properties to reduce the number of individual insert commands.

### Tasks
- [x] Add `IRISGraphEngine.create_node` with internal batching.
- [x] Add `IRISGraphEngine.create_edge` with transactional safety.
- [x] Refactor GraphQL mutations to use these new methods.

## 4. Scale Testing (10,000+ Entities)

### Technical Approach
- **Data Generation**: Create a script to generate synthetic graph data (10k nodes, 50k edges, 100k properties).
- **Latency Benchmarking**:
    - Measure `get_nodes` latency for batch sizes of 1, 10, 100, and 1000.
    - Measure substring search latency with and without `iFind` index.
    - Measure bulk load throughput.

### Tasks
- [ ] Create `tests/performance/scale_benchmark.py`.
- [ ] Implement data generator for 10k+ entities.
- [ ] Benchmark `get_nodes` N+1 vs Batch performance.
- [ ] Benchmark `CONTAINS` query performance with `iFind`.
- [ ] Document results in `docs/performance/benchmarks_v2.md`.

## 5. Verification Timeline

1.  **Unit Verification**: Run existing unit tests to ensure no regressions. (Done)
2.  **Schema Verification**: Verify indexes are correctly applied in a fresh IRIS instance.
3.  **Scale Benchmark**: Run the 10k entity benchmark and verify target latencies (SC-001 through SC-003).
