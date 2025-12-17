
# Implementation Plan: openCypher Query Endpoint

**Branch**: `002-add-opencypher-endpoint` | **Date**: 2025-10-02 | **Spec**: /Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/spec.md
**Input**: Feature specification from `/specs/002-add-opencypher-endpoint/spec.md`

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

Add openCypher query endpoint with Cypher-to-SQL translation for intuitive graph pattern matching on the existing NodePK schema. Biomedical researchers will be able to express complex graph traversals using declarative Cypher syntax (e.g., `MATCH (p:Protein)-[:INTERACTS_WITH]->(target:Protein)`) instead of writing multi-table SQL JOINs. The translation layer parses Cypher queries, generates optimized IRIS SQL that operates on the existing `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, and `kg_NodeEmbeddings` tables, and executes with <10% performance overhead compared to hand-written SQL. Custom Cypher procedures enable hybrid vector+graph queries leveraging HNSW indexes.

## Technical Context
**Language/Version**: Python 3.11+ (existing codebase)
**Primary Dependencies**: FastAPI (ASGI framework), opencypher parser (or libcypher-parser), iris.connect() for database access, uvicorn (ASGI server), IRIS 2025.3+ (VECTOR support required)
**Storage**: InterSystems IRIS database with NodePK schema (nodes, rdf_edges, rdf_labels, rdf_props, kg_NodeEmbeddings)
**Testing**: pytest with live IRIS database (no mocked database per constitution), pytest-asyncio for async endpoints, contract tests for REST API
**Target Platform**: ASGI server (uvicorn/hypercorn), IRIS database backend (Linux/macOS containers)
**Project Type**: web (Python ASGI backend + IRIS database)
**Performance Goals**: Query translation <10ms for simple queries, end-to-end execution within 10% of hand-written SQL, vector k-NN <10ms with HNSW, ≥100 concurrent requests/second
**Constraints**: 30-second timeout (configurable), 10-hop max depth for variable-length paths (configurable), SQL injection prevention via parameterized queries, async/await for concurrent query handling
**Scale/Scope**: Support graphs with 1M+ nodes and 10M+ edges, ≥100 queries/second concurrency, 100+ test cases for syntax coverage

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**I. IRIS-Native Development** ✅
- Cypher-to-SQL translator will use iris.connect() for database access
- Custom vector search procedures will be implemented as SQL stored procedures (CALL db.index.vector.queryNodes)
- REST API will use FastAPI (Python ASGI) for async request handling with iris.connect()
- No external graph database dependencies; all operations on existing IRIS tables

**II. Test-First Development with Live Database Validation** ✅
- Contract tests MUST be written for REST API endpoint before implementation
- All Cypher-to-SQL translation tests MUST execute against live IRIS database
- Performance tests MUST validate <10% overhead vs hand-written SQL
- Integration tests MUST verify FK constraint validation during query execution
- Test categories: @pytest.mark.requires_database, @pytest.mark.integration

**III. Performance as a Feature** ✅
- Query translation target: <10ms for simple queries (<5 nodes)
- End-to-end execution MUST be within 10% of hand-written SQL
- Vector k-NN queries MUST use HNSW index (ACORN-1 / IRIS 2025.3+)
- Query complexity limits enforced: max 10 hops for variable-length paths
- Performance benchmarks tracked in docs/performance/

**IV. Hybrid Search by Default** ✅
- Custom Cypher procedures enable combining vector k-NN with graph pattern matching
- CALL db.index.vector.queryNodes() integrates with MATCH clauses
- RRF fusion can be invoked via Cypher stored procedures
- Design supports semantic (vector) + structural (graph) queries

**V. Observability & Debuggability** ✅
- Cypher queries MUST be logged with execution time and result row count
- Translation errors MUST include line/column numbers from parser
- Failed queries MUST include trace IDs for debugging
- Performance metrics exposed: query rate, error rate, translation time, execution time

**VI. Modular Core Library** ✅
- Cypher parser module will be independent (iris_vector_graph/cypher/)
- AST-to-SQL translator will be database-agnostic (abstract SQL generation)
- IRIS-specific integration in FastAPI router (api/routers/cypher.py)
- Reusable for integration with other RAG systems

**VII. Explicit Error Handling** ✅
- Parser errors MUST surface with actionable messages (line/column numbers)
- SQL translation errors MUST indicate specific Cypher construct that failed
- Query execution errors MUST distinguish timeout vs syntax vs FK constraint violations
- No silent failures; all error paths explicitly handled

**VIII. Standardized Database Interfaces** ✅
- Will use existing iris.connect() patterns from IRISGraphEngine
- SQL query execution via cursor.execute() with parameterized queries
- Leverage existing operators (kg_KNN_VEC, kg_RRF_FUSE) where applicable
- Contribute Cypher translation utilities back to core library

**Initial Constitution Check: PASS** ✅
All 8 core principles satisfied. No constitutional violations identified.

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
├── main.py                 # NEW: FastAPI application entry point
├── routers/
│   ├── __init__.py
│   └── cypher.py           # NEW: Cypher endpoint router (POST /api/cypher)
├── models/
│   ├── __init__.py
│   └── cypher.py           # NEW: Pydantic models for requests/responses
└── dependencies.py         # NEW: IRIS connection pool, auth dependencies

iris_vector_graph/
├── cypher/                 # NEW: Cypher parser and translator (database-agnostic)
│   ├── __init__.py
│   ├── parser.py           # Cypher query parser (opencypher wrapper)
│   ├── ast.py              # AST node definitions
│   ├── translator.py       # AST-to-SQL translator
│   ├── optimizer.py        # Query optimization (label pushdown, property pushdown)
│   └── procedures.py       # Custom Cypher procedures (vector search)

sql/
├── procedures/             # NEW: Cypher-related SQL procedures
│   └── cypher_vector_search.sql   # CALL db.index.vector.queryNodes()

tests/
├── contract/
│   └── test_cypher_api.py          # NEW: REST API contract tests
├── integration/
│   ├── test_cypher_parser.py       # NEW: Parser tests with live IRIS
│   ├── test_cypher_translator.py   # NEW: Translation tests with live IRIS
│   └── test_cypher_e2e.py          # NEW: End-to-end Cypher query tests
└── unit/
    ├── test_cypher_ast.py          # NEW: AST construction tests
    └── test_cypher_optimizer.py    # NEW: Optimization logic tests

scripts/performance/
└── test_cypher_performance.py      # NEW: Cypher vs SQL performance benchmarks
```

**Structure Decision**: Web application structure with Python ASGI backend. Cypher components split into:
1. **Database-agnostic core**: iris_vector_graph/cypher/ (parser, translator, optimizer)
2. **ASGI API layer**: api/ directory with FastAPI routers, Pydantic models, async endpoints
3. **IRIS integration**: Connection pooling via dependencies.py, iris.connect() in async context

All components leverage existing IRIS database schema (NodePK) without modifications. FastAPI provides async/await support for concurrent query handling (≥100 req/sec target).

## Phase 0: Outline & Research ✅ COMPLETE

**Research Topics Covered**:
1. Cypher parser library selection → opencypher (Python, Apache 2.0)
2. SQL translation patterns → Node, relationship, variable-length path patterns documented
3. IRIS SQL capabilities → Recursive CTEs fully supported with cycle detection
4. Performance optimization → Label pushdown, property pushdown, index hints, query caching
5. Vector search integration → Custom CALL procedures invoking kg_KNN_VEC
6. Error handling & validation → Line/column error reporting, parameterized queries

**Output**: `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/research.md` (complete, all unknowns resolved)

## Phase 1: Design & Contracts ✅ COMPLETE

**Entities Defined** (in data-model.md):
1. **Cypher AST**: CypherQuery, MatchClause, GraphPattern, NodePattern, RelationshipPattern, WhereClause, ReturnClause, CypherProcedureCall
2. **Translation Artifacts**: SQLQuery, QueryMetadata, TranslationContext
3. **Response Models**: QueryResult, QueryError
4. **Configuration**: CypherConfig

**API Contracts Generated**:
- OpenAPI 3.0 spec: `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/contracts/cypher_api.yaml`
- Endpoint: POST /api/cypher
- Request schema: CypherQueryRequest (query, parameters, timeout, optimization flags)
- Response schemas: CypherQueryResponse (success), CypherErrorResponse (errors)
- Error codes: SYNTAX_ERROR, UNDEFINED_VARIABLE, QUERY_TIMEOUT, FK_CONSTRAINT_VIOLATION, etc.

**Test Scenarios Extracted** (from user stories):
1. Simple protein lookup with label + property filters
2. Multi-hop graph traversal (2-3 hops)
3. Variable-length paths with cycle detection
4. Hybrid vector+graph query with CALL procedure
5. Parameterized queries for SQL injection prevention
6. Aggregation queries (count, collect)

**Documentation**:
- Data model: `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/data-model.md`
- Quickstart guide: `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/quickstart.md`

**Agent Context Update**: To be executed post-approval (update CLAUDE.md with Cypher-specific guidance)

**Output**: All Phase 1 artifacts complete, ready for constitution re-check

---

## Post-Design Constitution Re-Check ✅ PASS

Re-evaluating design artifacts against constitutional principles:

**I. IRIS-Native Development** ✅
- Design uses iris.connect() exclusively (no external graph database)
- Custom procedures leverage existing kg_KNN_VEC SQL operator
- REST API follows Graph.KG.Service pattern (ObjectScript)

**II. Test-First Development** ✅
- Contract tests specified in quickstart.md (test_cypher_protein_lookup, test_cypher_performance_overhead)
- All tests require live IRIS database (@pytest.mark.requires_database)
- Performance tests validate <10% overhead requirement

**III. Performance as a Feature** ✅
- Translation target <10ms documented in data model
- Query optimization strategies defined (label pushdown, property pushdown, caching)
- Performance benchmarks tracked in docs/performance/cypher_benchmarks.json

**IV. Hybrid Search by Default** ✅
- Custom CALL procedures enable vector+graph queries
- CypherProcedureCall AST node supports db.index.vector.queryNodes()
- Design integrates with existing kg_KNN_VEC and kg_RRF_FUSE operators

**V. Observability & Debuggability** ✅
- QueryResult includes executionTimeMs, translationTimeMs, traceId
- QueryError includes line/column numbers, error codes, suggestions
- queryMetadata optional field for debugging (SQL query, indexes used, optimizations applied)

**VI. Modular Core Library** ✅
- Cypher parser/translator in iris_vector_graph/cypher/ (database-agnostic)
- IRIS integration separated in Graph.KG.CypherService.cls
- AST design allows reuse in other systems

**VII. Explicit Error Handling** ✅
- No silent failures: all error paths return QueryError
- Error types: syntax, translation, execution, timeout
- Specific error codes with actionable suggestions

**VIII. Standardized Database Interfaces** ✅
- Uses existing iris.connect() patterns
- Parameterized queries prevent SQL injection
- Leverages existing SQL operators (kg_KNN_VEC)

**Post-Design Constitution Check: PASS** ✅
No new violations introduced. Design aligns with all 8 core principles.

---

## Phase 2: Task Planning Approach
*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy** (TDD order):
1. **Contract Tests First** (fail initially):
   - test_cypher_api_contract.py → POST /api/cypher schema validation
   - test_cypher_syntax_errors.py → Error response format validation
   - test_cypher_performance.py → <10% overhead validation

2. **Core AST Models** (independent, parallel):
   - cypher/ast.py → AST node classes (CypherQuery, NodePattern, etc.)
   - cypher/parser.py → opencypher wrapper with error handling

3. **SQL Translation** (depends on AST):
   - cypher/translator.py → AST-to-SQL translator
   - cypher/optimizer.py → Query optimization (label/property pushdown)

4. **Custom Procedures** (depends on translator):
   - sql/procedures/cypher_vector_search.sql → db.index.vector.queryNodes()
   - cypher/procedures.py → Procedure invocation handling

5. **REST API Integration** (depends on all above):
   - iris/src/Graph/KG/CypherService.cls → ObjectScript REST endpoint
   - Integration tests → End-to-end Cypher query execution

6. **Performance Validation**:
   - scripts/performance/test_cypher_performance.py → Benchmark suite
   - docs/performance/cypher_benchmarks.json → Results tracking

**Ordering Strategy**:
- TDD: All contract tests before implementation
- Dependency: AST → Translator → Procedures → REST API
- Parallel: AST models, parser tests, optimization logic (independent)
- Sequential: Integration tests last (require full stack)

**Task Categories**:
- [P] = Parallel (can execute simultaneously)
- [S] = Sequential (depends on previous tasks)
- @requires_database = Must use live IRIS

**Estimated Output**: 28-32 numbered tasks in tasks.md

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
- [x] research.md → `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/research.md`
- [x] data-model.md → `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/data-model.md`
- [x] contracts/cypher_api.yaml → `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/contracts/cypher_api.yaml`
- [x] quickstart.md → `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/quickstart.md`
- [x] plan.md (this file) → Complete

**Next Command**: `/tasks` to generate tasks.md

---
*Based on Constitution v1.1.0 - See `.specify/memory/constitution.md`*
