"""Seeded small directed multigraphs with the shapes that broke IVG's algorithms (4.6.0 R306).

``generate(seed)`` is deterministic: the same seed gives the same graph on every machine and Python (it uses only
``random.Random(seed)``, never ``hash()``). A graph has 3 to 25 nodes in the default graph, and the shapes are drawn
from ``SHAPES``; seed ``i`` always includes ``SHAPES[i % len(SHAPES)]``, so any run of ``len(SHAPES)`` consecutive
seeds covers every shape. Some graphs carry a named graph next to the default one.

An edge is a triple ``(s, p, o)`` with an optional weight. Two edges between the same nodes are parallel edges under
different predicates (a triple is written once). ``OracleGraph.literal()`` prints a graph as Python that rebuilds it,
for a failure message.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: Ids that sort differently in Python and in IRIS collation (canonical numbers first, by value: "-1", "0", "1.5",
#: "9", "10", then strings by code point: "07", "B", "P1", ...), and ids with quotes, a backslash, a space, non-ASCII
#: characters and a leading ``P``.
ADVERSARIAL_IDS = (
    "9", "10", "-1", "07", "0", "1.5", "P1", "Pz", "a'b", 'q"t', "b\\s", "sp ace", "ünï", "Ω", "a", "B", "b",
)

PREDICATES = ("R", "S", "T")
WEIGHTS = (1.0, 2.0, 0.5, 3.0)

SHAPES = (
    "isolated",
    "self_loop",
    "parallel",
    "sink",
    "two_cycle",
    "even_cycle",
    "disconnected",
    "twins",
    "weights",
    "named_graph",
)

NAMED_GRAPH = "g2"


@dataclass(frozen=True)
class Edge:
    s: str
    p: str
    o: str
    weight: Optional[float] = None


@dataclass
class OracleGraph:
    seed: int
    nodes: List[str]
    edges: List[Edge]
    shapes: Tuple[str, ...] = ()
    #: A named graph next to the default one: its nodes and edges (the same id may be in both graphs).
    named_nodes: List[str] = field(default_factory=list)
    named_edges: List[Edge] = field(default_factory=list)

    @property
    def has_named_edges(self) -> bool:
        return bool(self.named_edges)

    def isolated(self) -> List[str]:
        touched = {e.s for e in self.edges} | {e.o for e in self.edges}
        return [n for n in self.nodes if n not in touched]

    def with_edges(self) -> List[str]:
        touched = {e.s for e in self.edges} | {e.o for e in self.edges}
        return [n for n in self.nodes if n in touched]

    def literal(self) -> str:
        """Python that rebuilds this graph."""
        lines = [
            f"OracleGraph(seed={self.seed}, shapes={self.shapes!r},",
            f"  nodes={self.nodes!r},",
            f"  edges=[{', '.join(_edge_literal(e) for e in self.edges)}],",
        ]
        if self.named_nodes or self.named_edges:
            lines.append(f"  named_nodes={self.named_nodes!r},")
            lines.append(f"  named_edges=[{', '.join(_edge_literal(e) for e in self.named_edges)}],")
        lines.append(")")
        return "\n".join(lines)


def _edge_literal(e: Edge) -> str:
    w = "" if e.weight is None else f", {e.weight!r}"
    return f"Edge({e.s!r}, {e.p!r}, {e.o!r}{w})"


class _Builder:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.edges: Dict[Tuple[str, str, str], Edge] = {}

    def add(self, s: str, o: str, p: Optional[str] = None, weight: Optional[float] = None) -> None:
        p = p or "R"
        key = (s, p, o)
        if key not in self.edges:
            self.edges[key] = Edge(s, p, o, weight)

    def parallel(self, s: str, o: str) -> None:
        """A second edge s -> o under a predicate s -> o does not have yet."""
        used = {k[1] for k in self.edges if k[0] == s and k[2] == o}
        for p in PREDICATES:
            if p not in used:
                self.add(s, o, p)
                return


def _ids(rng: random.Random, n: int, ascii_only: bool = False) -> List[str]:
    pool = [v for v in ADVERSARIAL_IDS if v.isascii()] if ascii_only else list(ADVERSARIAL_IDS)
    k = min(n, rng.randint(2, 8))
    chosen = rng.sample(pool, k)
    filler = []
    while len(chosen) + len(filler) < n:
        # numbers and words mixed, so a number-vs-text ordering shows
        candidate = str(rng.randint(2, 120)) if rng.random() < 0.4 else f"v{rng.randint(0, 99)}"
        if candidate not in chosen and candidate not in filler:
            filler.append(candidate)
    ids = chosen + filler
    rng.shuffle(ids)
    return ids


def generate(seed: int, ascii_only: bool = False) -> OracleGraph:
    """The graph of ``seed``. ``ascii_only`` leaves out the non-ASCII ids: the Arno library's answers to such a graph
    end in stray characters (its length is the UTF-8 byte count), so IVG falls back to ObjectScript or Python and the
    library itself goes unchecked."""
    rng = random.Random(seed)
    forced = SHAPES[seed % len(SHAPES)]
    shapes = {forced} | {s for s in SHAPES if rng.random() < 0.3}
    n = rng.randint(3, 25)
    ids = _ids(rng, n, ascii_only)
    b = _Builder(rng)

    n_isolated = rng.randint(1, min(3, n - 2)) if "isolated" in shapes else 0
    live = ids[n_isolated:]

    if "twins" in shapes and len(live) >= 6:
        # two copies of one motif on disjoint nodes: every score ties between the copies
        k = rng.randint(3, min(5, len(live) // 2))
        left, right = live[:k], live[k:2 * k]
        motif = [(i, j) for i in range(k) for j in range(k) if i != j and rng.random() < 0.45]
        motif = motif or [(0, 1)]
        for i, j in motif:
            b.add(left[i], left[j])
            b.add(right[i], right[j])
        live = live[2 * k:] + left[:1]
        if len(live) < 2:
            live = left + right

    parts = [live]
    if "disconnected" in shapes and len(live) >= 4:
        cut = rng.randint(2, len(live) - 2)
        parts = [live[:cut], live[cut:]]

    for part in parts:
        p_edge = rng.choice((0.12, 0.2, 0.3))
        for s in part:
            for o in part:
                if s != o and rng.random() < p_edge:
                    b.add(s, o)
        # keep each part connected (weakly): a path through it
        for s, o in zip(part, part[1:]):
            if not any(k[0] in (s, o) and k[2] in (s, o) for k in b.edges):
                b.add(s, o) if rng.random() < 0.5 else b.add(o, s)

    every = [x for part in parts for x in part]
    if "self_loop" in shapes and every:
        for x in rng.sample(every, min(len(every), rng.randint(1, 2))):
            b.add(x, x)
    if "parallel" in shapes and b.edges:
        for s, _p, o in rng.sample(sorted(b.edges), min(len(b.edges), rng.randint(1, 3))):
            b.parallel(s, o)
    if "two_cycle" in shapes and len(every) >= 2:
        s, o = rng.sample(every, 2)
        b.add(s, o)
        b.add(o, s)
    if "even_cycle" in shapes and len(every) >= 4:
        length = 4 if len(every) < 6 or rng.random() < 0.5 else 6
        cyc = rng.sample(every, length)
        for s, o in zip(cyc, cyc[1:] + cyc[:1]):
            b.add(s, o)
    if "sink" in shapes and every:
        # a node with in-edges and no out-edge
        sink = rng.choice(every)
        for k in [k for k in b.edges if k[0] == sink]:
            del b.edges[k]
        for x in rng.sample(every, min(len(every), 2)):
            if x != sink:
                b.add(x, sink)

    edges = sorted(b.edges.values(), key=lambda e: (e.s, e.p, e.o))
    if "weights" in shapes:
        edges = [Edge(e.s, e.p, e.o, rng.choice(WEIGHTS + (None,))) for e in edges]

    nodes = list(ids)
    rng.shuffle(nodes)
    g = OracleGraph(seed=seed, nodes=nodes, edges=edges, shapes=tuple(s for s in SHAPES if s in shapes))
    # nodes the edges reach that were not drawn as nodes cannot exist: every endpoint is an id
    assert {e.s for e in edges} | {e.o for e in edges} <= set(nodes)

    if "named_graph" in shapes:
        shared = rng.sample(nodes, min(len(nodes), 3))
        own = ["g2-only", "10"] if "10" not in shared else ["g2-only"]
        g.named_nodes = shared + own
        named = _Builder(rng)
        for s, o in zip(g.named_nodes, g.named_nodes[1:]):
            named.add(s, o)
        named.add(g.named_nodes[-1], g.named_nodes[0])
        g.named_edges = sorted(named.edges.values(), key=lambda e: (e.s, e.p, e.o))
    return g


def corpus(seeds) -> List[OracleGraph]:
    return [generate(s) for s in seeds]


# ── periodic graphs (4.6.0, round-3 walkthrough) ─────────────────────────────────────────────────────────────────
#
# A strongly connected component whose cycle lengths share a divisor k > 1 is periodic: an unshifted power iteration
# (x A) cycles with period k on it and never converges. The seeded corpus drew none whose period showed (its cycles
# overlap at random, so they share no divisor), and IVG's eigenvector centrality answered a directed 3-cycle with
# tails by max_iter mod 3. These are fixed graphs, each named in ``shapes[-1]``, with negative seeds so no failure
# message reads as a generated seed's.

#: The novice-460 round-3 walkthrough's graph (~/.cache/ivg-bench/novice-460/r3-python/graph_design.py): the KNOWS
#: 3-cycle ann -> ben -> cat -> ann with tails, LIVES_IN and LIKES sinks, gus with no edge.
WALKTHROUGH_NODES = ["ann", "ben", "cat", "dan", "eve", "fay", "gus", "bos", "den", "alien", "brazil", "hal", "cube"]
WALKTHROUGH_EDGES = (
    [Edge(s, "KNOWS", o, w) for s, o, w in (
        ("ann", "ben", 1.0), ("ben", "cat", 1.0), ("cat", "ann", 1.0), ("cat", "dan", 1.0), ("dan", "eve", 1.0),
        ("eve", "fay", 2.0), ("ann", "eve", 5.0), ("hal", "ann", 1.0))]
    + [Edge(s, "LIVES_IN", o, 1.0) for s, o in (
        ("ann", "bos"), ("ben", "bos"), ("cat", "den"), ("dan", "den"), ("eve", "bos"), ("fay", "den"))]
    + [Edge(s, "LIKES", o, 1.0) for s, o in (
        ("ann", "alien"), ("ben", "alien"), ("cat", "brazil"), ("eve", "brazil"), ("fay", "brazil"),
        ("hal", "cube"))]
)
#: Its named graph "lab": ann -> x1 -> x2.
WALKTHROUGH_LAB_NODES = ["ann", "x1", "x2"]
WALKTHROUGH_LAB_EDGES = [Edge("ann", "KNOWS", "x1"), Edge("x1", "KNOWS", "x2")]


def _cycle(ids: List[str]) -> List[Tuple[str, str]]:
    return list(zip(ids, ids[1:] + ids[:1]))


def periodic() -> List[OracleGraph]:
    """The periodic families: directed k-cycles (k = 2, 3, 4, with ids that collate differently in IRIS and Python,
    and a node with no edge beside the 3-cycle), a 3-cycle with tails in and out, a bipartite (period 2) strongly
    connected component, two components of periods 2 and 3 joined by one edge, and the walkthrough's graph with and
    without its named graph."""
    fams: List[Tuple[str, List[str], List[Tuple[str, str]]]] = [
        ("cycle2", ["9", "10"], _cycle(["9", "10"])),
        ("cycle3", ["a", "B", "07", "solo"], _cycle(["a", "B", "07"])),
        ("cycle4", ["-1", "0", "P1", "a'b"], _cycle(["-1", "0", "P1", "a'b"])),
        ("cycle3_tails", ["c1", "c2", "c3", "in1", "in2", "out1", "out2"],
         _cycle(["c1", "c2", "c3"]) + [("in2", "in1"), ("in1", "c1"), ("c2", "out1"), ("out1", "out2")]),
        ("bipartite_scc", ["u1", "u2", "u3", "v1", "v2"],
         [("u1", "v1"), ("v1", "u2"), ("u2", "v2"), ("v2", "u1"), ("u1", "v2"), ("v2", "u3"), ("u3", "v1"),
          ("v1", "u1")]),
        ("two_periods", ["p", "q", "r", "s", "t", "z"],
         _cycle(["p", "q"]) + [("q", "r")] + _cycle(["r", "s", "t"]) + [("t", "z")]),
    ]
    out = [
        OracleGraph(seed=-(i + 1), nodes=nodes, edges=[Edge(s, "R", o) for s, o in pairs], shapes=("periodic", name))
        for i, (name, nodes, pairs) in enumerate(fams)
    ]
    base = -(len(out) + 1)
    out.append(OracleGraph(seed=base, nodes=list(WALKTHROUGH_NODES), edges=list(WALKTHROUGH_EDGES),
                           shapes=("periodic", "walkthrough")))
    out.append(OracleGraph(seed=base - 1, nodes=list(WALKTHROUGH_NODES), edges=list(WALKTHROUGH_EDGES),
                           shapes=("periodic", "named_graph", "walkthrough_lab"),
                           named_nodes=list(WALKTHROUGH_LAB_NODES), named_edges=list(WALKTHROUGH_LAB_EDGES)))
    return out


def by_seed(seed: int, ascii_only: bool = False) -> OracleGraph:
    """``generate(seed)`` for a seed of 0 or more, the periodic family of a negative one."""
    if seed >= 0:
        return generate(seed, ascii_only)
    return next(g for g in periodic() if g.seed == seed)
