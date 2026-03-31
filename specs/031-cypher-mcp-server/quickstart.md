# Quickstart: Graph Knowledge MCP Tools

**Feature**: 031-cypher-mcp-server

## Setup

### 1. Deploy the ObjectScript classes

```bash
# Copy .cls files to IRIS container
docker cp iris_src/src/Graph/KG/MCPTools.cls <container>:/tmp/
docker cp iris_src/src/Graph/KG/MCPToolSet.cls <container>:/tmp/
docker cp iris_src/src/Graph/KG/MCPService.cls <container>:/tmp/

# Compile in IRIS
iris session IRIS -U USER
> Set sc = $System.OBJ.Load("/tmp/MCPTools.cls", "ck")
> Set sc = $System.OBJ.Load("/tmp/MCPToolSet.cls", "ck")
> Set sc = $System.OBJ.Load("/tmp/MCPService.cls", "ck")
```

### 2. Create CSP web application

In IRIS Management Portal → System Administration → Security → Applications → Web Applications:
- **Name**: `/mcp/graph`
- **Namespace**: USER
- **Dispatch Class**: `Graph.KG.MCPService`
- **CSP/ZEN**: Yes
- **Authentication**: Unauthenticated (for development) or Password

### 3. Configure iris-mcp-server

```toml
# config/mcp-graph.toml
[mcp]
transport = "stdio"

[[iris]]
name = "local"
server = { host = "localhost", port = 52773, username = "CSPSystem", password = "SYS" }
pool = { min = 2, max = 5 }
endpoints = [{ path = "/mcp/graph" }]

[logging]
level = "info"
output = "file"
file = "iris-mcp-graph.log"
```

### 4. Configure Claude Desktop

Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "iris-graph": {
      "command": "/path/to/iris-mcp-server",
      "args": ["--config=/path/to/mcp-graph.toml", "run"]
    }
  }
}
```

### 5. Use it

Load a graph:
> "Load the spondyloarthritis knowledge graph from /data/KG_8.graphml"

Query triples:
> "What is HLA-B27 associated with?"

Find insights:
> "Find surprising connections from HLA-B27 in the graph"

## Tool Reference

| Tool | Description | Params |
|------|-------------|--------|
| CypherQuery | Execute Cypher query | query (string) |
| RunSQL | Execute SQL directly | query (string) |
| LoadGraph | Load GraphML/OBO file | filePath, format |
| GraphStats | Graph overview | (none) |
| PPRWalk | PPR insight discovery | seeds (array), topK, damping |
| EvidenceSearch | Vector literature search | query (string), topK |
