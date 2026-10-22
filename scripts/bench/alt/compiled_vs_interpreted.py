"""Spec 257 US2: the SNB queries with native plans compiled (Graph.KG.Cypher.Gen "sync") against interpreted ("off").

One process, one engine, the same parameter sets for both; each query's median over the sets of the best of
`--repeat` runs. Answers are compared row for row. Usage:

    python -m scripts.bench.alt.compiled_vs_interpreted --ns SNB1B --params <query_ivg_native.json> [--sets 8] [--out f.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time

import iris

from iris_vector_graph.engine import IRISGraphEngine
from scripts.bench.alt.snb_queries import QUERIES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="SNB1B")
    ap.add_argument("--port", type=int, default=31972)
    ap.add_argument("--params", required=True, help="a run's JSON whose 'params' list holds the parameter sets")
    ap.add_argument("--sets", type=int, default=8)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated query-name prefixes")
    ap.add_argument("--skip", default="", help="comma-separated query-name prefixes to leave out")
    ap.add_argument("--parallel-min", type=int, default=None, help="the engine's native_parallel_min (-1: one process)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    logging.getLogger("iris_vector_graph").setLevel(logging.ERROR)
    conn = iris.connect("localhost", a.port, a.ns, "_SYSTEM", "SYS")
    native = iris.createIRIS(conn)
    engine = IRISGraphEngine(conn)
    engine.native_route_ms = 0
    if a.parallel_min is not None:
        engine.native_parallel_min = a.parallel_min
    with open(a.params) as f:
        sets = json.load(f)["params"][: a.sets]
    only = tuple(x for x in a.only.split(",") if x)
    skip = tuple(x for x in a.skip.split(",") if x)
    out = {}
    for name, q in QUERIES.items():
        if (only and not name.startswith(only)) or (skip and name.startswith(skip)):
            continue
        cypher = q.get("ivg", q["cypher"])
        times = {"off": [], "sync": []}
        same = 0
        for ps in sets:
            params = {k: ps[k] for k in q["params"] if k in ps}
            rows = {}
            for mode in ("off", "sync"):
                native.classMethodValue("Graph.KG.Cypher.Gen", "Mode", mode)
                best = 1e9
                for _ in range(a.repeat):
                    t = time.perf_counter()
                    r = engine.execute_cypher(cypher, params)
                    best = min(best, time.perf_counter() - t)
                if getattr(r, "error", None):
                    best = float("nan")
                times[mode].append(best * 1000)
                rows[mode] = [list(map(str, x)) for x in (r.rows or [])]
            same += rows["off"] == rows["sync"]
        off, on = statistics.median(times["off"]), statistics.median(times["sync"])
        speedup = off / on if on else None
        out[name] = {"interpreted_ms": off, "compiled_ms": on, "speedup": speedup, "same": f"{same}/{len(sets)}"}
        print(f"{name:34s} interpreted {off:9.2f} ms  compiled {on:9.2f} ms  x{speedup or 0:5.2f}  same {same}/{len(sets)}", flush=True)
    native.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
