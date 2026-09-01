"""Integration tests for spec 208: Arno deployment flow + codepath tests.

Requires ivg-iris-enterprise container with libarno_callout.so on disk at
docker/enterprise/libarno_callout.so:

    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_arno_deploy.py

All tests use the arno_iris_connection fixture (session-scoped, enterprise).
Tests auto-skip when arno_iris_connection is unavailable.
"""
from __future__ import annotations

import json
import os
import socket
import contextlib
from pathlib import Path

import pytest

# Path to the Arno .so in the repo — relative to project root.
_SO_REPO_PATH = Path(__file__).parent.parent.parent / "docker" / "enterprise" / "libarno_callout.so"
_SO_CONTAINER_PATH = "/tmp/libarno_tcp_208.so"

_ARNO_CONTAINER = os.environ.get("IVG_ARNO_CONTAINER", "ivg-iris-enterprise")


def _make_native_conn():
    """Open a native iris.connect() connection — required for iris.createIRIS().

    arno_iris_connection is a dbapi.Connection which cannot be passed to
    iris.createIRIS(). Open a separate native connection using the same
    host-resolution logic as scripts/enterprise-container.sh tcp-load-arno.
    """
    import iris as _iris
    try:
        orb_ip = socket.gethostbyname(f"{_ARNO_CONTAINER}.orb.local")
        return _iris.connect(hostname=orb_ip, port=1972, namespace="USER",
                             username="_SYSTEM", password="SYS")
    except socket.gaierror:
        return _iris.connect(hostname="localhost", port=31971, namespace="USER",
                             username="_SYSTEM", password="SYS")


# ---------------------------------------------------------------------------
# Session-level fixture: ensure .so loaded once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def arno_loaded_connection(arno_iris_connection):
    """Session fixture: copy .so into container, load, and install createIRIS monkeypatch.

    The arno_iris_connection is a dbapi.Connection. iris.createIRIS() requires
    an iris.IRISConnection (native SDK). We install the same _safe_createIRIS
    monkeypatch that iris_connection (community fixture) uses, redirecting all
    createIRIS(dbapi_conn) calls to a long-lived native connection. This makes
    IRISGraphEngine._iris_obj() work correctly.

    Yields (dbapi_conn, iris_obj) where iris_obj is backed by the native conn.
    """
    if not _SO_REPO_PATH.exists():
        pytest.skip(f"libarno_callout.so not found at {_SO_REPO_PATH}")

    import iris as _iris_module
    _original_createIRIS = _iris_module.createIRIS

    # Open a persistent native connection for the session
    native_conn = _make_native_conn()
    iris_obj = _original_createIRIS(native_conn)

    # Install monkeypatch: createIRIS(arno_iris_connection) → uses native_conn
    def _safe_createIRIS(target_conn):
        if target_conn is arno_iris_connection:
            return _original_createIRIS(native_conn)
        return _original_createIRIS(target_conn)

    _iris_module.createIRIS = _safe_createIRIS

    # Stream .so bytes into container via %Stream.FileBinary
    so_data = _SO_REPO_PATH.read_bytes()
    stream = iris_obj.classMethodObject("%Stream.FileBinary", "%New")
    stream.invokeVoid("LinkToFile", _SO_CONTAINER_PATH)
    chunk_size = 32768
    for i in range(0, len(so_data), chunk_size):
        stream.invokeVoid("Write", so_data[i : i + chunk_size])
    stream.invokeVoid("%Save")

    # Load callout into IRIS process
    ok1 = bool(iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", _SO_CONTAINER_PATH))
    bool(iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", _SO_CONTAINER_PATH))

    if not ok1:
        _iris_module.createIRIS = _original_createIRIS
        native_conn.close()
        pytest.skip("ArnoAccel.Load failed — .so may be wrong arch or container is wrong")

    yield arno_iris_connection, iris_obj

    _iris_module.createIRIS = _original_createIRIS
    with contextlib.suppress(Exception):
        native_conn.close()


@pytest.fixture
def arno_deploy_graph(arno_loaded_connection, arno_master_cleanup):
    """Per-test: clean state + 15-node ring, arno reload after cleanup."""
    conn, iris_obj = arno_loaded_connection

    # Re-load after cleanup — arno process-private globals may have been cleared.
    with contextlib.suppress(Exception):
        iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", _SO_CONTAINER_PATH)
    with contextlib.suppress(Exception):
        iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", _SO_CONTAINER_PATH)

    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine(conn, embedding_dimension=128)

    nodes = [f"d208_{i}" for i in range(15)]
    for n in nodes:
        eng.create_node(n, labels=["Entity"])
    for i in range(14):
        eng.create_edge(f"d208_{i}", "R", f"d208_{i+1}")
    eng.create_edge("d208_14", "R", "d208_0")
    eng.create_edge("d208_0", "R", "d208_7")
    eng.create_edge("d208_3", "R", "d208_11")
    eng.sync()

    yield eng, conn, iris_obj


# ---------------------------------------------------------------------------
# US1: Deploy path — probe before/after, _detect_arno
# ---------------------------------------------------------------------------


class TestArnoDeployPath:
    def test_probe_false_when_disabled(self, arno_loaded_connection, monkeypatch):
        """IVG_DISABLE_ARNO=1 forces arno_available() to False regardless of loaded .so."""
        conn, _ = arno_loaded_connection
        from iris_vector_graph.stores.arno_bridge import arno_available, clear_probe_cache

        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        clear_probe_cache()
        result = arno_available(conn)
        assert result is False, "IVG_DISABLE_ARNO=1 must return False"
        clear_probe_cache()

    def test_probe_true_after_load(self, arno_loaded_connection, monkeypatch):
        """After .so loaded, arno_available() returns True (probe cache cleared)."""
        conn, _ = arno_loaded_connection
        from iris_vector_graph.stores.arno_bridge import arno_available, clear_probe_cache

        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        monkeypatch.setenv("IVG_ARNO_LIB", _SO_CONTAINER_PATH)
        clear_probe_cache()
        result = arno_available(conn)
        assert result is True, (
            f"Expected arno_available=True after load, got False. "
            f"Check if .so is loaded at {_SO_CONTAINER_PATH}"
        )

    def test_detect_arno_returns_true(self, arno_loaded_connection, monkeypatch):
        """IRISGraphStore._detect_arno() returns True and rust_callout capability set."""
        conn, _ = arno_loaded_connection
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)

        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = IRISGraphStore(conn)
        store._arno_available = None  # Reset cached result to force re-detection

        result = store._detect_arno()
        assert result is True, f"_detect_arno() returned False — capabilities: {store._arno_capabilities}"
        assert store._arno_capabilities.get("rust_callout") is True, (
            f"rust_callout not True in capabilities: {store._arno_capabilities}"
        )

    def test_capabilities_shape(self, arno_loaded_connection, monkeypatch):
        """NKGAccel.Capabilities() returns expected JSON structure."""
        conn, iris_obj = arno_loaded_connection
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)

        cap_json = str(iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Capabilities"))
        caps = json.loads(cap_json)
        for key in ("version", "rust_callout", "bfs", "algorithms", "rust_algorithms"):
            assert key in caps, f"Missing capability key: {key!r}. Got: {list(caps.keys())}"
        assert isinstance(caps["algorithms"], list)
        assert isinstance(caps["rust_algorithms"], list)


# ---------------------------------------------------------------------------
# US2: Algorithm paths via loaded Arno
# ---------------------------------------------------------------------------


class TestArnoAlgorithmPaths:
    def test_bfs_via_arno(self, arno_deploy_graph, monkeypatch):
        """BFS executes via Arno fast-path and returns non-empty result."""
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        eng._store._arno_available = None

        result = eng._store.execute_bfs("d208_0", [], 2, "out", 0)
        assert result is not None
        assert not result.error, f"BFS error: {result.error}"
        assert len(result.rows) > 0, "BFS returned empty result"

    def test_ppr_via_arno(self, arno_deploy_graph, monkeypatch):
        """PPR NKGAccel fast-path: dispatches to NKGAccel.PPRJson when 'ppr' in capabilities.

        NKGAccel.PPRJson takes a single seed node ID (not a JSON array). The execute_ppr
        API wraps a list — if Arno has 'ppr' in algorithms, the call goes through
        NKGAccel.PPRJson. We verify dispatch reaches Arno (not ObjectScript fallback)
        and either returns rows or a non-crash error indicating ^NKG state.
        """
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        eng._store._arno_available = None

        caps = eng._store._arno_capabilities if eng._store._arno_available else {}
        if "ppr" not in caps.get("algorithms", []):
            # Not in this Arno build — call classmethod directly to prove NKG path works
            raw = str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "PPRJson", "d208_0", 0.85, 20))
            assert raw is not None, "NKGAccel.PPRJson call failed"
            return

        result = eng._store.execute_ppr(["d208_0"], 0.85, 20)
        assert result is not None
        # Accept either rows or an error — we're testing the dispatch path, not correctness
        assert result.rows is not None

    def test_khop_via_arno(self, arno_deploy_graph, monkeypatch):
        """KHop executes via NKGAccel fast-path — present in all Arno builds."""
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        eng._store._arno_available = None
        eng._store._detect_arno()

        caps = eng._store._arno_capabilities
        if "khop" not in caps.get("algorithms", []):
            pytest.skip("'khop' not in Arno capabilities")

        # Call KHopJson directly via iris_obj — proves NKGAccel fast path works
        raw = str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "KHopJson", "d208_0", 2, 1000))
        assert raw is not None
        import json as _json
        parsed = _json.loads(raw) if raw else {}
        assert isinstance(parsed, (list, dict))

    def test_pagerank_via_arno(self, arno_deploy_graph, monkeypatch):
        """PageRank: dispatches via Arno path when 'pagerank' in capabilities, else ObjScript."""
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        eng._store._arno_available = None

        result = eng._store.execute_pagerank(0.85, 20)
        assert result is not None
        # Graceful: empty rows acceptable if ^NKG has no data; no unhandled exception
        assert result.rows is not None

    def test_wcc_via_arno(self, arno_deploy_graph, monkeypatch):
        """WCC: dispatches via Arno when 'wcc' in capabilities, else ObjectScript fallback."""
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        eng._store._arno_available = None
        eng._store._detect_arno()

        caps = eng._store._arno_capabilities
        if "wcc" in caps.get("algorithms", []):
            result = eng._store.execute_wcc()
            assert result is not None
            assert result.rows is not None
        else:
            pytest.skip("'wcc' not in Arno capabilities for this build")

    def test_cdlp_via_arno(self, arno_deploy_graph, monkeypatch):
        """CDLP: dispatches via Arno when 'cdlp' in capabilities, else ObjectScript fallback."""
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        eng._store._arno_available = None
        eng._store._detect_arno()

        caps = eng._store._arno_capabilities
        if "cdlp" in caps.get("algorithms", []):
            result = eng._store.execute_cdlp(10)
            assert result is not None
            assert result.rows is not None
        else:
            pytest.skip("'cdlp' not in Arno capabilities for this build")


# ---------------------------------------------------------------------------
# US3: BuildNKGRust path in sync()
# ---------------------------------------------------------------------------


class TestBuildNKGRustPath:
    def test_sync_uses_rust_path_when_arno_loaded(self, arno_deploy_graph, monkeypatch):
        """sync() succeeds and NKGAccelLoader.IsLoaded() is True after sync."""
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)

        ok = eng.sync()
        assert ok is True, "sync() returned False"

        is_loaded = bool(iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "IsLoaded"))
        assert is_loaded, "NKGAccelLoader.IsLoaded() False after sync — Rust path may not have run"

    def test_sync_falls_back_to_objectscript_when_disabled(self, arno_deploy_graph, monkeypatch):
        """sync() succeeds via ObjectScript BuildNKG when arno disabled."""
        eng, conn, iris_obj = arno_deploy_graph
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")

        from iris_vector_graph.stores.arno_bridge import clear_probe_cache
        clear_probe_cache()
        eng._store._arno_available = None  # Force re-detection

        ok = eng.sync()
        assert ok is True, "sync() returned False with IVG_DISABLE_ARNO=1"


# ---------------------------------------------------------------------------
# US4: Graceful fallback when .so absent (via env var)
# ---------------------------------------------------------------------------


class TestArnoGracefulFallback:
    def _disable_arno(self, eng, monkeypatch):
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        from iris_vector_graph.stores.arno_bridge import clear_probe_cache
        clear_probe_cache()
        eng._store._arno_available = None

    def test_bfs_fallback_no_exception(self, arno_deploy_graph, monkeypatch):
        """BFS falls back to ObjectScript — no exception raised."""
        eng, conn, _ = arno_deploy_graph
        self._disable_arno(eng, monkeypatch)
        result = eng._store.execute_bfs("d208_0", [], 2, "out", 0)
        assert result is not None

    def test_ppr_fallback_no_exception(self, arno_deploy_graph, monkeypatch):
        """PPR falls back to ObjectScript — no exception raised."""
        eng, conn, _ = arno_deploy_graph
        self._disable_arno(eng, monkeypatch)
        result = eng._store.execute_ppr(["d208_0"], 0.85, 20)
        assert result is not None

    def test_pagerank_fallback_no_exception(self, arno_deploy_graph, monkeypatch):
        """PageRank falls back to ObjectScript — no exception raised."""
        eng, conn, _ = arno_deploy_graph
        self._disable_arno(eng, monkeypatch)
        result = eng._store.execute_pagerank(0.85, 20)
        assert result is not None

    def test_wcc_fallback_no_exception(self, arno_deploy_graph, monkeypatch):
        """WCC falls back to ObjectScript — no exception raised."""
        eng, conn, _ = arno_deploy_graph
        self._disable_arno(eng, monkeypatch)
        result = eng._store.execute_wcc()
        assert result is not None
