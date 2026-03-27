
# Implementation Plan: GraphQL API Endpoint

**Branch**: `003-add-graphql-endpoint` | **Date**: 2025-10-02 | **Spec**: /Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/spec.md
**Input**: Feature specification from `/specs/003-add-graphql-endpoint/spec.md`

## Execution Flow (/plan command scope)
```
1. Load feature spec from Input path
   → If not found: ERROR "No feature spec at {path}"
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   → Detect Project Type from file system structure or context (web=frontend+backend, mobile=app+api)
   → Set Structure Decision based on project type
3. Fill the Constitution Check section based on the content of the constitution document.
4. Evaluate Constitution Check section below
   → If violations exist: Document in Complexity Tracking
   → If no justification possible: ERROR "Simplify approach first"
   → Update Progress Tracking: Initial Constitution Check
5. Execute Phase 0 → research.md
   → If NEEDS CLARIFICATION remain: ERROR "Resolve unknowns"
6. Execute Phase 1 → contracts, data-model.md, quickstart.md, agent-specific template file (e.g., `CLAUDE.md` for Claude Code, `.github/copilot-instructions.md` for GitHub Copilot, `GEMINI.md` for Gemini CLI, `QWEN.md` for Qwen Code or `AGENTS.md` for opencode).
7. Re-evaluate Constitution Check section
   → If new violations: Refactor design, return to Phase 1
   → Update Progress Tracking: Post-Design Constitution Check
8. Plan Phase 2 → Describe task generation approach (DO NOT create tasks.md)
9. STOP - Ready for /tasks command
```

**IMPORTANT**: The /plan command STOPS at step 7. Phases 2-4 are executed by other commands:
- Phase 2: /tasks command creates tasks.md
- Phase 3-4: Implementation execution (manual or via tools)

## Summary

Add GraphQL API endpoint with type-safe schema and efficient DataLoader batching for client-driven nested queries. Web/mobile developers will be able to fetch exactly the data they need in a single request using GraphQL's declarative syntax (e.g., `protein(id: "PROTEIN:TP53") { name, interactsWith { name } }`), with auto-generated documentation and strong typing eliminating API documentation overhead. The GraphQL layer uses Strawberry GraphQL with FastAPI (ASGI), provides real-time subscriptions via WebSocket, and implements mandatory DataLoader batching to prevent N+1 query problems. Resolvers execute on the existing NodePK schema with <10% overhead compared to hand-written SQL, leveraging HNSW indexes for vector similarity queries and supporting hybrid vector+graph operations.

## Technical Context
**Language/Version**: Python 3.11+ (existing codebase)
**Primary Dependencies**: FastAPI (ASGI framework), Strawberry GraphQL (type-safe schema), strawberry-graphql-django (DataLoader support), uvicorn (ASGI server), iris.connect() for database access, pydantic (validation), IRIS 2025.3+ (VECTOR support required)
**Storage**: InterSystems IRIS database with NodePK schema (nodes, rdf_edges, rdf_labels, rdf_props, kg_NodeEmbeddings)
**Testing**: pytest with live IRIS database (no mocked database per constitution), pytest-asyncio for async resolvers, contract tests for GraphQL schema validation
**Target Platform**: ASGI server (uvicorn/hypercorn), IRIS database backend (Linux/macOS containers), WebSocket support for subscriptions
**Project Type**: web (Python ASGI backend + IRIS database)
**Performance Goals**: Simple GraphQL queries (<3 levels deep) <10ms, query execution within 10% of SQL performance, DataLoader batching reduces N+1 to ≤2 SQL queries, vector k-NN <10ms with HNSW, ≥100 concurrent queries/second
**Constraints**: 10-level max query depth (configurable), 60-second resolver caching TTL with manual invalidation, 1000 concurrent WebSocket connections limit (configurable), CORS configurable (`*` for dev, explicit domains for prod), complexity limits prevent DoS attacks
**Scale/Scope**: Support graphs with 1M+ nodes without resolver degradation, ≥1000 WebSocket connections, 100+ GraphQL test cases for type coverage and edge cases

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**I. IRIS-Native Development** ✅
- GraphQL resolvers will use iris.connect() for all database access
- Vector search resolvers will leverage existing kg_KNN_VEC SQL operator via HNSW index
- REST API uses FastAPI (Python ASGI) for async request handling with iris.connect()
- No external graph database dependencies; all operations on existing IRIS tables
- DataLoader batching executes optimized SQL queries against IRIS

**II. Test-First Development with Live Database Validation** ✅
- GraphQL schema tests MUST validate type introspection before implementation
- All resolver tests MUST execute against live IRIS database
- DataLoader batching tests MUST verify ≤2 SQL queries for nested fetches
- Performance tests MUST validate <10% overhead vs hand-written SQL
- Subscription tests MUST validate real-time event delivery via WebSocket
- Test categories: @pytest.mark.requires_database, @pytest.mark.integration, @pytest.mark.e2e

**III. Performance as a Feature** ✅
- Simple GraphQL queries (<3 levels) target: <10ms
- Query execution MUST be within 10% of equivalent SQL
- DataLoader batching MUST reduce N+1 queries to ≤2 SQL queries
- Vector similarity queries MUST use HNSW index (ACORN-1 / IRIS 2025.3+)
- Query complexity limits enforced: max 10-level depth (configurable)
- Resolver caching with 60-second TTL reduces redundant database hits
- Performance benchmarks tracked in docs/performance/

**IV. Hybrid Search by Default** ✅
- Vector similarity resolver integrates HNSW index via kg_KNN_VEC
- Protein.similar field resolves using vector embeddings
- Design supports combining vector k-NN with graph pattern matching
- RRF fusion available via custom field resolvers
- Hybrid queries combine semantic (vector) + structural (graph) data

**V. Observability & Debuggability** ✅
- GraphQL queries MUST be logged with operation name, execution time, result size
- Resolver errors MUST include trace IDs and detailed error paths
- Failed queries MUST include GraphQL error format (message, locations, path)
- Performance metrics exposed: query rate, error rate, resolver execution time
- DataLoader batch metrics tracked (batch size, SQL query count)

**VI. Modular Core Library** ✅
- GraphQL resolvers module will be independent (iris_vector_graph/graphql/)
- Resolver logic will be database-agnostic (abstract data fetching)
- IRIS-specific integration in FastAPI router (api/graphql/)
- Reusable for integration with other RAG systems
- DataLoader pattern separates batching logic from business logic

**VII. Explicit Error Handling** ✅
- Schema validation errors MUST surface with actionable messages
- Resolver errors MUST distinguish timeout vs data not found vs FK constraint violations
- Mutation errors MUST indicate specific validation failures
- No silent failures; all error paths return structured GraphQL errors
- Subscription connection errors MUST be logged and handled gracefully

**VIII. Standardized Database Interfaces** ✅
- Will use existing iris.connect() patterns from IRISGraphEngine
- SQL query execution via cursor.execute() with parameterized queries
- Leverage existing operators (kg_KNN_VEC, kg_RRF_FUSE) where applicable
- DataLoader SQL generation follows established query patterns
- Contribute GraphQL utilities back to core library

**Initial Constitution Check: PASS** ✅
All 8 core principles satisfied. ASGI architecture preserves IRIS-Native principle via iris.connect() integration. No constitutional violations identified.

## Project Structure

### Documentation (this feature)
```
specs/[###-feature]/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
# Python ASGI Backend
api/
├── __init__.py
├── main.py                     # FastAPI application + Strawberry GraphQL integration
├── graphql/                    # NEW: GraphQL schema and resolvers
│   ├── __init__.py
│   ├── schema.py               # NEW: Strawberry GraphQL schema definition
│   ├── types.py                # NEW: Strawberry types (Node, Protein, Gene, Pathway)
│   ├── loaders.py              # NEW: DataLoader batching (MANDATORY)
│   ├── resolvers/
│   │   ├── __init__.py
│   │   ├── query.py            # NEW: Query resolvers (protein, gene, pathway lookups)
│   │   ├── mutation.py         # NEW: Mutation resolvers (create, update, delete)
│   │   └── subscription.py     # NEW: Subscription resolvers (WebSocket events)
│   └── directives.py           # NEW: Custom directives (caching, auth)
├── models/
│   ├── __init__.py
│   └── graphql.py              # NEW: Pydantic models for validation
└── dependencies.py             # IRIS connection pool, auth dependencies

iris_vector_graph/
├── graphql/                    # NEW: Database-agnostic GraphQL utilities
│   ├── __init__.py
│   ├── base_resolvers.py       # NEW: Abstract resolver base classes
│   ├── dataloader_base.py      # NEW: Generic DataLoader patterns
│   ├── complexity.py           # NEW: Query complexity calculator
│   └── cache.py                # NEW: Resolver caching utilities

tests/
├── contract/
│   └── test_graphql_schema.py           # NEW: GraphQL schema introspection tests
├── integration/
│   ├── test_graphql_queries.py          # NEW: Query resolver tests with live IRIS
│   ├── test_graphql_mutations.py        # NEW: Mutation resolver tests
│   ├── test_graphql_subscriptions.py    # NEW: Subscription WebSocket tests
│   └── test_graphql_dataloader.py       # NEW: DataLoader batching tests
└── unit/
    ├── test_graphql_types.py            # NEW: Strawberry type validation
    └── test_graphql_complexity.py       # NEW: Complexity calculation tests

scripts/performance/
└── test_graphql_performance.py          # NEW: GraphQL vs SQL performance benchmarks
```

**Structure Decision**: Web application structure with Python ASGI backend following openCypher endpoint pattern. GraphQL components split into:
1. **Database-agnostic core**: iris_vector_graph/graphql/ (base resolvers, DataLoader patterns, complexity calculation, caching)
2. **ASGI API layer**: api/graphql/ directory with Strawberry GraphQL schema, types, resolvers (query/mutation/subscription)
3. **IRIS integration**: Connection pooling via dependencies.py, iris.connect() in async context, DataLoader batching for SQL queries

All components leverage existing IRIS database schema (NodePK) without modifications. Strawberry GraphQL provides type-safe schema definition with Python type hints. FastAPI provides async/await support for concurrent query handling and WebSocket subscriptions (≥100 req/sec target).

## Phase 0: Outline & Research ✅ COMPLETE

**Research Topics Covered**:
1. GraphQL library selection → Strawberry GraphQL (native FastAPI integration, Python type hints)
2. DataLoader batching patterns → strawberry.dataloader with async/await, SQL batch query generation
3. Schema design → Node interface, Protein/Gene/Pathway types, Input types for mutations
4. Resolver implementation → Query/mutation/subscription resolvers, field resolvers, async patterns
5. WebSocket integration → strawberry.subscriptions with FastAPI WebSocket support
6. Vector search integration → Similar field resolver using HNSW index via kg_KNN_VEC
7. Caching strategies → @strawberry.field directive with 60s TTL, manual invalidation on mutations
8. Query complexity limits → Depth-based algorithm (10 levels max), configurable

**Output**: `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/research.md` (complete, all unknowns resolved)

## Phase 1: Design & Contracts ✅ COMPLETE

**Entities Defined** (in data-model.md):
1. **GraphQL Types**: Node (interface), Protein, Gene, Pathway, Interaction, SimilarProtein
2. **Input Types**: CreateProteinInput, UpdateProteinInput, ProteinFilter
3. **Custom Scalars**: JSON, DateTime
4. **Query Results**: ProteinNeighborhood, Path, GraphStats
5. **DataLoader Models**: ProteinLoader, EdgeLoader, PropertyLoader, LabelLoader

**GraphQL Schema Generated**:
- GraphQL SDL schema: `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/schema.graphql`
- Example queries: `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_queries.graphql`
- Example mutations: `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_mutations.graphql`
- Example subscriptions: `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_subscriptions.graphql`

**Test Scenarios Extracted** (from user stories):
1. Simple protein lookup with nested properties
2. Multi-hop graph traversal with DataLoader batching
3. Vector similarity query with hybrid results
4. Create protein mutation with FK validation
5. Subscription to real-time protein creation events
6. Query complexity limit enforcement (>10 levels rejected)

**Documentation**:
- Data model: `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/data-model.md`
- Quickstart guide: `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/quickstart.md`

**Agent Context Update**: To be executed post-approval (update CLAUDE.md with GraphQL-specific guidance)

**Output**: All Phase 1 artifacts complete, ready for constitution re-check

---

## Post-Design Constitution Re-Check ✅ PASS

Re-evaluating design artifacts against constitutional principles:

**I. IRIS-Native Development** ✅
- Design uses iris.connect() exclusively (no external graph database)
- Vector search resolvers leverage existing kg_KNN_VEC SQL operator with HNSW index
- FastAPI ASGI pattern maintains IRIS-native data access
- DataLoader SQL generation uses iris.cursor.execute() with parameterized queries

**II. Test-First Development** ✅
- GraphQL schema introspection tests specified in quickstart.md
- All resolver tests require live IRIS database (@pytest.mark.requires_database)
- DataLoader batching tests validate ≤2 SQL queries requirement
- Performance tests validate <10% overhead vs hand-written SQL

**III. Performance as a Feature** ✅
- Simple query target <10ms documented in data model
- DataLoader batching implementation prevents N+1 queries (mandatory requirement)
- Vector similarity queries use HNSW index (<10ms for k=10)
- Resolver caching with 60-second TTL reduces redundant database hits
- Performance benchmarks tracked in docs/performance/

**IV. Hybrid Search by Default** ✅
- Protein.similar field resolver integrates HNSW vector search
- Design supports combining vector k-NN with graph pattern matching
- Hybrid queries documented in example_queries.graphql
- RRF fusion available via custom field resolvers

**V. Observability & Debuggability** ✅
- QueryResult logging includes operation name, execution time, result size
- GraphQL errors include line/column numbers, error codes, trace IDs
- DataLoader batch metrics tracked (batch size, SQL query count)
- Resolver performance metrics exposed (query rate, error rate, execution time)

**VI. Modular Core Library** ✅
- GraphQL utilities in iris_vector_graph/graphql/ (database-agnostic)
- Resolver logic separated from IRIS integration
- DataLoader pattern generic and reusable
- Schema design allows integration with other RAG systems

**VII. Explicit Error Handling** ✅
- No silent failures: all error paths return structured GraphQL errors
- Error types: NOT_FOUND, VALIDATION_ERROR, FK_CONSTRAINT_VIOLATION, MAX_DEPTH_EXCEEDED, TIMEOUT
- Specific error codes with actionable suggestions
- Subscription connection errors logged and handled gracefully

**VIII. Standardized Database Interfaces** ✅
- Uses existing iris.connect() patterns from IRISGraphEngine
- Parameterized queries prevent SQL injection
- Leverages existing SQL operators (kg_KNN_VEC, kg_RRF_FUSE)
- DataLoader SQL generation follows established query patterns

**Post-Design Constitution Check: PASS** ✅
No new violations introduced. Design aligns with all 8 core principles. ASGI architecture maintains IRIS-Native principle through iris.connect() integration.

---

## Phase 2: Task Planning Approach
*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy** (TDD order):
1. **Contract Tests First** (fail initially):
   - test_graphql_schema_introspection.py → Schema type validation
   - test_graphql_queries.py → Query resolver contract tests
   - test_graphql_mutations.py → Mutation resolver contract tests
   - test_graphql_subscriptions.py → Subscription WebSocket tests
   - test_graphql_dataloader.py → DataLoader batching validation (≤2 SQL queries)
   - test_graphql_performance.py → <10% overhead validation

2. **Core GraphQL Types** (independent, parallel):
   - api/graphql/types.py → Strawberry types (Node, Protein, Gene, Pathway)
   - api/models/graphql.py → Pydantic validation models
   - iris_vector_graph/graphql/base_resolvers.py → Abstract resolver base classes

3. **DataLoader Implementation** (depends on types):
   - api/graphql/loaders.py → ProteinLoader, EdgeLoader, PropertyLoader, LabelLoader
   - iris_vector_graph/graphql/dataloader_base.py → Generic DataLoader patterns

4. **Resolvers** (depends on types and loaders):
   - api/graphql/resolvers/query.py → Query resolvers (protein, gene, pathway lookups)
   - api/graphql/resolvers/mutation.py → Mutation resolvers (create, update, delete)
   - api/graphql/resolvers/subscription.py → Subscription resolvers (WebSocket events)

5. **GraphQL Schema Integration** (depends on all above):
   - api/graphql/schema.py → Strawberry schema definition
   - api/main.py → FastAPI + Strawberry GraphQL integration
   - iris_vector_graph/graphql/complexity.py → Query complexity calculator
   - iris_vector_graph/graphql/cache.py → Resolver caching utilities

6. **Integration Tests** (depends on full stack):
   - tests/integration/test_graphql_e2e.py → End-to-end GraphQL execution
   - tests/integration/test_graphql_vector_search.py → HNSW integration tests

7. **Performance Validation**:
   - scripts/performance/test_graphql_performance.py → Benchmark suite
   - docs/performance/graphql_benchmarks.json → Results tracking

**Ordering Strategy**:
- TDD: All contract tests before implementation
- Dependency: Types → DataLoaders → Resolvers → Schema → Integration
- Parallel: Types, base classes, and utilities (independent)
- Sequential: Integration tests last (require full stack)

**Task Categories**:
- [P] = Parallel (can execute simultaneously)
- [S] = Sequential (depends on previous tasks)
- @requires_database = Must use live IRIS

**Estimated Output**: 30-35 numbered tasks in tasks.md

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation
*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)  
**Phase 4**: Implementation (execute tasks.md following constitutional principles)  
**Phase 5**: Validation (run tests, execute quickstart.md, performance validation)

## Complexity Tracking
*Fill ONLY if Constitution Check has violations that must be justified*

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |


## Progress Tracking
*This checklist is updated during execution flow*

**Phase Status**:
- [x] Phase 0: Research complete (/plan command) ✅
- [x] Phase 1: Design complete (/plan command) ✅
- [x] Phase 2: Task planning approach described (/plan command) ✅
- [ ] Phase 3: Tasks generated (/tasks command) - NEXT STEP
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS ✅
- [x] Post-Design Constitution Check: PASS ✅
- [x] All NEEDS CLARIFICATION resolved (none in Technical Context) ✅
- [x] Complexity deviations documented (none required) ✅

**Artifacts Generated**:
- [x] research.md → `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/research.md`
- [x] data-model.md → `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/data-model.md`
- [x] contracts/schema.graphql → `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/schema.graphql`
- [x] contracts/example_queries.graphql → `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_queries.graphql`
- [x] contracts/example_mutations.graphql → `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_mutations.graphql`
- [x] contracts/example_subscriptions.graphql → `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_subscriptions.graphql`
- [x] quickstart.md → `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/quickstart.md`
- [x] plan.md (this file) → Complete

**Next Command**: `/tasks` to generate tasks.md

---
*Based on Constitution v1.1.0 - See `.specify/memory/constitution.md`*
