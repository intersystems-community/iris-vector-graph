"""Shared helpers for spec 213 ledger integration tests (T005).

`canonical_tables` reads the governed structural graph directly from the
canonical SQL tables, excluding the reserved pseudo-properties `id` and
`__graph` (FR-014b), so tests can compare the materialized view to ledger
reconstructions without going through the ledger itself.
"""

from __future__ import annotations

from typing import Dict, Set, Tuple

from iris_vector_graph.engine import IRISGraphEngine

RESERVED = {"id", "__graph"}


def make_engine(conn, **kw) -> IRISGraphEngine:
    kw.setdefault("embedding_dimension", 4)
    return IRISGraphEngine(conn, **kw)


def seed_graph(
    engine: IRISGraphEngine, n_nodes: int, n_rels: int, prefix: str = "n"
) -> Tuple[list, list]:
    """Deterministic seed via legacy APIs: nodes n0..n{k-1}, rels n{i}-R->n{i+1}."""
    nodes = [f"{prefix}{i}" for i in range(n_nodes)]
    for i, nid in enumerate(nodes):
        engine.create_node(nid, labels=["Seed"], properties={"idx": str(i)})
    rels = []
    for i in range(min(n_rels, max(n_nodes - 1, 0))):
        engine.create_edge(nodes[i], "R", nodes[i + 1])
        rels.append((nodes[i], "R", nodes[i + 1], None))
    return nodes, rels


def canonical_tables(conn, schema: str = "Graph_KG") -> Dict[str, object]:
    """Snapshot of the governed graph: nodes → (labels, props); rels → quals.

    Returns {"nodes": {id: {"labels": set, "props": dict}},
             "rels": {(s, p, o, graph_or_None): {"quals": dict}}}
    """
    import json

    cur = conn.cursor()
    try:
        nodes: Dict[str, dict] = {}
        cur.execute(f"SELECT node_id FROM {schema}.nodes")
        for (nid,) in cur.fetchall():
            nodes[nid] = {"labels": set(), "props": {}}
        cur.execute(f"SELECT s, label FROM {schema}.rdf_labels")
        for s, label in cur.fetchall():
            nodes.setdefault(s, {"labels": set(), "props": {}})["labels"].add(label)
        cur.execute(f'SELECT s, "key", val FROM {schema}.rdf_props')
        for s, key, val in cur.fetchall():
            if key in RESERVED:
                continue
            nodes.setdefault(s, {"labels": set(), "props": {}})["props"][key] = val
        rels: Dict[Tuple, dict] = {}
        cur.execute(f"SELECT s, p, o_id, qualifiers, graph_id FROM {schema}.rdf_edges")
        for s, p, o, quals, graph in cur.fetchall():
            q: dict = {}
            if quals:
                try:
                    parsed = json.loads(quals) if isinstance(quals, str) else dict(quals)
                    q = {k: str(v) for k, v in parsed.items()}
                except Exception:
                    q = {}
            rels[(s, p, o, graph if graph not in ("", None) else None)] = {"quals": q}
        return {"nodes": nodes, "rels": rels}
    finally:
        cur.close()


def rel_keys(conn, schema: str = "Graph_KG") -> Set[Tuple]:
    return set(canonical_tables(conn, schema)["rels"].keys())
