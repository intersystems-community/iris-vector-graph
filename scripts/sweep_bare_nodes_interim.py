#!/usr/bin/env python3
"""Delete the bare nodes of one graph on IVG 4.4.1 / 4.4.2, which have no ``erase_bare_nodes``.

A node is BARE when no edge names it (as source or target) and it has no label, property, document or embedding.
A temporal workload leaves one per distinct target, because a purge retracts the structural edge and keeps the nodes.

Why not ``Graph.KG.Eraser.EraseNodeIds`` (``engine.delete_nodes``)? It deletes the node and EVERY row naming it,
unconditionally, and the temporal data of that id too. Check-then-erase races a writer: an edge asserted between the
check and the erase is deleted with the node. This script's statement is a plain ``DELETE FROM Graph_KG.nodes``:

* the foreign keys ``fk_edges_source``, ``fk_edges_dest``, ``fk_labels_node`` and the embedding tables' own refuse it
  (SQLCODE -124) while a dependant exists, including an edge another connection has written and not committed; that
  node is counted as kept, and nothing is lost;
* ``rdf_props`` and ``docs`` have no foreign key, so the statement itself says ``NOT EXISTS`` for them.

A writer that inserted a node and has not yet written its edge, and loses to this DELETE, gets a foreign-key error on its
edge INSERT: loud, retryable. ``--min-age-seconds`` keeps the sweep off such young nodes altogether.

Derived state: nothing in ``^KG`` names a bare node except ``gen``/``degc`` of a source whose last edge was retracted
(a few bytes; killed here), so the only thing to do is to move ``^KG("__version")`` so that cached answers are re-asked.

Usage (batch of 5000, default graph, count only first)::

    python scripts/sweep_bare_nodes_interim.py --host H --port P --namespace USER --graph "" --dry-run
    python scripts/sweep_bare_nodes_interim.py --host H --port P --namespace USER --graph "" --batch 5000

The tables hold a node as (node_id, graph_id); the default graph is graph_id ''.
"""

from __future__ import annotations

import argparse
import sys

BARE = (
    "NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges x WHERE x.s = n.node_id AND COALESCE(x.graph_id, '') = COALESCE(n.graph_id, '')) "
    "AND NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges x WHERE x.o_id = n.node_id AND COALESCE(x.graph_id, '') = COALESCE(n.graph_id, '')) "
    "AND NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels x WHERE x.s = n.node_id AND COALESCE(x.graph_id, '') = COALESCE(n.graph_id, '')) "
    "AND NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props x WHERE x.s = n.node_id AND COALESCE(x.graph_id, '') = COALESCE(n.graph_id, '')) "
    "AND NOT EXISTS (SELECT 1 FROM Graph_KG.docs x WHERE x.id = n.node_id AND COALESCE(x.graph_id, '') = COALESCE(n.graph_id, '')) "
    "AND NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings x WHERE x.node_id = n.node_id AND COALESCE(x.graph_id, '') = COALESCE(n.graph_id, '')) "
    "AND NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings_optimized x WHERE x.node_id = n.node_id AND COALESCE(x.graph_id, '') = COALESCE(n.graph_id, ''))"
)
# a routed embedding table (kg_emb_*) has a foreign key too, so a node it holds is refused by the DELETE (-124) and kept


def sweep(conn, graph: str = "", batch: int = 5000, min_age_seconds: int = 0, dry_run: bool = False,
          limit: int = 0) -> dict:
    """Returns {"bare": found, "erased": removed, "kept": refused by a foreign key}. ``limit`` caps ``erased``."""
    age = ""
    if min_age_seconds > 0:
        age = f" AND (n.created_at IS NULL OR n.created_at <= DATEADD(ss, -{int(min_age_seconds)}, CURRENT_TIMESTAMP))"
    where = f"FROM Graph_KG.nodes n WHERE COALESCE(n.graph_id, '') = COALESCE(?, '') AND {BARE}{age}"
    cur = conn.cursor()
    if dry_run:
        cur.execute(f"SELECT COUNT(*) {where}", [graph])
        return {"bare": cur.fetchone()[0], "erased": 0, "kept": 0}
    cur.execute(f"SELECT n.node_id {where}", [graph])
    ids = [r[0] for r in cur.fetchall()]
    if limit:
        ids = ids[:limit]
    erased = kept = 0
    delete = f"DELETE FROM Graph_KG.nodes AS n WHERE n.node_id = ? AND COALESCE(n.graph_id, '') = COALESCE(?, '') AND {BARE}"
    for start in range(0, len(ids), batch):
        for node_id in ids[start:start + batch]:
            try:
                cur.execute(delete, [node_id, graph])
                erased += max(cur.rowcount, 0)
            except Exception as exc:  # a dependant appeared since the scan: the node stays
                if "<-124>" in str(exc):
                    kept += 1
                else:
                    conn.rollback()
                    raise
        conn.commit()
    if erased:
        import iris

        native = iris.createIRIS(conn)
        native.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")
    return {"bare": len(ids), "erased": erased, "kept": kept}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1972)
    ap.add_argument("--namespace", default="USER")
    ap.add_argument("--user", default="_SYSTEM")
    ap.add_argument("--password", default="SYS")
    ap.add_argument("--graph", default="", help="graph_id; '' is the default graph")
    ap.add_argument("--batch", type=int, default=5000, help="deletes per commit")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many (0: all)")
    ap.add_argument("--min-age-seconds", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    import iris.dbapi as dbapi

    conn = dbapi.connect(hostname=a.host, port=a.port, namespace=a.namespace, username=a.user, password=a.password)
    print(sweep(conn, a.graph, a.batch, a.min_age_seconds, a.dry_run, a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
