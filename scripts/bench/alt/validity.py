"""Answer validity of the bench.py traversal parity rows: a timing is only evidence of parity if the query returned an answer.

`bench.py` records `result_count` and `errors` per query and path, but the harness only parsed its latencies, so a leg that
returned nothing, or failed every call, counted as parity. This reads the raw JSON of each run and classifies every leg.

    python -m scripts.bench.alt.validity docs/performance/alt-bench/results/final-1
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def bench_q_rows(results_dir):
    legs = defaultdict(lambda: {"A": {"counts": [], "errors": []}, "B": {"counts": [], "errors": []}})
    for out in sorted(Path(results_dir).glob("bench-q1q6-*_?_*.out")):
        side = out.name.split("_")[1]
        for path in out.glob("bench_*.json"):
            for r in json.loads(path.read_text()).get("results", []):
                leg = legs[(r.get("dataset"), r.get("query"), r.get("path"))][side]
                leg["counts"].append(r.get("result_count"))
                leg["errors"].append(r.get("errors") or 0)
    rows = []
    for (ds, q, p), sides in sorted(legs.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        a, b = sides["A"], sides["B"]
        ca = statistics.median([c for c in a["counts"] if c is not None] or [None]) if a["counts"] else None
        cb = statistics.median([c for c in b["counts"] if c is not None] or [None]) if b["counts"] else None
        ea, eb = sum(a["errors"]), sum(b["errors"])
        if ca is not None and cb is not None and ca != cb:
            status = "answers differ"
        elif ea and eb:
            status = "errors on both sides"
        elif ea or eb:
            status = "errors on one side"
        elif not ca and not cb:
            status = "empty on both sides"
        else:
            status = "real"
        rows.append({"dataset": ds, "query": q, "path": p, "count_A": ca, "count_B": cb, "errors_A": ea, "errors_B": eb, "status": status})
    return rows


def render(rows, only_problems=False):
    out = ["| dataset | query | path | answer rows A | answer rows B | failed calls A / B | status |", "|---|---|---|---:|---:|---|---|"]
    for r in rows:
        if only_problems and r["status"] == "real":
            continue
        out.append(f"| {r['dataset']} | {r['query']} | {r['path']} | {r['count_A']} | {r['count_B']} | {r['errors_A']} / {r['errors_B']} | {r['status']} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    for d in (argv or sys.argv[1:]):
        print(f"## {d}\n")
        print(render(bench_q_rows(d)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
