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
- 020-initialize-schema-stored-procedures: Added Python 3.11 (project target per AGENTS.md) + `intersystems-irispython`, `iris-devtester` (test only)
- 019-ivg-gql-autogen: Added auto-generating GraphQL layer with connection pooling.
- 018-cypher-vector-search: Added native Cypher `ivg.vector.search` procedure.

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
