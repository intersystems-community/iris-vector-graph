"""Spec 163 NFR-009 — 4-way Leiden benchmark.

Compares 4 implementations of community detection on the same fixture:

| Engine                 | Algorithm     | Notes                                  |
|------------------------|---------------|----------------------------------------|
| IVG                    | Leiden (CPM)  | engine.leiden_communities, gamma param |
| networkx Louvain       | Louvain       | nx.community.louvain_communities       |
| cdlib/igraph Leiden    | Leiden (CPM)  | leidenalg.find_partition direct        |
| Neo4j GDS              | Leiden (Mod)  | gds.leiden.stream native modularity    |

IVG runs through the arno Rust kernel (libarno_callout `kg_leiden_run` backed by
the `leiden-rs` crate) when libarno is deployed and `IVG_DISABLE_ARNO` is unset;
otherwise falls back to LazyKG path (Python `leidenalg`). Both produce ARI=1.0
with leidenalg reference (TestArnoVsLazyKG cross-check enforces ARI > 0.9).

Each engine runs in its natural mode rather than forcing a common gamma —
modularity-based engines (Louvain, GDS) and CPM-based engines (IVG, igraph
direct) report different resolution sensitivities by design.

Metrics captured per implementation:
- Wall-clock time (mean + stddev over 5 runs)
- Modularity score (Q) on resulting partition
- Number of communities
- Pairwise ARI between every engine pair

Fixtures:
- Karate club (34 nodes, 78 edges) — correctness sanity
- Erdős-Rényi G(500, 0.02) — small perf
- Erdős-Rényi G(2000, 0.005) — scaling perf

Output: prints results to stdout AND writes JSON to
benchmarks/leiden_4way_<timestamp>.json for CI tracking.

Skip behavior:
- networkx is always available (test dep)
- igraph+leidenalg required for IVG LazyKG path (runtime dep)
- libarno_callout.so optional — auto-detected, falls back to LazyKG
- Neo4j GDS gated on env var IVG_GDS_BENCHMARK_NEO4J_URI; skips if not set

This is opt-in: pytest -m perf to run. Default skipped to keep CI fast.
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


def _build_erdos_renyi(n: int, p: float, seed: int = 42) -> Tuple[List[str], List[Tuple[str, str]]]:
    import networkx as nx
    G = nx.erdos_renyi_graph(n, p, seed=seed, directed=False)
    nodes = [f"er{n}_{v}" for v in G.nodes()]
    edges = [(f"er{n}_{u}", f"er{n}_{v}") for u, v in G.edges()]
    return nodes, edges


def _modularity_nx(node_to_label: Dict[str, int], edges: List[Tuple[str, str]]) -> float:
    import networkx as nx
    G = nx.Graph()
    for u, v in edges:
        G.add_edge(u, v)
    communities: Dict[int, set] = {}
    for n, c in node_to_label.items():
        communities.setdefault(c, set()).add(n)
    return nx.community.modularity(G, list(communities.values()))


def _ari(labels_a: Dict[str, int], labels_b: Dict[str, int]) -> Optional[float]:
    try:
        from sklearn.metrics import adjusted_rand_score
    except ImportError:
        return None
    common = sorted(set(labels_a) & set(labels_b))
    if len(common) < 2:
        return None
    a = [labels_a[k] for k in common]
    b = [labels_b[k] for k in common]
    return float(adjusted_rand_score(a, b))


def _bench_ivg(
    iris_connection,
    nodes: List[str],
    edges: List[Tuple[str, str]],
    *,
    gamma: float,
    seed: int,
    n_runs: int,
) -> Dict[str, Any]:
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.schema import _call_classmethod

    engine = IRISGraphEngine(iris_connection)
    prefix = f"bench_{uuid.uuid4().hex[:8]}_"
    for n in nodes:
        engine.create_node(prefix + n)
    for u, v in edges:
        engine.create_edge(prefix + u, "EDGE", prefix + v)
    iris_connection.commit()
    _call_classmethod(iris_connection, "Graph.KG.Traversal", "BuildKG")

    times: List[float] = []
    last_labels: Dict[str, int] = {}
    for _ in range(n_runs):
        t0 = time.perf_counter()
        result = engine.leiden_communities(random_seed=seed, top_k=0, gamma=gamma)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        last_labels = {
            r["id"][len(prefix):]: r["community"]
            for r in result
            if "_approximate" not in r and r["id"].startswith(prefix)
        }

    kernel_times: List[float] = []
    try:
        from iris_vector_graph.stores.arno_bridge import (
            arno_available, build_kg_adjacency_chunked, arno_call,
        )
        if arno_available(iris_connection):
            for _ in range(n_runs):
                build_kg_adjacency_chunked(iris_connection)
                t0 = time.perf_counter()
                arno_call(iris_connection, "kg_leiden_run",
                          10, gamma, 1e-4, 0, 256,
                          -1 if seed is None else int(seed))
                kernel_times.append(time.perf_counter() - t0)
    except Exception:
        pass

    return {
        "label": "IVG (arno+leiden-rs)" if kernel_times else "IVG (LazyKG+leidenalg)",
        "mean_s": statistics.mean(times),
        "stdev_s": statistics.stdev(times) if len(times) > 1 else 0.0,
        "kernel_mean_s": (statistics.mean(kernel_times) if kernel_times else None),
        "n_communities": len(set(last_labels.values())),
        "labels": last_labels,
    }


def _bench_networkx_louvain(
    nodes: List[str],
    edges: List[Tuple[str, str]],
    *,
    seed: int,
    n_runs: int,
) -> Dict[str, Any]:
    import networkx as nx

    G = nx.Graph()
    for n in nodes:
        G.add_node(n)
    for u, v in edges:
        G.add_edge(u, v)

    times: List[float] = []
    last_labels: Dict[str, int] = {}
    for _ in range(n_runs):
        t0 = time.perf_counter()
        communities = nx.community.louvain_communities(G, seed=seed, threshold=1e-7)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        last_labels = {n: cid for cid, comm in enumerate(communities) for n in comm}

    return {
        "label": "networkx Louvain",
        "mean_s": statistics.mean(times),
        "stdev_s": statistics.stdev(times) if len(times) > 1 else 0.0,
        "n_communities": len(set(last_labels.values())),
        "labels": last_labels,
    }


def _bench_igraph_leiden(
    nodes: List[str],
    edges: List[Tuple[str, str]],
    *,
    gamma: float,
    seed: int,
    n_runs: int,
) -> Dict[str, Any]:
    import igraph as ig
    import leidenalg as la

    sorted_nodes = sorted(nodes)
    idx = {n: i for i, n in enumerate(sorted_nodes)}
    edge_pairs = [(idx[u], idx[v]) for u, v in edges]
    G = ig.Graph(n=len(sorted_nodes), edges=edge_pairs, directed=False)

    times: List[float] = []
    last_labels: Dict[str, int] = {}
    for _ in range(n_runs):
        t0 = time.perf_counter()
        if abs(gamma - 1.0) < 1e-9:
            partition = la.find_partition(G, la.ModularityVertexPartition, seed=seed, n_iterations=10)
        else:
            partition = la.find_partition(
                G, la.CPMVertexPartition, resolution_parameter=gamma, seed=seed, n_iterations=10
            )
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        last_labels = {
            sorted_nodes[i]: cid
            for cid, comm in enumerate(partition)
            for i in comm
        }

    return {
        "label": "cdlib/igraph Leiden",
        "mean_s": statistics.mean(times),
        "stdev_s": statistics.stdev(times) if len(times) > 1 else 0.0,
        "n_communities": len(set(last_labels.values())),
        "labels": last_labels,
    }


def _bench_neo4j_gds(
    nodes: List[str],
    edges: List[Tuple[str, str]],
    *,
    gamma: float,
    seed: int,
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

    graph_name = f"ivg_bench_{uuid.uuid4().hex[:8]}"
    try:
        gds.run_cypher("MATCH (n:IvgBench) DETACH DELETE n")
        for n in nodes:
            gds.run_cypher("CREATE (:IvgBench {id: $id})", {"id": n})
        for u, v in edges:
            gds.run_cypher(
                "MATCH (a:IvgBench {id: $u}), (b:IvgBench {id: $v}) CREATE (a)-[:EDGE]->(b)",
                {"u": u, "v": v},
            )
        G_proj, _ = gds.graph.project(
            graph_name,
            "IvgBench",
            {"EDGE": {"orientation": "UNDIRECTED"}},
        )
        times: List[float] = []
        last_labels: Dict[str, int] = {}
        for _ in range(n_runs):
            t0 = time.perf_counter()
            df = gds.leiden.stream(G_proj, randomSeed=seed)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            id_lookup = gds.run_cypher(
                "MATCH (n:IvgBench) RETURN id(n) AS nid, n.id AS id"
            )
            nid_to_id = {int(r["nid"]): str(r["id"]) for _, r in id_lookup.iterrows()}
            last_labels = {
                nid_to_id[int(row["nodeId"])]: int(row["communityId"])
                for _, row in df.iterrows()
                if int(row["nodeId"]) in nid_to_id
            }
        G_proj.drop()
        gds.run_cypher("MATCH (n:IvgBench) DETACH DELETE n")
        gds.close()
        return {
            "label": "Neo4j GDS Leiden",
            "mean_s": statistics.mean(times),
            "stdev_s": statistics.stdev(times) if len(times) > 1 else 0.0,
            "n_communities": len(set(last_labels.values())),
            "labels": last_labels,
        }
    except Exception as e:
        try:
            gds.close()
        except Exception:
            pass
        return {"label": "Neo4j GDS", "error": str(e)[:200]}


def _summarize(
    fixture_name: str,
    edges: List[Tuple[str, str]],
    benches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"fixture": fixture_name, "engines": []}
    for b in benches:
        if b is None:
            continue
        if "skipped" in b or "error" in b:
            summary["engines"].append({k: v for k, v in b.items() if k != "labels"})
            continue
        try:
            mod = _modularity_nx(b["labels"], edges)
        except Exception as e:
            mod = None
        entry = {
            "label": b["label"],
            "mean_s": round(b["mean_s"], 4),
            "stdev_s": round(b["stdev_s"], 4),
            "n_communities": b["n_communities"],
            "modularity": round(mod, 4) if mod is not None else None,
        }
        kernel = b.get("kernel_mean_s")
        if kernel is not None:
            entry["kernel_only_s"] = round(kernel, 4)
        summary["engines"].append(entry)

    valid = [b for b in benches if b is not None and "labels" in b]
    pairwise: Dict[str, Optional[float]] = {}
    for i, b1 in enumerate(valid):
        for b2 in valid[i + 1:]:
            key = f"{b1['label']} vs {b2['label']}"
            pairwise[key] = _ari(b1["labels"], b2["labels"])
    summary["pairwise_ari"] = {
        k: (round(v, 3) if v is not None else None) for k, v in pairwise.items()
    }
    return summary


def _print_summary(summary: Dict[str, Any]) -> None:
    print(f"\n{'='*82}")
    print(f"FIXTURE: {summary['fixture']}")
    print(f"{'='*82}")
    print(f"{'Engine':<28} {'Total (s)':<14} {'Kernel (s)':<12} {'#Comms':<8} {'Modularity':<10}")
    print("-" * 82)
    for e in summary["engines"]:
        if "skipped" in e:
            print(f"{e['label']:<28} SKIPPED ({e['skipped']})")
            continue
        if "error" in e:
            print(f"{e['label']:<28} ERROR ({e['error']})")
            continue
        time_s = f"{e['mean_s']:.3f}±{e['stdev_s']:.3f}"
        kernel_s = f"{e['kernel_only_s']:.3f}" if e.get("kernel_only_s") is not None else "—"
        mod = f"{e['modularity']:.3f}" if e["modularity"] is not None else "n/a"
        print(f"{e['label']:<28} {time_s:<14} {kernel_s:<12} {e['n_communities']:<8} {mod:<10}")
    if summary["pairwise_ari"]:
        print(f"\nPairwise ARI:")
        for pair, ari in summary["pairwise_ari"].items():
            print(f"  {pair:<55} {ari if ari is not None else 'n/a'}")


def _save_json(all_summaries: List[Dict[str, Any]]) -> Path:
    out_dir = Path(__file__).parent.parent.parent / "benchmarks"
    out_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"leiden_4way_{ts}.json"
    with out_path.open("w") as f:
        json.dump(all_summaries, f, indent=2, default=str)
    return out_path


@pytest.mark.parametrize(
    "fixture_name,fixture_fn,gamma",
    [
        ("karate_club", _build_karate, 1.0),
        ("erdos_renyi_n500_p0.02", lambda: _build_erdos_renyi(500, 0.02, seed=42), 1.0),
        ("erdos_renyi_n2000_p0.005", lambda: _build_erdos_renyi(2000, 0.005, seed=42), 1.0),
    ],
)
def test_leiden_four_way_benchmark(iris_connection, iris_master_cleanup, fixture_name, fixture_fn, gamma):
    nodes, edges = fixture_fn()
    print(f"\nFixture {fixture_name}: {len(nodes)} nodes, {len(edges)} edges, gamma={gamma}")

    n_runs = 3 if len(nodes) < 1000 else 1
    benches = []

    benches.append(_bench_ivg(iris_connection, nodes, edges, gamma=gamma, seed=42, n_runs=n_runs))
    benches.append(_bench_networkx_louvain(nodes, edges, seed=42, n_runs=n_runs))
    benches.append(_bench_igraph_leiden(nodes, edges, gamma=gamma, seed=42, n_runs=n_runs))
    gds_result = _bench_neo4j_gds(nodes, edges, gamma=gamma, seed=42, n_runs=n_runs)
    if gds_result is not None:
        benches.append(gds_result)

    summary = _summarize(fixture_name, edges, benches)
    _print_summary(summary)

    if not hasattr(test_leiden_four_way_benchmark, "_results"):
        test_leiden_four_way_benchmark._results = []
    test_leiden_four_way_benchmark._results.append(summary)

    if fixture_name == "erdos_renyi_n2000_p0.005":
        out_path = _save_json(test_leiden_four_way_benchmark._results)
        print(f"\nResults saved to: {out_path}")

    valid_engines = [e for e in summary["engines"] if "skipped" not in e and "error" not in e]
    assert len(valid_engines) >= 3, "Expected at least IVG + networkx + igraph to run"
