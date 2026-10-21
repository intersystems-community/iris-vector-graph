#!/usr/bin/env python3
"""Give an IVG DRKG load the competitors' node model, so the DRKG suite's queries run unchanged on all three engines.

`load.py` gives every Neo4j and Memgraph DRKG node the label `Entity` beside its entity type, and an `id` property.
`scripts/load_drkg.py` (the bulk path) gives an IVG node its entity type alone and no `id` property row, which
`create_node` would have written. The suite's `MATCH (s:Entity {id: $x})` therefore matched nothing in IVG. This adds
both, for every node that lacks them (idempotent). `^KG("label")` follows the label rows (spec 252).

    python scripts/bench/competitors/ivg_drkg_align.py --port 31972 --namespace DRKGB
"""

from __future__ import annotations

import argparse
import time

import iris

# (what, the rows that hold it already, the insert, its guard against a row already there). The guard is a correlated
# NOT EXISTS, which IRIS runs at about 17 ms a row here (97,238 rows in 14 minutes; the plain insert wrote 5,000 in
# 0.07 s), so it is used only for a partial state; a statement that long also outlives an idle OrbStack connection.
STEPS = [
    (
        "Entity label",
        "SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'Entity'",
        "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) SELECT n.graph_id, n.node_id, 'Entity' FROM Graph_KG.nodes n",
        " WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels l WHERE l.s = n.node_id AND l.graph_id = n.graph_id AND l.label = 'Entity')",
    ),
    (
        "id property",
        "SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE \"key\" = 'id'",
        "INSERT INTO Graph_KG.rdf_props (graph_id, s, \"key\", val) SELECT n.graph_id, n.node_id, 'id', n.node_id FROM Graph_KG.nodes n",
        " WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props p WHERE p.s = n.node_id AND p.graph_id = n.graph_id AND p.\"key\" = 'id')",
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=31972)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--user", default="_SYSTEM")
    ap.add_argument("--password", default="SYS")
    args = ap.parse_args()
    conn = iris.connect(args.host, args.port, args.namespace, args.user, args.password)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
    nodes = cur.fetchone()[0]
    for name, held_sql, insert_sql, guard in STEPS:
        cur.execute(held_sql)
        held = cur.fetchone()[0]
        if held >= nodes:
            print(f"{name}: every node has it ({held})")
            continue
        t0 = time.perf_counter()
        cur.execute(insert_sql + (guard if held else ""))
        conn.commit()
        print(f"{name}: {cur.rowcount} rows in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
