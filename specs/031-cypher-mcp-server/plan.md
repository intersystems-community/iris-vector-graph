# Implementation Plan: Graph Knowledge MCP Tools

**Branch**: `031-cypher-mcp-server` | **Date**: 2026-03-31 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification + ReadyAI demo patterns + aicore MCP guide

## Summary

Implement 5 ObjectScript `%AI.Tool` methods for knowledge graph operations, mount via `%AI.MCP.Service` at `/mcp/graph`, and provide `config.toml` for iris-mcp-server. Two tools use embedded Python (CypherQuery, LoadGraph); three are pure ObjectScript (GraphStats, PPRWalk, EvidenceSearch). Ships as .cls files in `iris_src/` alongside existing Graph.KG classes.

## Technical Context

**Language/Version**: ObjectScript (IRIS 2026.2.0AI) + embedded Python 3.12
**Primary Dependencies**: IRIS ai-core framework (`%AI.Tool`, `%AI.ToolSet`, `%AI.MCP.Service`), `iris-mcp-server` (Rust binary), `iris_vector_graph` Python package (embedded)
**Storage**: Existing Graph_KG schema + ^KG/^NKG globals
**Testing**: Manual MCP client testing + pytest e2e for tool method verification
**Target Platform**: IRIS 2026.2.0AI / IRISHealth 2026.2.0AI on Linux (docker) or macOS
**Constraints**: Embedded Python calls must handle import errors gracefully if `iris_vector_graph` is not installed

## Constitution Check

- [x] A dedicated, named IRIS container managed by `iris-devtester` or docker-compose
- [x] Test coverage for tool methods
- [x] No hardcoded connection details in ObjectScript (config.toml handles IRIS connection)
- [x] Schema is additive — no changes to existing Graph_KG tables

**Gate status**: PASS

## Project Structure

### Source Code

```text
iris_src/src/Graph/KG/
├── MCPTools.cls         # NEW: %AI.Tool with 5 methods
├── MCPToolSet.cls       # NEW: %AI.ToolSet grouping with audit policy
├── MCPService.cls       # NEW: %AI.MCP.Service mounted at /mcp/graph

config/
├── mcp-graph.toml       # NEW: iris-mcp-server config for /mcp/graph endpoint

scripts/setup/
├── setup_mcp.sh         # NEW: CSP web application creation script
```

### ReadyAI Demo Reference

The patterns follow `ReadyAI.SQLTools` / `ReadyAI.ToolSet` / `ReadyAI.MCPService` from `ready2026-hackathon/ReadyAI-demo/iris/projects/ObjectScript/ReadyAI/`.

## Complexity Tracking

No constitution violations. Embedded Python in ObjectScript is an established IRIS pattern.
