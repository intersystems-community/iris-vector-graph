"""Spec 191 Path B — MSBFS closeness parity + speed regression.

ClosenessGlobalMSBFS (dependency-free Multi-Source BFS, wide bit-string frontiers)
must produce results identical to the sequential ClosenessGlobal oracle and to
networkx, across harmonic/classical formulas and out/in directions, and must be
substantially faster on a non-trivial graph.
"""
from __future__ import annotations

import json
import statistics
import time
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _parse(raw):
    raw = str(raw)
    if not raw.startswith("OK:"):
        return None
    return {r["id"]: r["score"] for r in json.loads(raw[3:])}


def _pearson(a, b):
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return float("nan")
    xs = [a[k] for k in common]
    ys = [b[k] for k in common]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (sx * sy) if sx and sy else float("nan")


def _load(engine, G, prefix, directed):
    cur = engine.conn.cursor()
    for t in ("Graph_KG.rdf_edges", "Graph_KG.rdf_labels", "Graph_KG.rdf_props", "Graph_KG.nodes"):
        try:
            cur.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    engine.conn.commit()
    for v in G.nodes():
        engine.create_node(f"{prefix}{v}")
    for u, v in G.edges():
        engine.create_edge(f"{prefix}{u}", "E", f"{prefix}{v}")
        if not directed:
            engine.create_edge(f"{prefix}{v}", "E", f"{prefix}{u}")
    engine.conn.commit()
    engine.sync()


class TestMSBFSClosenessParity:
    @pytest.mark.parametrize("formula", ["harmonic", "classical"])
    @pytest.mark.parametrize("direction", ["out", "in"])
    def test_msbfs_matches_sequential(self, iris_connection, iris_master_cleanup, formula, direction):
        import iris as _iris
        nx = pytest.importorskip("networkx")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"msbp_{uuid.uuid4().hex[:8]}_"
        G = nx.gnp_random_graph(80, 0.06, seed=11, directed=True)
        _load(engine, G, prefix, directed=True)

        iris_obj = _iris.createIRIS(iris_connection)
        seq = _parse(iris_obj.classMethodValue("Graph.KG.NKGAccel", "ClosenessGlobal", formula, direction, 0, 10000))
        msb = _parse(iris_obj.classMethodValue("Graph.KG.NKGAccel", "ClosenessGlobalMSBFS", formula, direction, 0, 10000))

        assert seq is not None and msb is not None
        assert _pearson(msb, seq) >= 0.99999, f"MSBFS diverges from sequential ({formula}/{direction})"

    def test_msbfs_matches_networkx_harmonic(self, iris_connection, iris_master_cleanup):
        import iris as _iris
        nx = pytest.importorskip("networkx")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"msbn_{uuid.uuid4().hex[:8]}_"
        G = nx.connected_watts_strogatz_graph(120, 6, 0.15, seed=3)
        _load(engine, G, prefix, directed=False)

        iris_obj = _iris.createIRIS(iris_connection)
        msb = _parse(iris_obj.classMethodValue("Graph.KG.NKGAccel", "ClosenessGlobalMSBFS", "harmonic", "out", 0, 10000))
        ref = {f"{prefix}{k}": v for k, v in nx.harmonic_centrality(G).items()}
        assert _pearson(msb, ref) >= 0.9999

    def test_msbfs_faster_than_sequential(self, iris_connection, iris_master_cleanup):
        import iris as _iris
        nx = pytest.importorskip("networkx")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"msbs_{uuid.uuid4().hex[:8]}_"
        G = nx.erdos_renyi_graph(500, 0.02, seed=42)
        _load(engine, G, prefix, directed=False)

        iris_obj = _iris.createIRIS(iris_connection)

        def _time(method):
            t0 = time.perf_counter()
            iris_obj.classMethodValue("Graph.KG.NKGAccel", method, "harmonic", "out", 0, 10000)
            return time.perf_counter() - t0

        seq = _time("ClosenessGlobal")
        msb = _time("ClosenessGlobalMSBFS")
        assert msb * 3 < seq, f"MSBFS ({msb*1000:.0f}ms) not >=3x faster than sequential ({seq*1000:.0f}ms)"
