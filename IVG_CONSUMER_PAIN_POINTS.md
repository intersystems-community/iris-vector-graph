# IVG Consumer Pain Points — Extracted from Claude Sessions

Extracted from 41 Claude JSONL session files (Apr 16-28, 2026) documenting real-world IVG usage in Mindwalk knowledge graph project.

---

## 1. INITIALIZE_SCHEMA() VECTOR DIMENSION FAILURES

**Impact**: Blocks test setup, makes schema initialization non-idempotent

**Evidence**:
- **Session**: `8d6d9511-d98c-47fc-8f9a-7f9bcd7978d2` (Apr 20)
  > "initialize_schema() raises RuntimeError when kg_NodeEmbeddings.emb has no vector dimension. This blocks fixtures for tests that call initialize_schema(). The pure Cypher tests pass but embedding-dependent tests fail."

- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "SQLCODE -260: Cannot perform vector operation on vectors with unspecified length. The kg_NodeEmbeddings.emb column was created as VECTOR(DOUBLE, {embedding_dimension}) but on IRIS build 2026.2.0AI.147 the schema was initialized without the dimension."

**Root Cause**: `initialize_schema()` doesn't validate that embedding_dimension is set before creating KG_KNN_VEC procedure. On fresh namespace without pre-set embedding_dimension, vector operations fail.

**Workaround Used**: Skip vector tests, bypass to direct engine.load_networkx()

**IVG Should Do**:
- Detect missing embedding_dimension at initialize_schema() time and raise clear error
- Accept embedding_dimension as a parameter (currently relies on environment/class var)
- Make vector column creation conditional and fail-safe

---

## 2. SELECT TOP N DISTINCT QUERY FAILURES

**Impact**: Cypher MATCH queries with LIMIT fail in IRIS SQL translation

**Evidence**:
- **Session**: `079aad36-a0b9-4def-a7ea-61a44964dbd8` (Apr 18)
  > "IRIS SQL requires DISTINCT before TOP, not after. The Cypher translator produces 'SELECT TOP N DISTINCT' which IRIS parser rejects. Error: 'SELECT TOP ? DISTINCT' with confusing parameter representation."

**Root Cause**: Line 187-195 in cypher_query() injects `TOP N` immediately after SELECT:
```python
# Current broken code:
re.sub(r'^(SELECT)\s+', f'\1 TOP {n} ', sql)
# Produces: SELECT TOP 200 DISTINCT ... (IRIS rejects)
# Should be: SELECT DISTINCT TOP 200 ... (IRIS accepts)
```

**Workaround Used**: Manually rewrote SQL translation, tested with params

**IVG Should Do**:
- Fix regex to inject TOP after DISTINCT (not before)
- Add test case for MATCH + LIMIT + DISTINCT combinations
- Validate IRIS SQL syntax before execution

---

## 3. BUILDIKG() PARTIAL/FAILED LOADS

**Impact**: Graph data loads incompletely, requires debugging and manual recovery

**Evidence**:
- **Session**: `079aad36-a0b9-4def-a7ea-61a44964dbd8` (Apr 18)
  > "BuildKG() call fails mid-execution. KG8 partially loaded — 'uveitis' and 'hla-b27 positive' are in but 'hla-b27' and 'spondyloarthritis (spa)' are not. The BuildKG() error likely interrupted KG8 mid-load."

**Root Cause**: 
- BuildKG() is not idempotent — fails if edges already exist
- No transaction rollback — partial state persists
- Error doesn't indicate where in load process failure occurred

**Workaround Used**: 
- Skip BuildKG(), use engine.load_networkx() directly
- Manually verify node/edge counts before/after

**IVG Should Do**:
- Make BuildKG() idempotent or provide rebuild/cleanup utilities
- Wrap in transaction or provide atomic semantics
- Log progress checkpoints so recovery can restart from checkpoint
- Return detailed error context (how many nodes/edges loaded before failure)

---

## 4. VECTOR DATATYPE MISMATCH (FLOAT vs DOUBLE)

**Impact**: Vector search returns SQLCODE -259, queries fail silently

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "SQLCODE -259: Cannot perform vector operation on vectors of different datatypes. The embeddings in kg_NodeEmbeddings were stored as VECTOR(DOUBLE, 384) but the query vector from TO_VECTOR(?, FLOAT) uses FLOAT type — a datatype mismatch at the IRIS SQL level."

- **Session**: `23bc6cd3-7e1d-4f2a-a490-4348a5c49bd0` (Apr 22)
  > "vector_search failed with -259 because embeddings were generated with paraphrase-multilingual-MiniLM-L12-v2 (stored as VARCHAR) but hybrid_search is using all-MiniLM-L6-v2 (stored as VECTOR)."

**Root Cause**:
- embed_nodes() uses `TO_VECTOR(?)` without type specifier → defaults to FLOAT
- But kg_NodeEmbeddings.emb column is declared as VECTOR(DOUBLE, 384)
- No validation that stored type matches query type

**Workaround Used**: Manually specify `TO_VECTOR(?, DOUBLE)` in schema initialization

**IVG Should Do**:
- Standardize on one vector type (recommend DOUBLE for precision)
- Update embed_nodes() to use `TO_VECTOR(?, DOUBLE, {dim})` consistently
- Add schema validation that column type matches embedding type
- Add unit test comparing insert/query vector types

---

## 5. VECTOR DIMENSION NOT SPECIFIED / HNSW INDEX CREATION FAILS

**Impact**: Vector search unavailable, KG_KNN_VEC stored procedure missing

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "The KG_KNN_VEC stored procedure is also missing — vector search fell back to Python-side but even that fails. IRIS Build 147 changed the way vector dimensions are handled."

**Root Cause**:
- HNSW index creation requires explicit dimension in column definition
- If embedding_dimension is unset at initialize_schema() time, index fails
- No fallback to Python-based vector search

**Workaround Used**: Skip vector search, use BFS traversal instead

**IVG Should Do**:
- Make embedding_dimension a required parameter to initialize_schema()
- Validate dimension before creating VECTOR columns
- Add clear error message if IRIS build doesn't support HNSW
- Provide Python fallback for vector search when native index unavailable

---

## 6. BOLT SERVER CONNECTION LOSS (EPIPE)

**Impact**: Cypher queries fail mid-session, requires full restart

**Evidence**:
- **Session**: `079aad36-a0b9-4def-a7ea-61a44964dbd8` (Apr 18)
  > "The bolt container is broken (engine=false — the IRIS connection is getting EPIPE, probably because it's trying to connect to mindwalk-iris:1972 via the container name and the network config may have changed). Network is fine — EPIPE is happening at the IRIS Python connection level."

**Root Cause**:
- Bolt server uses old connection parameters that become stale
- No connection pooling or retry logic
- EPIPE (broken pipe) kills entire session

**Workaround Used**: Restart bolt container, recreate Cypher queries

**IVG Should Do**:
- Add connection pooling with exponential backoff retry
- Implement heartbeat/ping to detect dead connections early
- Wrap Cypher execution in try/reconnect logic
- Log connection errors with full context (which operation, which IRIS instance)

---

## 7. NAMESPACE/SCHEMA PREFIX CONFUSION

**Impact**: Queries execute against wrong namespace, data not found

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "The bolt container connects to IRIS and runs Cypher queries that translate to SQL — but Graph_KG.docs isn't in the bolt container's Cypher schema at all. The namespace mismatch causes queries to fail silently or return empty results."

- **Session**: `079aad36-a0b9-4def-a7ea-61a44964dbd8` (Apr 18)
  > "set_schema_prefix('Graph_KG') needs to be called before every Cypher translation. Without it, the translator generates SQL against the wrong tables."

**Root Cause**:
- set_schema_prefix() is global state, not thread-safe
- Multiple Cypher translators in same process can interfere
- No validation that schema_prefix matches IRIS actual namespace

**Workaround Used**: Always call set_schema_prefix() before translate_to_sql()

**IVG Should Do**:
- Make schema_prefix instance state, not global
- Pass schema_prefix as parameter to translator, not setter
- Validate at query time that schema exists in target namespace
- Add clear error if tables don't exist in expected schema

---

## 8. CUSTOM ZFENTRY FUNCTION LOOKUP ISSUES (IRIS Build 147)

**Impact**: BFS traversal fails with "function not found"

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "IRIS Build 147 changed $ZF(-4, 3) to only look up function names in the ZFENTRY table returned by GetZFTable, not by scanning all exported symbols in the .so. Build 144 did a dlsym-style lookup that found KG_BFS_GLOBAL_WRAPPER when asked for kg_bfs_global. Build 147 doesn't."

**Root Cause**:
- IVG uses lowercase function names (kg_bfs_global)
- IRIS 147 expects uppercase entry names in ZFENTRY
- .so function wrapper macros don't auto-register in ZFENTRY

**Workaround Used**: Upgraded to IRIS 158/159, rewrote function entry name registration

**IVG Should Do**:
- Add IRIS build detection to set_schema_prefix or equivalent
- Maintain separate function name mappings for IRIS 144, 147, 158+
- Document minimum IRIS version requirement explicitly
- Add unit test for $ZF(-4) function lookup

---

## 9. MCP SERVER INITIALIZATION TIMEOUT

**Impact**: Claude Desktop / notebook can't connect to IVG tools within timeout window

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "The MCP session initialization takes ~35-40 seconds on a cold connection (tool discovery delay) but the notebook's _mcp_tool_call has a 10-second timeout on the initialize call. It's never going to work reliably from nbconvert or on first run."

**Root Cause**:
- Tool discovery from %AI.ToolMgr is slow (scans IRIS procedures)
- No caching of tool list between requests
- External client timeout < server startup time

**Workaround Used**: 
- Pre-cache tool list in MCP server
- Increase client timeout to 45s

**IVG Should Do**:
- Cache tool definitions in memory with TTL
- Implement async tool discovery (don't block on startup)
- Document expected initialization time in README
- Add progress/ping messages during init to keep connection alive

---

## 10. SHORTESTPATH + WHERE CLAUSE RETURNS 0 ROWS

**Impact**: Graph traversal queries fail to filter correctly

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "shortestPath with WHERE clause returns 0 rows. Works with {id: 'literal'} inline syntax but not with WHERE parameters or UNWIND."

**Root Cause**:
- Cypher translator doesn't handle WHERE constraints on relationship returns
- shortestPath CTE doesn't preserve filter conditions properly

**Workaround Used**: 
- Use inline ID literals instead of WHERE
- Post-filter results in Python

**IVG Should Do**:
- Add translator support for WHERE clauses after shortestPath
- Add test case: `shortestPath(from, to) WHERE ... RETURN ...`
- Document current limitations in Cypher docs
- Prioritize this as next Cypher feature

---

## 11. NO UNAUTHENTICATED READ MODE FOR EMBEDDING ACCESS

**Impact**: Can't run read-only embedding operations without auth setup

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "AutheEnabled isn't being applied at CSP startup. When CreateResource is called after CSP init, the auth setting change never takes effect because the %CSP.PROCESSOR is already built."

**Root Cause**:
- Auth changes require IRIS restart
- No way to disable auth for demo/read-only access

**Workaround Used**: Manually set AutheEnabled before starting IRIS

**IVG Should Do**:
- Make auth configuration explicit in initialize_schema()
- Add explicit `enable_auth=False` parameter for read-only mode
- Document auth setup in README (currently not mentioned)

---

## 12. EDGE EMBEDDINGS NOT SUPPORTED

**Impact**: Can't search on edge properties/semantics

**Evidence**:
- **Session**: `23bc6cd3-7e1d-4f2a-a490-4348a5c49bd0` (Apr 22)
  > "initialize_schema() needs to create kg_EdgeEmbeddings table and HNSW index. Acceptance criteria: [1] kg_EdgeEmbeddings created without error on fresh namespace [2] Edge embeddings can be inserted and searched."

**Root Cause**:
- Only kg_NodeEmbeddings is implemented
- Edge embedding schema, insert, and search are missing

**Workaround Used**: Skip edge similarity, use node-only search

**IVG Should Do**:
- Design and implement edge embedding tables
- Add embed_edges() method analogous to embed_nodes()
- Support edge vector search in Cypher (e.g., edge_similarity query)

---

## 13. PARTIAL/INCONSISTENT CLASS COMPILATION ACROSS BUILDS

**Impact**: Different IRIS builds have different compiled procedures available

**Evidence**:
- **Session**: `0c4d6a88-6673-4b06-9b56-e53b84888e84` (Apr 27)
  > "Graph.KG.Traversal class doesn't exist (version mismatch between installed iris-vector-graph package and what load_graph expects)."

- **Session**: `e1106395-a03f-4fd6-a847-ac45e747df0e`
  > "ShortestPath depends on Graph.KG.Traversal class which isn't compiled in the green container. ListExpansions — list_expansions function missing from mindwalk_tools.py."

**Root Cause**:
- Build processes don't sync between Python package version and compiled ObjectScript
- No version check at runtime to validate class availability

**Workaround Used**: Rebuild container, verify all classes present before queries

**IVG Should Do**:
- Add version check function to validate compiled classes
- Fail fast at initialize_schema() if required classes missing
- Document class dependencies per feature in README
- Add CI test to verify all builds produce complete class set

---

## SUMMARY: TOP 5 PRIORITIES FOR IVG

| Priority | Issue | Impact | Effort |
|----------|-------|--------|--------|
| **P1** | SELECT TOP DISTINCT order (SQL syntax) | Query failures in production | Small (~30 min fix) |
| **P2** | Vector type consistency (FLOAT vs DOUBLE) | Silent data loss in search | Small (~1 hour fix) |
| **P3** | initialize_schema() vector dimension handling | Test setup blocks, schema non-idempotent | Medium (~2 hours) |
| **P4** | BuildKG() idempotence / atomic loads | Partial graph loads, data loss recovery | Medium (~3 hours) |
| **P5** | Bolt connection pooling + retry logic | Session stability, production reliability | Medium-Large (~4 hours) |

**Suggested Next Steps**:
1. File IVG GitHub issues for P1-P3 bugs
2. Add regression tests for each issue
3. Backport fixes to last released version if possible
4. Update documentation to call out version requirements for each feature

