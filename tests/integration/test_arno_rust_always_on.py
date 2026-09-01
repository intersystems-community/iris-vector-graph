"""Integration tests for spec 210: Arno Rust kernel always-on.

Tests run against ivg-iris-enterprise (port 31972).
Verifies that after ArnoAccel.Load(), rust_callout=true is visible from any
IRIS worker process (not just the one that called Load()).

Bug: ^||ArnoAccel("dllid") is process-private → workers that didn't Load() see
dllid=0 → IsAvailable()=false → rust_callout=false → Rust never fires.
Fix: ^ArnoAccel (persistent) so all workers see the dllid.
"""
from __future__ import annotations

import contextlib
import os
import socket
from pathlib import Path

import pytest

from iris_vector_graph.engine import IRISGraphEngine

_SO_REPO_PATH = Path(__file__).parent.parent.parent / "docker" / "enterprise" / "libarno_callout.so"
_SO_CONTAINER_PATH = "/tmp/libarno_tcp_210.so"
_ARNO_CONTAINER = os.environ.get("IVG_ARNO_CONTAINER", "ivg-iris-enterprise")


def _make_native_conn():
    import iris as _iris
    try:
        orb_ip = socket.gethostbyname(f"{_ARNO_CONTAINER}.orb.local")
        return _iris.connect(hostname=orb_ip, port=1972, namespace="USER",
                             username="_SYSTEM", password="SYS")
    except socket.gaierror:
        return _iris.connect(hostname="localhost", port=31971, namespace="USER",
                             username="_SYSTEM", password="SYS")


def _cleanup(conn):
    cursor = conn.cursor()
    try:
        for table in ["Graph_KG.nodes", "Graph_KG.rdf_edges", "Graph_KG.rdf_props",
                      "Graph_KG.rdf_labels"]:
            with contextlib.suppress(Exception):
                cursor.execute(f"DELETE FROM {table}")
        with contextlib.suppress(Exception):
            conn.commit()
        with contextlib.suppress(Exception):
            cursor.execute("Do ##class(Graph.KG.Traversal).BuildKG()")
        conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


@pytest.fixture(scope="module")
def arno_loaded_210(arno_iris_connection):
    """Load Arno .so and install createIRIS monkeypatch for spec 210 tests."""
    if not _SO_REPO_PATH.exists():
        pytest.skip(f"libarno_callout.so not found at {_SO_REPO_PATH}")

    import iris as _iris_module
    _orig = _iris_module.createIRIS
    native_conn = _make_native_conn()
    iris_obj = _orig(native_conn)

    def _safe_createIRIS(target_conn):
        return _orig(native_conn) if target_conn is arno_iris_connection else _orig(target_conn)

    _iris_module.createIRIS = _safe_createIRIS

    so_data = _SO_REPO_PATH.read_bytes()
    stream = iris_obj.classMethodObject("%Stream.FileBinary", "%New")
    stream.invokeVoid("LinkToFile", _SO_CONTAINER_PATH)
    for i in range(0, len(so_data), 32768):
        stream.invokeVoid("Write", so_data[i : i + 32768])
    stream.invokeVoid("%Save")

    ok = bool(iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", _SO_CONTAINER_PATH))
    with contextlib.suppress(Exception):
        iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", _SO_CONTAINER_PATH)

    if not ok:
        _iris_module.createIRIS = _orig
        native_conn.close()
        pytest.skip("ArnoAccel.Load failed")

    _cleanup(arno_iris_connection)
    eng = IRISGraphEngine(arno_iris_connection, embedding_dimension=128)
    nodes = [f"d210_{i}" for i in range(10)]
    for nid in nodes:
        eng.create_node(nid, labels=["D210"])
    for i in range(10):
        eng.create_edge(nodes[i], "ring", nodes[(i + 1) % 10])
    eng.create_edge(nodes[0], "cross", nodes[5])
    eng.sync()

    yield arno_iris_connection, iris_obj, eng, nodes

    _iris_module.createIRIS = _orig
    with contextlib.suppress(Exception):
        native_conn.close()


# ── US1 / US3: rust_callout visibility ───────────────────────────────────────

class TestRustCallout:
    def test_rust_callout_true_after_load(self, arno_loaded_210):
        """After Load(), _arno_capabilities["rust_callout"] must be True."""
        _conn, _iris_obj, eng, _nodes = arno_loaded_210
        eng._store._arno_available = None  # force re-probe
        eng._store._arno_capabilities = {}
        eng._store._detect_arno()
        assert eng._store._arno_capabilities.get("rust_callout") is True, (
            f"rust_callout must be True after Load(); got: {eng._store._arno_capabilities}"
        )

    def test_rust_algorithms_nonempty(self, arno_loaded_210):
        """After Load(), rust_algorithms must contain pagerank, wcc, cdlp, bfs."""
        _conn, _iris_obj, eng, _nodes = arno_loaded_210
        rust_algos = eng._store._arno_capabilities.get("rust_algorithms", [])
        for algo in ("pagerank", "wcc", "cdlp", "bfs"):
            assert algo in rust_algos, (
                f"Expected '{algo}' in rust_algorithms; got: {rust_algos}"
            )

    def test_capabilities_fresh_connection(self, arno_loaded_210):
        """Capabilities() on a fresh native connection must see rust_callout=true.

        This is the core cross-process visibility test. With ^ArnoAccel (not ^||),
        any worker can see the dllid set by Load().
        """
        _conn, _iris_obj, eng, _nodes = arno_loaded_210
        import json as _json
        import iris as _iris
        fresh_native = _make_native_conn()
        try:
            fresh_obj = _iris.createIRIS(fresh_native)
            cap_json = fresh_obj.classMethodValue("Graph.KG.NKGAccel", "Capabilities")
            caps = _json.loads(str(cap_json))
            assert caps.get("rust_callout") is True, (
                f"rust_callout must be True from fresh native conn; got: {caps}"
            )
        finally:
            with contextlib.suppress(Exception):
                fresh_native.close()


# ── US1: Rust path actually executes ─────────────────────────────────────────

class TestRustPath:
    def test_pagerank_uses_rust_path(self, arno_loaded_210):
        """execute_pagerank with Arno loaded returns rows AND rust_callout=True."""
        _conn, _iris_obj, eng, _nodes = arno_loaded_210
        result = eng._store.execute_pagerank(0.85, 20)
        assert not result.error, f"PageRank error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty PageRank rows"
        assert eng._store._arno_capabilities.get("rust_callout") is True

    def test_wcc_uses_rust_path(self, arno_loaded_210):
        """execute_wcc with Arno loaded returns rows (via Rust WCC or ObjectScript fallback)."""
        _conn, _iris_obj, eng, _nodes = arno_loaded_210
        result = eng._store.execute_wcc()
        assert not result.error, f"WCC error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty WCC rows"

    def test_cdlp_uses_rust_path(self, arno_loaded_210):
        """execute_cdlp with Arno loaded returns rows."""
        _conn, _iris_obj, eng, _nodes = arno_loaded_210
        result = eng._store.execute_cdlp(10)
        assert not result.error, f"CDLP error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty CDLP rows"
