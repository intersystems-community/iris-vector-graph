# Data Model: Graph Knowledge MCP Tools

**Feature**: 031-cypher-mcp-server | **Date**: 2026-03-31

## New ObjectScript Classes

### Graph.KG.MCPTools (extends %AI.Tool)

```
MCPTools
├── CypherQuery(query: String) → DynamicObject         # Embedded Python → Cypher translator → SQL → results
├── RunSQL(query: String) → DynamicObject               # Direct SQL execution (LLM-generated)
├── LoadGraph(filePath: String, format: String) → DynamicObject  # Embedded Python → load_networkx/load_obo + BuildKG
├── GraphStats() → DynamicObject                        # SQL counts + aggregations
├── PPRWalk(seeds: DynamicArray, topK: Integer, damping: Double) → DynamicObject  # PageRank.RunJson
└── EvidenceSearch(query: String, topK: Integer) → DynamicObject  # Vector cosine SQL
```

Each method returns a `%DynamicObject` with at minimum `{"status": "OK"|"ERROR", ...}`.

### Graph.KG.MCPToolSet (extends %AI.ToolSet)

```xml
<ToolSet Name="Graph.KG.MCPToolSet">
    <Description>Knowledge graph query, loading, and analysis tools</Description>
    <Policies>
        <Audit Class="%AI.Policy.ConsoleAudit" />
    </Policies>
    <Include Class="Graph.KG.MCPTools" />
</ToolSet>
```

### Graph.KG.MCPService (extends %AI.MCP.Service)

```
MCPService
└── Parameter SPECIFICATION = "Graph.KG.MCPToolSet"
```

Mounted as CSP web application at `/mcp/graph`.

## No Schema Changes

No new tables, columns, or indexes. All tools operate on existing Graph_KG tables and ^KG/^NKG globals.

## Configuration File

### config/mcp-graph.toml

```toml
[mcp]
transport = "stdio"    # or "http" for remote access

[[iris]]
name = "local"
server = { host = "localhost", port = 52773, username = "CSPSystem", password = "SYS" }
pool = { min = 2, max = 5 }
endpoints = [
    { path = "/mcp/graph" }
]

[logging]
level = "info"
output = "file"
file = "iris-mcp-graph.log"
```
