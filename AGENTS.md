# iris-vector-graph Development Guidelines

**Owner:** Thomas Dyar (Tom) — Sr. Manager, AI Platform and Ecosystems, InterSystems Corporation  
> NEVER use "Tim" — that is Tim Leavitt, a colleague. Always use "Tom" in conversation.

Auto-generated from all feature plans. Last updated: 2026-04-18

## Active Technologies
- Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`, `lark`. (Cypher/GraphQL Core)
- InterSystems IRIS (RDF Schema: nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings)
- Python 3.11 (build) + ObjectScript (query) + `sklearn.cluster.MiniBatchKMeans`, `numpy` (046-ivfflat-index)
- Release Tools: `hatchling` (backend), `build`, `twine`
- Python 3.11 + `fastapi`, `strawberry-graphql[fastapi]`, `intersystems-irispython`, `iris-devtester` (019-ivg-gql-autogen)
- Python 3.11 + `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (018-cypher-vector-search)
- Python 3.11 (project target per AGENTS.md) + `intersystems-irispython`, `iris-devtester` (test only) (020-initialize-schema-stored-procedures)
- InterSystems IRIS — SQL schema `Graph_KG` (data), `iris_vector_graph` (procedures) (020-initialize-schema-stored-procedures)
- Python 3.11 + ObjectScript (IRIS 2025.1+) + `intersystems-irispython`, `iris-devtester` (test only) (024-graph-kernels)
- InterSystems IRIS — `Graph_KG` schema, `^KG` global (adjacency index) (024-graph-kernels)
- Python 3.11 + `iris_vector_graph.cypher` (ast, lexer, parser, translator) — no new deps (025-named-path-bindings)
- InterSystems IRIS — existing `Graph_KG` schema (nodes, rdf_edges, rdf_labels, rdf_props) (025-named-path-bindings)
- Python 3.11 + `iris_vector_graph` (engine, operators, schema), `intersystems-irispython` (027-fhir-kg-bridge)
- InterSystems IRIS — extends `Graph_KG` schema with one new table (`fhir_bridges`) (027-fhir-kg-bridge)
- ObjectScript (IRIS 2025.1+) + None — pure ObjectScript over globals (028-nkg-integer-index)
- `^NKG` global (new), `^KG` global (existing, maintained for backward compat) (028-nkg-integer-index)
- Python 3.11 (build) + ObjectScript (query) + `iris_vector_graph` (engine), `sklearn` (K-means at build time), `numpy` (029-plaid-search)
- InterSystems IRIS — new `^PLAID` global (independent of `^KG` and `^VecIdx`) (029-plaid-search)
- Python 3.11 + ObjectScrip + `iris_vector_graph` (engine, schema, security) (030-rdf-reification)
- ObjectScript (IRIS 2026.2.0AI) + embedded Python 3.12 + IRIS ai-core framework (`%AI.Tool`, `%AI.ToolSet`, `%AI.MCP.Service`), `iris-mcp-server` (Rust binary), `iris_vector_graph` Python package (embedded) (031-cypher-mcp-server)
- Existing Graph_KG schema + ^KG/^NKG globals (031-cypher-mcp-server)
- Python 3.11 (build) + ObjectScript (write/query) + `iris_vector_graph` (engine, schema), `intersystems-irispython` (036-temporal-edges)
- InterSystems IRIS — new `^KG("tout",...)` + `^KG("tin",...)` + `^KG("bucket",...)` subscripts (additive, zero schema changes) (036-temporal-edges)
- `^KG("edgeprop", ts, s, p, o, key) = value` — new subscript key in existing `^KG` (037-edgeprop-ndjson-ingest)
- Python 3.11 (translator), ObjectScript (TemporalIndex — already complete in v1.41.0) + `iris_vector_graph.cypher.{ast,lexer,parser,translator}`, `iris_vector_graph.engine` (039-temporal-cypher)
- IRIS `^KG("tout"/"tin")` globals via `Graph.KG.TemporalIndex.QueryWindow/QueryWindowInbound` (039-temporal-cypher)
- ObjectScript (IRIS 2024.1+), Python 3.11 (044-bm25-index)
- Python 3.11 (wiring/tests) + ObjectScript (IRIS 2024.1+, BFS engine) + `intersystems-irispython`, `iris-devtester` (test only) (047-shortest-path)
- `^KG("out")` / `^KG("in")` globals — no new schema, no new globals (047-shortest-path)
- Python 3.11 + ObjectScript (IRIS 2024.1+) + `intersystems-irispython`, `iris-devtester` (test only) (048-unified-edge-store)

## Project Structure
```text
iris_vector_graph/  # Core Library
api/                # FastAPI Application
tests/              # Test Suite
specs/              # Feature Specifications
```

## Commands
- **Test**: `pytest`
- **Lint**: `ruff check .`
- **Build**: `python3 -m build`
- **Publish**: `twine upload dist/*`

## Code Style
Python 3.11, InterSystems IRIS 2025.1+: Follow standard conventions

## Recent Changes
- 048-unified-edge-store PR-A: Graph.KG.EdgeScan (MatchEdges/WriteAdjacency/DeleteAdjacency), create_edge syncs to ^KG("out",0,...), translator EdgeScan CTE for MATCH, TemporalIndex + BFS/shortestPath updated to shard-0
- 048-unified-edge-store: Added Python 3.11 + ObjectScript (IRIS 2024.1+) + `intersystems-irispython`, `iris-devtester` (test only)
- 047-shortest-path: Added Python 3.11 (wiring/tests) + ObjectScript (IRIS 2024.1+, BFS engine) + `intersystems-irispython`, `iris-devtester` (test only)
- 046-ivfflat-index: Added IVFFlat vector index — `ivf_build/search/drop/info` on `IRISGraphEngine`, `Graph.KG.IVFIndex` ObjectScript class, `CALL ivg.ivf.search(...)` Cypher procedure

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
