# Contracts: Graph Knowledge MCP Tools

**Feature**: 031-cypher-mcp-server | **Date**: 2026-03-31

## Contract 1: CypherQuery

**MCP Tool Name**: `mcp_graph_CypherQuery`
**Input**: `{"query": "MATCH (a)-[r]->(b) WHERE a.id = 'hla-b27' RETURN a.id, r, b.id"}`
**Output**:
```json
{"status": "OK", "columns": ["a_id", "r", "b_id"], "rows": [["hla-b27", "is strongly associated with", "spondyloarthritis (spa)"]], "rowCount": 1, "sql": "SELECT ..."}
```
**Error**: `{"status": "ERROR", "error": "Parse error: unexpected token at position 15"}`

## Contract 2: RunSQL

**MCP Tool Name**: `mcp_graph_RunSQL`
**Input**: `{"query": "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE s = 'hla-b27' FETCH FIRST 10 ROWS ONLY"}`
**Output**:
```json
{"status": "OK", "columns": ["s", "p", "o_id"], "rows": [["hla-b27", "is strongly associated with", "spondyloarthritis (spa)"]], "rowCount": 1}
```

## Contract 3: LoadGraph

**MCP Tool Name**: `mcp_graph_LoadGraph`
**Input**: `{"filePath": "/data/KG_8.graphml", "format": "graphml"}`
**Output**: `{"status": "OK", "nodesLoaded": 911, "edgesLoaded": 1144, "buildKGCompleted": true}`
**Error**: `{"status": "ERROR", "error": "File not found: /data/KG_8.graphml"}`

## Contract 4: GraphStats

**MCP Tool Name**: `mcp_graph_GraphStats`
**Input**: `{}` (no parameters)
**Output**:
```json
{"status": "OK", "nodeCount": 911, "edgeCount": 1144, "topPredicates": [{"predicate": "is strongly associated with", "count": 45}, ...], "topLabels": [{"label": "ENTITY", "count": 800}, ...]}
```

## Contract 5: PPRWalk

**MCP Tool Name**: `mcp_graph_PPRWalk`
**Input**: `{"seeds": ["hla-b27"], "topK": 10, "damping": 0.85}`
**Output**:
```json
{"status": "OK", "results": [{"nodeId": "spondyloarthritis (spa)", "score": 0.42}, {"nodeId": "il-17", "score": 0.31}], "seedCount": 1}
```

## Contract 6: EvidenceSearch

**MCP Tool Name**: `mcp_graph_EvidenceSearch`
**Input**: `{"query": "HLA-B27 spondyloarthritis", "topK": 5}`
**Output**:
```json
{"status": "OK", "results": [{"id": "PMID:12345", "score": 0.94, "title": "..."}, ...]}
```
**No embeddings**: `{"status": "OK", "results": [], "message": "No embeddings loaded in kg_NodeEmbeddings"}`
