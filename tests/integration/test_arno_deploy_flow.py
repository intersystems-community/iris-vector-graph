"""Integration tests for Arno deployment flow (Spec 208 / IVG-A001).

Tests the end-to-end deployment path for libarno_callout.so:
  1. Binary upload via %Stream.FileBinary (the tcp-load-arno script path)
  2. ArnoAccel.Load / NKGAccelLoader.Load
  3. arno_available() probe returns True after load
  4. A real Rust kernel call succeeds (triangle count on tiny graph)
  5. Graceful degradation when IVG_DISABLE_ARNO=1 (no container needed)
  6. IVG_ARNO_LIB env var override path

Requires: ivg-iris-enterprise container with .so volume-mounted at /tmp/libarno_callout.so
  IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest tests/integration/test_arno_deploy_flow.py

The `arno_iris_connection` fixture skips automatically when the container is absent.
"""
from __future__ import annotations

import json
import os
import socket

import pytest

SO_LOCAL = "docker/enterprise/libarno_callout.so"
SO_CONTAINER = "/tmp/libarno_callout.so"
SO_TCP_PATH = "/tmp/libarno_tcp_deploy_test.so"

_ARNO_CONTAINER = os.environ.get("IVG_ARNO_CONTAINER", "ivg-iris-enterprise")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_native_conn():
    """Open a native iris.connect() connection to the enterprise container.

    The arno_iris_connection fixture uses dbapi.connect() which is incompatible
    with iris.createIRIS(). We open a separate native connection here — same
    pattern as scripts/enterprise-container.sh tcp-load-arno.
    """
    import iris as _iris
    try:
        orb_ip = socket.gethostbyname(f"{_ARNO_CONTAINER}.orb.local")
        return _iris.connect(hostname=orb_ip, port=1972, namespace="USER",
                             username="_SYSTEM", password="SYS")
    except socket.gaierror:
        return _iris.connect(hostname="localhost", port=31971, namespace="USER",
                             username="_SYSTEM", password="SYS")


def _load_so(iris_obj, path: str) -> bool:
    """Load .so via ArnoAccel.Load + NKGAccelLoader.Load. Returns True on success."""
    try:
        iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", path)
    except Exception:
        return False
    try:
        iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", path)
    except Exception:
        pass
    caps_str = str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "Capabilities"))
    caps = json.loads(caps_str)
    return bool(caps.get("rust_callout"))


# ---------------------------------------------------------------------------
# Fixture: skip if .so local file absent (can't deploy what we don't have)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def so_present():
    if not os.path.exists(SO_LOCAL):
        pytest.skip(f"libarno_callout.so not found at {SO_LOCAL} — skip deploy tests")


# ---------------------------------------------------------------------------
# T1: Full tcp-load-arno path — upload binary via stream, load, probe
# ---------------------------------------------------------------------------

class TestArnoTcpLoadPath:
    """Exercise the exact code path used by scripts/enterprise-container.sh tcp-load-arno."""

    def test_binary_upload_and_load(self, arno_iris_connection, so_present):
        """Write .so via %Stream.FileBinary, call Load, verify Capabilities.

        Mirrors tcp-load-arno: open a native iris.connect() connection,
        write binary chunks via %Stream.FileBinary, then Load + probe.
        """
        native_conn = _make_native_conn()
        try:
            import iris as _iris
            iris_obj = _iris.createIRIS(native_conn)

            with open(SO_LOCAL, "rb") as f:
                so_data = f.read()

            assert len(so_data) > 1024 * 1024, (
                f"libarno_callout.so looks too small: {len(so_data)} bytes — may be corrupt"
            )

            stream = iris_obj.classMethodObject("%Stream.FileBinary", "%New")
            stream.invokeVoid("LinkToFile", SO_TCP_PATH)
            chunk_size = 32768
            for i in range(0, len(so_data), chunk_size):
                stream.invokeVoid("Write", so_data[i : i + chunk_size])
            stream.invokeVoid("%Save")

            loaded = _load_so(iris_obj, SO_TCP_PATH)
            assert loaded, (
                "ArnoAccel.Load succeeded but Capabilities.rust_callout is False — "
                "check that the container is Linux ARM64 and the .so is the correct build"
            )
        finally:
            try:
                native_conn.close()
            except Exception:
                pass

    def test_arno_available_true_after_load(self, arno_iris_connection, so_present, monkeypatch):
        """arno_available() returns True on a dbapi connection where .so is loaded.

        Load via native conn, then probe via the dbapi connection that tests use.
        IVG_ARNO_LIB must match the path used in Load() — the probe calls
        $ZF(-4,1,libpath) to verify the already-loaded .so is callable.
        """
        import iris as _iris
        from iris_vector_graph.stores.arno_bridge import arno_available, clear_probe_cache

        native_conn = _make_native_conn()
        try:
            iris_obj = _iris.createIRIS(native_conn)
            _load_so(iris_obj, SO_CONTAINER)
        finally:
            try:
                native_conn.close()
            except Exception:
                pass

        clear_probe_cache()
        # Probe must use the same path we loaded to; SO_CONTAINER = /tmp/libarno_callout.so
        monkeypatch.setenv("IVG_ARNO_LIB", SO_CONTAINER)
        result = arno_available(arno_iris_connection)
        assert result is True, (
            "arno_available() returned False even though ArnoAccel.Load succeeded. "
            f"Probing {SO_CONTAINER!r} — check that container has .so at this path."
        )

    def test_arno_call_triangle_succeeds(self, arno_iris_connection, so_present, monkeypatch):
        """Real Rust triangle-count kernel call on the ^KG global."""
        import iris as _iris
        from iris_vector_graph.stores.arno_bridge import (
            arno_available,
            arno_call,
            clear_probe_cache,
        )
        from iris_vector_graph.engine import IRISGraphEngine

        native_conn = _make_native_conn()
        try:
            iris_obj = _iris.createIRIS(native_conn)
            _load_so(iris_obj, SO_CONTAINER)
        finally:
            try:
                native_conn.close()
            except Exception:
                pass

        clear_probe_cache()
        monkeypatch.setenv("IVG_ARNO_LIB", SO_CONTAINER)
        if not arno_available(arno_iris_connection):
            pytest.skip("libarno_callout.so not available after load attempt")

        # Ensure at least a triangle in ^KG
        eng = IRISGraphEngine(arno_iris_connection, embedding_dimension=128)
        for n in ("tri_a", "tri_b", "tri_c"):
            eng.create_node(n, labels=["T"])
        eng.create_edge("tri_a", "T_REL", "tri_b")
        eng.create_edge("tri_b", "T_REL", "tri_c")
        eng.create_edge("tri_c", "T_REL", "tri_a")
        eng.sync()

        result = arno_call(arno_iris_connection, "kg_triangle_count_global", "^KG", 10)
        parsed = json.loads(result)
        assert isinstance(parsed, list), f"Expected list of triangle results, got: {result!r}"

    def test_capabilities_dict_shape(self, arno_iris_connection, so_present):
        """NKGAccel.Capabilities returns expected keys after load."""
        import iris as _iris

        native_conn = _make_native_conn()
        try:
            iris_obj = _iris.createIRIS(native_conn)
            _load_so(iris_obj, SO_CONTAINER)

            caps_str = str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "Capabilities"))
            caps = json.loads(caps_str)
        finally:
            try:
                native_conn.close()
            except Exception:
                pass

        assert "rust_callout" in caps, f"Capabilities missing rust_callout: {caps}"
        assert "bfs" in caps, f"Capabilities missing bfs: {caps}"
        assert isinstance(caps.get("rust_algorithms"), list), (
            f"rust_algorithms should be a list: {caps}"
        )
        assert len(caps["rust_algorithms"]) > 0, "Expected at least one Rust algorithm"


# ---------------------------------------------------------------------------
# T2: Graceful degradation — IVG_DISABLE_ARNO=1 (no container needed)
# ---------------------------------------------------------------------------

class TestArnoDegradation:
    """These tests need only a mock connection — no container."""

    def test_disable_env_forces_false(self, monkeypatch):
        from unittest.mock import MagicMock
        from iris_vector_graph.stores.arno_bridge import arno_available, clear_probe_cache

        clear_probe_cache()
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        conn = MagicMock()
        result = arno_available(conn)
        assert result is False

    def test_disable_env_does_not_probe(self, monkeypatch):
        from unittest.mock import MagicMock
        from iris_vector_graph.stores.arno_bridge import arno_available, clear_probe_cache

        clear_probe_cache()
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        conn = MagicMock()
        arno_available(conn)
        conn.cursor.assert_not_called()

    def test_disable_arno_arno_call_raises(self, monkeypatch):
        from unittest.mock import MagicMock
        from iris_vector_graph.stores.arno_bridge import arno_call, ArnoError, clear_probe_cache

        clear_probe_cache()
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        conn = MagicMock()
        with pytest.raises(ArnoError, match="not available"):
            arno_call(conn, "kg_leiden_global", "^KG", 10)

    def test_ivg_arno_lib_override(self, monkeypatch):
        """IVG_ARNO_LIB env var changes the lib_path used in probe."""
        from unittest.mock import MagicMock, patch
        from iris_vector_graph.stores.arno_bridge import (
            arno_available,
            clear_probe_cache,
            _probe_cache,
            _conn_key,
        )

        clear_probe_cache()
        monkeypatch.setenv("IVG_ARNO_LIB", "/custom/path/libarno.so")

        conn = MagicMock()
        with patch("iris_vector_graph.stores.arno_bridge._ensure_zf_call_function"):
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (7,)
            mock_cur.close = MagicMock()
            with patch.object(conn, "cursor", return_value=mock_cur):
                arno_available(conn)

        key = _conn_key(conn)
        assert _probe_cache[key]["lib_path"] == "/custom/path/libarno.so", (
            "IVG_ARNO_LIB override not applied to probe cache"
        )


# ---------------------------------------------------------------------------
# T3: So-absent degradation path on real connection
# ---------------------------------------------------------------------------

class TestArnoSoAbsent:
    """Verify probe returns False when .so is not at the expected path."""

    def test_missing_path_returns_false(self, arno_iris_connection, monkeypatch):
        """Probe with a nonexistent .so path → available=False (not a crash)."""
        from iris_vector_graph.stores.arno_bridge import arno_available, clear_probe_cache

        clear_probe_cache()
        monkeypatch.setenv("IVG_ARNO_LIB", "/nonexistent/libarno_xxxx.so")
        result = arno_available(arno_iris_connection)
        assert result is False, (
            "arno_available() should return False for nonexistent .so path, not raise"
        )
        clear_probe_cache()
