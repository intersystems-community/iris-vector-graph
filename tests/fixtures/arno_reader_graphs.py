"""Five deterministic graphs for the Native Reader evidence gate (spec 236, FR-009).

Each graph is ``{"nodes": [...], "edges": [(s, p, o), ...], "seeds": [...]}`` with fixed
node ids, so an Owner and a Native Reader are asked the same question on the same data.
Shapes: a path, a star, a dense cluster, several components, and one built to tie.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

PRED = "IVG236_LINK"


def _edges(pairs) -> List[Tuple[str, str, str]]:
    return [(s, PRED, o) for s, o in pairs]


def _path() -> dict:
    nodes = [f"p{i}" for i in range(8)]
    return {
        "nodes": nodes,
        "edges": _edges(zip(nodes, nodes[1:])),
        "seeds": ["p0"],
        "shape": "a directed path of 8 nodes",
    }


def _star() -> dict:
    leaves = [f"s{i}" for i in range(1, 9)]
    return {
        "nodes": ["hub"] + leaves,
        "edges": _edges([("hub", leaf) for leaf in leaves] + [(leaf, "hub") for leaf in leaves[:3]]),
        "seeds": ["hub"],
        "shape": "one hub with 8 leaves, 3 pointing back",
    }


def _dense() -> dict:
    nodes = [f"d{i}" for i in range(6)]
    pairs = [(a, b) for a in nodes for b in nodes if a != b]
    return {
        "nodes": nodes,
        "edges": _edges(pairs),
        "seeds": ["d0", "d3"],
        "shape": "a complete directed cluster of 6 nodes",
    }


def _multi_component() -> dict:
    a = [f"ma{i}" for i in range(4)]
    b = [f"mb{i}" for i in range(3)]
    c = [f"mc{i}" for i in range(3)]
    pairs = (
        list(zip(a, a[1:]))
        + list(zip(b, b[1:]))
        + [(b[-1], b[0])]
        + [(c[0], c[1]), (c[1], c[2]), (c[2], c[0])]
        + [(c[0], a[0])]
    )
    return {
        "nodes": a + b + c,
        "edges": _edges(pairs),
        "seeds": ["ma0", "mb0"],
        "shape": "a path, a 3-cycle and a 3-cycle that feeds the path",
    }


def _tie_heavy() -> dict:
    # Two identical branches and a symmetric ring: equal PageRank scores and equal
    # label counts wherever the structure is symmetric.
    ring = [f"t{i}" for i in range(6)]
    pairs = [(ring[i], ring[(i + 1) % 6]) for i in range(6)] + [(ring[i], ring[(i - 1) % 6]) for i in range(6)]
    pairs += [("tl", "tx"), ("tr", "tx"), ("tx", "t0")]
    return {
        "nodes": ring + ["tl", "tr", "tx"],
        "edges": _edges(pairs),
        "seeds": ["tl"],
        "shape": "a symmetric 6-ring with two identical feeder branches",
    }


GRAPHS: Dict[str, dict] = {
    "path": _path(),
    "star": _star(),
    "dense": _dense(),
    "multi_component": _multi_component(),
    "tie_heavy": _tie_heavy(),
}

FIXTURE_IDS = list(GRAPHS)


def node_ids(name: str) -> List[str]:
    return list(GRAPHS[name]["nodes"])
