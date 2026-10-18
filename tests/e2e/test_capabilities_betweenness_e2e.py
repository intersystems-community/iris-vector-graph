"""Live proof for two fixes on a deployed namespace.

1. A fresh `IRISGraphEngine(conn)` detects the deployed Graph.KG classes on first use,
   so `bulk_ingest_edges` goes through `Graph.KG.EdgeScan.BulkIngestEdgesSQL` without
   `initialize_schema()`; `bulk_create_nodes` stays on the Python path.
2. `betweenness_centrality(sample_size >= N)` is exact Brandes, matching networkx on a
   graph larger than the 200-source default.

Run against one scratch namespace, e.g.
`IVG_NAMESPACE=TCKJ IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972`.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _spy_large(monkeypatch):
    import iris_vector_graph.schema as schema_mod

    calls = []
    real = schema_mod._call_classmethod_large

    def spy(iris_obj, cls, method, *args):
        calls.append(method)
        return real(iris_obj, cls, method, *args)

    monkeypatch.setattr(schema_mod, "_call_classmethod_large", spy)
    return calls


def _rdf_edge_count(conn, prefix):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ?", [prefix])
    return int(cur.fetchone()[0])


class TestFreshEngineCapabilities:
    def test_fresh_engine_bulk_ingest_edges_uses_server_loader(
        self, iris_connection, iris_master_cleanup, monkeypatch
    ):
        engine = IRISGraphEngine(iris_connection)
        if not engine.capabilities.objectscript_deployed:
            pytest.skip("Graph.KG classes not deployed in this namespace")
        engine = IRISGraphEngine(iris_connection)  # fresh again: nothing detected yet
        assert engine._capabilities is None
        calls = _spy_large(monkeypatch)
        prefix = f"cap_{uuid.uuid4().hex[:8]}_"
        edges = [{"s": f"{prefix}{i}", "p": "R", "o": f"{prefix}{i + 1}"} for i in range(300)]

        n = engine.bulk_ingest_edges(edges, predicate="R", auto_sync=False)

        assert "BulkIngestEdgesSQL" in calls
        assert n == 300
        assert _rdf_edge_count(iris_connection, prefix) == 300

    def test_fresh_engine_bulk_create_nodes_keeps_python_path(
        self, iris_connection, iris_master_cleanup, monkeypatch
    ):
        engine = IRISGraphEngine(iris_connection)
        calls = _spy_large(monkeypatch)
        prefix = f"capn_{uuid.uuid4().hex[:8]}_"
        created = engine.bulk_create_nodes([{"id": f"{prefix}{i}", "labels": ["L"]} for i in range(20)])

        assert "BulkIngestNodesSQL" not in calls
        assert len(created) == 20


class TestExactBetweenness:
    def test_sample_size_at_node_count_matches_networkx(self, iris_connection, iris_master_cleanup):
        nx = pytest.importorskip("networkx")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"bcx_{uuid.uuid4().hex[:8]}_"
        g = nx.gnp_random_graph(260, 0.015, seed=236, directed=True)
        edges = [{"s": f"{prefix}{u}", "p": "R", "o": f"{prefix}{v}"} for u, v in g.edges()]
        engine.bulk_ingest_edges(edges, predicate="R", auto_sync=False)
        engine.sync()

        cur = iris_connection.cursor()
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
        total = int(cur.fetchone()[0])
        assert total > 200  # above the 200-source default, so a cap would show

        result = engine.betweenness_centrality(sample_size=total, direction="out", top_k=0)

        G = nx.DiGraph()
        G.add_edges_from((e["s"], e["o"]) for e in edges)
        exact = nx.betweenness_centrality(G, normalized=False)
        got = {r["id"]: r["score"] for r in result if "id" in r}
        diffs = [abs(exact[k] - got.get(k, 0.0)) for k in exact]
        assert max(diffs) < 1e-6, f"max |ivg - networkx| = {max(diffs)}"
