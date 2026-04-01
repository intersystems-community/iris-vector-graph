#!/usr/bin/env python3
"""IVG MCP Server — Cypher query and graph loading tools via stdio MCP protocol.

Runs as a subprocess spawned by IRIS ai-core <MCP><Stdio> in the ToolSet.
Requires: pip install mcp iris-vector-graph intersystems-irispython
"""
import json
import os
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "1972"))
IRIS_NAMESPACE = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USER", "SuperUser")
IRIS_PASS = os.environ.get("IRIS_PASS", "SYS")

server = Server("ivg-cypher")
_conn = None


def get_conn():
    global _conn
    if _conn is None:
        import intersystems_iris as irispy
        _conn = irispy.connect(IRIS_HOST, IRIS_PORT, IRIS_NAMESPACE, IRIS_USER, IRIS_PASS)
    return _conn


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="CypherQuery",
            description=(
                "Execute a Cypher query against the IRIS knowledge graph. "
                "The query is parsed and translated to SQL, then executed. "
                "Returns columns, rows, and the generated SQL. "
                "Example: MATCH (a)-[r]->(b) WHERE a.id = 'hla-b27' RETURN a.id, r, b.id LIMIT 10"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Cypher query string"}
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="LoadGraph",
            description=(
                "Load a graph file (GraphML or OBO format) into the knowledge graph. "
                "After loading, BuildKG() is called to populate the adjacency index. "
                "Returns node and edge counts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filePath": {"type": "string", "description": "Path to graph file on the server"},
                    "format": {"type": "string", "enum": ["graphml", "obo"], "default": "graphml"},
                },
                "required": ["filePath"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "CypherQuery":
            return await _cypher_query(arguments["query"])
        elif name == "LoadGraph":
            return await _load_graph(arguments["filePath"], arguments.get("format", "graphml"))
        else:
            return [TextContent(type="text", text=json.dumps({"status": "ERROR", "error": f"Unknown tool: {name}"}))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"status": "ERROR", "error": str(e)}))]


async def _cypher_query(query: str):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix

    set_schema_prefix("Graph_KG")
    parsed = parse_query(query)
    result = translate_to_sql(parsed)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(result.sql, result.parameters)
    cols = [cur.description[i][0] for i in range(len(cur.description))]
    rows = [list(row) for row in cur.fetchall()]
    cur.close()

    return [TextContent(type="text", text=json.dumps({
        "status": "OK", "columns": cols, "rows": rows, "rowCount": len(rows), "sql": result.sql
    }))]


async def _load_graph(file_path: str, fmt: str):
    from iris_vector_graph.engine import IRISGraphEngine

    conn = get_conn()
    engine = IRISGraphEngine(conn)

    if fmt == "obo":
        stats = engine.load_obo(file_path)
    else:
        import networkx as nx
        G = nx.read_graphml(file_path)
        stats = engine.load_networkx(G)

    try:
        from iris_vector_graph.schema import _call_classmethod
        _call_classmethod(conn, "Graph.KG.Traversal", "BuildKG")
        build_ok = True
    except Exception:
        build_ok = False

    return [TextContent(type="text", text=json.dumps({
        "status": "OK",
        "nodesLoaded": stats.get("nodes_created", 0),
        "edgesLoaded": stats.get("edges_created", 0),
        "buildKGCompleted": build_ok,
        "format": fmt,
    }))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
