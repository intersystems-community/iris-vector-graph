# Implementation Plan - Performance Optimization & Scalability

This plan addresses performance bottlenecks in the `iris-vector-graph` core and GraphQL API.

## User Review Required

> [!IMPORTANT]
> The implementation uses IRIS iFind indexes for substring search. This requires the `%iFind` package to be available in the IRIS instance. If not available, the system will fall back to standard indexes or functional indexes.

## Proposed Changes

### 1. IRIS Graph Core (Engine)

#### [IRISGraphEngine]
- Implement `get_nodes(node_ids)` using SQL `IN` clause to fetch labels and properties in batch.
- Implement `create_node(node_id, labels, properties)` with single-transaction atomic semantics.
- Add support for `executemany` in internal mutation methods.

#### [Schema]
- Update `rdf_props.val` to `VARCHAR(64000)`.
- Add `idx_props_val_ifind` on `rdf_props(val)`.
- Add `idx_edges_confidence` as a functional index on `JSON_VALUE(qualifiers, '$.confidence')`.

### 2. GraphQL API

#### [DataLoaders]
- Implement `GenericNodeLoader` to batch fetch nodes by ID.
- Optimize `ProteinLoader`, `GeneLoader`, and `PathwayLoader` to use the batch node retrieval logic.
- Ensure `created_at` is fetched in batch from the `nodes` table.

#### [Resolvers]
- Update `CoreQuery.node` and `nodes` to use `GenericNodeLoader`.
- Update `Mutation.create_protein` and `update_protein` to use transactional batching.

### 3. Substring Search Optimization

#### [Cypher Translator]
- Ensure `CONTAINS`, `STARTS WITH`, and `ENDS WITH` generate SQL that can leverage the iFind index.
- No changes needed to translator logic if standard `LIKE` syntax is used, as IRIS optimizer handles iFind mapping.

## Verification Plan

### Automated Tests
- `tests/unit/test_batch_retrieval.py`: Verify that `get_nodes` correctly aggregates data and executes minimal queries.
- `tests/unit/test_transactional_mutations.py`: Verify atomic behavior of batch creations.
- `tests/performance/test_property_search.py`: Benchmark substring search with and without iFind.
- `tests/integration/test_graphql_batching.py`: Use a mock database connection to count SQL executions for a nested GraphQL query.

### Manual Verification
- Check IRIS Management Portal to verify index creation and storage usage.
- Execute a large GraphQL query in Playground and monitor response time.
