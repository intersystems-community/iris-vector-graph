"""Recount the Adjacency Index from the tables, for a set of nodes, and report every difference.

Both directions: an expected entry missing from ^KG, and a ^KG entry with no row behind it (the
spike's first verification passed while entries with no row existed, RISKS R9). Spec 252, T001.
"""

from __future__ import annotations

import json


def _graph_key(native, graph_id):
    return native.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph_id if graph_id is not None else "")


def _weight(quals):
    try:
        w = json.loads(quals).get("weight") if quals else None
    except (ValueError, AttributeError):
        w = None
    return float(w) if w not in (None, "") else 1.0


def _children(native, *subs):
    out, k = [], ""
    while True:
        k = native.nextSubscript(False, *subs, k)
        if k is None or k == "":
            return out
        out.append(k)


def expected(conn, native, nodes):
    """{("out"|"in", g, a, p, b): weight} and {("label", g, label, s)} from the tables, for edges touching nodes."""
    cur = conn.cursor()
    marks = ", ".join("?" * len(nodes))
    cur.execute(
        f"SELECT s, p, o_id, graph_id, qualifiers FROM Graph_KG.rdf_edges WHERE s IN ({marks}) OR o_id IN ({marks})",
        list(nodes) * 2,
    )
    edges = {}
    for s, p, o, gid, quals in cur.fetchall():
        g, w = _graph_key(native, gid), _weight(quals)
        edges[("out", g, s, p, o)] = w
        edges[("in", g, o, p, s)] = w
    cur.execute(f"SELECT s, label, graph_id FROM Graph_KG.rdf_labels WHERE s IN ({marks})", list(nodes))
    labels = {("label", _graph_key(native, gid), label, s) for s, label, gid in cur.fetchall()}
    return edges, labels


def actual(native, nodes, graphs):
    """The same shape read off ^KG for the given nodes in the given graph keys."""
    edges, labels = {}, set()
    for g in graphs:
        for direction in ("out", "in"):
            for a in nodes:
                for p in _children(native, "KG", direction, g, a):
                    for b in _children(native, "KG", direction, g, a, p):
                        edges[(direction, g, a, p, b)] = float(native.get("KG", direction, g, a, p, b) or 1.0)
        for label in _children(native, "KG", "label", g):
            for s in nodes:
                if native.isDefined("KG", "label", g, label, s):
                    labels.add(("label", g, label, s))
    return edges, labels


def differences(conn, nodes, graphs=(0,), check_weights=True):
    """A list of human-readable differences; empty when the index equals the recount."""
    import iris

    native = iris.createIRIS(conn)
    want_e, want_l = expected(conn, native, nodes)
    graphs = set(graphs) | {k[1] for k in want_e} | {k[1] for k in want_l}
    have_e, have_l = actual(native, nodes, graphs)
    # actual() walks from the given nodes only; an expected entry keyed by an endpoint outside them (the out entry of
    # an edge whose target alone was given) is looked up directly instead of being reported missing
    for k in set(want_e) - set(have_e):
        if native.isDefined("KG", *k):
            have_e[k] = float(native.get("KG", *k) or 1.0)
    diffs = [f"missing {k}" for k in sorted(set(want_e) - set(have_e), key=str)]
    diffs += [f"no row behind {k}" for k in sorted(set(have_e) - set(want_e), key=str)]
    if check_weights:
        diffs += [f"weight {k}: {have_e[k]} != {want_e[k]}" for k in set(want_e) & set(have_e) if abs(have_e[k] - want_e[k]) > 1e-9]
    diffs += [f"missing {k}" for k in sorted(want_l - have_l, key=str)]
    diffs += [f"no row behind {k}" for k in sorted(have_l - want_l, key=str)]
    return diffs
