# iris-vector-graph Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-25

## Active Technologies
- Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`. Research needed for lexer/parser pattern. (001-cypher-rd-parser)
- InterSystems IRIS (NodePK schema) (001-cypher-rd-parser)
- Python 3.11 (embedded in InterSystems IRIS via `Language = python`) + `intersystems-irispython`, `json` (stdlib) (011-pyops-vector-extraction)
- N/A (refactoring existing code, no new storage) (011-pyops-vector-extraction)
- Python 3.11 (Embedded Python in InterSystems IRIS) + `intersystems-irispython` (012-sql-parameterization)
- InterSystems IRIS (globals and SQL) (012-sql-parameterization)
- Python 3.11 (Embedded in InterSystems IRIS via `Language = python`) and ObjectScrip + `intersystems-irispython`, `json`, `time` (for benchmarking) (013-bfs-refactoring)
- InterSystems IRIS (Globals) (013-bfs-refactoring)
- Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`, `lark` (for Cypher parsing) (015-los-cypher-api-gaps)
- InterSystems IRIS (RDF Schema: nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings) (015-los-cypher-api-gaps)
- Python 3.11 (same as existing library) + `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (dev/test only) — no new runtime dependencies (018-cypher-vector-search)
- InterSystems IRIS — `Graph_KG.kg_NodeEmbeddings`, `Graph_KG.nodes`, `Graph_KG.rdf_labels`, `Graph_KG.rdf_edges` (no schema changes) (018-cypher-vector-search)

- Python 3.11, InterSystems IRIS 2025.1+ + `intersystems-irispython`, `fastapi`, `strawberry-graphql` (001-cypher-relationship-patterns)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.11, InterSystems IRIS 2025.1+: Follow standard conventions

## Recent Changes
- 018-cypher-vector-search: Added Python 3.11 (same as existing library) + `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (dev/test only) — no new runtime dependencies
- 015-los-cypher-api-gaps: Added Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`, `lark` (for Cypher parsing)
- 013-bfs-refactoring: Added Python 3.11 (Embedded in InterSystems IRIS via `Language = python`) and ObjectScrip + `intersystems-irispython`, `json`, `time` (for benchmarking)


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
