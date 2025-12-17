# Tasks: openCypher Query Endpoint

**Feature**: openCypher-to-SQL translation for IRIS Vector Graph
**Branch**: `002-add-opencypher-endpoint`
**Input**: Design documents from `/Users/tdyar/ws/iris-vector-graph/specs/002-add-opencypher-endpoint/`
**Prerequisites**: plan.md, research.md, data-model.md, contracts/cypher_api.yaml, quickstart.md

## Execution Flow
```
1. Load plan.md from feature directory ✅
   → Extract: Python 3.11+, FastAPI, opencypher, uvicorn, IRIS 2025.3+
   → Structure: Web app (Python ASGI backend + IRIS database)
2. Load optional design documents ✅
   → data-model.md: 8 AST entities, 3 translation artifacts, 2 result types
   → contracts/cypher_api.yaml: POST /api/cypher endpoint
   → research.md: opencypher parser, SQL translation patterns, HNSW integration
3. Generate tasks by category (TDD order):
   → Setup: Dependencies, directory structure, linting (T001-T003)
   → Tests First: Contract tests, parser tests (T004-T005)
   → Core Models: AST classes, Pydantic models (T006-T013)
   → Parser: Cypher parser wrapper with error handling (T014-T016)
   → Translator: AST-to-SQL translation (T017-T020)
   → Optimizer: Query optimization (T021-T022)
   → SQL Procedures: Custom vector search procedure (T023)
   → FastAPI: Router, connection pooling (T024-T026)
   → Integration Tests: Live IRIS tests (T027-T029)
   → Performance & Docs: Benchmarks, documentation (T030-T032)
4. Apply task rules:
   → Different files = mark [P] for parallel
   → Same file = sequential (no [P])
   → Tests before implementation (TDD)
5. Number tasks sequentially (T001-T032)
6. Generate dependency graph
7. Create parallel execution examples
8. Validation: All contracts tested ✅, entities modeled ✅, TDD order ✅
9. Return: SUCCESS (32 tasks ready for execution)
```

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

---

## Phase 3.1: Setup (T001-T003)

### T001: Install Python dependencies for openCypher endpoint
**Files**: `/Users/tdyar/ws/iris-vector-graph/pyproject.toml`
**Description**: Add FastAPI, uvicorn, opencypher, pydantic, and pytest-asyncio dependencies to pyproject.toml. Run `uv sync` to install.
**Dependencies**: None
**Validation**: Import test passes: `python -c "import fastapi, opencypher, pydantic"`

### T002: Create directory structure for Cypher components
**Files**:
- `/Users/tdyar/ws/iris-vector-graph/api/__init__.py`
- `/Users/tdyar/ws/iris-vector-graph/api/routers/__init__.py`
- `/Users/tdyar/ws/iris-vector-graph/api/models/__init__.py`
- `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/__init__.py`
**Description**: Create directory structure: api/, api/routers/, api/models/, iris_vector_graph/cypher/ with __init__.py files.
**Dependencies**: None
**Validation**: All directories exist and are importable

### T003 [P]: Configure linting and type checking for async code
**Files**: `/Users/tdyar/ws/iris-vector-graph/pyproject.toml`
**Description**: Update pyproject.toml to configure mypy for async/await type checking, add FastAPI-specific black/isort rules.
**Dependencies**: T001
**Validation**: `mypy api/` passes with async type hints, `black --check api/` passes

---

## Phase 3.2: Tests First (TDD) - Contract Tests (T004-T005)
**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**

### T004 [P]: Write failing contract test for POST /api/cypher endpoint success case
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/contract/test_cypher_api.py`
**Description**: Create contract test validating CypherQueryResponse schema per contracts/cypher_api.yaml. Test simple MATCH query returning columns, rows, rowCount, executionTimeMs, translationTimeMs, traceId fields. Mark with @pytest.mark.requires_database.
**Dependencies**: T002
**Contract Reference**: cypher_api.yaml lines 60-78 (CypherQueryResponse schema)
**Expected**: Test FAILS (endpoint not implemented yet)

### T005 [P]: Write failing contract test for POST /api/cypher endpoint error cases
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/contract/test_cypher_api_errors.py`
**Description**: Create contract tests for error responses per contracts/cypher_api.yaml: syntax error (line 86-95), undefined variable (line 96-105), timeout (line 112-120), FK constraint violation (line 144-149). Validate CypherErrorResponse schema with errorType, message, errorCode, line, column, suggestion, traceId.
**Dependencies**: T002
**Contract Reference**: cypher_api.yaml lines 79-149 (error response schemas)
**Expected**: Tests FAIL (error handling not implemented yet)

---

## Phase 3.3: Core Models (T006-T013) - AST and Pydantic Models
**Run ONLY after contract tests are failing**

### T006 [P]: Create CypherQuery AST root node class
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/ast.py`
**Description**: Implement CypherQuery dataclass with fields: match_clauses (List[MatchClause]), where_clause (Optional[WhereClause]), return_clause (ReturnClause), order_by_clause, skip_clause, limit_clause, union_clause. Add validation: at least one MATCH clause, exactly one RETURN clause.
**Dependencies**: T002
**Data Model Reference**: data-model.md lines 11-40
**Expected**: AST root node ready for parser integration

### T007 [P]: Create NodePattern and RelationshipPattern AST classes
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/ast.py`
**Description**: Implement NodePattern dataclass (variable, labels, properties) and RelationshipPattern dataclass (variable, types, direction, properties, variable_length). Add VariableLength dataclass (min_hops, max_hops with validation: 1 ≤ min_hops ≤ max_hops ≤ 10).
**Dependencies**: T002
**Data Model Reference**: data-model.md lines 84-155
**Expected**: Node and relationship patterns ready for graph pattern construction

### T008 [P]: Create MatchClause and GraphPattern AST classes
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/ast.py`
**Description**: Implement MatchClause dataclass (optional: bool, pattern: GraphPattern) and GraphPattern dataclass (nodes: List[NodePattern], relationships: List[RelationshipPattern]). Add validation: len(relationships) = len(nodes) - 1.
**Dependencies**: T007
**Data Model Reference**: data-model.md lines 42-82
**Expected**: MATCH clause structure complete

### T009 [P]: Create WhereClause and BooleanExpression AST classes
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/ast.py`
**Description**: Implement WhereClause dataclass and recursive BooleanExpression dataclass (operator, operands). Support operators: AND, OR, NOT, EQUALS, LIKE, IN, GT, LT, etc. Include PropertyReference and Literal helper classes.
**Dependencies**: T002
**Data Model Reference**: data-model.md lines 157-195
**Expected**: WHERE clause filtering ready

### T010 [P]: Create ReturnClause AST class
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/ast.py`
**Description**: Implement ReturnClause dataclass (distinct: bool, items: List[ReturnItem]) and ReturnItem dataclass (expression, alias). Support PropertyReference, Variable, and AggregationFunction expressions.
**Dependencies**: T002
**Data Model Reference**: data-model.md lines 197-236
**Expected**: RETURN clause projection ready

### T011 [P]: Create CypherProcedureCall AST class for vector search
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/ast.py`
**Description**: Implement CypherProcedureCall dataclass (procedure_name, arguments, yield_items). Support db.index.vector.queryNodes procedure for hybrid vector+graph queries.
**Dependencies**: T002
**Data Model Reference**: data-model.md lines 238-263
**Expected**: Custom procedure invocation ready

### T012 [P]: Create SQLQuery translation artifact class
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/translator.py`
**Description**: Implement SQLQuery dataclass (sql: str, parameters: List[Any], query_metadata: QueryMetadata) and QueryMetadata dataclass (estimated_rows, index_usage, optimization_applied, complexity_score). Include TranslationContext for stateful SQL generation.
**Dependencies**: T002
**Data Model Reference**: data-model.md lines 265-343
**Expected**: SQL translation artifacts ready

### T013 [P]: Create Pydantic models for FastAPI endpoint
**Files**: `/Users/tdyar/ws/iris-vector-graph/api/models/cypher.py`
**Description**: Implement CypherQueryRequest Pydantic model (query: str, parameters: Optional[Dict], timeout: int = 30, enableOptimization: bool = True, enableCache: bool = True) and CypherQueryResponse model (columns, rows, rowCount, executionTimeMs, translationTimeMs, queryMetadata, traceId). Implement CypherErrorResponse model (errorType, message, line, column, errorCode, suggestion, traceId).
**Dependencies**: T002
**Contract Reference**: cypher_api.yaml lines 152-303
**Expected**: API request/response models ready for FastAPI router

---

## Phase 3.4: Parser Implementation (T014-T016)

### T014: Write failing unit tests for Cypher parser wrapper
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/unit/test_cypher_parser.py`
**Description**: Create unit tests for Cypher parser wrapper: test simple MATCH parsing, test syntax error with line/column numbers, test node pattern with labels and properties, test relationship pattern with direction, test variable-length paths. Each test should verify AST structure matches expected nodes.
**Dependencies**: T006-T011
**Expected**: Tests FAIL (parser not implemented yet)

### T015: Implement Cypher parser wrapper using opencypher
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/parser.py`
**Description**: Implement parse_query() function wrapping opencypher.parse_query(). Convert opencypher AST to internal AST classes (CypherQuery, NodePattern, etc.). Extract line/column numbers from parser for error reporting.
**Dependencies**: T014
**Research Reference**: research.md lines 9-44 (opencypher selection)
**Expected**: T014 tests PASS

### T016: Implement parser error handling with line/column numbers
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/parser.py`
**Description**: Add exception handling for opencypher SyntaxError. Return structured error with line, column, message, suggestion (e.g., "Did you mean 'RETURN'?" for typos). Include original query in error context.
**Dependencies**: T015
**Expected**: Syntax errors include actionable line/column information

---

## Phase 3.5: Translator Implementation (T017-T020)

### T017: Write failing unit tests for AST-to-SQL translator (simple MATCH)
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/unit/test_cypher_translator.py`
**Description**: Create unit tests for SQL translation: test simple node pattern (MATCH (n:Protein) RETURN n), test node with property filter (MATCH (p {id: 'PROTEIN:TP53'}) RETURN p), test parameterized queries. Validate generated SQL structure and parameters list.
**Dependencies**: T012
**Expected**: Tests FAIL (translator not implemented yet)

### T018: Implement AST-to-SQL translator for node patterns
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/translator.py`
**Description**: Implement translate_to_sql(cypher_query: CypherQuery) -> SQLQuery. Generate SELECT with JOINs to rdf_labels and rdf_props for node patterns. Use TranslationContext to track variable mappings and accumulate JOIN clauses. Apply label filter pushdown optimization.
**Dependencies**: T017
**Research Reference**: research.md lines 48-73 (node pattern translation)
**Expected**: T017 tests PASS

### T019: Implement AST-to-SQL translator for relationship patterns
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/translator.py`
**Description**: Extend translator to handle relationship traversal. Generate JOINs to rdf_edges table with direction handling (outgoing, incoming, bidirectional). Support bidirectional relationships via UNION. Add property filters on relationships.
**Dependencies**: T018
**Research Reference**: research.md lines 75-95 (relationship translation)
**Expected**: Graph traversal queries translate to SQL

### T020: Implement AST-to-SQL translator for WHERE clauses and RETURN clauses
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/translator.py`
**Description**: Translate BooleanExpression to SQL WHERE conditions. Support operators: AND, OR, NOT, EQUALS, LIKE, IN, comparison operators. Translate ReturnClause to SELECT projections with property references. Handle DISTINCT, ORDER BY, LIMIT, SKIP. Support aggregation functions (count, collect).
**Dependencies**: T019
**Data Model Reference**: data-model.md lines 157-236
**Expected**: Complex queries with filtering and ordering translate correctly

---

## Phase 3.6: Optimizer Implementation (T021-T022)

### T021 [P]: Write failing unit tests for query optimizer
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/unit/test_cypher_optimizer.py`
**Description**: Create unit tests for query optimization: test label filter pushdown to JOIN ON clause, test property filter pushdown, test index hint injection (if enabled), test query plan caching. Verify optimized SQL uses fewer table scans.
**Dependencies**: T012
**Expected**: Tests FAIL (optimizer not implemented yet)

### T022 [P]: Implement query optimizer with label and property pushdown
**Files**: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/cypher/optimizer.py`
**Description**: Implement optimize_sql(sql_query: SQLQuery) -> SQLQuery. Apply label filter pushdown (move label filters from WHERE to JOIN ON). Apply property filter pushdown (combine property JOINs where possible). Add optional index hints for IRIS query planner. Update QueryMetadata.optimization_applied.
**Dependencies**: T021
**Research Reference**: research.md lines 175-240 (optimization strategies)
**Expected**: T021 tests PASS, optimized queries faster than naive translation

---

## Phase 3.7: SQL Procedures (T023)

### T023: Create custom Cypher vector search SQL procedure
**Files**: `/Users/tdyar/ws/iris-vector-graph/sql/procedures/cypher_vector_search.sql`
**Description**: Create SQL stored procedure db_index_vector_queryNodes(indexName, k, queryVector) that wraps existing kg_KNN_VEC operator. Store results in session temp table cypher_vector_results(node_id, score). Validate index exists, handle FK constraints.
**Dependencies**: None (SQL-only)
**Research Reference**: research.md lines 256-307 (vector search integration)
**Expected**: CALL db.index.vector.queryNodes() invocable from SQL

---

## Phase 3.8: FastAPI Endpoint Implementation (T024-T026)

### T024: Write failing integration test for FastAPI Cypher router
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/integration/test_cypher_e2e.py`
**Description**: Create end-to-end integration test for POST /api/cypher endpoint. Test simple MATCH query, test parameterized query, test syntax error response, test timeout response. Mark with @pytest.mark.requires_database and @pytest.mark.integration. Use TestClient from fastapi.testclient.
**Dependencies**: T013
**Expected**: Tests FAIL (router not implemented yet)

### T025: Implement FastAPI Cypher router with async endpoint
**Files**: `/Users/tdyar/ws/iris-vector-graph/api/routers/cypher.py`
**Description**: Implement async POST /api/cypher endpoint. Parse CypherQueryRequest, call parser, translator, optimizer, execute SQL via iris.connect(), format CypherQueryResponse. Handle exceptions and return CypherErrorResponse with appropriate HTTP status codes (400 for syntax, 408 for timeout, 500 for execution errors). Generate unique traceId for each request. Log query execution with timing.
**Dependencies**: T024, T015-T020, T022
**Contract Reference**: cypher_api.yaml lines 16-149
**Expected**: T024 tests PASS, T004-T005 contract tests PASS

### T026: Implement IRIS connection pooling and dependencies
**Files**: `/Users/tdyar/ws/iris-vector-graph/api/dependencies.py`, `/Users/tdyar/ws/iris-vector-graph/api/main.py`
**Description**: Create async IRIS connection pool dependency using iris.connect(). Implement FastAPI lifespan for connection pool initialization and cleanup. Create main.py with FastAPI app instance, include Cypher router, configure CORS, add health check endpoint. Load connection params from .env file.
**Dependencies**: T025
**Expected**: ASGI server starts with `uvicorn api.main:app`, connection pool healthy

---

## Phase 3.9: Integration Tests with Live IRIS (T027-T029)
**All tests marked @pytest.mark.requires_database**

### T027 [P]: Write and run integration test for multi-hop graph traversal query
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/integration/test_cypher_graph_traversal.py`
**Description**: Create integration test for multi-hop query: MATCH (p:Protein {id: 'PROTEIN:TP53'})-[:INTERACTS_WITH*1..3]->(target:Protein) RETURN DISTINCT target.name. Execute against live IRIS database with sample protein interaction data. Validate cycle detection, max depth enforcement, FK constraint validation. Compare results with equivalent SQL query.
**Dependencies**: T026
**Quickstart Reference**: quickstart.md lines 153-185 (variable-length paths example)
**Expected**: Multi-hop traversal returns correct results, enforces depth limits

### T028 [P]: Write and run integration test for hybrid vector+graph query with CALL procedure
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/integration/test_cypher_vector_hybrid.py`
**Description**: Create integration test for hybrid query: CALL db.index.vector.queryNodes('protein_embeddings', 10, $queryVector) YIELD node, score MATCH (node)-[:ASSOCIATED_WITH]->(d:Disease) RETURN node.name, d.name, score ORDER BY score DESC. Load vector embeddings, execute against live IRIS with HNSW index. Validate vector search <10ms, graph expansion correct, RRF fusion if combined with text search.
**Dependencies**: T026, T023
**Quickstart Reference**: quickstart.md lines 187-233 (hybrid vector+graph example)
**Expected**: Hybrid query combines vector k-NN and graph traversal correctly

### T029 [P]: Write and run integration test for parameterized queries and SQL injection prevention
**Files**: `/Users/tdyar/ws/iris-vector-graph/tests/integration/test_cypher_security.py`
**Description**: Create integration test for parameterized queries: MATCH (p:Protein) WHERE p.id = $proteinId RETURN p. Test SQL injection attempts (e.g., $proteinId = "'; DROP TABLE nodes; --"). Validate parameters are safely bound, no SQL injection possible, query plan caching works for repeated patterns with different parameters.
**Dependencies**: T026
**Research Reference**: research.md lines 360-371 (SQL injection prevention)
**Expected**: Parameterized queries safe from injection, query plan cached

---

## Phase 3.10: Performance & Documentation (T030-T032)

### T030: Write performance benchmark comparing Cypher vs hand-written SQL
**Files**: `/Users/tdyar/ws/iris-vector-graph/scripts/performance/test_cypher_performance.py`
**Description**: Create benchmark script with 100+ test queries: simple node lookup, 2-3 hop traversal, variable-length paths, aggregations, hybrid vector+graph. For each query, measure translation time, Cypher execution time, equivalent SQL execution time. Calculate overhead percentage. Target: <10% overhead for 95% of queries. Output results to docs/performance/cypher_benchmarks.json.
**Dependencies**: T026
**Research Reference**: research.md lines 373-431 (benchmarking strategy)
**Expected**: Benchmark suite ready to run

### T031: Run performance benchmarks and validate <10% overhead requirement
**Files**: `/Users/tdyar/ws/iris-vector-graph/docs/performance/cypher_benchmarks.json`
**Description**: Execute test_cypher_performance.py against live IRIS database with HNSW index. Validate <10% overhead target met for 95% of queries. Document any queries exceeding target and optimization strategies applied. Generate performance report with timestamp, IRIS version, summary statistics (avg overhead, max overhead, pass rate).
**Dependencies**: T030
**Expected**: Performance report shows <10% overhead, benchmarks tracked in version control

### T032: Update CLAUDE.md with Cypher-specific development guidance
**Files**: `/Users/tdyar/ws/iris-vector-graph/CLAUDE.md`
**Description**: Add Cypher endpoint section to CLAUDE.md: document FastAPI server startup, Cypher query testing workflow, performance benchmarking commands, common error codes and troubleshooting. Document custom CALL procedures usage. Add examples of contract test execution and integration test patterns.
**Dependencies**: T031
**Expected**: CLAUDE.md includes comprehensive Cypher development guidance

---

## Dependencies Graph

```
T001 (dependencies) ──┬─> T003 (linting)
                      └─> T004-T005 (contract tests) ──> T024 (integration test) ──> T025 (router) ──> T026 (connection pool) ──┬─> T027-T029 (integration tests) ──> T030 (benchmark) ──> T031 (run benchmark) ──> T032 (docs)
                                                                                                                                    │
T002 (directories) ──┬─> T006-T011 (AST models) ──> T014 (parser tests) ──> T015 (parser) ──> T016 (error handling) ──────────────┤
                     ├─> T012 (SQL artifacts) ──┬─> T017 (translator tests) ──> T018-T020 (translator) ───────────────────────────┤
                     │                          └─> T021 (optimizer tests) ──> T022 (optimizer) ──────────────────────────────────┤
                     └─> T013 (Pydantic models) ────────────────────────────────────────────────────────────────────────────────┘

T023 (SQL procedure) ──> T028 (vector integration test)
```

**Critical Path**: T001 → T002 → T006-T013 → T014-T020 → T024 → T025 → T026 → T027-T029 → T030 → T031 → T032

**Parallelizable Stages**:
- T004-T005 (contract tests) - different files
- T006-T011 (AST models) - different classes in same file, but independent
- T021-T022 (optimizer) - can run parallel to translator if optimizer tests mocked
- T027-T029 (integration tests) - different test files

---

## Parallel Execution Examples

### Example 1: Contract tests in parallel (after T002)
```bash
# Launch T004 and T005 together:
Task T004: "Write failing contract test for POST /api/cypher endpoint success case in /Users/tdyar/ws/iris-vector-graph/tests/contract/test_cypher_api.py"
Task T005: "Write failing contract test for POST /api/cypher endpoint error cases in /Users/tdyar/ws/iris-vector-graph/tests/contract/test_cypher_api_errors.py"
```

### Example 2: Core AST types in parallel (after T002)
```bash
# Launch T006-T011 together (independent AST classes):
Task T006: "Create CypherQuery AST root node class"
Task T007: "Create NodePattern and RelationshipPattern AST classes"
Task T008: "Create MatchClause and GraphPattern AST classes"
Task T009: "Create WhereClause and BooleanExpression AST classes"
Task T010: "Create ReturnClause AST class"
Task T011: "Create CypherProcedureCall AST class for vector search"
```

### Example 3: Integration tests in parallel (after T026)
```bash
# Launch T027-T029 together (different test files):
Task T027: "Write and run integration test for multi-hop graph traversal query in /Users/tdyar/ws/iris-vector-graph/tests/integration/test_cypher_graph_traversal.py"
Task T028: "Write and run integration test for hybrid vector+graph query in /Users/tdyar/ws/iris-vector-graph/tests/integration/test_cypher_vector_hybrid.py"
Task T029: "Write and run integration test for parameterized queries in /Users/tdyar/ws/iris-vector-graph/tests/integration/test_cypher_security.py"
```

---

## Task Summary

**Total Tasks**: 32 (T001-T032)

**By Phase**:
- Setup: 3 tasks (T001-T003)
- Contract Tests: 2 tasks (T004-T005)
- Core Models: 8 tasks (T006-T013)
- Parser: 3 tasks (T014-T016)
- Translator: 4 tasks (T017-T020)
- Optimizer: 2 tasks (T021-T022)
- SQL Procedures: 1 task (T023)
- FastAPI: 3 tasks (T024-T026)
- Integration Tests: 3 tasks (T027-T029)
- Performance & Docs: 3 tasks (T030-T032)

**Parallel Groups**:
- Group 1: T003 (can run after T001)
- Group 2: T004-T005 (can run after T002)
- Group 3: T006-T011, T012, T013 (can run after T002, independent files/classes)
- Group 4: T021-T022 (can run after T012, independent from translator)
- Group 5: T027-T029 (can run after T026, different test files)

**TDD Validation**:
- ✅ Contract tests (T004-T005) before router implementation (T025)
- ✅ Unit tests (T014, T017, T021) before implementation (T015-T016, T018-T020, T022)
- ✅ Integration tests (T024, T027-T029) before final validation (T030-T031)

**Constitutional Compliance**:
- ✅ IRIS-Native: SQL procedures (T023), iris.connect() usage (T026)
- ✅ Test-First: All implementation tasks have preceding test tasks
- ✅ Performance: Benchmarking (T030-T031) validates <10% overhead
- ✅ Hybrid Search: Vector+graph integration test (T028)
- ✅ Observability: Logging, traceId in response (T025)
- ✅ Modular: Core library independent (ast.py, parser.py, translator.py)
- ✅ Explicit Errors: Error handling with line/column (T016, T025)
- ✅ Live Database: All integration tests marked @pytest.mark.requires_database

---

## Critical Dependencies Identified

1. **opencypher Library**: T001 must install before any parser work
2. **IRIS Database**: T023 SQL procedure must load before T028 vector test
3. **Contract Tests First**: T004-T005 must FAIL before T025 implementation
4. **Translation Pipeline**: T015 (parser) → T018-T020 (translator) → T022 (optimizer) → T025 (router)
5. **Connection Pooling**: T026 blocks all integration tests (T027-T029)
6. **Performance Baseline**: T031 requires T026 and sample data loaded

**Blocking Risks**:
- If opencypher incompatible with Python 3.11+, pivot to libcypher-parser
- If HNSW index not available, performance tests may need adjusted targets
- If IRIS connection pooling has issues, may need synchronous fallback

---

## Notes

- All file paths are absolute (starting with /Users/tdyar/ws/iris-vector-graph/)
- [P] tasks touch different files and have no dependencies
- Tests MUST fail before implementation (Red-Green-Refactor)
- @pytest.mark.requires_database on all integration tests per constitution
- Commit after each task completion
- Performance target: <10% overhead vs hand-written SQL (non-negotiable)
