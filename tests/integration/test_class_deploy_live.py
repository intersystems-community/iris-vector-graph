"""The packaged ObjectScript deploy, against a live server (DEBT entry 10, bug 3/4).

Runs in the namespace ``IVG_SECONDARY_NAMESPACE`` names (IVGSEC on the enterprise
container), which has its own database, so a forced redeploy does not race the
suite running in USER. Nothing here relies on ``/tmp/src`` or any server-side copy
of the sources: the classes go over the connection, as they do for a pip install.
"""

import os
import socket
import time

import pytest

from iris_vector_graph._engine import class_deploy as cd

NAMESPACE = os.environ.get("IVG_SECONDARY_NAMESPACE", "").strip()


@pytest.fixture(scope="module")
def conn():
    iris = pytest.importorskip("iris")
    if not NAMESPACE:
        pytest.skip("IVG_SECONDARY_NAMESPACE is unset (IVGSEC on the enterprise container)")
    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    port = int(os.environ.get("IVG_PORT", "31972"))
    try:
        ip = socket.gethostbyname(f"{container}.orb.local")
        c = iris.connect(ip, 1972, NAMESPACE, "_SYSTEM", "SYS")
    except Exception:
        try:
            c = iris.connect("localhost", port, NAMESPACE, "_SYSTEM", "SYS")
        except Exception as exc:
            pytest.skip(f"{NAMESPACE} not reachable: {exc}")
    yield c
    # Leave the namespace with the full set, whatever a test did to it.
    os.environ.pop("IVG_EMBEDDED_PYTHON", None)
    cd.deploy_packaged_classes(c, force=True)
    c.close()


def _iris(c):
    import iris

    return iris.createIRIS(c)


def test_a_forced_deploy_compiles_every_class_cleanly(conn):
    result = cd.deploy_packaged_classes(conn, force=True)
    assert result.deployed
    assert result.errors == []
    assert result.skipped == {}
    o = _iris(conn)
    for name in ("Graph.KG.PageRank", "Graph.KG.TraversalBFS", "Graph.KG.Eraser"):
        assert cd.class_exists(o, name), name
    assert o.get(*cd.MARKER) == cd.fingerprint(cd.read_class_sources(cd.packaged_class_dir()))


def test_an_unchanged_set_is_not_reinstalled(conn):
    cd.deploy_packaged_classes(conn)
    t0 = time.perf_counter()
    result = cd.deploy_packaged_classes(conn)
    elapsed = time.perf_counter() - t0
    assert result.unchanged and not result.deployed
    assert elapsed < 2.0, f"no-op deploy took {elapsed:.1f}s"


def test_without_embedded_python_the_core_still_compiles(conn, monkeypatch):
    monkeypatch.setenv("IVG_EMBEDDED_PYTHON", "0")
    result = cd.deploy_packaged_classes(conn, force=True)
    assert result.errors == []
    assert "Graph.KG.PyOps" in result.skipped
    assert "Graph.KG.Service" in result.skipped
    assert "Graph.KG.TraversalBFS" in result.compiled
    assert "Graph.KG.TraversalBFS" not in result.skipped


def test_a_namespace_missing_the_mcp_cluster_gets_it_back(conn):
    # Nothing compiled is the pip-install case: MCPToolSet's generator reads
    # MCPTools, so compiling both in one list fails its first pass.
    o = _iris(conn)
    for name in ("Graph.KG.MCPToolSet", "Graph.KG.MCPTools"):
        o.classMethodValue("%SYSTEM.OBJ", "Delete", name, "-d")
        assert not cd.class_exists(o, name)
    result = cd.deploy_packaged_classes(conn)
    assert result.deployed and result.errors == []
    for name in ("Graph.KG.MCPToolSet", "Graph.KG.MCPTools"):
        assert cd.class_exists(o, name), name


def test_initialize_schema_reports_the_deploy(conn):
    from iris_vector_graph.engine import IRISGraphEngine

    status = IRISGraphEngine(conn, embedding_dimension=4, namespace=NAMESPACE).initialize_schema()
    assert status["objectscript_deployed"] is True
    assert status["objectscript_skipped"] == {}
    assert not any("docker cp" in w for w in status["warnings"])
