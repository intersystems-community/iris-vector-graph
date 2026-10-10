"""A fresh namespace, driven from a process inside IRIS (irispython), end to end.

Unit tests with a fake ``iris`` module missed three 4.2.0 defects that only a real irispython
process shows (Mindwalk reports, 2026-10-10): ``deploy_packaged_classes`` called the absent
``iris.createIRIS``; ``iris.cls(...).%New`` is ``_New`` there; an adopted registry row's NULL width
comes back as ``''``. This runs the wheel's code the way Mindwalk does, in a namespace made for the
run, and asserts database state afterwards.

The repo checkout is copied into the container and run by the container's own ``irispython``; no
port is dialled, so which instance a port reaches does not matter.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
ROOT = Path(__file__).resolve().parents[2]
IRISPYTHON = "/usr/irissys/bin/irispython"

pytestmark = pytest.mark.e2e


def _docker(*args, input=None, timeout=900, user=None):
    cmd = ["docker", *args]
    return subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=timeout)


def _session(namespace, script):
    return _docker("exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", namespace, input=script, timeout=300).stdout


@pytest.fixture(scope="module")
def namespace():
    try:
        _docker("inspect", CONTAINER, timeout=30).check_returncode()
    except (OSError, subprocess.SubprocessError):
        pytest.skip(f"container {CONTAINER} not available")
    ns = "IVGEMB" + uuid.uuid4().hex[:6].upper()
    directory = f"/usr/irissys/mgr/{ns.lower()}/"
    created = _session(
        "%SYS",
        f'Set dir="{directory}"\nDo ##class(%File).CreateDirectoryChain(dir)\n'
        "Set db=##class(SYS.Database).%New()  Set db.Directory=dir  Set sc=db.%Save()\n"
        f'Kill p  Set p("Directory")=dir  Set sc=##class(Config.Databases).Create("{ns}",.p)\n'
        f'Kill p  Set p("Globals")="{ns}",p("Routines")="{ns}"\n'
        f'Set sc=##class(Config.Namespaces).Create("{ns}",.p)\n'
        f'Write "NS=",##class(Config.Namespaces).Exists("{ns}"),!\nHalt\n',
    )
    if "NS=1" not in created:
        pytest.skip(f"could not create {ns}: {created[-300:]}")
    yield ns
    # Order matters: removing the directory of a database that is still mounted, or still in the
    # configuration, leaves an instance that cannot start (its WIJ recovery opens IRIS.DAT).
    _session(
        "%SYS",
        f'Set sc=##class(Config.Namespaces).Delete("{ns}")\n'
        f'Set sc=##class(SYS.Database).DismountDatabase("{directory}")\n'
        f'Set sc=##class(Config.Databases).Delete("{ns}")\n'
        f'Write "LEFT=",##class(Config.Databases).Exists("{ns}"),!\n'
        f'If \'##class(Config.Databases).Exists("{ns}") {{ Do ##class(%File).RemoveDirectoryTree("{directory}") }}\nHalt\n',
    )


@pytest.fixture(scope="module")
def source_in_container():
    dest = f"/tmp/ivg_emb_{uuid.uuid4().hex[:8]}"
    _docker("exec", "-u", "root", CONTAINER, "mkdir", "-p", f"{dest}/iris_src").check_returncode()
    for src, to in ((ROOT / "iris_vector_graph", f"{dest}/iris_vector_graph"), (ROOT / "iris_src" / "src", f"{dest}/iris_src/src")):
        _docker("cp", str(src), f"{CONTAINER}:{to}").check_returncode()
    _docker("exec", "-u", "root", CONTAINER, "chmod", "-R", "a+rX", dest)
    yield dest
    _docker("exec", "-u", "root", CONTAINER, "rm", "-r", "-f", dest)


SCRIPT = r'''
import json, logging, sys
sys.path.insert(0, "{src}")
import iris
iris.cls("%SYSTEM.Process").SetNamespace("{ns}")
records = []
class H(logging.Handler):
    def emit(self, r): records.append(r.getMessage())
logging.getLogger().addHandler(H()); logging.getLogger().setLevel(logging.WARNING)
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.embedded import EmbeddedConnection
out = {{}}
e = IRISGraphEngine(EmbeddedConnection(), embedding_dimension=4)
out["init"] = e.initialize_schema()
out["deploy_warnings"] = [m for m in records if "deploy" in m.lower() and "fail" in m.lower()]
out["pagerank_class"] = bool(int(iris.cls("%Dictionary.CompiledClass")._ExistsId("Graph.KG.PageRank")))
e.create_node("emb:a", labels=["T"])
out["store"] = e.store_embedding("emb:a", [0.1, 0.2, 0.3, 0.4])
cur = e.conn.cursor()
cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = 'emb:a'")
out["rows"] = list(cur.fetchone())[0]
inv = e.embedding_inventory()
out["inventory"] = len(inv)
e.create_node("emb:b")
e.create_edge("emb:a", "R", "emb:b")
e.sync()
out["edges"] = e.get_edge_count() if hasattr(e, "get_edge_count") else None
out["deleted"] = e.delete_node("emb:a")
out["gone"] = e.get_node("emb:a") is None
e.store_node("TMP:1", labels=["Tmp"], properties={{"k": "v"}})
e.store_node("TMP:2")
e.store_edge("TMP:1", "R", "TMP:2")
e.store_embedding("TMP:1", [0.4, 0.3, 0.2, 0.1])
out["deleted_legacy_written"] = e.delete_node("TMP:1")
out["legacy_gone"] = e.get_node("TMP:1") is None
out["delete_missing"] = e.delete_node("TMP:nope")
print("RESULT=" + json.dumps(out, default=str))
'''


@pytest.fixture(scope="module")
def run(namespace, source_in_container):
    script = SCRIPT.format(src=source_in_container, ns=namespace)
    path = f"{source_in_container}/probe.py"
    local = ROOT / "tests" / "e2e" / ".probe_tmp.py"
    local.write_text(script)
    try:
        _docker("cp", str(local), f"{CONTAINER}:{path}").check_returncode()
    finally:
        local.unlink(missing_ok=True)
    done = _docker("exec", CONTAINER, "sh", "-c", f"cd /tmp && {IRISPYTHON} {path}", timeout=900)
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("RESULT=")]
    assert lines, f"no result from irispython.\nstdout: {done.stdout[-1500:]}\nstderr: {done.stderr[-1500:]}"
    return json.loads(lines[-1][len("RESULT="):])


def test_initialize_schema_deploys_the_packaged_classes_from_inside_iris(run):
    assert run["deploy_warnings"] == []
    assert run["pagerank_class"] is True
    assert run["init"].get("objectscript_deployed") is True


def test_the_first_store_embedding_after_a_fresh_schema_stores_the_vector(run):
    assert run["store"] is True
    assert run["rows"] == 1


def test_the_embedding_inventory_reads_adopted_routes(run):
    assert run["inventory"] >= 1


def test_delete_node_reports_that_it_deleted(run):
    assert run["gone"] is True
    assert run["deleted"] is True


def test_delete_node_on_a_node_written_with_the_legacy_writers_reports_that_it_deleted(run):
    assert run["legacy_gone"] is True
    assert run["deleted_legacy_written"] is True


def test_delete_node_of_a_node_that_does_not_exist_reports_false(run):
    assert run["delete_missing"] is False
