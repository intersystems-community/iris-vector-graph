# Feature Specification: Graph Knowledge MCP Tools

**Feature Branch**: `031-cypher-mcp-server`  
**Created**: 2026-03-31  
**Status**: Draft  
**Input**: Dirk Van Hyfte request (March 31 call) + aicore MCP architecture + full IVG layer-cake stack

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Query a Knowledge Graph via Cypher (Priority: P1)

A researcher using Claude Desktop asks natural language questions about a biomedical knowledge graph. The LLM formulates Cypher queries from the researcher's questions, executes them via the `CypherQuery` MCP tool, and returns results in natural language. The tool translates Cypher to SQL via the IVG translator (embedded Python), executes against IRIS, and returns structured rows.

**Why this priority**: This is the core deliverable Dirk requested. "I personally would first like to focus on that" — the Cypher endpoint enables concept-relation-concept triple queries and multi-hop traversals that are central to the READY talk.

**Independent Test**: Load Saskia's KG_8.graphml (911 nodes), connect Claude Desktop via iris-mcp-server, ask "What is HLA-B27 associated with?", verify the tool returns matching triples.

**Acceptance Scenarios**:

1. **Given** a BEL knowledge graph loaded in IRIS, **When** a user's LLM calls `CypherQuery` with `MATCH (a)-[r]->(b) WHERE a.id = 'hla-b27' RETURN a.id, r, b.id LIMIT 10`, **Then** the result contains triples from the graph with node IDs and relationship types.
2. **Given** the same graph, **When** a multi-hop query `MATCH p = (a)-[r1]->(b)-[r2]->(c) WHERE a.id = 'hla-b27' RETURN nodes(p), relationships(p)` is executed, **Then** the result contains 2-hop paths with named path data.
3. **Given** an invalid Cypher query, **When** executed, **Then** the tool returns a structured error message the LLM can interpret and retry.
4. **Given** a query that returns zero rows, **When** executed, **Then** the tool returns an empty result set (not an error).

---

### User Story 2 - Load a Graph File (Priority: P1)

A researcher loads a new BEL knowledge graph (GraphML or OBO format) into IRIS via the `LoadGraph` tool. After loading, the graph is immediately queryable via Cypher. The tool calls `load_networkx()` or `load_obo()` from the IVG engine and triggers `BuildKG()` to populate the adjacency index.

**Why this priority**: Dirk and Saskia produce GraphML files from their BEL pipeline. Loading is the prerequisite for all querying. This supports Dirk's "how easy to load?" question.

**Independent Test**: Call `LoadGraph` with KG_8.graphml, then query for "hla-b27" to verify it loaded.

**Acceptance Scenarios**:

1. **Given** a GraphML file with 911 nodes and 1144 edges, **When** `LoadGraph` is called with the file path, **Then** the tool reports node and edge counts and the graph is queryable.
2. **Given** an OBO ontology file, **When** `LoadGraph` is called with the path and format "obo", **Then** the ontology nodes and is_a/part_of relationships are loaded.
3. **Given** an already-populated graph, **When** a new file is loaded, **Then** new nodes and edges are added alongside existing data.

---

### User Story 3 - Explore Graph Statistics (Priority: P2)

A researcher wants to understand the graph structure before querying — node count, edge count, label distribution, relationship types. The `GraphStats` tool provides this overview. The LLM uses this context to formulate better Cypher queries.

**Why this priority**: Orientation before exploration. Without knowing what's in the graph, the LLM generates blind queries.

**Independent Test**: Call `GraphStats` after loading a graph, verify correct counts.

**Acceptance Scenarios**:

1. **Given** a loaded graph, **When** `GraphStats` is called, **Then** the result includes total node count, edge count, top 10 relationship types with counts, and top 10 labels with counts.

---

### User Story 4 - Run PPR for Insight Discovery (Priority: P2)

A researcher wants to find "surprising" connections from a seed concept — nodes that are structurally important relative to the seed but not directly connected. The `PPRWalk` tool runs Personalized PageRank from seed nodes and returns ranked results. This is Dirk's "segment 3" demo — using graph structure to find evidence.

**Why this priority**: This is the differentiating demo capability. Direct triple queries (US1) are table stakes; PPR-guided insight discovery is what makes IRIS+IVG unique.

**Independent Test**: Call `PPRWalk` with "hla-b27" as seed, verify ranked results include structurally important nodes.

**Acceptance Scenarios**:

1. **Given** a loaded graph with ^KG built, **When** `PPRWalk(seeds=["hla-b27"], topK=10)` is called, **Then** the result contains 10 ranked nodes with PPR scores sorted descending.
2. **Given** a seed node that doesn't exist, **When** `PPRWalk` is called, **Then** the tool returns an empty result (not an error).

---

### User Story 5 - Search for Evidence in Literature (Priority: P3)

A researcher finds an interesting triple and wants to find the original text passages that support it. The `EvidenceSearch` tool performs vector similarity search over embedded documents to find supporting literature.

**Why this priority**: Evidence retrieval depends on having vector embeddings loaded, which is a separate data pipeline step. Core graph querying (US1-US4) works without embeddings.

**Independent Test**: Call `EvidenceSearch` with "HLA-B27 spondyloarthritis", verify results contain relevant documents with scores.

**Acceptance Scenarios**:

1. **Given** document embeddings loaded in kg_NodeEmbeddings, **When** `EvidenceSearch("HLA-B27 spondyloarthritis", topK=5)` is called, **Then** the result contains ranked documents with similarity scores.
2. **Given** no embeddings loaded, **When** called, **Then** the tool returns a clear message indicating no embeddings are available.

---

### Edge Cases

- What happens when the IRIS instance is unreachable? iris-mcp-server's `iris_status` diagnostic tool surfaces the error to the LLM.
- What happens when `LoadGraph` is called with a file that doesn't exist on the server? The tool returns a structured error with the file path.
- What happens when multiple MCP clients call tools simultaneously? iris-mcp-server's connection pool handles concurrency; each tool call gets its own IRIS session.
- What happens when `CypherQuery` hits a query timeout? The tool returns a timeout error after a configurable limit.
- What happens when `BuildKG()` is running during a query? Queries against ^KG still work; the rebuild is atomic (kill + rebuild).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a `CypherQuery` tool that accepts a Cypher string, translates to SQL via the IVG Cypher translator, executes against IRIS, and returns structured results (columns + rows as JSON).
- **FR-002**: System MUST expose a `LoadGraph` tool that accepts a file path and format (graphml or obo), loads into Graph_KG via `load_networkx()` or `load_obo()`, and calls `BuildKG()` to populate ^KG.
- **FR-003**: System MUST expose a `GraphStats` tool that returns node count, edge count, top relationship types, and top labels from the current graph.
- **FR-004**: System MUST expose a `PPRWalk` tool that runs Personalized PageRank from seed node IDs and returns top-K ranked nodes with scores.
- **FR-005**: System MUST expose an `EvidenceSearch` tool that performs vector similarity search and returns ranked documents.
- **FR-006**: All tools MUST return errors as structured JSON messages that an LLM can interpret, not unhandled exceptions.
- **FR-007**: Tools MUST be implemented as methods on an ObjectScript `%AI.Tool` class, exposed via a `%AI.MCP.Service` subclass mounted as a CSP web application at `/mcp/graph`.
- **FR-008**: The `CypherQuery` and `LoadGraph` tools MUST call Python via embedded Python (`Language = python` or `builtins.%Import`) to use the IVG Python library.
- **FR-009**: Pure ObjectScript tools (`GraphStats`, `PPRWalk`) MUST operate directly on ^KG globals and/or SQL, with no Python dependency.
- **FR-010**: A `config.toml` for iris-mcp-server MUST be provided that configures the `/mcp/graph` endpoint.
- **FR-011**: System MUST expose a `RunSQL` tool that accepts a SQL query string and returns structured results (columns + rows as JSON), enabling direct graph queries when Cypher translation is not needed.

### Key Entities

- **Graph.KG.MCPTools**: ObjectScript `%AI.Tool` class containing all graph tool methods.
- **Graph.KG.MCPToolSet**: ObjectScript `%AI.ToolSet` class grouping tools with audit policy.
- **Graph.KG.MCPService**: ObjectScript `%AI.MCP.Service` subclass mounting the toolset at `/mcp/graph`.
- **config.toml**: iris-mcp-server configuration file pointing at the IRIS instance and `/mcp/graph` endpoint.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A Cypher query against a graph under 10K nodes returns results within 2 seconds via the MCP tool.
- **SC-002**: A GraphML file with 1000 nodes loads and is queryable within 10 seconds via the MCP tool.
- **SC-003**: All 5 tools are discoverable via MCP `tools/list` when Claude Desktop connects.
- **SC-004**: The MCP server handles tool call errors without crashing — 100% error recovery rate.
- **SC-005**: End-to-end demo works: load KG_8.graphml → query triples → run PPR → find evidence, all via Claude Desktop conversation.

## Assumptions

- iris-mcp-server (Rust binary) ships with IRIS 2026.2.0AI and handles MCP protocol, transport, and connection pooling. IVG only implements the IRIS-side tools.
- The `%AI.Tool`, `%AI.ToolSet`, and `%AI.MCP.Service` classes from the IRIS ai-core framework are available in the target IRIS version.
- Embedded Python is available in the target IRIS version for `CypherQuery` and `LoadGraph` tools.
- `iris-vector-graph` pip package is installed in the IRIS embedded Python environment.
- The CSP web application `/mcp/graph` must be created in the IRIS Management Portal (or via the install script) with appropriate dispatch class and authentication settings.
- Saskia's GraphML files (KG_7: 127 nodes, KG_8: 911 nodes) are available for testing in `data/mindwalk/`.
- The ReadyAI demo setup (`ready2026-hackathon/ReadyAI-demo/`) provides a working docker-compose reference for iris-mcp-server + IRIS integration.

## Scope Boundaries

**In scope**:
- 5 ObjectScript tool methods (CypherQuery, LoadGraph, GraphStats, PPRWalk, EvidenceSearch)
- ToolSet with audit policy
- MCP Service class mounted at /mcp/graph
- config.toml for iris-mcp-server
- CSP web application setup instructions/script
- Testing with Saskia's KG_8.graphml

**Out of scope (future)**:
- Web UI for the MCP server
- OBO format auto-detection (user specifies format explicitly)
- Multi-graph support (multiple named graphs)
- Streaming results for large queries
- Authentication beyond IRIS CSP defaults (handled by iris-mcp-server config)
- Arno acceleration integration (future — when FFI is wired)
