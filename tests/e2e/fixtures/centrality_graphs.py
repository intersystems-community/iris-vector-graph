"""Centrality graph fixtures (Spec 162).

Each builder returns dict with keys:
- nodes: list[str]
- edges: list[tuple[str, str, str]]   # (subject, predicate, object)
- nx_graph: networkx.DiGraph (when networkx available; otherwise None)
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _try_import_networkx():
    try:
        import networkx as nx
        return nx
    except ImportError:
        return None


def make_erdos_renyi_graph(
    n: int = 100, p: float = 0.1, seed: int = 42, directed: bool = True
) -> Dict[str, Any]:
    """Erdős-Rényi random graph for general centrality parity testing."""
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for centrality fixtures (pip install networkx)")
    G = nx.erdos_renyi_graph(n, p, seed=seed, directed=directed)
    nodes = [f"n{i}" for i in G.nodes()]
    edges = [(f"n{u}", "EDGE", f"n{v}") for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def make_disconnected_graph() -> Dict[str, Any]:
    """3 disconnected directed cliques (sizes 5, 4, 3) for Closeness divergence test.

    Harmonic Closeness gives nonzero scores per-component;
    Classical Closeness returns 0 for all (cannot reach all nodes).
    """
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for centrality fixtures")
    G = nx.DiGraph()
    next_id = 0
    for size in (5, 4, 3):
        component_ids = [f"n{next_id + i}" for i in range(size)]
        next_id += size
        for u in component_ids:
            G.add_node(u)
        for u in component_ids:
            for v in component_ids:
                if u != v:
                    G.add_edge(u, v)
    nodes = list(G.nodes())
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def make_directed_cycle(n: int = 5) -> Dict[str, Any]:
    """Directed cycle a→b→c→...→a — Eigenvector should give uniform scores."""
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for centrality fixtures")
    G = nx.DiGraph()
    nodes = [f"n{i}" for i in range(n)]
    for i in range(n):
        G.add_edge(nodes[i], nodes[(i + 1) % n])
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def make_high_fanout_graph(hub_degree: int = 5000) -> Dict[str, Any]:
    """Single hub node with many spokes — triggers Brandes memBudget skip on tight budgets.

    The hub has hub_degree successors; each successor has back-edge to hub.
    This is the worst case for Brandes' predecessor accumulation per source.
    """
    hub = "hub"
    spokes = [f"s{i}" for i in range(hub_degree)]
    nodes = [hub] + spokes
    edges = []
    for s in spokes:
        edges.append((hub, "EDGE", s))
        edges.append((s, "EDGE", hub))

    nx = _try_import_networkx()
    nx_graph = None
    if nx is not None:
        nx_graph = nx.DiGraph()
        for n in nodes:
            nx_graph.add_node(n)
        for u, _, v in edges:
            nx_graph.add_edge(u, v)
    return {"nodes": nodes, "edges": edges, "nx_graph": nx_graph}


def load_into_engine(fixture: Dict[str, Any], engine) -> None:
    """Load a fixture into an IRISGraphEngine — convenience helper."""
    for nid in fixture["nodes"]:
        engine.create_node(nid)
    for s, p, o in fixture["edges"]:
        engine.create_edge(s, p, o)
