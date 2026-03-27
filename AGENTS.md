# iris-vector-graph Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-02-24

## Active Technologies
- Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`, `lark`. (Cypher/GraphQL Core)
- InterSystems IRIS (RDF Schema: nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings)
- Release Tools: `hatchling` (backend), `build`, `twine`
- Python 3.11 + `fastapi`, `strawberry-graphql[fastapi]`, `intersystems-irispython`, `iris-devtester` (019-ivg-gql-autogen)
- Python 3.11 + `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (018-cypher-vector-search)
- Python 3.11 (project target per AGENTS.md) + `intersystems-irispython`, `iris-devtester` (test only) (020-initialize-schema-stored-procedures)
- InterSystems IRIS — SQL schema `Graph_KG` (data), `iris_vector_graph` (procedures) (020-initialize-schema-stored-procedures)
- Python 3.11 + ObjectScript (IRIS 2025.1+) + `intersystems-irispython`, `iris-devtester` (test only) (024-graph-kernels)
- InterSystems IRIS — `Graph_KG` schema, `^KG` global (adjacency index) (024-graph-kernels)
- Python 3.11 + `iris_vector_graph.cypher` (ast, lexer, parser, translator) — no new deps (025-named-path-bindings)
- InterSystems IRIS — existing `Graph_KG` schema (nodes, rdf_edges, rdf_labels, rdf_props) (025-named-path-bindings)

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
- 026-subquery-call: Added Python 3.11 + `iris_vector_graph.cypher` (ast, lexer, parser, translator) — no new deps
- 025-named-path-bindings: Added Python 3.11 + `iris_vector_graph.cypher` (ast, lexer, parser, translator) — no new deps
- 024-graph-kernels: Added Python 3.11 + ObjectScript (IRIS 2025.1+) + `intersystems-irispython`, `iris-devtester` (test only)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
