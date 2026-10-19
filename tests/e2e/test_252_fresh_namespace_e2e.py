"""Spec 252: the first initialize_schema() on a fresh namespace declares the adjacency index.

ensure_indexes runs before the ObjectScript deploy compiles Graph.KG.AdjIndex / LabelIndex, so on a namespace that
has never seen them it skips the index; initialize_schema declares it again once the deploy is done. Every other
spec 252 E2E runs in a namespace where the classes were already compiled, which hid this.

Builds its own namespace (IVG252NEW, own database) in the test container, wiped at the start and the end.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .adjacency_recount import differences

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
PORT = int(os.environ.get("IVG_PORT", "31972"))
NS = "IVG252NEW"
DIR = f"/usr/irissys/mgr/{NS.lower()}/"


def _running():
    p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], capture_output=True, text=True)
    return p.returncode == 0 and p.stdout.strip() == "true"


pytestmark = [
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
    pytest.mark.skipif(SKIP_IRIS_TESTS or not _running(), reason=f"{CONTAINER} not running"),
]

_DROP = f"""Set ns="{NS}", dir="{DIR}"
If ##class(Config.Namespaces).Exists(ns) {{ Set sc=##class(Config.Namespaces).Delete(ns) }}
If ##class(Config.Databases).Exists(ns) {{ Set sc=##class(Config.Databases).Delete(ns) }}
If ##class(%File).Exists(dir_"IRIS.DAT") {{ Set sc=##class(SYS.Database).DeleteDatabase(dir) }}
"""

_CREATE = (
    _DROP
    + """Set ok=##class(%File).CreateDirectoryChain(dir)
Set sc=##class(SYS.Database).CreateDatabase(dir)
If $System.Status.IsOK(sc) { Set pdb("Directory")=dir, sc=##class(Config.Databases).Create(ns,.pdb) }
If $System.Status.IsOK(sc) { Set pns("Globals")=ns, pns("Routines")=ns, sc=##class(Config.Namespaces).Create(ns,.pns) }
Write "RESULT ",$Select($System.Status.IsOK(sc):"OK",1:$System.Status.GetErrorText(sc)),!
"""
)


def _sys(script):
    p = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
        input=script + "Halt\n",
        capture_output=True,
        text=True,
        timeout=300,
    )
    return [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT")]


@pytest.fixture(scope="module")
def fresh():
    assert _sys(_CREATE) == ["RESULT OK"]
    import iris

    conns = []

    def connect():
        conns.append(iris.connect("localhost", PORT, NS, "_SYSTEM", "SYS"))
        return conns[-1]

    yield connect
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    _sys(_DROP)


def test_the_first_initialize_schema_declares_the_index_and_writes_keep_it(fresh):
    conn = fresh()
    status = IRISGraphEngine(conn, embedding_dimension=8).initialize_schema()
    assert status["objectscript_deployed"], status["warnings"]
    engine = IRISGraphEngine(conn, embedding_dimension=8)
    assert engine._adjacency_indexed, "the first initialize_schema() left the namespace unindexed"
    assert status["adjacency_index"].get("idx_edges_adj") == "created", status["adjacency_index"]
    assert status["adjacency_index"].get("idx_labels_adj") == "created", status["adjacency_index"]

    for n in ("fresh_a", "fresh_b"):
        engine.create_node(n, labels=["F"])
    engine.create_edge("fresh_a", "R", "fresh_b")
    r = engine.execute_cypher("MATCH (a {node_id: 'fresh_a'})-[:R]->(b) RETURN b.node_id")
    assert getattr(r, "error", None) is None, r.error
    assert [x[0] for x in r.rows] == ["fresh_b"]
    assert differences(conn, ["fresh_a", "fresh_b"]) == []


def test_a_second_initialize_schema_finds_it_present(fresh):
    status = IRISGraphEngine(fresh(), embedding_dimension=8).initialize_schema()
    assert status["adjacency_index"] == {"idx_edges_adj": "present", "idx_labels_adj": "present"}
