"""IVG.CypherEngine, Cypher from ObjectScript through embedded Python (scratch namespace IVG236).

The class imports iris_vector_graph inside the IRIS process, so the fixture copies the working
tree into the container's embedded-Python path first (and numpy, which the package needs and the
image lacks). It skips when the container has no network for that. The scratch namespace is
used because RebuildKG rewrites the whole adjacency index.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.integration._236_world import PRED, World

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


@pytest.fixture(scope="module")
def embedded_package():
    from tests.integration._embedded_pkg import install_package

    install_package()


@pytest.fixture
def world(ivg_236_scratch, embedded_package):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    e = w.engine()
    e.create_node("ce-a", labels=["Person"])
    e.create_node("ce-b", labels=["Person"])
    e.create_edge("ce-a", PRED, "ce-b")
    w.commit()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def session(*statements: str) -> str:
    script = 'write "<<"_"OUT",!\n' + "\n".join(statements) + '\nwrite !,"OUT"_">>",!\nhalt\n'
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "IVG236"],
        input=script, capture_output=True, text=True, timeout=300,
    ).stdout
    body = out.split("<<OUT", 1)[1].split("OUT>>", 1)[0]
    return "\n".join(line for line in body.splitlines() if line.strip() and not line.startswith("IVG236")).strip()


def test_query_returns_named_columns_and_rows(world):
    out = json.loads(session('set e=##class(IVG.CypherEngine).Local()', 'write e.QueryJson("MATCH (n:Person) RETURN count(n) AS cnt"),!'))
    assert out["error"] == "" and out["columns"] == ["cnt"] and out["rows"] == [[2]]


def test_query_with_parameters_and_a_cypher_error(world):
    ok = json.loads(session('set e=##class(IVG.CypherEngine).Local()', 'write e.QueryJson("MATCH (n) WHERE n.id = $id RETURN n.id AS id", "{""id"":""ce-a""}"),!'))
    assert ok["rows"] == [["ce-a"]]
    bad = json.loads(session('set e=##class(IVG.CypherEngine).Local()', 'write e.QueryJson("MATCH (n RETURN"),!'))
    assert bad["error"] and bad["rows"] == [] and bad["columns"] == []


def test_scalar_and_counts(world):
    out = session(
        'set e=##class(IVG.CypherEngine).Local()',
        'write "S:",e.Scalar("MATCH (n:Person) RETURN count(n)"),!',
        'write "D:",e.Scalar("MATCH (n:Nothing) RETURN n.id","dflt"),!',
        'write "N:",e.GetNodeCount(""),!',
        'write "NL:",e.GetNodeCount("Person"),!',
        'write "E:",e.GetEdgeCount(""),!',
        'write "EP:",e.GetEdgeCount("' + PRED + '"),!',
        'write "X:",e.NodeExists("ce-a"),e.NodeExists("ghost"),!',
    )
    lines = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
    assert lines["S"] == "2" and lines["D"] == "dflt"
    assert lines["N"] == "2" and lines["NL"] == "2"
    assert lines["E"] == "1" and lines["EP"] == "1"
    assert lines["X"] == "10"


def test_labels_schema_and_readiness(world):
    out = session(
        'set e=##class(IVG.CypherEngine).Local()',
        'write "L:",e.GetLabelsJson(),!',
        'write "LC:",e.GetLabelCountsJson(),!',
        'write "R:",e.IsReady(),!',
        'write "I:",e.InitSchemaJson(),!',
    )
    lines = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
    assert "Person" in json.loads(lines["L"])
    assert json.loads(lines["LC"])  # a non-empty summary
    assert lines["R"] == "1"
    assert lines["I"]


def test_rebuild_kg_and_the_smoke_test(world):
    out = session('set e=##class(IVG.CypherEngine).Local()', 'write "RB:",e.RebuildKG(),!', 'do ##class(IVG.CypherEngine).SmokeTest()')
    assert "RB:1" in out
    assert "SmokeTest PASSED" in out
