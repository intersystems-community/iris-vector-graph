# Feature Specification: Cypher MCP Server

**Feature Branch**: `031-cypher-mcp-server`  
**Created**: 2026-03-31  
**Status**: Draft  
**Input**: Dirk Van Hyfte request (March 31 call) — MCP server providing Cypher query tools for LLM clients

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Query a Knowledge Graph via Cypher (Priority: P1)

A researcher using Claude Desktop (or another MCP-compatible client) wants to explore a biomedical knowledge graph by asking natural language questions that get translated into Cypher queries. They connect to the MCP server, which exposes a `cypher_query` tool. The LLM formulates Cypher from the user's question, executes it via the tool, and returns the results in natural language.

**Why this priority**: This is the core deliverable Dirk requested. Without Cypher query access, the MCP server has no value. This enables the "concept-relation-concept" triple queries and multi-hop traversals that are central to the READY talk demo.

**Independent Test**: Connect an MCP client, execute `MATCH (a)-[r]->(b) WHERE a.id = 'hla-b27' RETURN a, r, b`, verify the results contain the expected triples from the loaded BEL graph.

**Acceptance Scenarios**:

1. **Given** a BEL knowledge graph loaded in IRIS, **When** a user's LLM calls the `cypher_query` tool with `MATCH (n)-[r:causes]->(m) RETURN n.id, type(r), m.id LIMIT 10`, **Then** the result contains up to 10 triples with node IDs and relationship types.
2. **Given** the same graph, **When** a multi-hop query is executed `MATCH (a)-[r1]->(b)-[r2]->(c) WHERE a.id = 'hla-b27' RETURN nodes(p), relationships(p)`, **Then** the result contains 2-hop paths from the seed node.
3. **Given** an invalid Cypher query, **When** executed, **Then** the tool returns a clear error message (not a crash) that the LLM can interpret and retry.

---

### User Story 2 - Load a GraphML File into the Knowledge Graph (Priority: P1)

A researcher wants to load a new BEL knowledge graph (provided as a GraphML file) into IRIS so it can be queried. They use the `load_graph` tool, passing a file path. The tool ingests nodes and edges into the Graph_KG schema.

**Why this priority**: Dirk and Saskia produce GraphML files from their BEL pipeline. Loading is the first step before any querying. This supports the "how easy to load?" question from the call.

**Independent Test**: Call `load_graph` with Saskia's KG_8.graphml (911 nodes, 1144 edges), then query for a known node to verify it loaded.

**Acceptance Scenarios**:

1. **Given** a GraphML file with 911 nodes and 1144 edges, **When** the `load_graph` tool is called, **Then** the graph is loaded into Graph_KG and the tool reports the node and edge counts.
2. **Given** an already-populated graph, **When** a new GraphML is loaded, **Then** the new nodes and edges are added alongside existing data (not replacing it).
3. **Given** a malformed GraphML file, **When** the tool is called, **Then** it returns an error message indicating the parse failure.

---

### User Story 3 - Search for Evidence in Literature (Priority: P2)

A researcher finds an interesting triple in the knowledge graph (e.g., "HLA-B27 is strongly associated with spondyloarthritis") and wants to find the original text passages that support this claim. They use the `evidence_search` tool, which performs a vector similarity search over embedded documents.

**Why this priority**: This is the "evidence retrieval" step Dirk emphasized — finding where in the literature a knowledge graph claim is expressed. It depends on having vector embeddings loaded (PubMed or MIMIC documents).

**Independent Test**: Call `evidence_search` with "HLA-B27 associated with spondyloarthritis", verify results contain relevant document passages with similarity scores.

**Acceptance Scenarios**:

1. **Given** PubMed embeddings loaded in kg_NodeEmbeddings, **When** `evidence_search("HLA-B27 spondyloarthritis")` is called, **Then** the result contains ranked documents with similarity scores.
2. **Given** no embeddings loaded, **When** `evidence_search` is called, **Then** the tool returns a clear message indicating no embeddings are available.

---

### User Story 4 - Explore Graph Structure and Statistics (Priority: P2)

A researcher wants to understand the loaded graph before querying — how many nodes, edges, what labels exist, what relationship types. They use the `graph_stats` tool to get an overview.

**Why this priority**: Orientation before exploration. The LLM needs this context to formulate meaningful Cypher queries.

**Independent Test**: Call `graph_stats` after loading a graph, verify it returns correct counts and label/type distributions.

**Acceptance Scenarios**:

1. **Given** a loaded graph, **When** `graph_stats` is called, **Then** the result includes total node count, edge count, top labels, and top relationship types.

---

### User Story 5 - Run PPR for Insight Discovery (Priority: P3)

A researcher wants to find "surprising" connections from a seed concept — nodes that are structurally important relative to the seed but not directly connected. They use the `ppr_walk` tool with seed node IDs.

**Why this priority**: This is Dirk's "segment 3" — using PPR to find surprising evidence. Depends on having the graph loaded and ^KG built.

**Independent Test**: Call `ppr_walk` with seed nodes from the BEL graph, verify ranked results.

**Acceptance Scenarios**:

1. **Given** a loaded graph with ^KG built, **When** `ppr_walk(["hla-b27"], top_k=10)` is called, **Then** the result contains 10 ranked nodes with PPR scores.

---

### Edge Cases

- What happens when the IRIS connection is unavailable? The MCP server returns a connection error on every tool call, not a crash.
- What happens when `cypher_query` returns zero rows? The tool returns an empty result set, not an error.
- What happens when `load_graph` is called with a very large file (100K+ nodes)? The tool streams inserts with progress reporting and completes within a reasonable time.
- What happens when multiple MCP clients connect simultaneously? The server handles concurrent requests (each gets its own IRIS connection or uses a connection pool).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a `cypher_query` tool that accepts a Cypher string, executes it against IRIS, and returns structured results (columns + rows).
- **FR-002**: System MUST expose a `load_graph` tool that accepts a file path to a GraphML file and loads nodes + edges into Graph_KG.
- **FR-003**: System MUST expose an `evidence_search` tool that performs vector similarity search over embedded documents.
- **FR-004**: System MUST expose a `graph_stats` tool that returns node count, edge count, label distribution, and relationship type distribution.
- **FR-005**: System MUST expose a `ppr_walk` tool that runs Personalized PageRank from seed nodes and returns ranked results.
- **FR-006**: All tools MUST return errors as structured messages (not exceptions) that an LLM can interpret and act on.
- **FR-007**: Tools MUST be implemented as ObjectScript classes extending `%AI.Tool`, exposed via a `%AI.MCP.Service` subclass mounted as a CSP web application.
- **FR-008**: The `load_graph` tool MUST call `BuildKG()` after loading to populate the ^KG adjacency index.
- **FR-009**: The MCP service MUST be configurable via `iris-mcp-server` TOML config pointing at the CSP web application path.

### Key Entities

- **MCP Tool**: A callable function exposed to LLM clients via the Model Context Protocol. Each tool has a name, description, and JSON schema for its parameters.
- **Tool Result**: The structured response from a tool call. Contains either data (columns, rows, counts) or an error message.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An LLM client can execute a Cypher query and receive structured results within 2 seconds for graphs under 10K nodes.
- **SC-002**: A GraphML file with 1000 nodes loads successfully via the `load_graph` tool.
- **SC-003**: The MCP server starts and accepts connections within 5 seconds.
- **SC-004**: All 5 tools are discoverable via the MCP `tools/list` endpoint.
- **SC-005**: The server handles tool call errors gracefully — no unhandled exceptions crash the server.

## Assumptions

- The MCP server is `iris-mcp-server`, a Rust binary shipped with IRIS 2026.2.0AI. We do NOT write a Python MCP server.
- Tools are ObjectScript classes extending `%AI.Tool`, mounted via `%AI.MCP.Service` subclasses as CSP web applications.
- `iris-mcp-server` handles MCP protocol, connection pooling, tool discovery, and transport (stdio/HTTP/HTTPS). We only implement the IRIS-side tools.
- GraphML loading uses ObjectScript or calls Python via embedded Python to parse GraphML and insert via SQL.
- The Cypher query tool calls `IRISGraphEngine.execute_cypher()` or translates and executes SQL directly in ObjectScript.
- dpgenai1 (or AWS) hosts the IRIS instance. `iris-mcp-server` runs alongside it or on the client machine pointing at the remote instance.
- Dirk's team connects Claude Desktop → `iris-mcp-server` → IRIS MCP endpoint.

## Scope Boundaries

**In scope**:
- MCP server with 5 tools (cypher_query, load_graph, evidence_search, graph_stats, ppr_walk)
- stdio and SSE transport support
- GraphML file loading
- IRIS connection management
- Error handling for all tools

**Out of scope (future)**:
- OBO format loading (separate enhancement when Saskia shares OBO files)
- Web UI for the MCP server
- Authentication/authorization on the MCP endpoint (handled by iris-mcp-server + IRIS CSP auth)
- Multi-graph support (multiple named graphs in one IRIS instance)
- Streaming results for large queries

## Clarifications

### Session 2026-03-31

- Q: Should we build a custom Python MCP server or use the IRIS aicore MCP infrastructure? → A: Use iris-mcp-server (Rust binary, ships with IRIS 2026.2.0AI). Implement tools as ObjectScript %AI.Tool classes exposed via %AI.MCP.Service. Python ToolSets via iris_llm are also supported for agent-side use. For the MCP service endpoint, ObjectScript tools are the native path; Python tools can be called from ObjectScript via embedded Python (`Language = python`) if needed.
