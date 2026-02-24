# Tasks: Auto-Generating GraphQL Layer for IRIS Graph Stores

**Feature**: Auto-Generating GraphQL Layer  
**Plan**: [plan.md](./plan.md)

## Implementation Strategy

We follow an **MVP-first** strategy, focusing on the zero-config server (US1) and basic node queries (US2) as the foundational increment. Each subsequent user story adds a specific graph capability (Search, Traversal, Cypher).

## Dependencies

- US1 (Discovery) is the prerequisite for all other stories.
- US2 (Node Queries) depends on US1.
- US3, US4, US5 are independent but require US1.

## Phase 1: Setup

- [x] T001 Create module directory `iris_vector_graph/gql/`
- [x] T002 Initialize `iris_vector_graph/gql/__init__.py` with `serve()` stub
- [x] T003 [P] Add necessary dependencies to `pyproject.toml` (fastapi, strawberry-graphql[fastapi])

## Phase 2: Foundational (Connection Management)

- [x] T004 Implement `AsyncConnectionPool` with `asyncio.Semaphore(5)` in `iris_vector_graph/gql/pooling.py`
- [x] T005 Implement `get_pool()` singleton and context manager for connection acquisition in `iris_vector_graph/gql/pooling.py`

## Phase 3: [US1] Core Server & Schema Discovery

**Goal**: Start a GraphQL server that introspects node labels.  
**Test**: `gql.serve(engine)` starts a server where `__schema { types { name } }` contains discovered labels.

- [x] T006 [US1] Implement graph introspection logic (label discovery) in `iris_vector_graph/gql/engine.py`
- [x] T007 [US1] Implement dynamic `Node` interface and base `Query` class in `iris_vector_graph/gql/schema.py`
- [x] T008 [US1] Implement `serve(engine, host, port)` to wrap Strawberry ASGI in FastAPI in `iris_vector_graph/gql/__init__.py`
- [x] T009 [US1] Create E2E test for zero-config startup in `tests/e2e/test_gql_autogen_startup.py`

## Phase 4: [US2] Node & Property Queries

**Goal**: Query nodes by label with top-level property fields.  
**Test**: Query `nodes(label: "Protein") { id p_name }` returns data.

- [x] T010 [P] [US2] Implement property sampling logic (1,000 nodes limit) in `iris_vector_graph/gql/engine.py`
- [x] T011 [US2] Implement dynamic Strawberry type factory for sampled properties, ensuring internal fields (e.g., embeddings) are filtered out in `iris_vector_graph/gql/schema.py`
- [x] T012 [US2] Implement property keyword collision handling (prefix `p_`) in `iris_vector_graph/gql/schema.py`
- [x] T013 [US2] Implement generic `node` and `nodes` resolvers in `iris_vector_graph/gql/resolvers.py`
- [x] T014 [US2] Create E2E test for node/property retrieval in `tests/e2e/test_gql_node_queries.py`

## Phase 5: [US3] Semantic Search

**Goal**: Find nodes using natural language similarity.  
**Test**: Query `semanticSearch(query: "diabetes")` returns nodes with scores.

- [x] T015 [US3] Implement `SemanticSearchResult` type in `iris_vector_graph/gql/schema.py`
- [x] T016 [US3] Implement semantic search resolver using engine's `kg_KNN_VEC` in `iris_vector_graph/gql/resolvers.py`
- [x] T017 [US3] Create E2E test for semantic search in `tests/e2e/test_gql_semantic_search.py`

## Phase 6: [US4] Bi-directional Traversal

**Goal**: Navigate incoming and outgoing relationships.  
**Test**: Query `node(id: "X") { outgoing { targetId } incoming { targetId } }` works.

- [x] T018 [US4] Implement `Relationship` type and `Direction` enum in `iris_vector_graph/gql/schema.py`
- [x] T019 [US4] Implement relationship resolvers (incoming/outgoing) in `iris_vector_graph/gql/resolvers.py`
- [x] T020 [US4] Implement `neighbors` helper field on `Node` in `iris_vector_graph/gql/resolvers.py`
- [x] T021 [US4] Create E2E test for graph traversal in `tests/e2e/test_gql_traversal.py`

## Phase 7: [US5] Advanced Query Passthrough

**Goal**: Execute raw Cypher via GraphQL.  
**Test**: Query `cypher(query: "MATCH ...") { rows }` returns serialized data.

- [x] T022 [US5] Implement recursive JSON serializer for IRIS result sets in `iris_vector_graph/gql/resolvers.py`
- [x] T023 [US5] Implement `cypher` query field and resolver in `iris_vector_graph/gql/resolvers.py`
- [x] T024 [US5] Create E2E test for Cypher passthrough in `tests/e2e/test_gql_cypher_passthrough.py`

## Phase 8: Polish & Refactoring

- [x] T025 [P] Add docstrings and type hints to all new modules
- [x] T026 Update `README.md` with GraphQL layer instructions
- [x] T027 Refactor existing Posos demo server to use `gql.serve()`
- [x] T028 Implement global exception handler for IRIS-specific errors (Access Denied, License Limit) in `iris_vector_graph/gql/__init__.py`
