"""The same IVG operations give the same answers on both hosts that can be reached from here.

``iris_vector_graph.host`` hides the difference between a client process (the Native API) and a process inside IRIS
(irispython). This runs one fixed sequence of operations on each, against the scratch namespace IVG236 of the enterprise
container, and requires identical results. The wgproto worker cannot be reached from here; its rule (never issue a
``%`` method) is held by the interface itself and tested in ``tests/unit/test_host.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
PORT = int(os.environ.get("IVG_PORT", "31972"))
NAMESPACE = "IVG236"
ROOT = Path(__file__).resolve().parents[2]
IRISPYTHON = "/usr/irissys/bin/irispython"

pytestmark = pytest.mark.e2e

OPERATIONS = r'''
def run(host):
    out = {}
    g = "^IVGHostConf"
    host.kill(g)
    host.set("v1", g, "a", "x")
    host.set("v2", g, "a", "y")
    host.set("v3", g, "b")
    out["get"] = [host.get(g, "a", "x"), host.get(g, "b"), host.get(g, "missing")]
    out["is_defined"] = [host.is_defined(g, "a"), host.is_defined(g, "a", "x"), host.is_defined(g, "b"), host.is_defined(g, "z")]
    walk, cur = [], host.next_subscript(g, "")
    while cur is not None:
        walk.append(cur)
        cur = host.next_subscript(g, cur)
    out["walk_top"] = walk
    out["walk_under_a"] = []
    cur = host.next_subscript(g, "a", "")
    while cur is not None:
        out["walk_under_a"].append(cur)
        cur = host.next_subscript(g, "a", cur)
    out["walk_reverse"] = host.next_subscript(g, "", reverse=True)
    host.kill(g, "a")
    out["after_kill"] = [host.is_defined(g, "a"), host.is_defined(g, "b")]
    out["call"] = str(host.call("%SYSTEM.Version", "GetNumber"))[:1]
    out["call_large_plain"] = str(host.call_large("Graph.KG.Meta", "IsSet", "kg_built_probe_never_set"))
    out["class_exists"] = [host.class_exists("Graph.KG.PageRank"), host.class_exists("Graph.KG.NoSuchClass")]
    out["method_exists"] = [host.method_exists("Graph.KG.Eraser", "EraseNodeIds"), host.method_exists("Graph.KG.Eraser", "NoSuchMethod")]
    try:
        host.call("%Dictionary.CompiledClass", "%ExistsId", "X")
        out["percent"] = "ran"
    except Exception as exc:
        out["percent"] = type(exc).__name__
    host.kill(g)
    return out
'''


def _client_results():
    import iris

    from iris_vector_graph.host import host_for

    conn = iris.connect("localhost", PORT, NAMESPACE, "_SYSTEM", "SYS")
    scope: dict = {}
    exec(OPERATIONS, scope)
    return scope["run"](host_for(conn))


def _in_process_results():
    dest = f"/tmp/ivg_host_{uuid.uuid4().hex[:8]}"
    run = lambda *a, **k: subprocess.run(["docker", *a], capture_output=True, text=True, timeout=300, **k)  # noqa: E731
    run("exec", "-u", "root", CONTAINER, "mkdir", "-p", f"{dest}").check_returncode()
    run("cp", str(ROOT / "iris_vector_graph"), f"{CONTAINER}:{dest}/iris_vector_graph").check_returncode()
    script = (
        "import sys, json\n"
        f"sys.path.insert(0, '{dest}')\n"
        "import iris\n"
        f"iris.cls('%SYSTEM.Process').SetNamespace('{NAMESPACE}')\n"
        "from iris_vector_graph.host import host_for\n"
        "from iris_vector_graph.embedded import EmbeddedConnection\n"
        + OPERATIONS
        + "\nprint('RESULT=' + json.dumps(run(host_for(EmbeddedConnection())), default=str))\n"
    )
    local = ROOT / "tests" / "e2e" / ".host_probe_tmp.py"
    local.write_text(script)
    try:
        run("cp", str(local), f"{CONTAINER}:{dest}/probe.py").check_returncode()
    finally:
        local.unlink(missing_ok=True)
    run("exec", "-u", "root", CONTAINER, "chmod", "-R", "a+rwX", dest)
    done = run("exec", CONTAINER, "sh", "-c", f"cd /tmp && {IRISPYTHON} {dest}/probe.py")
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("RESULT=")]
    assert lines, f"stdout: {done.stdout[-1200:]}\nstderr: {done.stderr[-1200:]}"
    run("exec", "-u", "root", CONTAINER, "rm", "-r", "-f", dest)
    return json.loads(lines[-1][len("RESULT="):])


@pytest.fixture(scope="module")
def results():
    try:
        client = _client_results()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"enterprise container not reachable on {PORT}: {exc}")
    return client, _in_process_results()


def _normalised(value):
    """Both hosts return numbers and text; compare them as text."""
    if isinstance(value, dict):
        return {k: _normalised(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalised(v) for v in value]
    return None if value is None else str(value)


def test_both_hosts_give_the_same_answers(results):
    client, in_process = results
    assert _normalised(client) == _normalised(in_process)


def test_the_operations_did_what_they_say(results):
    client, _ = results
    assert client["get"] == ["v1", "v3", None] or _normalised(client["get"]) == ["v1", "v3", None]
    assert [int(x) for x in client["is_defined"]] == [10, 1, 1, 0]
    assert client["walk_top"] == ["a", "b"]
    assert client["walk_under_a"] == ["x", "y"]
    assert client["after_kill"] == [0, 1]
    assert client["class_exists"] == [True, False]
    assert client["method_exists"] == [True, False]


def test_a_percent_method_is_refused_on_both(results):
    client, in_process = results
    assert client["percent"] == in_process["percent"] == "HostError"
