"""The documented answer of each public IVG algorithm on an `OracleGraph`, computed without IVG (4.6.0 R306).

Each function cites the rule it follows (docs/USER_GUIDE.md section 6, docs/COMMUNITIES.md, the method's
docstring). Where networkx implements that rule it is used; where the docs fix something networkx does not (a fixed
iteration count, a hop limit, a tie rule), the rule is implemented here and unit-tested against networkx where they
meet (tests/unit/test_460_r306_oracle_reference.py).

Every algorithm answers every node of the graph, edgeless ones included (``include_isolated=True``): PageRank sums to
1 over ``node_count()`` nodes, the ``n`` of degree and classical closeness is ``node_count()``, components, k-core
and triangles have a row for each node. ``include_isolated=False`` gives the 4.6.0 pre-R306 rule (only the nodes with
an edge), to tell that defect from others.

Ranking everywhere: score descending, ties by node id in IRIS collation (`collation_key`), then the ``top_k`` cut.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import networkx as nx

from tests.oracle.graphs import Edge, OracleGraph

#: Scores equal to this many decimal places are a tie, and the documented order then goes by id (USER_GUIDE section
#: 6): float noise between two nodes in the same position is far below it.
TIE_PLACES = 12
TIE = 10.0 ** -TIE_PLACES

# ── IRIS collation ────────────────────────────────────────────────────────────────────────────────────────────────

#: An IRIS canonical number: no leading zero (".5", not "0.5"), no trailing zero after the point, no "+", no "-0".
_CANONICAL = re.compile(r"0|-?(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|\.[0-9]*[1-9])")


def is_canonical_number(s: str) -> bool:
    return bool(_CANONICAL.fullmatch(s))


def collation_key(node_id) -> tuple:
    """Where ``node_id`` falls among IRIS subscripts (IRIS standard collation): canonical numbers first, by value,
    then every other string by character code. ``"9" < "10" < "07" < "B" < "P1" < "a"``; ``"-1" < "0" < "1.5"``."""
    s = str(node_id)
    if is_canonical_number(s):
        return (0, Decimal(s), "")
    return (1, Decimal(0), s)


def tied(a: float, b: float) -> bool:
    return round(a, TIE_PLACES) == round(b, TIE_PLACES)


def ranked(scores: Dict[str, float], top_k: int = 0) -> List[Tuple[str, float]]:
    """``(id, score)`` highest first, ties (equal to `TIE_PLACES` decimals) by id in IRIS collation, then the first
    ``top_k`` (0: all)."""
    out = sorted(scores.items(), key=lambda kv: (-round(kv[1], TIE_PLACES), collation_key(kv[0])))
    return out[:top_k] if top_k > 0 else out


# ── the graph as networkx sees it ─────────────────────────────────────────────────────────────────────────────────


def node_set(g: OracleGraph, include_isolated: bool = True) -> List[str]:
    return list(g.nodes) if include_isolated else g.with_edges()


def multidigraph(g: OracleGraph, include_isolated: bool = True, named: bool = False) -> nx.MultiDiGraph:
    """Every edge (each predicate one edge), with its weight (``None`` read as 1.0)."""
    G = nx.MultiDiGraph()
    nodes = g.named_nodes if named else node_set(g, include_isolated)
    edges = g.named_edges if named else g.edges
    G.add_nodes_from(nodes)
    for e in edges:
        G.add_edge(e.s, e.o, key=e.p, weight=1.0 if e.weight is None else e.weight)
    return G


def digraph(g: OracleGraph, include_isolated: bool = True, named: bool = False) -> nx.DiGraph:
    """The simple directed graph: parallel edges one edge, self-loops kept."""
    G = nx.DiGraph()
    G.add_nodes_from(multidigraph(g, include_isolated, named).nodes)
    for e in (g.named_edges if named else g.edges):
        G.add_edge(e.s, e.o)
    return G


def undirected_simple(g: OracleGraph, include_isolated: bool = True) -> nx.Graph:
    """Undirected, parallel edges one edge, self-loops removed (COMMUNITIES.md: "self-loops removed")."""
    G = nx.Graph()
    G.add_nodes_from(node_set(g, include_isolated))
    for e in g.edges:
        if e.s != e.o:
            G.add_edge(e.s, e.o)
    return G


def _out_lists(g: OracleGraph, nodes: Sequence[str], edges: Iterable[Edge]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {n: [] for n in nodes}
    for e in edges:
        out[e.s].append(e.o)
    return out


# ── PageRank ──────────────────────────────────────────────────────────────────────────────────────────────────────


def pagerank_global(
    g: OracleGraph, damping: float = 0.85, max_iterations: int = 20, include_isolated: bool = True
) -> Dict[str, float]:
    """``kg_PAGERANK()``: textbook PageRank, a fixed ``max_iterations`` from 1/N, no convergence test; each edge
    counts (a multigraph: parallel edges and self-loops are out-edges), weights ignored; a node with no out-edge
    spreads its rank over all N nodes; the scores sum to 1 (USER_GUIDE section 6, PageRank)."""
    nodes = node_set(g, include_isolated)
    n = len(nodes)
    if n == 0:
        return {}
    out = _out_lists(g, nodes, g.edges)
    x = {v: 1.0 / n for v in nodes}
    for _ in range(max_iterations):
        dangling = sum(x[v] for v in nodes if not out[v])
        new = {v: (1.0 - damping) / n + damping * dangling / n for v in nodes}
        for v in nodes:
            if out[v]:
                share = damping * x[v] / len(out[v])
                for w in out[v]:
                    new[w] += share
        x = new
    return x


def personalized_pagerank(
    g: OracleGraph,
    seeds: Sequence[str],
    damping: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6,
    include_isolated: bool = True,
    named: bool = False,
    start: str = "uniform",
) -> Dict[str, float]:
    """``kg_PERSONALIZED_PAGERANK``: networkx's ``pagerank(G, alpha=damping, personalization={seed: 1},
    tol=tolerance)`` on the multigraph; a dangling node hands its score to the seeds; stops after
    ``max_iterations`` or once the summed change is below N * tolerance. Only the nodes the seeds reach score (the
    others' 1/N start decays towards 0 and is left out). A seed outside the graph scores nothing."""
    nodes = g.named_nodes if named else node_set(g, include_isolated)
    edges = g.named_edges if named else g.edges
    seeds = [s for s in dict.fromkeys(seeds) if s in nodes]
    if not seeds:
        return {}
    out = _out_lists(g, nodes, edges)
    n = len(nodes)
    p = {v: 0.0 for v in nodes}
    for s in seeds:
        p[s] = 1.0 / len(seeds)
    # networkx starts from 1/N; IVG's walk starts at the seeds (``start="seeds"``). Converged, both are the answer;
    # after a fixed few iterations (kg_PAGERANK(seed_entities=...)'s 20) the start shows.
    x = dict(p) if start == "seeds" else {v: 1.0 / n for v in nodes}
    for _ in range(max_iterations):
        dangling = sum(x[v] for v in nodes if not out[v])
        new = {v: (1.0 - damping) * p[v] + damping * dangling * p[v] for v in nodes}
        for v in nodes:
            if out[v]:
                share = damping * x[v] / len(out[v])
                for w in out[v]:
                    new[w] += share
        err = sum(abs(new[v] - x[v]) for v in nodes)
        x = new
        # networkx's N is every node; IVG's walk, from the seeds, counts the nodes holding a score
        held = sum(1 for v in nodes if x[v] > 0) if start == "seeds" else n
        if err < held * tolerance:
            break
    reach = set()
    for s in seeds:
        reach |= set(bfs_distances({v: out[v] for v in nodes}, s))
    return {v: x[v] for v in nodes if v in reach}


# ── degree ────────────────────────────────────────────────────────────────────────────────────────────────────────


def degree_centrality(
    g: OracleGraph,
    direction: str = "out",
    predicate: Optional[str] = None,
    include_isolated: bool = True,
    named: bool = False,
) -> Dict[str, Tuple[float, int]]:
    """``{id: (score, degree)}``: degree is the edges in ``direction`` (each predicate one edge; "both" is out + in,
    so a self-loop counts twice there), score degree / (n - 1), n the graph's nodes (USER_GUIDE section 6)."""
    nodes = g.named_nodes if named else node_set(g, include_isolated)
    edges = g.named_edges if named else g.edges
    deg = {v: 0 for v in nodes}
    for e in edges:
        if predicate and e.p != predicate:
            continue
        if direction in ("out", "both"):
            deg[e.s] += 1
        if direction in ("in", "both"):
            deg[e.o] += 1
    norm = 1.0 / max(len(nodes) - 1, 1)
    return {v: (d * norm, d) for v, d in deg.items()}


# ── shortest-path distances, betweenness, closeness ───────────────────────────────────────────────────────────────


def _adjacency(g: OracleGraph, direction: str, include_isolated: bool = True) -> Dict[str, List[str]]:
    """Simple adjacency (each neighbour once, self-loops dropped) in ``direction``."""
    nodes = node_set(g, include_isolated)
    adj: Dict[str, set] = {v: set() for v in nodes}
    for e in g.edges:
        if e.s == e.o:
            continue
        if direction in ("out", "both"):
            adj[e.s].add(e.o)
        if direction in ("in", "both"):
            adj[e.o].add(e.s)
    return {v: sorted(ns, key=collation_key) for v, ns in adj.items()}


def bfs_distances(adj: Dict[str, List[str]], source: str, max_hops: int = 0) -> Dict[str, int]:
    dist = {source: 0}
    q = deque([source])
    while q:
        v = q.popleft()
        if max_hops and dist[v] >= max_hops:
            continue
        for w in adj[v]:
            if w not in dist:
                dist[w] = dist[v] + 1
                q.append(w)
    return dist


def _brandes(adj: Dict[str, List[str]], sources: Iterable[str], max_hops: int = 0) -> Dict[str, float]:
    """Unnormalised Brandes over ``sources``, counting only the pairs at most ``max_hops`` apart (0: every pair)."""
    bc = {v: 0.0 for v in adj}
    for s in sources:
        stack: List[str] = []
        pred: Dict[str, List[str]] = defaultdict(list)
        sigma = defaultdict(float)
        sigma[s] = 1.0
        dist = {s: 0}
        q = deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            if max_hops and dist[v] >= max_hops:
                continue
            for w in adj[v]:
                if w not in dist:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = defaultdict(float)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += sigma[v] / sigma[w] * (1.0 + delta[w])
            if w != s:
                bc[w] += delta[w]
    return bc


def betweenness(
    g: OracleGraph, direction: str = "out", max_hops: int = 0, include_isolated: bool = True
) -> Dict[str, float]:
    """``betweenness_centrality(sample_size >= N)``: exact Brandes, unnormalised, networkx's
    ``betweenness_centrality(G, normalized=False)``; "out" and "in" the directed graph, "both" the undirected one,
    each pair once. Every node, zeros included (the method lists only the nonzero ones)."""
    if direction == "both":
        adj = _adjacency(g, "both", include_isolated)
        return {v: s / 2.0 for v, s in _brandes(adj, adj, max_hops).items()}
    adj = _adjacency(g, direction, include_isolated)
    return _brandes(adj, adj, max_hops)


def betweenness_neighborhood(g: OracleGraph, seed: str, hops: int = 2) -> Dict[str, float]:
    """``betweenness_centrality_neighborhood``: directed Brandes on the subgraph induced by the nodes ``seed``
    reaches by out-edges within ``hops`` (the seed included), every pair. A seed with no node is empty."""
    if seed not in g.nodes:
        return {}
    adj = _adjacency(g, "out")
    reach = set(bfs_distances(adj, seed, hops))
    sub = {v: [w for w in adj[v] if w in reach] for v in reach}
    return _brandes(sub, sub)


def closeness(
    g: OracleGraph,
    formula: str = "harmonic",
    direction: str = "out",
    max_hops: int = 0,
    include_isolated: bool = True,
) -> Dict[str, float]:
    """``closeness_centrality``: from each node, the hop distances along ``direction`` ("out": the nodes it reaches;
    "in": the nodes that reach it; "both": undirected), at most ``max_hops`` (0: any). Harmonic: the sum of 1/d.
    Classical: (n - 1) / (sum of d) for a node that reaches all n - 1 others, else 0 (USER_GUIDE section 6)."""
    adj = _adjacency(g, direction, include_isolated)
    n = len(adj)
    out = {}
    for v in adj:
        dist = bfs_distances(adj, v, max_hops)
        ds = [d for w, d in dist.items() if w != v]
        if formula == "harmonic":
            out[v] = sum(1.0 / d for d in ds)
        else:
            out[v] = (n - 1) / sum(ds) if ds and len(ds) == n - 1 else 0.0
    return out


# ── eigenvector ───────────────────────────────────────────────────────────────────────────────────────────────────


def eigenvector(
    g: OracleGraph, max_iter: int = 100, tol: float = 1e-6, include_isolated: bool = True
) -> Tuple[Optional[Dict[str, float]], bool]:
    """``eigenvector_centrality``: networkx's ``eigenvector_centrality(G, max_iter, tol)`` on the simple directed
    graph (a source counts once however many edges it has to a node; every node, edgeless ones included), which
    iterates ``x (A + I)`` from 1/N, L2-normalised, until the summed change is below N * tol (USER_GUIDE section 6).
    Returns the scores and True, or ``(None, False)`` where networkx raises ``PowerIterationFailedConvergence``
    (IVG must raise it too)."""
    try:
        return nx.eigenvector_centrality(digraph(g, include_isolated), max_iter=max_iter, tol=tol), True
    except nx.PowerIterationFailedConvergence:
        return None, False


# ── communities and components ────────────────────────────────────────────────────────────────────────────────────


def wcc_labels(g: OracleGraph, include_isolated: bool = True) -> Dict[str, str]:
    """``kg_WCC()``: each node labelled with the first id of its component in IRIS collation."""
    G = digraph(g, include_isolated)
    out = {}
    for comp in nx.weakly_connected_components(G):
        label = min(comp, key=collation_key)
        for v in comp:
            out[v] = label
    return out


def scc_sets(g: OracleGraph, include_isolated: bool = True) -> List[frozenset]:
    return sorted(
        (frozenset(c) for c in nx.strongly_connected_components(digraph(g, include_isolated))),
        key=lambda c: (-len(c), collation_key(min(c, key=collation_key))),
    )


def core_numbers(g: OracleGraph, include_isolated: bool = True) -> Dict[str, int]:
    """``k_core()``: networkx's ``core_number`` on the undirected graph, self-loops removed."""
    return nx.core_number(undirected_simple(g, include_isolated))


def triangles(g: OracleGraph, include_isolated: bool = True) -> Dict[str, Tuple[int, float]]:
    """``triangle_count()``: ``{id: (triangles, lcc)}`` on the undirected graph, self-loops and parallel edges once,
    lcc = triangles / (k(k-1)/2), 0 below two neighbours (COMMUNITIES.md)."""
    G = undirected_simple(g, include_isolated)
    t = nx.triangles(G)
    out = {}
    for v in G:
        k = G.degree(v)
        out[v] = (t[v], (2.0 * t[v] / (k * (k - 1))) if k >= 2 else 0.0)
    return out


def ranked_rows(values: Dict[str, float], top_k: int = 0) -> List[str]:
    """The ids of `ranked` (k_core, triangle_count: value descending, ties by id in collation)."""
    return [v for v, _ in ranked(values, top_k)]


def cdlp(g: OracleGraph, max_iterations: int = 10, include_isolated: bool = True,
         self_loops_vote: bool = False) -> Dict[str, str]:
    """``kg_CDLP()``, simulated: each round every node at once takes the label most common among its neighbours in
    either direction (each edge one vote; its own label not counted, so a self-loop is no vote unless
    ``self_loops_vote``), a tie going to the label first in IRIS collation; a node with no vote keeps its label;
    stops when no label changes or after ``max_iterations`` rounds (COMMUNITIES.md)."""
    nodes = node_set(g, include_isolated)
    labels = {v: v for v in nodes}
    for _ in range(max_iterations):
        new = {}
        for v in nodes:
            counts: Dict[str, int] = defaultdict(int)
            for e in g.edges:
                if e.s == e.o and not self_loops_vote:
                    continue
                if e.s == v:
                    counts[labels[e.o]] += 1
                if e.o == v:
                    counts[labels[e.s]] += 1
            if counts:
                best = max(counts.values())
                new[v] = min((lb for lb, c in counts.items() if c == best), key=collation_key)
            else:
                new[v] = labels[v]
        changed = new != labels
        labels = new
        if not changed:
            break
    return labels


def modularity(g: OracleGraph, parts: Iterable[Iterable[str]]) -> float:
    """Modularity of a partition on the undirected simple unweighted graph (COMMUNITIES.md: compare with
    ``nx.community.modularity(nx.Graph(G.to_undirected()), parts, weight=None)``). Self-loops are removed: the
    graph is simple. Nodes the partition leaves out are singletons."""
    G = undirected_simple(g)
    if G.number_of_edges() == 0:
        return 0.0
    parts = [set(p) & set(G) for p in parts]
    covered = set().union(*parts) if parts else set()
    parts = [p for p in parts if p] + [{v} for v in G if v not in covered]
    return nx.community.modularity(G, parts, weight=None)


def best_louvain_modularity(g: OracleGraph, seeds: Iterable[int] = range(8)) -> float:
    G = undirected_simple(g)
    if G.number_of_edges() == 0:
        return 0.0
    return max(modularity(g, nx.community.louvain_communities(G, seed=s, weight=None)) for s in seeds)


# ── shortest paths ────────────────────────────────────────────────────────────────────────────────────────────────


def hop_distance(g: OracleGraph, src: str, dst: str) -> Optional[int]:
    """Edges on a shortest directed path, or None when ``dst`` is unreachable."""
    try:
        return nx.shortest_path_length(digraph(g), src, dst)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def weighted_cost(g: OracleGraph, src: str, dst: str, direction: str = "out") -> Optional[float]:
    """Cheapest path by edge weight (an edge without one, or one of 0 or less, costs 1.0; of parallel edges the
    cheapest), networkx's ``dijkstra_path_length``: along the edges ("out"), on the reversed graph ("in", a path
    from ``src`` to ``dst`` against the edges), or on the undirected graph ("both")."""
    G = nx.DiGraph()
    G.add_nodes_from(g.nodes)
    for e in g.edges:
        w = 1.0 if e.weight is None or e.weight <= 0 else e.weight
        if not G.has_edge(e.s, e.o) or G[e.s][e.o]["weight"] > w:
            G.add_edge(e.s, e.o, weight=w)
    if direction == "out":
        view = G
    elif direction == "in":
        view = G.reverse(copy=False)
    else:
        # undirected: of a -> b and b -> a, the cheaper
        view = nx.Graph()
        view.add_nodes_from(G)
        for s, o, d in G.edges(data=True):
            if not view.has_edge(s, o) or view[s][o]["weight"] > d["weight"]:
                view.add_edge(s, o, weight=d["weight"])
    try:
        return nx.dijkstra_path_length(view, src, dst, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def is_weighted_path(g: OracleGraph, nodes: Sequence[str], direction: str) -> bool:
    """``nodes`` is a path of the graph walked in ``direction``."""
    edges = {(e.s, e.o) for e in g.edges}
    ok = {"out": lambda a, b: (a, b) in edges, "in": lambda a, b: (b, a) in edges,
          "both": lambda a, b: (a, b) in edges or (b, a) in edges}[direction]
    return all(ok(a, b) for a, b in zip(nodes, nodes[1:]))


def neighbors(g: OracleGraph, sources: Sequence[str], direction: str, predicate: Optional[str] = None) -> set:
    """The ids one hop from ``sources`` (``ivg.neighbors``, ``kg_NEIGHBORS``): networkx's successors ("out"),
    predecessors ("in"), or both, of the multigraph, along ``predicate`` edges when given."""
    G = nx.MultiDiGraph()
    G.add_nodes_from(g.nodes)
    G.add_edges_from((e.s, e.o) for e in g.edges if predicate is None or e.p == predicate)
    out = set()
    for s in sources:
        if s not in G:
            continue
        if direction in ("out", "both"):
            out |= set(G.successors(s))
        if direction in ("in", "both"):
            out |= set(G.predecessors(s))
    return out


def is_path(g: OracleGraph, nodes: Sequence[str]) -> bool:
    edges = {(e.s, e.o) for e in g.edges}
    return all((a, b) in edges for a, b in zip(nodes, nodes[1:]))


# ── betweenness sampling order (docs: the SHA-1 of the id's UTF-8 bytes, first 7 bytes, then the id) ─────────────


def sample_key(node_id: str) -> Tuple[int, str]:
    return int.from_bytes(hashlib.sha1(node_id.encode("utf-8")).digest()[:7], "big"), node_id
