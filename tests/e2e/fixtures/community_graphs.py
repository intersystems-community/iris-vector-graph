"""Community detection graph fixtures (Spec 163).

Each builder returns dict with keys:
- nodes: list[str]
- edges: list[tuple[str, str, str]]   # (subject, predicate, object)
- nx_graph: networkx.DiGraph (when networkx available)
- ground_truth: dict[str, int] (only for karate_club_graph — community labels)
"""

from __future__ import annotations

from typing import Any, Dict, List


def _try_import_networkx():
    try:
        import networkx as nx
        return nx
    except ImportError:
        return None


def make_karate_club_graph() -> Dict[str, Any]:
    """Zachary's karate club — canonical community-detection test graph.

    34 nodes, 78 edges, 2 ground-truth communities (Mr. Hi's faction vs Officer's).
    Standard threshold: ARI > 0.85 for "good" community detection.
    """
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for community fixtures")
    G = nx.karate_club_graph()
    nodes = [f"karate_{n}" for n in G.nodes()]
    edges = [(f"karate_{u}", "KNOWS", f"karate_{v}") for u, v in G.edges()]
    ground_truth = {
        f"karate_{n}": (0 if G.nodes[n]["club"] == "Mr. Hi" else 1)
        for n in G.nodes()
    }
    return {
        "nodes": nodes,
        "edges": edges,
        "nx_graph": nx.DiGraph(G),
        "ground_truth": ground_truth,
    }


def make_three_cliques() -> Dict[str, Any]:
    """3 disconnected directed cliques (sizes 5, 4, 3) for Leiden disconnected test.

    Leiden on this fixture should produce exactly 3 communities, one per clique.
    """
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for community fixtures")
    G = nx.DiGraph()
    next_id = 0
    cliques: List[List[str]] = []
    for size in (5, 4, 3):
        component_ids = [f"n{next_id + i}" for i in range(size)]
        next_id += size
        cliques.append(component_ids)
        for u in component_ids:
            G.add_node(u)
        for u in component_ids:
            for v in component_ids:
                if u != v:
                    G.add_edge(u, v)
    nodes = list(G.nodes())
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    ground_truth: Dict[str, int] = {}
    for cid, members in enumerate(cliques):
        for m in members:
            ground_truth[m] = cid
    return {"nodes": nodes, "edges": edges, "nx_graph": G, "ground_truth": ground_truth}


def make_complete_graph(n: int = 5) -> Dict[str, Any]:
    """K_n complete graph — every node has triangles=C(n-1, 2), lcc=1.0, coreness=n-1."""
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for community fixtures")
    G = nx.DiGraph()
    nodes = [f"n{i}" for i in range(n)]
    for u in nodes:
        for v in nodes:
            if u != v:
                G.add_edge(u, v)
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def make_star_graph(n: int = 5) -> Dict[str, Any]:
    """Star graph: 1 center + n leaves, no leaf-leaf edges. Triangles=0, LCC=0 everywhere."""
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for community fixtures")
    G = nx.DiGraph()
    center = "center"
    leaves = [f"leaf_{i}" for i in range(n)]
    nodes = [center] + leaves
    for leaf in leaves:
        G.add_edge(center, leaf)
        G.add_edge(leaf, center)
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def make_directed_cycle(n: int = 5) -> Dict[str, Any]:
    """Directed cycle a→b→c→...→a — single SCC of size n."""
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for community fixtures")
    G = nx.DiGraph()
    nodes = [f"n{i}" for i in range(n)]
    for i in range(n):
        G.add_edge(nodes[i], nodes[(i + 1) % n])
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def make_path_graph(n: int = 5) -> Dict[str, Any]:
    """Path 1—2—3—...—n. Coreness=1 for all (no triangles, lowest degree is 1)."""
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for community fixtures")
    G = nx.DiGraph()
    nodes = [f"n{i}" for i in range(n)]
    for i in range(n - 1):
        G.add_edge(nodes[i], nodes[i + 1])
        G.add_edge(nodes[i + 1], nodes[i])
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def make_dag() -> Dict[str, Any]:
    """Simple DAG a→b→c (no cycle) — every node is its own SCC of size 1."""
    nx = _try_import_networkx()
    if nx is None:
        raise RuntimeError("networkx required for community fixtures")
    G = nx.DiGraph()
    G.add_edge("a", "b")
    G.add_edge("b", "c")
    nodes = ["a", "b", "c"]
    edges = [(u, "EDGE", v) for u, v in G.edges()]
    return {"nodes": nodes, "edges": edges, "nx_graph": G}


def load_into_engine(fixture: Dict[str, Any], engine, prefix: str = "") -> str:
    """Load a fixture into an IRISGraphEngine, prefixing all node IDs to avoid contamination.

    Calls `engine.build_graph_globals()` after SQL ingest to repair `^KG`
    adjacency. This is required because `create_edge`'s synchronous `^KG` write
    via `Graph.KG.EdgeScan.WriteAdjacency` fails with Bug S
    (`<CLASS DOES NOT EXIST> *Graph.KG.EdgeScan` from external Python). Without
    this, LazyKG (which reads `^KG`) sees an empty graph and Leiden/Triangle
    Count/SCC/K-Core all produce incorrect results.
    """
    for nid in fixture["nodes"]:
        engine.create_node(prefix + nid)
    for s, p, o in fixture["edges"]:
        engine.create_edge(prefix + s, p, prefix + o)
    # Bug S workaround: rebuild ^KG from rdf_edges via BuildKG ObjectScript routine
    # (BuildKG runs inside IRIS — Bug S only affects external `##class()` calls)
    try:
        engine.build_graph_globals()
    except Exception as exc:
        # Surface the failure rather than silently producing wrong results
        raise RuntimeError(
            f"BuildKG failed after fixture load — LazyKG-backed community algorithms "
            f"will see an empty ^KG and produce wrong results. Underlying error: {exc}"
        ) from exc
    return prefix
