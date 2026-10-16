"""The graphs IVG benchmarks, reproduced for the comparison engines.

Every function here is pure Python and needs no database. The loaders write what this module
returns; the runners derive their seeds and start nodes from it.

Dataset names
    S, M, L        RMAT, seed 42, a fresh generator per dataset (tests/benchmarks/bench.py)
    S193, M193, L193
                   RMAT as bench_193_full.py builds it: one generator for the whole run, so M
                   continues the random stream after S, and L after S and M
    NCM            the 10k node / 50k edge graph of benchmarks/neo4j_comparison.py
    LDBC10, LDBC10_10k, LDBC100
                   the LDBC SNB person_knows_person file (SF10 all rows, SF10 first 10,000 rows
                   as benchmarks/three_engine_benchmark.py reads it, SF100 all rows)
    DRKG           data/drkg/drkg.tsv, 97,238 entities, 5.87M triples
    karate, er500, er2000
                   the head-to-head fixtures of tests/perf/test_head_to_head.py
    btw500, btw2000
                   the betweenness fixtures of tests/perf/test_betweenness_vs_gds.py
"""

from __future__ import annotations

import csv
import functools
import hashlib
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

RMAT_PARAMS: Dict[str, Tuple[int, int]] = {
    "S": (1_000, 5_000),
    "M": (10_000, 50_000),
    "L": (100_000, 500_000),
}
RMAT_SEED = 42

# benchmarks/three_engine_benchmark.py SEEDS
LDBC_SEEDS = [
    "Person:933",
    "Person:36226",
    "Person:51934",
    "Person:6597069780295",
    "Person:10995116282236",
]


@dataclass
class Graph:
    name: str
    nodes: List[str]
    edges: List[Tuple[str, str]]
    label: str = "Node"
    node_prop: str = "node_id"
    rel_type: str = "R"
    # per-edge relationship type, aligned with edges; None means rel_type for every edge
    edge_types: Optional[List[str]] = None
    # False for the undirected networkx fixtures: each edge is stored once and algorithms project it undirected
    directed_source: bool = True
    meta: Dict[str, object] = field(default_factory=dict)

    def type_of(self, i: int) -> str:
        return self.edge_types[i] if self.edge_types is not None else self.rel_type


# ---------------------------------------------------------------------------
# RMAT
# ---------------------------------------------------------------------------


def _import_rmat():
    bench = REPO / "tests" / "benchmarks"
    if str(bench) not in sys.path:
        sys.path.insert(0, str(bench))
    from graph_gen import RMATGenerator  # the repo's own generator, not a copy

    return RMATGenerator


def _distinct(raw: Sequence[Tuple[int, int]]) -> List[Tuple[str, str]]:
    """First-seen order, one entry per (s, o): rdf_edges holds one row per (s, p, o)."""
    seen = set()
    out: List[Tuple[str, str]] = []
    for s, o in raw:
        if (s, o) in seen:
            continue
        seen.add((s, o))
        out.append((f"node_{s}", f"node_{o}"))
    return out


def _rmat_graph(label: str, continued: bool) -> Graph:
    RMATGenerator = _import_rmat()
    order = ["S", "M", "L"]
    if continued:
        gen = RMATGenerator(seed=RMAT_SEED)
        raw = None
        for ds in order[: order.index(label) + 1]:
            n, m = RMAT_PARAMS[ds]
            raw = gen.generate_edges(n, m)
    else:
        n, m = RMAT_PARAMS[label]
        raw = RMATGenerator(seed=RMAT_SEED).generate_edges(n, m)
    n, m = RMAT_PARAMS[label]
    name = f"{label}193" if continued else label
    return Graph(
        name=name,
        nodes=[f"node_{i}" for i in range(n)],
        edges=_distinct(raw),
        meta={"generated_edges": m, "continued_stream": continued},
    )


def _ncm_graph() -> Graph:
    """benchmarks/neo4j_comparison.py generate_edges(10000, 50000, seed=42), label N, property id."""
    n_nodes, n_edges = 10_000, 50_000
    rng = random.Random(42)
    edges = set()
    for i in range(1, min(n_nodes, 1000)):
        edges.add((i, rng.randint(0, i - 1)))
    attempts = 0
    while len(edges) < n_edges and attempts < n_edges * 10:
        s = rng.randint(0, n_nodes - 1)
        o = rng.randint(0, n_nodes - 1)
        if s != o:
            edges.add((s, o))
        attempts += 1
    return Graph(
        name="NCM",
        nodes=[f"node_{i}" for i in range(n_nodes)],
        edges=[(f"node_{s}", f"node_{o}") for s, o in list(edges)],
        label="N",
        node_prop="id",
        rel_type="R",
    )


def _algo_scale_graph(n: int) -> Graph:
    """The seeded G(n, m) graph (mean degree 6) the IVG sides load in scripts/bench/alt/algo_scale.py: the same edges on every engine."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scripts.bench.alt import algo_scale

    edges = algo_scale.er_edges(n, 6, 42)
    return Graph(
        name=f"algo{n}",
        nodes=[algo_scale.node_id(i) for i in range(n)],
        edges=[(algo_scale.node_id(u), algo_scale.node_id(v)) for u, v in edges],
        label="Node",
        node_prop="node_id",
        rel_type="R",
        directed_source=False,
    )


# ---------------------------------------------------------------------------
# networkx fixtures (algorithm legs)
# ---------------------------------------------------------------------------


def _nx_fixture(name: str) -> Graph:
    import networkx as nx

    if name == "karate":
        g = nx.karate_club_graph()
    elif name == "er500":
        g = nx.gnp_random_graph(500, 0.02, seed=42)
    elif name == "er2000":
        g = nx.gnp_random_graph(2000, 0.005, seed=42)
    elif name == "btw500":
        g = nx.erdos_renyi_graph(500, 0.01, seed=42, directed=False)
    elif name == "btw2000":
        g = nx.erdos_renyi_graph(2000, 0.003, seed=42, directed=False)
    else:
        raise KeyError(name)
    return Graph(
        name=name,
        nodes=[f"n{v}" for v in g.nodes()],
        edges=[(f"n{u}", f"n{v}") for u, v in g.edges()],
        label="Node",
        node_prop="node_id",
        rel_type="R",
        directed_source=False,
    )


# ---------------------------------------------------------------------------
# LDBC SNB (person_knows_person only: it is the one SNB file present locally)
# ---------------------------------------------------------------------------


def _find_first(candidates: Sequence[Path]) -> Optional[Path]:
    for c in candidates:
        if c.exists():
            return c
    return None


def _search_roots() -> List[Path]:
    roots = [REPO]
    # a worktree lives under <main>/.claude/worktrees/<name>; the data is in the main checkout
    for p in REPO.parents:
        if (p / "data").exists() or (p / "social_network-sf10-CsvBasic-LongDateFormatter").exists():
            roots.append(p)
    return roots


def ldbc_file(sf: int) -> Path:
    env = os.environ.get(f"IVG_LDBC_SF{sf}")
    if env:
        return Path(env)
    rel = Path(f"social_network-sf{sf}-CsvBasic-LongDateFormatter") / "dynamic" / "person_knows_person_0_0.csv"
    found = _find_first([r / rel for r in _search_roots()])
    if not found:
        raise FileNotFoundError(f"LDBC SF{sf} person_knows_person_0_0.csv not found; set IVG_LDBC_SF{sf}")
    return found


def _ldbc_graph(name: str, sf: int, limit: Optional[int]) -> Graph:
    """Rows are directed edges, read in file order, as benchmarks/three_engine_benchmark.py reads them."""
    nodes_seen = {}
    edges: List[Tuple[str, str]] = []
    with open(ldbc_file(sf), newline="") as f:
        reader = csv.reader(f, delimiter="|")
        next(reader)
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            s, d = f"Person:{row[0]}", f"Person:{row[1]}"
            nodes_seen.setdefault(s, None)
            nodes_seen.setdefault(d, None)
            edges.append((s, d))
    return Graph(
        name=name,
        nodes=list(nodes_seen),
        edges=edges,
        label="Person",
        node_prop="id",
        rel_type="KNOWS",
        meta={"source_file": str(ldbc_file(sf)), "sf": sf, "row_limit": limit},
    )


# ---------------------------------------------------------------------------
# DRKG
# ---------------------------------------------------------------------------


def drkg_dir() -> Path:
    env = os.environ.get("IVG_DRKG_DIR")
    if env:
        return Path(env)
    found = _find_first([r / "data" / "drkg" for r in _search_roots() if (r / "data" / "drkg" / "drkg.tsv").exists()])
    if not found:
        raise FileNotFoundError("data/drkg/drkg.tsv not found; set IVG_DRKG_DIR")
    return found


def drkg_node_type(entity_id: str) -> str:
    return entity_id.split("::", 1)[0] if "::" in entity_id else "Entity"


def _drkg_graph(limit: Optional[int] = None) -> Graph:
    """scripts/load_drkg.py: nodes from embed/entities.tsv, triples from drkg.tsv.

    Edges are made distinct on (head, relation, tail), as rdf_edges' (s, p, o) key does.
    """
    import pandas as pd

    d = drkg_dir()
    nodes = [line.split("\t", 1)[0] for line in open(d / "embed" / "entities.tsv")]
    df = pd.read_csv(
        d / "drkg.tsv",
        sep="\t",
        header=None,
        names=["h", "r", "t"],
        dtype="string",
        nrows=limit,
        quoting=csv.QUOTE_NONE,
    )
    raw_rows = len(df)
    df = df.dropna().drop_duplicates()
    return Graph(
        name="DRKG",
        nodes=nodes,
        edges=list(zip(df["h"].tolist(), df["t"].tolist())),
        edge_types=df["r"].tolist(),
        label="Entity",
        node_prop="id",
        rel_type="",
        meta={"raw_rows": raw_rows, "distinct_rows": len(df), "dir": str(d)},
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BUILDERS = {
    "S": lambda: _rmat_graph("S", False),
    "M": lambda: _rmat_graph("M", False),
    "L": lambda: _rmat_graph("L", False),
    "S193": lambda: _rmat_graph("S", True),
    "M193": lambda: _rmat_graph("M", True),
    "L193": lambda: _rmat_graph("L", True),
    "NCM": _ncm_graph,
    "LDBC10": lambda: _ldbc_graph("LDBC10", 10, None),
    "LDBC10_10k": lambda: _ldbc_graph("LDBC10_10k", 10, 10_000),
    "LDBC100": lambda: _ldbc_graph("LDBC100", 100, None),
    "DRKG": _drkg_graph,
    "karate": lambda: _nx_fixture("karate"),
    "er500": lambda: _nx_fixture("er500"),
    "er2000": lambda: _nx_fixture("er2000"),
    "btw500": lambda: _nx_fixture("btw500"),
    "btw2000": lambda: _nx_fixture("btw2000"),
    "algo5000": lambda: _algo_scale_graph(5000),
    "algo10000": lambda: _algo_scale_graph(10000),
    "algo20000": lambda: _algo_scale_graph(20000),
}


@functools.lru_cache(maxsize=4)
def get_graph(name: str) -> Graph:
    if name not in BUILDERS:
        raise KeyError(f"unknown dataset {name!r}; known: {', '.join(BUILDERS)}")
    return BUILDERS[name]()


# ---------------------------------------------------------------------------
# Degrees and the checksum
# ---------------------------------------------------------------------------


def degrees(graph: Graph) -> Tuple[Dict[str, int], Dict[str, int]]:
    out_deg: Dict[str, int] = {}
    in_deg: Dict[str, int] = {}
    for s, o in graph.edges:
        out_deg[s] = out_deg.get(s, 0) + 1
        in_deg[o] = in_deg.get(o, 0) + 1
    return out_deg, in_deg


def out_degree(graph: Graph, node: str) -> int:
    return degrees(graph)[0].get(node, 0)


def checksum_from_degrees(nodes: Sequence[str], out_deg: Dict[str, int], in_deg: Dict[str, int]) -> str:
    """sha256 over `node<TAB>out<TAB>in` lines, nodes in sorted order. A database reads its own degrees and calls this."""
    h = hashlib.sha256()
    for n in sorted(nodes):
        h.update(f"{n}\t{out_deg.get(n, 0)}\t{in_deg.get(n, 0)}\n".encode())
    return h.hexdigest()


def degree_checksum(graph: Graph) -> str:
    out_deg, in_deg = degrees(graph)
    return checksum_from_degrees(graph.nodes, out_deg, in_deg)


# ---------------------------------------------------------------------------
# Seed and shortest-path pair: the rules the IVG suites use
# ---------------------------------------------------------------------------


def highest_out_degree_seed(graph: Graph) -> str:
    """Highest out-degree node, ties by node id.

    tests/benchmarks/bench_utils.get_highest_degree_seed returns ^NKG("$ND", 0), the first node BuildNKG
    interned. On the RMAT graphs that is node_0, which is also the highest out-degree node (a unit test pins
    both). For graphs with no recorded IVG seed (DRKG, LDBC KNOWS) the highest out-degree node is the rule.
    """
    out_deg, _ = degrees(graph)
    if not out_deg:
        return graph.nodes[0]
    best = max(out_deg.values())
    return min(n for n, d in out_deg.items() if d == best)


def highest_total_degree_seed(graph: Graph) -> str:
    """benchmarks/neo4j_comparison.py highest_degree_seed: in plus out degree; first maximum in edge order."""
    deg: Dict[str, int] = {}
    for s, o in graph.edges:
        deg[s] = deg.get(s, 0) + 1
        deg[o] = deg.get(o, 0) + 1
    return max(deg, key=lambda k: deg[k])


_ADJ_CACHE: Dict[int, Tuple[Graph, Dict[str, List[Tuple[str, str]]]]] = {}


def _adjacency(graph: Graph) -> Dict[str, List[Tuple[str, str]]]:
    """out entries per node in (type, target) order; built once per Graph object (DRKG takes seconds)."""
    hit = _ADJ_CACHE.get(id(graph))
    if hit is not None and hit[0] is graph:
        return hit[1]
    adj = _build_adjacency(graph)
    _ADJ_CACHE.clear()
    _ADJ_CACHE[id(graph)] = (graph, adj)
    return adj


def _build_adjacency(graph: Graph) -> Dict[str, List[Tuple[str, str]]]:
    adj: Dict[str, List[Tuple[str, str]]] = {}
    for i, (s, o) in enumerate(graph.edges):
        adj.setdefault(s, []).append((graph.type_of(i), o))
    for lst in adj.values():
        lst.sort()
    return adj


def ivg_bfs_edges(graph: Graph, seed: str, hops: int) -> List[Tuple[str, str, int]]:
    """Replay Graph.KG.Traversal.BFSFast: the order of BFSFastJson's result rows.

    Each hop walks the frontier in ASCII order of node id (a local-array $Order), and each node's out entries in
    (predicate, target) order (a global $Order). Every edge is recorded with its hop, including edges into nodes
    already seen; only unseen targets join the next frontier.
    """
    adj = _adjacency(graph)
    rows: List[Tuple[str, str, int]] = []
    seen = {seed}
    frontier = {seed}
    for hop in range(1, hops + 1):
        nxt = set()
        for s in sorted(frontier):
            for _p, o in adj.get(s, ()):
                rows.append((s, o, hop))
                if o not in seen:
                    nxt.add(o)
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return rows


def ivg_sp_pair(graph: Graph, seed: str, target_distance: int = 4) -> Tuple[str, Optional[str]]:
    """bench_utils.pick_shortest_path_pair: BFSFastJson to min(target_distance, 3) hops, then the first row of
    maximum step, or the first row of step == target_distance when one exists (it cannot at 3 hops)."""
    rows = ivg_bfs_edges(graph, seed, min(target_distance, 3))
    candidates = [r for r in rows if r[2] == target_distance]
    if candidates:
        return seed, candidates[0][1]
    if rows:
        best = max(r[2] for r in rows)
        return seed, next(r[1] for r in rows if r[2] == best)
    return seed, None


def ivg_sp_pair_distinct(graph: Graph, seed: str, hops: int = 3) -> Tuple[str, Optional[str]]:
    """Same rule as ivg_sp_pair, but the first maximum-step row whose target is not the seed.

    On RMAT S and M the IVG rule lands on the seed itself (a 3-hop edge back into it), so the recorded Q5 asks for
    the path from node_0 to node_0. This variant gives a real pair for the same query.
    """
    rows = ivg_bfs_edges(graph, seed, hops)
    rows = [r for r in rows if r[1] != seed]
    if not rows:
        return seed, None
    best = max(r[2] for r in rows)
    return seed, next(r[1] for r in rows if r[2] == best)


def undirected_distance(graph: Graph, a: str, b: str, max_hops: int = 8) -> Optional[int]:
    """Length of the shortest path between a and b ignoring edge direction, or None beyond max_hops."""
    if a == b:
        return 0
    nbr: Dict[str, List[str]] = {}
    for s, o in graph.edges:
        nbr.setdefault(s, []).append(o)
        nbr.setdefault(o, []).append(s)
    seen = {a}
    frontier = [a]
    for d in range(1, max_hops + 1):
        nxt = []
        for u in frontier:
            for v in nbr.get(u, ()):
                if v == b:
                    return d
                if v not in seen:
                    seen.add(v)
                    nxt.append(v)
        frontier = nxt
        if not frontier:
            break
    return None


def directed_distance(graph: Graph, a: str, b: str, max_hops: int = 10) -> Optional[int]:
    if a == b:
        return 0
    adj: Dict[str, List[str]] = {}
    for s, o in graph.edges:
        adj.setdefault(s, []).append(o)
    seen = {a}
    frontier = [a]
    for d in range(1, max_hops + 1):
        nxt = []
        for u in frontier:
            for v in adj.get(u, ()):
                if v == b:
                    return d
                if v not in seen:
                    seen.add(v)
                    nxt.append(v)
        frontier = nxt
        if not frontier:
            break
    return None


def reach_counts(graph: Graph, seed: str, max_depth: int, include_seed: bool = True) -> List[int]:
    """Distinct nodes reached by 1..d out-hops, cumulative per depth; the seed counts when a cycle leads back to it.

    Depth 1 is the out-degree in distinct edges only when targets are distinct (true for the RMAT graphs, whose
    edges are distinct per (s, o)); the recorded IVG counts are these numbers. With include_seed=False the seed is
    left out even when a cycle leads back to it (Memgraph's BFS expansion operator never returns its start node).
    """
    adj = _adjacency(graph)
    seen = {seed}
    frontier = {seed}
    reached = set()
    counts: List[int] = []
    for _ in range(max_depth):
        nxt = set()
        for s in frontier:
            for _p, o in adj.get(s, ()):
                reached.add(o)
                if o not in seen:
                    nxt.add(o)
        seen |= nxt
        frontier = nxt
        counts.append(len(reached) - (0 if include_seed or seed not in reached else 1))
    return counts


def summary(graph: Graph) -> Dict[str, object]:
    out_deg, in_deg = degrees(graph)
    return {
        "dataset": graph.name,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "degree_checksum": checksum_from_degrees(graph.nodes, out_deg, in_deg),
        "seed": highest_out_degree_seed(graph),
        **{k: v for k, v in graph.meta.items() if k in ("generated_edges", "raw_rows", "distinct_rows")},
    }


if __name__ == "__main__":
    import json

    names = sys.argv[1:] or ["S", "M", "L"]
    for nm in names:
        g = get_graph(nm)
        info = summary(g)
        if nm in RMAT_PARAMS or nm.endswith("193"):
            info["sp_pair"] = ivg_sp_pair(g, info["seed"])
        print(json.dumps(info))
