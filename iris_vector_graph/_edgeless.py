"""Nodes with no edge to another node, in every algorithm's answer (4.6.0 R306).

The algorithms read the adjacency (``^KG``, ``^NKG``, the Arno library's upload of it), which holds no node without
an edge, and the symmetrised adjacency of triangles, k-core and Leiden holds no node whose only edge is a self-loop.
Before R306 such nodes were in no answer: PageRank summed to 1 over the nodes with an edge, ``n`` of degree and
classical closeness counted only them, and components, communities, k-core and triangles had no row for them.

`loners` reads the graph's nodes that have no edge to another node; the ``complete_*`` functions add the rows the
documented answer gives them to an answer read with no top-k cut, then rank and cut it. A route that already listed
a node keeps its row. ``Graph.KG.Edgeless`` does the same in IRIS for the Cypher procedures.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from iris_vector_graph._collation import collation_key, rank_key


def loners(conn, nodes_table: str, edges_table: str, graph: Optional[str] = "") -> Dict[str, bool]:
    """``{node_id: has_self_loop}`` for each node of ``graph`` (``""``: the default graph) with no edge to or from
    another node: ``False`` for a node with no edge at all, ``True`` for one whose only edges are self-loops."""
    gid = graph or ""
    sql = (
        f"SELECT n.node_id, CASE WHEN EXISTS (SELECT 1 FROM {edges_table} x WHERE x.s = n.node_id "
        f"AND COALESCE(x.graph_id, '') = ?) THEN 1 ELSE 0 END "
        f"FROM {nodes_table} n WHERE COALESCE(n.graph_id, '') = ? "
        f"AND NOT EXISTS (SELECT 1 FROM {edges_table} e WHERE e.s = n.node_id AND e.o_id <> n.node_id "
        f"AND COALESCE(e.graph_id, '') = ?) "
        f"AND NOT EXISTS (SELECT 1 FROM {edges_table} e WHERE e.o_id = n.node_id AND e.s <> n.node_id "
        f"AND COALESCE(e.graph_id, '') = ?)"
    )
    cur = conn.cursor()
    try:
        cur.execute(sql, [gid, gid, gid, gid])
        return {str(r[0]): bool(int(r[1] or 0)) for r in cur.fetchall()}
    finally:
        cur.close()


def edgeless(found: Dict[str, bool]) -> List[str]:
    """The nodes of `loners` with no edge at all, in collation order."""
    return sorted((v for v, loop in found.items() if not loop), key=collation_key)


def ranked(rows: Sequence[list], top_k: int) -> List[list]:
    """``rows`` (``[id, value, ...]``) highest value first, ties by id in IRIS collation, then the first ``top_k``."""
    out = sorted((list(r) for r in rows), key=lambda r: rank_key(r[0], r[1]))
    return out[:top_k] if top_k > 0 else out


def _absent(rows: Sequence[list], add: Sequence[str]) -> List[str]:
    """The nodes of ``add`` with no row in ``rows``."""
    have = {r[0] for r in rows}
    return [v for v in add if v not in have]


def complete_scored(rows: Sequence[list], add: Sequence[str], zero: list, top_k: int) -> List[list]:
    """``rows`` with a row ``[id] + zero`` for each node of ``add`` not in them, ranked and cut."""
    return ranked(list(rows) + [[v] + list(zero) for v in _absent(rows, add)], top_k)


def complete_degree(rows: Sequence[list], add: Sequence[str], top_k: int) -> List[list]:
    """Degree rows ``[id, score, degree]``, every node with an edge (no cut): a 0 row for each node of ``add``, and
    every score degree / (n - 1), n now every node (the route's n counted only the nodes with an edge)."""
    full = [list(r) for r in rows] + [[v, 0.0, 0] for v in _absent(rows, add)]
    norm = 1.0 / max(len(full) - 1, 1)
    return ranked([[v, d * norm, d] for v, _s, d in full], top_k)


def complete_closeness(rows: Sequence[list], add: Sequence[str], formula: str, top_k: int) -> List[list]:
    """Closeness rows ``[id, score]``: a 0 for each node of ``add``; classical closeness is 0 for every node once a
    node has no edge, since then no node reaches all n - 1 others."""
    missing = _absent(rows, add)
    full = [list(r) for r in rows] + [[v, 0.0] for v in missing]
    if formula == "classical" and missing and len(full) > 1:
        full = [[v, 0.0] for v, _ in full]
    return ranked(full, top_k)


def complete_groups(rows: Sequence[list], add: Sequence[str], top_k: int) -> List[list]:
    """Community or component rows ``[id, group, size]`` (group 0 the largest): each node of ``add`` not in them a
    group of its own, numbered after the others, then the first ``top_k`` rows."""
    out = [list(r) for r in rows]
    nxt = max((int(r[1]) for r in out), default=-1) + 1
    for v in _absent(rows, add):
        out.append([v, nxt, 1])
        nxt += 1
    return out[:top_k] if top_k > 0 else out


def complete_labels(labels: Dict[str, object], add: Sequence[str]) -> Dict[str, object]:
    """``{node: label}`` (WCC, CDLP) with each node of ``add`` labelled with itself, keys in collation order."""
    out = dict(labels)
    for v in add:
        out.setdefault(v, v)
    return dict(sorted(out.items(), key=lambda kv: collation_key(kv[0])))
