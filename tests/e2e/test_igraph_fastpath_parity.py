"""Spec 191 Path A — igraph fast-path parity tests.

Verifies the embedded-Python igraph/leidenalg tiers (ClosenessJsonPy,
LeidenJsonAuto) produce results that match the reference implementations:
closeness vs networkx (harmonic + classical), Leiden vs leidenalg on karate.

Each test detects whether igraph is importable INSIDE IRIS embedded Python and
skips when it is not — so a Community-only / no-igraph environment never fails.
"""
from __future__ import annotations

import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _embedded_igraph_available(conn) -> bool:
    import iris as _iris
    iris_obj = _iris.createIRIS(conn)
    probe = str(iris_obj.classMethodValue("Graph.KG.Communities", "ClosenessJsonPy", "harmonic", 1))
    return not probe.startswith("PYUNAVAIL")


def _load_er(engine, n, p, seed, prefix):
    import networkx as nx
    G = nx.erdos_renyi_graph(n, p, seed=seed)
    for v in G.nodes():
        engine.create_node(f"{prefix}{v}")
    for u, v in G.edges():
        engine.create_edge(f"{prefix}{u}", "EDGE", f"{prefix}{v}")
        engine.create_edge(f"{prefix}{v}", "EDGE", f"{prefix}{u}")
    engine.conn.commit()
    engine.sync()
    return G


def _pearson(a, b):
    import statistics
    common = sorted(set(a) & set(b))
    xs = [a[k] for k in common]
    ys = [b[k] for k in common]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (sx * sy) if sx and sy else float("nan")


class TestIgraphClosenessParity:
    def test_harmonic_matches_networkx(self, iris_connection, iris_master_cleanup):
        nx = pytest.importorskip("networkx")
        if not _embedded_igraph_available(iris_connection):
            pytest.skip("igraph not installed in IRIS embedded Python")

        engine = IRISGraphEngine(iris_connection)
        prefix = f"p191c_{uuid.uuid4().hex[:8]}_"
        G = _load_er(engine, 300, 0.03, 42, prefix)

        r = engine.closeness_centrality(formula="harmonic")
        ivg = {row["id"]: row["score"] for row in r}
        ref = {f"{prefix}{k}": v for k, v in nx.harmonic_centrality(G).items()}

        assert _pearson(ivg, ref) >= 0.9999

    def test_classical_matches_networkx(self, iris_connection, iris_master_cleanup):
        nx = pytest.importorskip("networkx")
        if not _embedded_igraph_available(iris_connection):
            pytest.skip("igraph not installed in IRIS embedded Python")

        engine = IRISGraphEngine(iris_connection)
        prefix = f"p191cc_{uuid.uuid4().hex[:8]}_"
        # Connected graph: classical closeness is undefined on disconnected graphs.
        G = nx.connected_watts_strogatz_graph(200, 6, 0.1, seed=42)
        for v in G.nodes():
            engine.create_node(f"{prefix}{v}")
        for u, v in G.edges():
            engine.create_edge(f"{prefix}{u}", "EDGE", f"{prefix}{v}")
            engine.create_edge(f"{prefix}{v}", "EDGE", f"{prefix}{u}")
        engine.conn.commit()
        engine.sync()

        r = engine.closeness_centrality(formula="classical")
        ivg = {row["id"]: row["score"] for row in r}
        ref = {f"{prefix}{k}": v for k, v in nx.closeness_centrality(G).items()}

        assert _pearson(ivg, ref) >= 0.9999


class TestIgraphLeidenParity:
    def test_karate_ari_vs_leidenalg(self, iris_connection, iris_master_cleanup):
        nx = pytest.importorskip("networkx")
        pytest.importorskip("igraph")
        pytest.importorskip("leidenalg")
        if not _embedded_igraph_available(iris_connection):
            pytest.skip("igraph not installed in IRIS embedded Python")
        sklearn_metrics = pytest.importorskip("sklearn.metrics")

        engine = IRISGraphEngine(iris_connection)
        prefix = f"p191l_{uuid.uuid4().hex[:8]}_"
        G = nx.karate_club_graph()
        for v in G.nodes():
            engine.create_node(f"{prefix}{v}")
        for u, v in G.edges():
            engine.create_edge(f"{prefix}{u}", "EDGE", f"{prefix}{v}")
            engine.create_edge(f"{prefix}{v}", "EDGE", f"{prefix}{u}")
        engine.conn.commit()
        engine.sync()

        result = engine.leiden_communities(random_seed=42, top_k=0)
        ivg_comm = {row["id"]: row["community"] for row in result}

        import igraph as ig
        import leidenalg as la
        nodes = sorted(ivg_comm)
        idx = {nid: i for i, nid in enumerate(nodes)}
        edges = [(idx[f"{prefix}{u}"], idx[f"{prefix}{v}"]) for u, v in G.edges()]
        g = ig.Graph(n=len(nodes), edges=edges)
        part = la.find_partition(g, la.ModularityVertexPartition, seed=42)
        ref_labels = [0] * len(nodes)
        for cid, members in enumerate(part):
            for m in members:
                ref_labels[m] = cid

        ivg_labels = [ivg_comm[nid] for nid in nodes]
        ari = sklearn_metrics.adjusted_rand_score(ref_labels, ivg_labels)
        assert ari >= 0.95, f"Leiden ARI vs leidenalg reference = {ari:.3f}, expected >= 0.95"


class TestFallbackWhenIgraphAbsent:
    """T050 / FR-X1 — forcing the embedded-Python tier off must not raise; the
    ObjectScript / networkx fallback tiers must still return valid results."""

    def test_closeness_fallback_returns_valid(self, iris_connection, iris_master_cleanup, monkeypatch):
        nx = pytest.importorskip("networkx")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"p191fb_{uuid.uuid4().hex[:8]}_"
        G = _load_er(engine, 60, 0.08, 7, prefix)

        monkeypatch.setattr(engine._store, "_closeness_serverside", lambda *a, **k: None)

        r = engine.closeness_centrality(formula="harmonic")
        assert isinstance(r, list) and len(r) > 0
        ivg = {row["id"]: row["score"] for row in r}
        ref = {f"{prefix}{k}": v for k, v in nx.harmonic_centrality(G).items()}
        assert _pearson(ivg, ref) >= 0.9999

    def test_leiden_fallback_returns_valid(self, iris_connection, iris_master_cleanup, monkeypatch):
        pytest.importorskip("networkx")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"p191fl_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 60, 0.08, 7, prefix)

        monkeypatch.setattr(engine._store, "_leiden_serverside", lambda *a, **k: None)

        r = engine.leiden_communities(random_seed=42, top_k=0)
        assert isinstance(r, list) and len(r) > 0
        assert all("id" in row and "community" in row for row in r)
