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
    # for the upgrade test below: the benchmark script seeds a legacy namespace from the frozen 3.2.0 DDL
    _docker("exec", "-u", "root", CONTAINER, "mkdir", "-p", f"{dest}/tests/e2e", f"{dest}/tests/fixtures").check_returncode()
    for src, to in (
        (ROOT / "scripts", f"{dest}/scripts"),
        (ROOT / "tests" / "e2e" / "fixtures", f"{dest}/tests/e2e/fixtures"),
        (ROOT / "tests" / "fixtures" / "snapshots", f"{dest}/tests/fixtures/snapshots"),
    ):
        _docker("cp", str(src), f"{CONTAINER}:{to}").check_returncode()
    _docker("exec", "-u", "root", CONTAINER, "sh", "-c", f"touch {dest}/tests/__init__.py {dest}/tests/e2e/__init__.py")
    _docker("exec", "-u", "root", CONTAINER, "chmod", "-R", "a+rwX", dest)
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
cur2 = e.conn.cursor()
for verb, sql, args in (
    ("update", "UPDATE Graph_KG.rdf_labels SET label = ? WHERE s = ?", ["x", "no-such-node"]),
    ("update_inline", "UPDATE Graph_KG.rdf_labels SET label = ? WHERE s = ?", ["", "no-such-node"]),
    ("delete", "DELETE FROM Graph_KG.rdf_labels WHERE s = ?", ["no-such-node"]),
):
    try:
        cur2.execute(sql, args)
        out["nodata_" + verb] = [cur2.rowcount, cur2.fetchall()]
    except Exception as exc:
        out["nodata_" + verb] = "RAISED " + repr(exc)
from iris_vector_graph.embedded import EmbeddedIRIS
n = EmbeddedIRIS()
n.set("1", "^IVGEmbProbe", "a", "x"); n.set("2", "^IVGEmbProbe", "b")
seen, cur = [], n.nextSubscript(False, "^IVGEmbProbe", "")
while cur is not None:
    seen.append(cur); cur = n.nextSubscript(False, "^IVGEmbProbe", cur)
out["native_order"] = seen
out["native_string"] = n.getString("^IVGEmbProbe", "b")
out["native_defined"] = [n.isDefined("^IVGEmbProbe", "a"), n.isDefined("^IVGEmbProbe", "b"), n.isDefined("^IVGEmbProbe", "zz")]
n.kill("^IVGEmbProbe", "a")
out["native_after_kill"] = [n.isDefined("^IVGEmbProbe", "a"), n.getString("^IVGEmbProbe", "a")]
n.kill("^IVGEmbProbe")
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


@pytest.mark.parametrize("verb", ["update", "update_inline", "delete"])
def test_a_statement_that_matches_no_row_is_an_empty_result_not_an_error(run, verb):
    assert run["nodata_" + verb] == [0, []]


def test_the_native_subset_that_migrations_use_works_on_real_globals(run):
    assert run["native_order"] == ["a", "b"]
    assert run["native_string"] == "2"
    assert run["native_defined"] == [10, 1, 0]
    assert run["native_after_kill"] == [0, None]


def test_the_4_0_0_upgrade_applies_inside_iris_on_a_legacy_namespace(source_in_container):
    """The whole upgrade (embeddings, docs, edge vectors, ^KG stores), applied by irispython, in batches.

    Its failures so far were all in the gap between a client connection and a process inside IRIS: the
    declared width came back as a $LIST blob, a statement with hundreds of parameters died with <STACK>,
    and the Native handle for the ^KG step did not exist.
    """
    nodes, edge_vectors = 600, 200
    done = _docker(
        "exec", CONTAINER, "sh", "-c",
        f"cd {source_in_container} && {IRISPYTHON} scripts/bench_migration_4_0_0.py --embedded --apply "
        f"--nodes {nodes} --vectors {nodes} --docs {nodes} --edge-vectors {edge_vectors} --dim 8 --steps all",
        timeout=900,
    )
    assert done.returncode == 0, f"stdout: {done.stdout[-1500:]}\nstderr: {done.stderr[-1500:]}"
    assert "refused: None" in done.stdout

    import iris

    conn = iris.connect("localhost", int(os.environ.get("IVG_PORT", "31972")), "IVGLEGACY", "_SYSTEM", "SYS")
    cur = conn.cursor()

    def count(sql):
        cur.execute(sql)
        return int(cur.fetchone()[0])

    assert count("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE graph_id = ''") == nodes
    assert count("SELECT COUNT(*) FROM Graph_KG.docs WHERE graph_id = ''") == nodes
    assert count("SELECT COUNT(*) FROM Graph_KG.edge_vector_quarantine WHERE reason = 'no_edge'") == edge_vectors
