"""Graph.KG.MCPTools: the tools Claude Desktop reaches through the MCP service.

The tool methods are instance methods that touch only SQL, ^KG and embedded Python, so a
terminal session that instantiates the class runs the same code an MCP call would. Output is
wrapped in markers so the banner and prompts can be ignored.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


def tool(expression: str):
    """Evaluate `expression` (an ObjectScript expression giving a %DynamicObject) on a fresh tool."""
    script = (
        "set t=##class(Graph.KG.MCPTools).%New()\n"
        'write "<<"_"R",!\n'
        f"write {expression}.%ToJSON(),!\n"
        'write "R"_">>",!\n'
        "halt\n"
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "USER"],
        input=script, capture_output=True, text=True, timeout=180,
    )
    raw = proc.stdout.split("<<R", 1)[1].split("R>>", 1)[0]
    body = "".join(line for line in raw.splitlines() if line.strip() and not line.startswith("USER"))
    return json.loads(body)


@pytest.fixture
def graph(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    p = f"mcp{uuid.uuid4().hex[:6]}"
    for n in "abc":
        eng.create_node(f"{p}:{n}", labels=["Thing"])
    eng.create_edge(f"{p}:a", "LINKS", f"{p}:b")
    eng.create_edge(f"{p}:b", "LINKS", f"{p}:c")
    yield p
    eng.delete_nodes_by_prefix(p)


def test_run_sql_returns_named_columns_and_rows():
    out = tool("t.RunSQL(\"SELECT 1 AS x, 'two' AS y\")")
    assert out["status"] == "OK"
    assert out["columns"] == ["x", "y"]
    assert out["rows"] == [[1, "two"]] and out["rowCount"] == 1


def test_run_sql_reports_a_statement_that_does_not_prepare():
    out = tool('t.RunSQL("SELEC nothing")')
    assert out["status"] == "ERROR" and out["error"]


def test_graph_stats_counts_and_ranks(graph):
    out = tool("t.GraphStats()")
    assert out["status"] == "OK"
    assert int(out["nodeCount"]) >= 3 and int(out["edgeCount"]) >= 2
    assert "LINKS" in {p["predicate"] for p in out["topPredicates"]}
    assert "Thing" in {label["label"] for label in out["topLabels"]}


def test_ppr_walk_ranks_the_seed_first_and_honours_top_k(graph):
    out = tool(f't.PPRWalk(["{graph}:a"], 2, 0.85)')
    assert out["status"] == "OK" and out["seedCount"] == 1
    results = out["results"]
    assert 1 <= len(results) <= 2
    assert results[0]["nodeId"] == f"{graph}:a" and results[0]["score"] > 0
    assert [r["score"] for r in results] == sorted((r["score"] for r in results), reverse=True)
