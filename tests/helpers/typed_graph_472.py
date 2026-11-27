"""A synthetic typed graph of the size an outside run reported performance problems on (4.7.2).

About 17,000 nodes and 52,000 edges: documents, requirements and tests tied by a few predicates, a code part (classes,
members, API entries) with `extends` and `calls` edges that carry a `state` qualifier, and a few hubs. Everything is
generated from a fixed seed, so a test can compute its expected answer from the rows below in plain Python (an oracle
that never asks the system under test).

`rows()` gives (nodes, edges) in the shapes `BulkLoader.load_nodes` and `BulkLoader.load_edges` take. `load()` writes
them to a connection's default graph and rebuilds the indexes.

`layered()` is a small graph with many trails: `width` nodes in each of `layers` layers, every node tied to every node
of the next layer, so a walk of k hops from the one start has width**k trails and only width distinct far ends. It is
the cheapest way to get thousands of trails in a test.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

SEED = 472
N_DOC, N_REQ, N_TEST, N_SPEC = 1250, 1400, 1300, 200
N_CLASS, N_MEMBER, N_API = 3300, 8000, 1400

Node = Tuple[str, Dict]
Edge = Tuple[str, str, str, Optional[Dict]]


def _ids(prefix: str, n: int) -> List[str]:
    return [f"{prefix}:{i:05d}" for i in range(n)]


def rows(seed: int = SEED, prefix: str = "") -> Tuple[List[Node], List[Edge]]:
    """(nodes, edges): nodes as (id, {"namespace": label, "name": ...}), edges as (s, predicate, o, qualifiers).
    `prefix` starts every node id (`"T472:"` gives `T472:DOC:00000`), so a test can remove what it loaded."""
    rnd = random.Random(seed)
    docs, reqs = _ids(prefix + "DOC", N_DOC), _ids(prefix + "REQ", N_REQ)
    tests, specs = _ids(prefix + "TEST", N_TEST), _ids(prefix + "SPEC", N_SPEC)
    classes, members, apis = _ids(prefix + "CLASS", N_CLASS), _ids(prefix + "MEMBER", N_MEMBER), _ids(prefix + "API", N_API)
    labelled = [("Doc", docs), ("Req", reqs), ("Test", tests), ("Spec", specs), ("Class", classes),
                ("Member", members), ("Api", apis)]
    nodes: List[Node] = [(nid, {"namespace": label, "name": nid.lower()}) for label, ids in labelled for nid in ids]

    seen: Set[Tuple[str, str, str]] = set()
    edges: List[Edge] = []

    def add(s: str, p: str, o: str, q: Optional[Dict] = None) -> bool:
        if s != o and (s, p, o) not in seen:
            seen.add((s, p, o))
            edges.append((s, p, o, q))
            return True
        return False

    for d in docs:  # every document is part of one spec
        add(d, "PART_OF", rnd.choice(specs))
    for r in reqs:  # a requirement is cited by a document and belongs to a spec
        add(rnd.choice(docs), "CITES", r)
        add(r, "PART_OF", rnd.choice(specs))
    for t in tests:
        add(t, "PART_OF", rnd.choice(specs))
    # REFERS: documents and requirements or tests, the edge pointing either way (the undirected hop's case), and a
    # wider REFERS population between code nodes, so the predicate is not rare
    refers = 0
    while refers < 450:
        d, far = rnd.choice(docs), rnd.choice(reqs if rnd.random() < 0.55 else tests)
        s, o = (d, far) if rnd.random() < 0.5 else (far, d)
        refers += add(s, "REFERS", o)
    for _ in range(500):
        add(rnd.choice(classes), "REFERS", rnd.choice(members))
    for m in members:  # a member belongs to a class
        add(m, "MEMBER_OF", rnd.choice(classes))
    for a in apis:
        add(a, "WRAPS", rnd.choice(classes))
    for r in reqs:  # intent: requirement to a member or a class
        if rnd.random() < 0.6:
            add(r, "INTENT", rnd.choice(members if rnd.random() < 0.5 else classes))
    # the code graph: extends and calls, each edge with a state qualifier, one in ten stale
    code = classes + members + apis
    hubs = rnd.sample(classes, 5)

    def state() -> Dict:
        return {"state": "stale" if rnd.random() < 0.10 else "live", "weight": round(rnd.uniform(0.5, 3.0), 2)}

    for c in classes:
        if rnd.random() < 0.7:
            add(c, "EXTENDS", rnd.choice(hubs if rnd.random() < 0.3 else classes), state())
    for _ in range(36000):
        add(rnd.choice(code), "CALLS", rnd.choice(hubs if rnd.random() < 0.05 else code), state())
    for _ in range(1000):  # fan-in on the hubs, from documents
        add(rnd.choice(docs), "CITES", rnd.choice(hubs))
    return nodes, edges


def layered(width: int = 6, layers: int = 6, prefix: str = "LY",
            stale_every: Optional[int] = None) -> Tuple[List[Node], List[Edge], str]:
    """(nodes, edges, start): `layers` layers of `width` nodes plus one start tied to every node of layer 1, all
    edges `NEXT` with a `state` qualifier ("stale" on every `stale_every`-th edge, else "live")."""
    start = f"{prefix}:start"
    layer = [[f"{prefix}:{k}:{i}" for i in range(width)] for k in range(1, layers + 1)]
    nodes: List[Node] = [(start, {"namespace": "Lay", "name": "start"})]
    nodes += [(nid, {"namespace": "Lay", "name": nid}) for row in layer for nid in row]
    edges: List[Edge] = []
    k = 0

    def q() -> Dict:
        nonlocal k
        k += 1
        return {"state": "stale" if stale_every and k % stale_every == 0 else "live"}

    edges += [(start, "NEXT", nid, q()) for nid in layer[0]]
    for a, b in zip(layer, layer[1:]):
        edges += [(s, "NEXT", o, q()) for s in a for o in b]
    return nodes, edges, start


def walk_ends(edges: Iterable[Edge], start: str, min_hops: int, max_hops: int, predicates: Optional[Set[str]] = None,
              directed: bool = True, skip=None) -> Tuple[Set[str], int]:
    """(far ends, trails): the nodes `min_hops..max_hops` trails from `start` end at, and how many trails there are,
    computed by a plain depth-first walk over `edges` (a trail repeats no edge). `skip(qualifiers)` drops an edge.
    This is the oracle: it reads the rows, never the database."""
    adj: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for i, (s, p, o, q) in enumerate(edges):
        if (predicates and p not in predicates) or (skip and skip(q or {})):
            continue
        adj[s].append((o, i))
        if not directed:
            adj[o].append((s, i))
    ends: Set[str] = set()
    trails = 0
    stack = [(start, 0, frozenset())]
    while stack:
        node, depth, used = stack.pop()
        if depth >= min_hops:
            ends.add(node)
            trails += 1
        if depth < max_hops:
            for nxt, eid in adj.get(node, ()):
                if eid not in used:
                    stack.append((nxt, depth + 1, used | {eid}))
    return ends, trails


def unload(conn, prefix: str) -> None:
    """Remove every node whose id starts with `prefix`, and its edges, labels and properties (default graph)."""
    cur = conn.cursor()
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    for table, col in (("rdf_edges", "s"), ("rdf_edges", "o_id"), ("rdf_labels", "s"), ("rdf_props", "s"), ("nodes", "node_id")):
        cur.execute(f"DELETE FROM Graph_KG.{table} WHERE {col} LIKE ? ESCAPE '\\'", [like])
    conn.commit()


def load(conn, nodes: List[Node], edges: List[Edge]) -> Dict[str, int]:
    """Bulk-load `nodes` and `edges` into the default graph and rebuild what the loaders leave undone."""
    from iris_vector_graph.bulk_loader import BulkLoader

    loader = BulkLoader(conn)
    loader.load_nodes(nodes)
    out = loader.load_edges(edges)
    loader.rebuild_all_indices()
    return out
