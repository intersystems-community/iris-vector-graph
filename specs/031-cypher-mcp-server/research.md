# Research: Graph Knowledge MCP Tools

**Feature**: 031-cypher-mcp-server | **Date**: 2026-03-31

## R1: %AI.Tool Method Signature Pattern

**Decision**: Each tool method returns `%DynamicObject` (JSON). Method parameters become the MCP tool's input schema automatically. The ai-core framework introspects method signatures to generate JSON Schema for the LLM.

**Rationale**: Verified from ReadyAI demo — `SQLTools.ListTables()` returns `%DynamicObject`, `SQLTools.QueryTable(tableName, patientIds)` takes typed parameters. The framework handles serialization.

**Key pattern**:
```objectscript
Method ToolName(param1 As %String, param2 As %Integer) As %DynamicObject
{
    Set result = {}
    // ... do work ...
    Do result.%Set("status", "OK")
    Return result
}
```

## R2: Embedded Python for CypherQuery

**Decision**: Use `builtins.%Import("iris_vector_graph")` inside the ObjectScript method to call the Python Cypher translator. Alternatively, use a `Language = python` method — but the ReadyAI demo uses pure ObjectScript with SQL, so we'll start with a SQL-based approach for the MVP and add Cypher translation as an enhancement.

**Rationale**: The Cypher translator is Python-only (parser.py + translator.py). There's no ObjectScript Cypher parser. Two options:
1. Embedded Python call from ObjectScript method
2. Pre-translate Cypher to SQL client-side (LLM generates SQL directly)

For MVP: expose a `RunSQL` tool alongside `CypherQuery`. The LLM can generate SQL directly (it knows the Graph_KG schema from `GraphStats` output). CypherQuery with embedded Python is the enhanced path.

**Alternatives**: Pure ObjectScript Cypher parser — rejected, would duplicate the Python implementation.

## R3: GraphStats via SQL

**Decision**: Pure ObjectScript using `%SQL.Statement` against `INFORMATION_SCHEMA` and Graph_KG tables.

**Queries**:
```sql
SELECT COUNT(*) FROM Graph_KG.nodes                    -- node count
SELECT COUNT(*) FROM Graph_KG.rdf_edges                -- edge count
SELECT p, COUNT(*) FROM Graph_KG.rdf_edges GROUP BY p ORDER BY 2 DESC  -- top predicates
SELECT label, COUNT(*) FROM Graph_KG.rdf_labels GROUP BY label ORDER BY 2 DESC  -- top labels
```

## R4: PPRWalk via Existing ObjectScript

**Decision**: Call `##class(Graph.KG.PageRank).RunJson(seedJson, damping, maxIter, topK)` directly. Already returns JSON.

**Rationale**: The ObjectScript PPR is already implemented and tested (62ms on 10K nodes). No Python needed.

## R5: EvidenceSearch via SQL

**Decision**: Use IRIS vector search SQL: `SELECT TOP ? id, VECTOR_COSINE(emb, TO_VECTOR(?)) AS score FROM Graph_KG.kg_NodeEmbeddings WHERE label = ? ORDER BY score DESC`.

**Rationale**: HNSW index search is SQL-native in IRIS. No Python or ObjectScript global walking needed.

## R6: LoadGraph via Embedded Python

**Decision**: Call `iris_vector_graph.engine.IRISGraphEngine.load_networkx()` from embedded Python. The method already handles GraphML parsing, node/edge creation, and returns counts.

**Rationale**: `load_networkx()` and `load_obo()` are the canonical ingest paths in IVG. Reimplementing in ObjectScript would be redundant.

## R7: CSP Web Application Setup

**Decision**: Provide a setup script that creates the `/mcp/graph` CSP web application via `%SYS.Security.Applications` API, sets the dispatch class to `Graph.KG.MCPService`, and configures Unauthenticated access for development.

**Rationale**: The ReadyAI demo uses `iris.script` to configure the CSP application during Docker build. Same pattern.
