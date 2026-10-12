#!/usr/bin/env python3
"""Reproduce the slow, silent handling of rejected rows in `cursor.executemany` (spec 250, CATALOG.md 1.4).

Creates ``--nodes`` nodes in a harness namespace, then inserts ``--rows`` edges into ``Graph_KG.rdf_edges`` in one
`executemany`. With ``--violate none`` every row is valid; ``duplicate`` repeats the first row at the end (unique
constraint); ``fk`` ends with an edge to a node that does not exist (foreign key). A single `execute` of such a row raises
`IntegrityError` at once; ``fk-many`` makes every row past ``--nodes`` fail the foreign key. The script aborts with a stack dump after ``--timeout`` seconds and cleans up.

    python scripts/bench/alt/repro_executemany_hang.py --rows 20 --violate duplicate --timeout 20
"""

from __future__ import annotations

import argparse
import faulthandler
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.bench.alt import sides as sd  # noqa: E402

PREFIX = "zzhang"


def edge_rows(rows: int, nodes: int, violate: str):
    out = [[f"{PREFIX}{i % nodes}", "REL", f"{PREFIX}{(i + 1) % nodes}"] for i in range(rows)]
    if violate == "fk-many":
        # Source ids run past the nodes that exist, so every row beyond `nodes` fails the foreign key.
        out = [[f"{PREFIX}{i}", "REL", f"{PREFIX}{(i * 7 + 1) % nodes}"] for i in range(rows)]
    elif violate == "duplicate":
        out.append(list(out[0]))
    elif violate == "fk":
        out.append([f"{PREFIX}0", "REL", f"{PREFIX}missing"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows", type=int, default=20)
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--violate", choices=["none", "duplicate", "fk", "fk-many"], default="none")
    ap.add_argument("--namespace", default="IVGBENCHA")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args(argv)
    ns = sd.check_namespace(args.namespace)
    rows = edge_rows(args.rows if args.violate == "fk-many" else min(args.rows, args.nodes), args.nodes, args.violate)

    import iris

    conn = iris.connect(sd.HOST, sd.PORT, ns, sd.USER, sd.PASSWORD)
    cur = conn.cursor()
    try:
        cur.executemany("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [[f"{PREFIX}{i}"] for i in range(args.nodes)])
        conn.commit()
        faulthandler.dump_traceback_later(args.timeout, exit=True)
        t0 = time.perf_counter()
        try:
            cur.executemany("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", rows)
            conn.commit()
            print(f"executemany of {len(rows)} rows ({args.violate}) returned in {time.perf_counter() - t0:.3f} s")
        except Exception as exc:
            conn.rollback()
            print(f"executemany of {len(rows)} rows ({args.violate}) raised after {time.perf_counter() - t0:.3f} s: {type(exc).__name__}")
        faulthandler.cancel_dump_traceback_later()
    finally:
        for stmt in (
            f"DELETE FROM Graph_KG.rdf_edges WHERE s LIKE '{PREFIX}%'",
            f"DELETE FROM Graph_KG.nodes WHERE node_id LIKE '{PREFIX}%'",
        ):
            try:
                cur.execute(stmt)
                conn.commit()
            except Exception as exc:
                print(f"cleanup failed: {stmt}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
