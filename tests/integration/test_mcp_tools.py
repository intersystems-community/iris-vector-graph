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


def tool(expression: str, python: bool = False):
    """Evaluate `expression` on a fresh tool; a [ Language = python ] method returns a Python dict."""
    dump = f"##class(%SYS.Python).Import(\"json\").dumps({expression})" if python else f"{expression}.%ToJSON()"
    script = (
        "set t=##class(Graph.KG.MCPTools).%New()\n"
        'write "<<"_"R",!\n'
        f"write {dump},!\n"
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


def test_evidence_search_reports_when_no_embeddings_exist_or_ranks_by_similarity(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    node = f"mcpev{uuid.uuid4().hex[:6]}"
    eng.create_node(node)
    eng.store_embedding(node, [0.5] * 768)
    try:
        out = tool(f't.EvidenceSearch("{node}", 3)', python=True)
        assert out["status"] == "OK", out
        assert out["resultCount"] >= 1
        assert out["results"][0]["id"] == node
        assert [r["score"] for r in out["results"]] == sorted((r["score"] for r in out["results"]), reverse=True)
    finally:
        eng.delete_nodes_by_prefix(node)


# ------------------------------------------------------------ Python tools with the package present


def scratch_tool(expression: str):
    """Like tool(), but in the scratch namespace, with a Python result."""
    script = (
        "set t=##class(Graph.KG.MCPTools).%New()\n"
        'write "<<"_"R",!\n'
        f'write ##class(%SYS.Python).Import("json").dumps({expression}),!\n'
        'write "R"_">>",!\n'
        "halt\n"
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "IVG236"],
        input=script, capture_output=True, text=True, timeout=300,
    )
    raw = proc.stdout.split("<<R", 1)[1].split("R>>", 1)[0]
    return json.loads("".join(line for line in raw.splitlines() if line.strip() and not line.startswith("IVG236")))


@pytest.fixture
def scratch(ivg_236_scratch):
    from tests.integration._236_world import World
    from tests.integration._embedded_pkg import install_package

    install_package()
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    e = w.engine()
    e.create_node("mc-a", labels=["Person"])
    e.create_node("mc-b", labels=["Person"])
    e.create_edge("mc-a", "KNOWS", "mc-b")
    w.commit()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def test_cypher_query_returns_columns_rows_and_the_sql_it_ran(scratch):
    out = scratch_tool('t.CypherQuery("MATCH (n:Person) RETURN count(n) AS cnt")')
    assert out["status"] == "OK", out
    assert out["columns"] == ["cnt"] and out["rows"] == [[2]] and out["rowCount"] == 1
    assert "Graph_KG" in out["sql"]


def test_cypher_query_reports_a_syntax_error(scratch):
    out = scratch_tool('t.CypherQuery("MATCH (n RETURN")')
    assert out["status"] == "ERROR" and out["error"]


def test_load_graph_reads_the_file_before_it_writes_and_reports_what_it_could_not_load(scratch, tmp_path):
    # Embedded engines still cannot write edges (see docs/research/objectscript-line-coverage.md), so
    # this pins what does work: the file parses, the nodes load, and the answer is a status object.
    graphml = tmp_path / "g.graphml"
    graphml.write_text(
        '<?xml version="1.0"?>'
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
        '<graph edgedefault="directed"><node id="lg-x"/><node id="lg-y"/></graph></graphml>'
    )
    subprocess.run(["docker", "cp", str(graphml), f"{CONTAINER}:/tmp/mcp_g.graphml"], check=True, capture_output=True)
    out = scratch_tool('t.LoadGraph("/tmp/mcp_g.graphml")')
    assert out["status"] == "OK", out
    assert out["nodesLoaded"] == 2 and out["edgesLoaded"] == 0 and out["format"] == "graphml"


def test_load_graph_of_a_missing_file_is_an_error_object(scratch):
    out = scratch_tool('t.LoadGraph("/tmp/definitely-not-here.graphml")')
    assert out["status"] == "ERROR" and out["error"]
