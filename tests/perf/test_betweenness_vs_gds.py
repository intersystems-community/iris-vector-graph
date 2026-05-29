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
    from iris_vector_graph.schema import _call_classmethod
    for n in nodes:
        engine.create_node(n)
    for u, v in edges:
        engine.create_edge(u, "EDGE", v)
        engine.create_edge(v, "EDGE", u)
    _call_classmethod(engine.conn, "Graph.KG.Traversal", "BuildKG")


def _bench_ivg(
    engine,
    nodes: List[str],
    edges: List[Tuple[str, str]],
    *,
    n_runs: int,
) -> Dict[str, Any]:
    _load_into_ivg(engine, nodes, edges)
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
            df = gds.betweenness.stream(G_proj)
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
            "label": "Neo4j GDS betweenness",
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
    gds_result = _bench_neo4j_gds(nodes, edges, n_runs=n_runs)

    summary: Dict[str, Any] = {
        "fixture": fixture_name,
        "nodes": len(nodes),
        "edges": len(edges),
        "engines": [],
    }

    for r in [ivg_result, gds_result]:
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
    gds_scores = gds_result.get("scores", {}) if gds_result else {}

    if ivg_scores and gds_scores:
        pearson = _pearson(ivg_scores, gds_scores)
        print(f"\n  Pearson(IVG, GDS): {pearson:.4f}")
        summary["pearson_ivg_gds"] = round(pearson, 4)
        assert pearson > 0.85, (
            f"Pearson correlation {pearson:.4f} < 0.85 on {fixture_name}"
        )

    ivg_ms = ivg_result["mean_s"] * 1000
    gds_ms = (gds_result or {}).get("mean_s", 0) * 1000
    if gds_ms > 0:
        ratio = gds_ms / ivg_ms
        print(f"  Speedup IVG vs GDS: {ratio:.1f}×")
        summary["speedup_ivg_vs_gds"] = round(ratio, 2)

    return summary


class TestBetweennessVsGDS:
    def test_betweenness_karate(self, iris_connection, iris_master_cleanup):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        nodes, edges = _build_karate()
        _run_fixture("karate_club", nodes, edges, engine, n_runs=3)

    def test_betweenness_er500(self, iris_connection, iris_master_cleanup):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        nodes, edges = _build_er(500, 0.01, seed=42)
        _run_fixture("er_500", nodes, edges, engine, n_runs=3)

    def test_betweenness_er2000(self, iris_connection, iris_master_cleanup):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        nodes, edges = _build_er(2000, 0.003, seed=42)
        _run_fixture("er_2000", nodes, edges, engine, n_runs=3)

    def test_betweenness_full_suite(self, iris_connection, iris_master_cleanup):
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
