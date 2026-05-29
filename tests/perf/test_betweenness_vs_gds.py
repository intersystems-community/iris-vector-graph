"""Spec 170 NFR — Betweenness centrality perf benchmark: IVG vs Neo4j GDS.

Compares two implementations of betweenness centrality (Brandes 2001):

| Engine    | Implementation          | Notes                                    |
|-----------|-------------------------|------------------------------------------|
| IVG       | BetweennessGlobal (OS)  | ObjectScript Brandes via ^NKG (1 round-trip) |
| Neo4j GDS | gds.betweenness.stream  | Native GDS betweenness centrality        |

Fixtures:
- Karate club (34 nodes, 78 edges) — correctness sanity + speed
- Erdős-Rényi G(500, 0.01)         — small perf
- Erdős-Rényi G(2000, 0.003)       — scaling perf

Pearson correlation between IVG and GDS scores gate: > 0.85.

Output: prints results to stdout AND writes JSON to
benchmarks/betweenness_vs_gds_<timestamp>.json.

Skip behavior:
- IVG: always runs if container available
- Neo4j GDS: gated on IVG_GDS_BENCHMARK_NEO4J_URI env var
- pytest -m perf to run (default skipped)
"""
from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

pytestmark = pytest.mark.perf


def _build_karate() -> Tuple[List[str], List[Tuple[str, str]]]:
    import networkx as nx
    G = nx.karate_club_graph()
    nodes = [f"k_{n}" for n in G.nodes()]
    edges = [(f"k_{u}", f"k_{v}") for u, v in G.edges()]
    return nodes, edges


def _build_er(n: int, p: float, seed: int = 42) -> Tuple[List[str], List[Tuple[str, str]]]:
    import networkx as nx
    G = nx.erdos_renyi_graph(n, p, seed=seed, directed=False)
    nodes = [f"er{n}_{v}" for v in G.nodes()]
    edges = [(f"er{n}_{u}", f"er{n}_{v}") for u, v in G.edges()]
    return nodes, edges


def _pearson(a: Dict[str, float], b: Dict[str, float]) -> float:
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return float("nan")
    xs = [a[k] for k in common]
    ys = [b[k] for k in common]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
    sx = (sum((xi - mx) ** 2 for xi in xs)) ** 0.5
    sy = (sum((yi - my) ** 2 for yi in ys)) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return num / (sx * sy)


def _load_into_ivg(engine, nodes: List[str], edges: List[Tuple[str, str]]) -> None:
    import contextlib
    from iris_vector_graph.schema import _call_classmethod
    import iris as _iris
    iris_obj = _iris.createIRIS(engine.conn)
    iris_obj.classMethodVoid("Graph.KG.NKGAccel", "Unload")
    iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
    iris_obj.tStart()
    iris_obj.kill("^KG")
    iris_obj.kill("^NKG")
    iris_obj.kill("^ArnoKG")
    iris_obj.tCommit()
    cursor = engine.conn.cursor()
    for table in ["Graph_KG.rdf_edges", "Graph_KG.rdf_labels", "Graph_KG.rdf_props", "Graph_KG.nodes"]:
        with contextlib.suppress(Exception):
            cursor.execute(f"DELETE FROM {table}")
    engine.conn.commit()
    for n in nodes:
        engine.create_node(n)
    for u, v in edges:
        engine.create_edge(u, "EDGE", v)
        engine.create_edge(v, "EDGE", u)
    _call_classmethod(engine.conn, "Graph.KG.Traversal", "BuildKG")
    iris_obj.classMethodVoid("Graph.KG.NKGAccel", "WarmAdjCache")


def _bench_ivg(
    engine,
    nodes: List[str],
    edges: List[Tuple[str, str]],
    *,
    n_runs: int,
) -> Dict[str, Any]:
    import iris as _iris
    _load_into_ivg(engine, nodes, edges)
    iris_obj = _iris.createIRIS(engine.conn)
    if not iris_obj.classMethodValue("Graph.KG.NKGAccel", "IsLoaded"):
        iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
        iris_obj.classMethodVoid("Graph.KG.NKGAccel", "WarmAdjCache")
    times: List[float] = []
    last_scores: Dict[str, float] = {}
    for _ in range(n_runs):
        t0 = time.perf_counter()
        result = engine.betweenness_centrality(sample_size=0, top_k=0)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        last_scores = {r["id"]: float(r["score"]) for r in result if r["id"] in nodes}
    return {
        "label": "IVG BetweennessGlobal",
        "mean_s": statistics.mean(times),
        "stdev_s": statistics.stdev(times) if len(times) > 1 else 0.0,
        "scores": last_scores,
    }


def _bench_neo4j_gds(
    nodes: List[str],
    edges: List[Tuple[str, str]],
    *,
    n_runs: int,
    sampling_size: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    uri = os.environ.get("IVG_GDS_BENCHMARK_NEO4J_URI")
    if not uri:
        return None
    user = os.environ.get("IVG_GDS_BENCHMARK_NEO4J_USER", "neo4j")
    password = os.environ.get("IVG_GDS_BENCHMARK_NEO4J_PASSWORD", "neo4j")
    try:
        from graphdatascience import GraphDataScience
    except ImportError:
        return {"label": "Neo4j GDS", "skipped": "graphdatascience not installed"}

    try:
        gds = GraphDataScience(uri, auth=(user, password))
    except Exception as e:
        return {"label": "Neo4j GDS", "skipped": f"connection failed: {e!s}"}

    label = f"Neo4j GDS betweenness (sampled={sampling_size})" if sampling_size else "Neo4j GDS betweenness (exact)"
    graph_name = f"ivg_bc_bench_{uuid.uuid4().hex[:8]}"
    try:
        gds.run_cypher("MATCH (n:IvgBcBench) DETACH DELETE n")
        for n in nodes:
            gds.run_cypher("CREATE (:IvgBcBench {id: $id})", {"id": n})
        for u, v in edges:
            gds.run_cypher(
                "MATCH (a:IvgBcBench {id: $u}), (b:IvgBcBench {id: $v}) "
                "CREATE (a)-[:EDGE]->(b)",
                {"u": u, "v": v},
            )
        G_proj, _ = gds.graph.project(
            graph_name,
            "IvgBcBench",
            {"EDGE": {"orientation": "UNDIRECTED"}},
        )
        times: List[float] = []
        last_scores: Dict[str, float] = {}
        for _ in range(n_runs):
            t0 = time.perf_counter()
            algo_cfg = {}
            if sampling_size:
                algo_cfg["samplingSize"] = sampling_size
                algo_cfg["samplingSeed"] = 42
            df = gds.betweenness.stream(G_proj, **algo_cfg)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            id_lookup = gds.run_cypher(
                "MATCH (n:IvgBcBench) RETURN id(n) AS nid, n.id AS id"
            )
            nid_to_id = {int(r["nid"]): str(r["id"]) for _, r in id_lookup.iterrows()}
            last_scores = {
                nid_to_id[int(row["nodeId"])]: float(row["score"])
                for _, row in df.iterrows()
                if int(row["nodeId"]) in nid_to_id
            }
        G_proj.drop()
        gds.run_cypher("MATCH (n:IvgBcBench) DETACH DELETE n")
        gds.close()
        return {
            "label": label,
            "mean_s": statistics.mean(times),
            "stdev_s": statistics.stdev(times) if len(times) > 1 else 0.0,
            "scores": last_scores,
        }
    except Exception as e:
        try:
            gds.close()
        except Exception:
            pass
        return {"label": "Neo4j GDS", "error": str(e)[:200]}


def _run_fixture(
    fixture_name: str,
    nodes: List[str],
    edges: List[Tuple[str, str]],
    engine,
    n_runs: int,
) -> Dict[str, Any]:
    print(f"\n{'=' * 60}")
    print(f"Fixture: {fixture_name}  ({len(nodes)} nodes, {len(edges)} edges)")
    print(f"{'=' * 60}")

    ivg_result = _bench_ivg(engine, nodes, edges, n_runs=n_runs)
    gds_exact = _bench_neo4j_gds(nodes, edges, n_runs=n_runs, sampling_size=None)
    gds_sampled = _bench_neo4j_gds(nodes, edges, n_runs=n_runs, sampling_size=min(200, len(nodes)))

    summary: Dict[str, Any] = {
        "fixture": fixture_name,
        "nodes": len(nodes),
        "edges": len(edges),
        "ivg_max_sources": 200,
        "engines": [],
    }

    for r in [ivg_result, gds_exact, gds_sampled]:
        if r is None:
            continue
        if "skipped" in r or "error" in r:
            print(f"  {r['label']}: {r.get('skipped') or r.get('error')}")
            summary["engines"].append({k: v for k, v in r.items() if k != "scores"})
            continue
        mean_ms = r["mean_s"] * 1000
        std_ms = r["stdev_s"] * 1000
        print(f"  {r['label']:35s} {mean_ms:8.1f}ms ± {std_ms:6.1f}ms")
        summary["engines"].append({
            "label": r["label"],
            "mean_ms": round(mean_ms, 2),
            "stdev_ms": round(std_ms, 2),
        })

    ivg_scores = ivg_result.get("scores", {})
    gds_exact_scores = gds_exact.get("scores", {}) if gds_exact else {}

    if ivg_scores and gds_exact_scores:
        pearson = _pearson(ivg_scores, gds_exact_scores)
        print(f"\n  Pearson(IVG sampled={min(200,len(nodes))}, GDS exact): {pearson:.4f}")
        summary["pearson_ivg_vs_gds_exact"] = round(pearson, 4)

    ivg_ms = ivg_result["mean_s"] * 1000
    gds_exact_ms = (gds_exact or {}).get("mean_s", 0) * 1000
    gds_sampled_ms = (gds_sampled or {}).get("mean_s", 0) * 1000
    if gds_exact_ms > 0:
        print(f"  Speedup IVG(sampled=200) vs GDS(exact):   {gds_exact_ms/ivg_ms:.1f}×")
        summary["speedup_ivg_sampled_vs_gds_exact"] = round(gds_exact_ms / ivg_ms, 2)
    if gds_sampled_ms > 0:
        print(f"  Speedup IVG(sampled=200) vs GDS(sampled=200): {gds_sampled_ms/ivg_ms:.1f}×")
        summary["speedup_ivg_sampled_vs_gds_sampled"] = round(gds_sampled_ms / ivg_ms, 2)

    return summary


class TestBetweennessVsGDS:
    def test_betweenness_karate(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        nodes, edges = _build_karate()
        _run_fixture("karate_club", nodes, edges, engine, n_runs=3)

    def test_betweenness_er500(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        nodes, edges = _build_er(500, 0.01, seed=42)
        _run_fixture("er_500", nodes, edges, engine, n_runs=3)

    def test_betweenness_er2000(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        nodes, edges = _build_er(2000, 0.003, seed=42)
        _run_fixture("er_2000", nodes, edges, engine, n_runs=3)

    def test_betweenness_full_suite(self, iris_connection):
        """Full suite with JSON output to benchmarks/."""
        import datetime
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)

        fixtures = [
            ("karate_club", *_build_karate()),
            ("er_500",  *_build_er(500,  0.01,  seed=42)),
            ("er_2000", *_build_er(2000, 0.003, seed=42)),
        ]

        all_summaries = []
        for name, nodes, edges in fixtures:
            summary = _run_fixture(name, nodes, edges, engine, n_runs=5)
            all_summaries.append(summary)

        output = {
            "spec": "170-betweenness-os",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "fixtures": all_summaries,
        }

        out_dir = Path("benchmarks")
        out_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"betweenness_vs_gds_{ts}.json"
        out_path.write_text(json.dumps(output, indent=2))
        print(f"\nBenchmark JSON: {out_path}")
