"""Integration tests for spec 209: Arno algorithm dispatch correctness.

Tests run against ivg-iris-enterprise (port 31972). Arno-path tests use a
local arno_loaded_connection that mirrors the spec 208 fixture. Fallback-path
tests use arno_iris_connection with _arno_available forced off.

Bug coverage:
  B1 — execute_ppr routed to ArnoAccel.PPRJson (was NKGAccel, causing parse error)
  B2 — execute_wcc / execute_cdlp fallback routed to Graph.KG.Algorithms (was PageRank)
  B3 — execute_pagerank fallback uses PageRankGlobalJson (was RunJson, PPR method)
  FR-006 — error field populated on exception
"""
from __future__ import annotations

import contextlib
import os
import socket
from pathlib import Path

import pytest

from iris_vector_graph.engine import IRISGraphEngine

_SO_REPO_PATH = Path(__file__).parent.parent.parent / "docker" / "enterprise" / "libarno_callout.so"
_SO_CONTAINER_PATH = "/tmp/libarno_tcp_209.so"
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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def arno_loaded_connection_209(arno_iris_connection):
    """Module-scoped: load Arno .so + install createIRIS monkeypatch for spec 209."""
    if not _SO_REPO_PATH.exists():
        pytest.skip(f"libarno_callout.so not found at {_SO_REPO_PATH}")

    import iris as _iris_module
    _original_createIRIS = _iris_module.createIRIS

    native_conn = _make_native_conn()
    iris_obj = _original_createIRIS(native_conn)

    def _safe_createIRIS(target_conn):
        if target_conn is arno_iris_connection:
            return _original_createIRIS(native_conn)
        return _original_createIRIS(target_conn)

    _iris_module.createIRIS = _safe_createIRIS

    so_data = _SO_REPO_PATH.read_bytes()
    stream = iris_obj.classMethodObject("%Stream.FileBinary", "%New")
    stream.invokeVoid("LinkToFile", _SO_CONTAINER_PATH)
    chunk_size = 32768
    for i in range(0, len(so_data), chunk_size):
        stream.invokeVoid("Write", so_data[i : i + chunk_size])
    stream.invokeVoid("%Save")

    ok1 = bool(iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", _SO_CONTAINER_PATH))
    with contextlib.suppress(Exception):
        iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", _SO_CONTAINER_PATH)

    if not ok1:
        _iris_module.createIRIS = _original_createIRIS
        native_conn.close()
        pytest.skip("ArnoAccel.Load failed")

    yield arno_iris_connection, iris_obj

    _iris_module.createIRIS = _original_createIRIS
    with contextlib.suppress(Exception):
        native_conn.close()


def _cleanup_graph_data(conn):
    """Wipe graph tables for a clean slate."""
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


def _build_graph(eng, prefix, n=10):
    """Build an n-node ring + 2 cross-edges using this prefix."""
    nodes = [f"{prefix}_{i}" for i in range(n)]
    for nid in nodes:
        eng.create_node(nid, labels=["D209"])
    for i in range(n):
        eng.create_edge(nodes[i], "ring", nodes[(i + 1) % n])
    eng.create_edge(nodes[0], "cross", nodes[n // 2])
    eng.create_edge(nodes[2], "cross", nodes[7])
    eng.sync()
    return nodes


@pytest.fixture(scope="module")
def dispatch_graph(arno_loaded_connection_209):
    """Module graph for Arno-path dispatch tests."""
    conn, iris_obj = arno_loaded_connection_209
    with contextlib.suppress(Exception):
        iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", _SO_CONTAINER_PATH)
    _cleanup_graph_data(conn)
    eng = IRISGraphEngine(conn, embedding_dimension=128)
    nodes = _build_graph(eng, "d209a")
    yield eng, conn, nodes


@pytest.fixture(scope="module")
def fallback_graph(arno_iris_connection):
    """Module graph for fallback-path tests (no Arno loading)."""
    _cleanup_graph_data(arno_iris_connection)
    eng = IRISGraphEngine(arno_iris_connection, embedding_dimension=128)
    nodes = _build_graph(eng, "d209f")
    yield eng, arno_iris_connection, nodes


# ── US1: PPR dispatch ─────────────────────────────────────────────────────────

class TestPPRDispatch:
    def test_ppr_arno_single_seed_returns_rows(self, dispatch_graph):
        """B1: Arno PPR single-seed returns non-empty rows without error."""
        eng, _conn, nodes = dispatch_graph
        result = eng._store.execute_ppr([nodes[0]], 0.85, 20)
        assert not result.error, f"Unexpected error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty PPR rows via Arno"

    def test_ppr_arno_multiseed_returns_rows(self, dispatch_graph):
        """B1: Arno PPR multi-seed returns rows without silent parse error."""
        eng, _conn, nodes = dispatch_graph
        result = eng._store.execute_ppr([nodes[0], nodes[5]], 0.85, 20)
        assert result.rows is not None
        assert not result.error, f"Unexpected error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty PPR rows for multi-seed"

    def test_ppr_empty_seed_raises(self, dispatch_graph):
        """B1: empty seed_ids raises ValueError before any IRIS call."""
        eng, _conn, _nodes = dispatch_graph
        with pytest.raises(ValueError, match="seed"):
            eng._store.execute_ppr([], 0.85, 20)

    def test_ppr_fallback_returns_rows(self, fallback_graph):
        """B1: Non-Arno fallback PPR (Graph.KG.PageRank.PPRJson) returns rows."""
        eng, _conn, nodes = fallback_graph
        eng._store._arno_available = None
        result = eng._store.execute_ppr([nodes[0]], 0.85, 20)
        assert len(result.rows) > 0, f"Expected PPR fallback rows; error={result.error}"


# ── US2: WCC / CDLP fallback ─────────────────────────────────────────────────

class TestWCCCDLPFallback:
    def test_wcc_fallback_returns_rows(self, fallback_graph):
        """B2: WCC fallback via Graph.KG.Algorithms.WCCJson returns component rows."""
        eng, _conn, _nodes = fallback_graph
        eng._store._arno_available = None
        result = eng._store.execute_wcc()
        assert not result.error, f"WCC error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty WCC component rows"

    def test_cdlp_fallback_returns_rows(self, fallback_graph):
        """B2: CDLP fallback via Graph.KG.Algorithms.CDLPJson returns community rows."""
        eng, _conn, _nodes = fallback_graph
        eng._store._arno_available = None
        result = eng._store.execute_cdlp(10)
        assert not result.error, f"CDLP error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty CDLP community rows"


# ── US3: PageRank fallback ────────────────────────────────────────────────────

class TestPageRankFallback:
    def test_pagerank_fallback_returns_scored_rows(self, fallback_graph):
        """B3: PageRank fallback via PageRankGlobalJson returns float-scored nodes."""
        eng, _conn, _nodes = fallback_graph
        eng._store._arno_available = None
        result = eng._store.execute_pagerank(0.85, 20)
        assert not result.error, f"PageRank error: {result.error}"
        assert len(result.rows) > 0, "Expected non-empty PageRank rows"
        for row in result.rows:
            assert isinstance(row[1], float), f"Expected float score, got {type(row[1])}: {row[1]}"
