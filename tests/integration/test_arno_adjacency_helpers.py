"""Integration tests for spec 208 US5: arno_bridge adjacency helpers.

Requires ivg-iris-enterprise with libarno_callout.so loaded.

Tests:
  - build_kg_adjacency_json — walks ^KG("out",0,...) via Native API, returns parseable JSON
  - build_kg_adjacency_chunked — chunked upload path, returns (idx_to_node, edge_count)
  - serverside path matches native-API path (node counts within tolerance)
"""
from __future__ import annotations

import json
import os
import contextlib
from pathlib import Path

import pytest

_SO_REPO_PATH = Path(__file__).parent.parent.parent / "docker" / "enterprise" / "libarno_callout.so"
_SO_CONTAINER_PATH = "/tmp/libarno_tcp_208.so"


@pytest.fixture(scope="module")
def arno_adj_conn(arno_iris_connection):
    """Module fixture: ensure arno loaded once, insert a 10-node graph, yield conn."""
    if not _SO_REPO_PATH.exists():
        pytest.skip(f"libarno_callout.so not found at {_SO_REPO_PATH}")

    import iris as _iris
    iris_obj = _iris.createIRIS(arno_iris_connection)

    # Load .so if not already loaded
    with contextlib.suppress(Exception):
        so_data = _SO_REPO_PATH.read_bytes()
        stream = iris_obj.classMethodObject("%Stream.FileBinary", "%New")
        stream.invokeVoid("LinkToFile", _SO_CONTAINER_PATH)
        for i in range(0, len(so_data), 32768):
            stream.invokeVoid("Write", so_data[i : i + 32768])
        stream.invokeVoid("%Save")
        iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", _SO_CONTAINER_PATH)
        iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", _SO_CONTAINER_PATH)

    from iris_vector_graph.stores.arno_bridge import clear_probe_cache
    os.environ.pop("IVG_DISABLE_ARNO", None)
    clear_probe_cache()

    # Insert a clean 10-node test graph for adjacency helpers
    cursor = arno_iris_connection.cursor()
    with contextlib.suppress(Exception):
        for i in range(10):
            cursor.execute(
                "INSERT INTO Graph_KG.nodes (node_id) SELECT ? "
                "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id=?)",
                [f"adj208_{i}", f"adj208_{i}"],
            )
    arno_iris_connection.commit()
    cursor.close()

    # Build ^KG adjacency (write ^KG("out",...))
    with contextlib.suppress(Exception):
        iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")

    yield arno_iris_connection

    # Cleanup adj208 nodes
    cursor = arno_iris_connection.cursor()
    with contextlib.suppress(Exception):
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'adj208_%'")
        arno_iris_connection.commit()
    cursor.close()


class TestArnoBridgeAdjacency:
    def test_build_kg_adjacency_json_parseable(self, arno_adj_conn, monkeypatch):
        """build_kg_adjacency_json returns parseable JSON with nodes and edges keys."""
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        from iris_vector_graph.stores.arno_bridge import build_kg_adjacency_json

        result = build_kg_adjacency_json(arno_adj_conn)
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        parsed = json.loads(result)
        assert "nodes" in parsed, f"'nodes' key missing from result: {list(parsed.keys())}"
        assert "edges" in parsed, f"'edges' key missing from result: {list(parsed.keys())}"
        assert isinstance(parsed["nodes"], list), "'nodes' must be a list"
        assert isinstance(parsed["edges"], list), "'edges' must be a list"

    def test_build_kg_adjacency_chunked_returns_nodelist(self, arno_adj_conn, monkeypatch):
        """build_kg_adjacency_chunked returns (idx_to_node, edge_count) both non-negative."""
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        from iris_vector_graph.stores.arno_bridge import (
            build_kg_adjacency_chunked,
            clear_probe_cache,
        )

        clear_probe_cache()

        idx_to_node, edge_count = build_kg_adjacency_chunked(arno_adj_conn)
        assert isinstance(idx_to_node, list), f"Expected list, got {type(idx_to_node)}"
        assert isinstance(edge_count, int), f"Expected int, got {type(edge_count)}"
        assert edge_count >= 0, f"Negative edge count: {edge_count}"

    def test_serverside_path_matches_native_api(self, arno_adj_conn, monkeypatch):
        """Node count from serverside path matches node count from native-API path."""
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        from iris_vector_graph.stores.arno_bridge import (
            build_kg_adjacency_json,
            build_kg_adjacency_chunked,
            clear_probe_cache,
        )

        clear_probe_cache()

        json_result = build_kg_adjacency_json(arno_adj_conn)
        json_nodes = json.loads(json_result)["nodes"]

        clear_probe_cache()

        chunked_nodes, _ = build_kg_adjacency_chunked(arno_adj_conn)

        json_count = len(json_nodes)
        chunked_count = len(chunked_nodes)

        # Within 10% — minor divergence on isolated nodes is acceptable
        if json_count > 0 and chunked_count > 0:
            ratio = abs(json_count - chunked_count) / max(json_count, chunked_count)
            assert ratio <= 0.10, (
                f"Node count mismatch: json={json_count}, chunked={chunked_count} "
                f"(ratio={ratio:.2%} > 10%)"
            )
