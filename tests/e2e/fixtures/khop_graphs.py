"""Spec 164 — Test fixture builders for k-hop seed-local fast path.

Each builder returns a dict with:
    nodes: list of node IDs (strings)
    edges: list of (s, p, o) tuples — source, predicate, target
    expected_1hop: dict[seed_id -> set of 1-hop reachable node IDs]
    expected_2hop: dict[seed_id -> set of 2-hop reachable node IDs]

Used by AS-164-3 (set-equality vs Cypher path) and the multi-predicate dedup
test (T008b) per FR-164-003.
"""
from typing import Dict, List, Set, Tuple


def make_chain(n: int = 10, predicate: str = "NEXT") -> Dict:
    """Linear chain: node_0 -> node_1 -> ... -> node_{n-1}.

    Each node has exactly one out-edge to its successor (last has none).
    Useful for clean BFS layer testing — node_0's 1-hop = {node_1}, 2-hop = {node_2}.
    """
    nodes = [f"chain_node_{i}" for i in range(n)]
    edges = [(nodes[i], predicate, nodes[i + 1]) for i in range(n - 1)]
    expected_1hop = {nodes[i]: {nodes[i + 1]} for i in range(n - 1)}
    expected_1hop[nodes[n - 1]] = set()
    expected_2hop = {nodes[i]: {nodes[i + 2]} if i + 2 < n else set() for i in range(n)}
    return {
        "nodes": nodes,
        "edges": edges,
        "expected_1hop": expected_1hop,
        "expected_2hop": expected_2hop,
    }


def make_fork(n_leaves: int = 5, predicate: str = "FORK") -> Dict:
    """1 source + n_leaves leaves, each with a single edge from source.

    Useful for high-fanout 1-hop testing — fork_root's 1-hop = all n leaves.
    """
    root = "fork_root"
    leaves = [f"fork_leaf_{i}" for i in range(n_leaves)]
    nodes = [root] + leaves
    edges = [(root, predicate, leaf) for leaf in leaves]
    expected_1hop = {root: set(leaves)}
    expected_1hop.update({leaf: set() for leaf in leaves})
    expected_2hop = {nid: set() for nid in nodes}
    return {
        "nodes": nodes,
        "edges": edges,
        "expected_1hop": expected_1hop,
        "expected_2hop": expected_2hop,
    }


def make_complete(n: int = 4, predicate: str = "EDGE") -> Dict:
    """Complete graph K_n (no self-loops). Every node has out-edges to every other node.

    Useful for dedup testing — every node's 2-hop = all other nodes (deduped via
    `^||khop_seen`).
    """
    nodes = [f"k_node_{i}" for i in range(n)]
    edges = [
        (nodes[i], predicate, nodes[j])
        for i in range(n)
        for j in range(n)
        if i != j
    ]
    expected_1hop = {nodes[i]: set(nodes[j] for j in range(n) if j != i) for i in range(n)}
    expected_2hop = {nodes[i]: set(nodes[j] for j in range(n) if j != i) for i in range(n)}
    return {
        "nodes": nodes,
        "edges": edges,
        "expected_1hop": expected_1hop,
        "expected_2hop": expected_2hop,
    }


def make_multi_predicate_dedup(predicate_a: str = "EDGE_A", predicate_b: str = "EDGE_B") -> Dict:
    """Two-node graph where target is reachable from seed via TWO different predicates.

    Used by T008b to verify FR-164-003 dedup-per-node: when `predicate=""` (all
    predicates), `target` must appear in result rows EXACTLY ONCE despite being
    reachable via both EDGE_A and EDGE_B.
    """
    seed = "mpd_seed"
    target = "mpd_target"
    nodes = [seed, target]
    edges = [(seed, predicate_a, target), (seed, predicate_b, target)]
    expected_1hop = {seed: {target}, target: set()}
    expected_2hop = {seed: set(), target: set()}
    return {
        "nodes": nodes,
        "edges": edges,
        "expected_1hop": expected_1hop,
        "expected_2hop": expected_2hop,
    }
