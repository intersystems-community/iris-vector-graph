# iris-vector-graph Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-25

## Active Technologies
- Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`. Research needed for lexer/parser pattern. (001-cypher-rd-parser)
- InterSystems IRIS (NodePK schema) (001-cypher-rd-parser)
- Python 3.11 (embedded in InterSystems IRIS via `Language = python`) + `intersystems-irispython`, `json` (stdlib) (011-pyops-vector-extraction)
- N/A (refactoring existing code, no new storage) (011-pyops-vector-extraction)
- Python 3.11 (Embedded Python in InterSystems IRIS) + `intersystems-irispython` (012-sql-parameterization)
- InterSystems IRIS (globals and SQL) (012-sql-parameterization)

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
- 012-sql-parameterization: Added Python 3.11 (Embedded Python in InterSystems IRIS) + `intersystems-irispython`
- 011-pyops-vector-extraction: Added Python 3.11 (embedded in InterSystems IRIS via `Language = python`) + `intersystems-irispython`, `json` (stdlib)
- 010-cypher-advanced-features: Added Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`.


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
