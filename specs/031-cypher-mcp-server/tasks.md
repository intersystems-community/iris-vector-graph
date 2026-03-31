# Tasks: Graph Knowledge MCP Tools

**Input**: Design documents from `/specs/031-cypher-mcp-server/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Manual MCP client testing (Claude Desktop) + method-level verification via native API. No pytest e2e for ObjectScript MCP tools — testing requires a running iris-mcp-server + Claude Desktop.

**Organization**: US1 (CypherQuery) and US2 (LoadGraph) are both P1. US3 (GraphStats) and US4 (PPRWalk) are P2. US5 (EvidenceSearch) is P3. The foundational phase (ToolSet + Service + CSP app + config) blocks all stories.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Verify existing ObjectScript classes compile on the target IRIS container
- [X] T002 Verify `iris-mcp-server` binary is available (either in IRIS bin/ or downloaded separately)

**Checkpoint**: IRIS container running with ai-core framework available. iris-mcp-server binary accessible.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T003 Create `iris_src/src/Graph/KG/MCPTools.cls` — empty `%AI.Tool` class with class definition and package structure
- [X] T004 Create `iris_src/src/Graph/KG/MCPToolSet.cls` — `%AI.ToolSet` with XML `<ToolSet>` definition including `Graph.KG.MCPTools` and `%AI.Policy.ConsoleAudit` in `iris_src/src/Graph/KG/MCPToolSet.cls`
- [X] T005 Create `iris_src/src/Graph/KG/MCPService.cls` — `%AI.MCP.Service` with `Parameter SPECIFICATION = "Graph.KG.MCPToolSet"` in `iris_src/src/Graph/KG/MCPService.cls`
- [X] T006 Create `config/mcp-graph.toml` — iris-mcp-server config pointing at `/mcp/graph` endpoint with stdio transport, localhost connection, pool min=2 max=5
- [X] T007 Create CSP web application `/mcp/graph` on the IRIS instance: dispatch class `Graph.KG.MCPService`, namespace USER, Unauthenticated for dev
- [X] T008 Deploy and compile MCPTools, MCPToolSet, MCPService on the IRIS container
- [X] T009 Verify iris-mcp-server connects and discovers 0 tools (empty ToolSet) via `iris-mcp-server --config=config/mcp-graph.toml --log-level=debug run`

**Checkpoint**: iris-mcp-server connects to IRIS, discovers the empty MCP service. Claude Desktop can connect (shows gear icon).

---

## Phase 3: User Story 1 — CypherQuery + RunSQL (Priority: P1) MVP

**Goal**: LLM can execute Cypher queries and raw SQL against the knowledge graph.

**Independent Test**: Connect Claude Desktop, ask "run this cypher: MATCH (a)-[r]->(b) RETURN a.id, r, b.id LIMIT 5", verify results.

- [X] T010 [US1] Implement `RunSQL(query As %String) As %DynamicObject` method in `iris_src/src/Graph/KG/MCPTools.cls`: use `%SQL.Statement` to execute query, collect columns + rows into `%DynamicObject`, handle errors gracefully
- [X] T011 [US1] Implement `CypherQuery(query As %String) As %DynamicObject` method in `iris_src/src/Graph/KG/MCPTools.cls`: use embedded Python to call `iris_vector_graph.cypher.parser.parse_query()` and `iris_vector_graph.cypher.translator.translate_to_sql()`, then execute resulting SQL via `%SQL.Statement`, return columns + rows + generated SQL
- [X] T012 [US1] Redeploy MCPTools.cls, verify iris-mcp-server discovers CypherQuery and RunSQL tools
- [X] T013 [US1] Manual test: connect Claude Desktop, ask it to query the graph via CypherQuery and RunSQL, verify JSON results

**Checkpoint**: Cypher and SQL queries work via MCP. LLM receives structured results.

---

## Phase 4: User Story 2 — LoadGraph (Priority: P1)

**Goal**: LLM can load GraphML and OBO files into the knowledge graph.

**Independent Test**: Ask Claude to load KG_8.graphml, then query for hla-b27.

- [X] T014 [US2] Implement `LoadGraph(filePath As %String, format As %String = "graphml") As %DynamicObject` method in `iris_src/src/Graph/KG/MCPTools.cls`: use embedded Python to call `IRISGraphEngine.load_networkx()` (graphml) or `IRISGraphEngine.load_obo()` (obo), then call `##class(Graph.KG.Traversal).BuildKG()`, return node/edge counts
- [X] T015 [US2] Copy Saskia's KG_8.graphml to a path accessible inside the IRIS container (e.g., `/data/KG_8.graphml`)
- [X] T016 [US2] Redeploy MCPTools.cls, manual test: ask Claude to load the graph and verify with GraphStats or RunSQL

**Checkpoint**: Graph loading works via MCP. Loaded data is immediately queryable.

---

## Phase 5: User Story 3 — GraphStats (Priority: P2)

**Goal**: LLM can get graph overview (node count, edge count, top predicates, top labels).

- [X] T017 [P] [US3] Implement `GraphStats() As %DynamicObject` method in `iris_src/src/Graph/KG/MCPTools.cls`: execute 4 SQL queries (COUNT nodes, COUNT edges, GROUP BY p on rdf_edges, GROUP BY label on rdf_labels), assemble into JSON
- [X] T018 [US3] Redeploy and manual test: ask Claude "what's in the graph?", verify it calls GraphStats and reports counts

**Checkpoint**: Graph overview available. LLM uses this context to formulate better queries.

---

## Phase 6: User Story 4 — PPRWalk (Priority: P2)

**Goal**: LLM can run Personalized PageRank from seed nodes for insight discovery.

- [X] T019 [P] [US4] Implement `PPRWalk(seeds As %DynamicArray, topK As %Integer = 10, damping As %Double = 0.85) As %DynamicObject` method in `iris_src/src/Graph/KG/MCPTools.cls`: serialize seeds to JSON, call `##class(Graph.KG.PageRank).RunJson(seedJson, damping, 50, topK)`, parse JSON result into `%DynamicObject`
- [X] T020 [US4] Redeploy and manual test: ask Claude "find surprising connections from hla-b27", verify PPR results with scores

**Checkpoint**: PPR insight discovery works via MCP. LLM interprets ranked results.

---

## Phase 7: User Story 5 — EvidenceSearch (Priority: P3)

**Goal**: LLM can search for supporting literature via vector similarity.

- [X] T021 [P] [US5] Implement `EvidenceSearch(query As %String, topK As %Integer = 5) As %DynamicObject` method in `iris_src/src/Graph/KG/MCPTools.cls`: execute VECTOR_COSINE SQL against kg_NodeEmbeddings, return ranked results with scores; handle empty embeddings table gracefully
- [X] T022 [US5] Redeploy and manual test (requires embeddings loaded)

**Checkpoint**: Evidence retrieval works when embeddings are available. Graceful fallback when not.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T023 Add method descriptions/comments visible to the LLM (the ai-core framework uses method descriptions for tool descriptions in the MCP catalog)
- [X] T024 [P] Create `scripts/setup/setup_mcp.sh` — automated CSP web application creation via ObjectScript `$System.Security` API
- [X] T025 [P] Update `docs/python/PYTHON_SDK.md` with MCP tools section and quickstart
- [X] T026 End-to-end demo: load KG_8.graphml → GraphStats → CypherQuery "what is hla-b27 associated with" → PPRWalk from hla-b27 → all via single Claude Desktop conversation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all stories
- **US1 (Phase 3)**: Depends on Phase 2 — MVP
- **US2 (Phase 4)**: Depends on Phase 2 — can parallelize with US1
- **US3 (Phase 5)**: Depends on Phase 2 — can parallelize with US1/US2
- **US4 (Phase 6)**: Depends on Phase 2 — needs ^KG built (US2 triggers BuildKG)
- **US5 (Phase 7)**: Depends on Phase 2 — needs embeddings loaded
- **Polish (Phase 8)**: Depends on all stories

### User Story Dependencies

- **US1 (P1)**: Independent after Phase 2
- **US2 (P1)**: Independent after Phase 2
- **US3 (P2)**: Independent after Phase 2 — useful for all other stories
- **US4 (P2)**: Needs ^KG built (from US2's BuildKG call or manual)
- **US5 (P3)**: Needs embeddings loaded (separate data pipeline)

### Parallel Opportunities

- T010-T011 (US1 tools) are in the same file but independent methods
- T017, T019, T021 (US3/US4/US5 tools) can all be written in parallel
- US1 and US2 can be implemented in parallel
- T024-T025 (polish scripts/docs) can run in parallel

---

## Implementation Strategy

### MVP First (User Story 1 — CypherQuery + RunSQL)

1. Phase 1: Verify IRIS + iris-mcp-server available
2. Phase 2: Create ToolSet + Service + CSP app + config (T003-T009)
3. Phase 3: Implement RunSQL + CypherQuery (T010-T013)
4. **STOP and VALIDATE**: Claude Desktop can query the graph

### Incremental Delivery

1. Foundational → Empty MCP service connects
2. US1 → CypherQuery + RunSQL work
3. US2 → LoadGraph works (graph loading from Claude)
4. US3 → GraphStats gives LLM context
5. US4 → PPRWalk for insight discovery
6. US5 → EvidenceSearch for literature
7. Polish → Demo script, setup automation, docs
