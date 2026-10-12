"""networkx and igraph references for the algorithm legs.

These are what tests/perf/test_head_to_head.py (networkx), test_leiden_four_way.py (networkx Louvain, igraph
leidenalg) and test_betweenness_vs_gds.py already use as the reference when a database has no procedure for the
algorithm. They also stand in for Neo4j GDS or Memgraph MAGE when an engine lacks one.

The graph is built once per engine (its time is the `build` record), then each algorithm is timed alone.
"""

from __future__ import annotations

import statistics
from typing import Any, Callable, Dict, List, Optional, Tuple

from datasets import Graph

ALGOS = [
    "pagerank",
    "degree_out",
    "degree_both",
    "wcc",
    "scc",
    "betweenness_exact",
    "betweenness_sampled200",
    "closeness_harmonic",
    "louvain",
    "leiden",
    "triangle_count",
]

# Exact algorithms are O(n*m); a reference that would run for hours is skipped, and the skip is recorded.
DEFAULT_EXACT_MAX_NODES = {"networkx": 3_000, "igraph": 20_000}
BETWEENNESS_SAMPLE = 200  # test_head_to_head.py: exact up to 600 nodes, k=200 sample above


def pearson(a: Dict[str, float], b: Dict[str, float]) -> float:
    keys = sorted(set(a) & set(b))
    if len(keys) < 2:
        return 1.0
    xa = [a[k] for k in keys]
    xb = [b[k] for k in keys]
    ma, mb = statistics.mean(xa), statistics.mean(xb)
    num = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    da = sum((x - ma) ** 2 for x in xa) ** 0.5
    db = sum((y - mb) ** 2 for y in xb) ** 0.5
    return num / (da * db) if da > 0 and db > 0 else 0.0


class Reference:
    def __init__(self, graph: Graph, impl: str, seed: int = 42, exact_max_nodes: Optional[int] = None):
        if impl not in ("networkx", "igraph"):
            raise ValueError(impl)
        self.graph, self.impl, self.seed = graph, impl, seed
        self.exact_max_nodes = exact_max_nodes if exact_max_nodes is not None else DEFAULT_EXACT_MAX_NODES[impl]
        self.nodes = list(graph.nodes)
        self._index = {n: i for i, n in enumerate(self.nodes)}
        if impl == "networkx":
            self._build_nx()
        else:
            self._build_ig()

    # -- construction -------------------------------------------------------

    def _build_nx(self) -> None:
        import networkx as nx

        d = nx.DiGraph() if self.graph.directed_source else nx.Graph()
        d.add_nodes_from(self.nodes)
        d.add_edges_from(self.graph.edges)
        self.D = d
        u = nx.Graph()
        u.add_nodes_from(self.nodes)
        u.add_edges_from((a, b) for a, b in self.graph.edges if a != b)
        self.U = u

    def _build_ig(self) -> None:
        import igraph as ig

        pairs = [(self._index[a], self._index[b]) for a, b in self.graph.edges]
        n = len(self.nodes)
        self.D = ig.Graph(n=n, edges=pairs, directed=self.graph.directed_source)
        self.U = ig.Graph(n=n, edges=pairs, directed=False)
        self.U.simplify()

    # -- legs ---------------------------------------------------------------

    def _named(self, values: List[float]) -> Dict[str, float]:
        return {self.nodes[i]: float(v) for i, v in enumerate(values)}

    def legs(self) -> Dict[str, Tuple[Optional[Callable[[], Dict[str, Any]]], Dict[str, Any]]]:
        """algo -> (callable returning a result dict, params). The callable is None when the leg is skipped; params
        then carries `skipped`."""
        fn = self._nx_legs() if self.impl == "networkx" else self._ig_legs()
        out = {}
        n = len(self.nodes)
        for algo in ALGOS:
            entry = fn.get(algo)
            if entry is None:
                out[algo] = (None, {"skipped": f"{self.impl} has no {algo}"})
                continue
            call, params = entry
            if algo in ("betweenness_exact", "closeness_harmonic") and n > self.exact_max_nodes:
                out[algo] = (None, {"skipped": f"exact {algo} on {n} nodes exceeds --ref-max-nodes {self.exact_max_nodes}"})
                continue
            if algo == "betweenness_sampled200" and n <= BETWEENNESS_SAMPLE:
                out[algo] = (None, {"skipped": f"graph has {n} nodes, no more than the sample size"})
                continue
            out[algo] = (call, params)
        return out

    def _nx_legs(self):
        import networkx as nx

        D, U, seed = self.D, self.U, self.seed
        directed = self.graph.directed_source
        legs: Dict[str, Any] = {
            "pagerank": (
                lambda: {"scores": nx.pagerank(D, alpha=0.85, max_iter=100, tol=1e-6)},
                {"implementation": "networkx.pagerank", "damping": 0.85, "max_iter": 100, "tol": 1e-6},
            ),
            "degree_out": (
                lambda: {"scores": dict(D.out_degree() if directed else D.degree())},
                {"implementation": "networkx.out_degree"},
            ),
            "degree_both": (
                lambda: {"scores": dict(D.degree())},
                {"implementation": "networkx.degree"},
            ),
            "wcc": (
                lambda: {
                    "components": nx.number_weakly_connected_components(D)
                    if directed
                    else nx.number_connected_components(D)
                },
                {"implementation": "networkx.weakly_connected_components"},
            ),
            "betweenness_exact": (
                lambda: {"scores": nx.betweenness_centrality(U, k=None, normalized=False)},
                {"implementation": "networkx.betweenness_centrality", "sampled": False},
            ),
            "betweenness_sampled200": (
                lambda: {"scores": nx.betweenness_centrality(U, k=BETWEENNESS_SAMPLE, normalized=False, seed=seed)},
                {
                    "implementation": "networkx.betweenness_centrality",
                    "sampled": True,
                    "sample_size": BETWEENNESS_SAMPLE,
                    "seed": seed,
                },
            ),
            "closeness_harmonic": (
                lambda: {"scores": nx.harmonic_centrality(U)},
                {"implementation": "networkx.harmonic_centrality"},
            ),
            "triangle_count": (
                lambda: {"triangles": sum(nx.triangles(U).values()) // 3},
                {"implementation": "networkx.triangles"},
            ),
            "louvain": (
                lambda: {
                    "communities": len(nx.community.louvain_communities(U, seed=seed, threshold=1e-7))
                },
                {"implementation": "networkx.louvain_communities", "seed": seed, "threshold": 1e-7},
            ),
        }
        if directed:
            legs["scc"] = (
                lambda: {"components": nx.number_strongly_connected_components(D)},
                {"implementation": "networkx.strongly_connected_components"},
            )
        return legs

    def _ig_legs(self):
        import igraph as ig  # noqa: F401
        import leidenalg as la

        D, U, seed = self.D, self.U, self.seed
        directed = self.graph.directed_source
        legs: Dict[str, Any] = {
            "pagerank": (
                lambda: {"scores": self._named(D.pagerank(directed=directed, damping=0.85))},
                {"implementation": "igraph.pagerank(PRPACK)", "damping": 0.85},
            ),
            "degree_out": (
                lambda: {"scores": self._named(D.outdegree() if directed else D.degree())},
                {"implementation": "igraph.outdegree"},
            ),
            "degree_both": (
                lambda: {"scores": self._named(D.degree(mode="all"))},
                {"implementation": "igraph.degree(all)"},
            ),
            "wcc": (
                lambda: {"components": len(D.connected_components(mode="weak" if directed else "strong"))},
                {"implementation": "igraph.connected_components(weak)"},
            ),
            "betweenness_exact": (
                lambda: {"scores": self._named(U.betweenness(directed=False))},
                {"implementation": "igraph.betweenness(exact)", "sampled": False},
            ),
            "closeness_harmonic": (
                lambda: {"scores": self._named(U.harmonic_centrality())},
                {"implementation": "igraph.harmonic_centrality"},
            ),
            "triangle_count": (
                lambda: {"triangles": int(U.motifs_randesu(size=3)[3])},
                {"implementation": "igraph.motifs_randesu(size=3)"},
            ),
            "louvain": (
                lambda: {"communities": len(U.community_multilevel())},
                {"implementation": "igraph.community_multilevel"},
            ),
            "leiden": (
                lambda: {
                    "communities": len(
                        la.find_partition(U, la.ModularityVertexPartition, seed=seed, n_iterations=10)
                    )
                },
                {"implementation": "leidenalg.ModularityVertexPartition", "seed": seed, "n_iterations": 10},
            ),
        }
        if directed:
            legs["scc"] = (
                lambda: {"components": len(D.connected_components(mode="strong"))},
                {"implementation": "igraph.connected_components(strong)"},
            )
        return legs


def run_reference(
    graph: Graph, algo: str, impl: str, seed: int = 42, return_scores: bool = False
) -> Dict[str, Any]:
    ref = Reference(graph, impl, seed=seed, exact_max_nodes=10**9)
    call, params = ref.legs()[algo]
    if call is None:
        raise ValueError(params["skipped"])
    res = call()
    out: Dict[str, Any] = {"implementation": params["implementation"]}
    if "scores" in res:
        if return_scores:
            out["scores"] = res["scores"]
    for key in ("communities", "components", "triangles"):
        if key in res:
            out[key] = res[key]
    return out
