"""Map competitor results onto the harness's suite ids and metric names.

The competitor runners (scripts/bench/competitors) write one row per query:
``{engine, suite, query, metric: "latency_ms", values_ms, params: {dataset}}``. ``compare.py`` wants
``competitor_<engine>_<suite id>.jsonl`` with ``{engine, suite, metric, values_ms}``, where ``metric`` is a metric name
the IVG suite itself reports and ``values_ms`` is one number (the competitor's p50 or p90 for the metric's percentile).

Only rows with an IVG counterpart are mapped (``bench.py`` Q1 to Q6 and the spec-193 hop counts); algorithm legs and
other rows are listed under ``_unmatched`` and are compared through the suites that already carry their own competitor
legs (head-to-head, Leiden four-way, betweenness).

Usage: python -m scripts.bench.alt.competitor_adapter RESULTS_DIR COMPETITOR.jsonl [...]
The IVG metric names are read from the side A files of RESULTS_DIR.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def percentile(values, q):
    """Linear-interpolated percentile (the numpy default) of a non-empty list."""
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo), 6)


def _q(metric):
    if "p90" in metric:
        return 90
    return 50


def _targets(row, a_metrics):
    """(suite id, [metric names]) for a competitor row, or None when the IVG suites have no counterpart."""
    suite, query = row["suite"], row["query"]
    dataset = (row.get("params") or {}).get("dataset") or ""
    if suite == "bench_q" and re.fullmatch(r"Q[1-6]", query) and dataset in ("S", "M", "L"):
        rx = re.compile(rf"^{dataset}\.{query}\.[^.]+\.hot_p(50|90)_ms$")
        return f"bench-q1q6-{dataset.lower()}", [m for m in a_metrics if rx.match(m)]
    m = re.fullmatch(r"B193_h(\d+)", query)
    if suite == "bench193" and m and dataset == "S":
        rx = re.compile(rf"^s1\.S\.hops\.{m.group(1)}\.(direct|orig)\.p(50|90)$")
        return "bench-193-full-s", [k for k in a_metrics if rx.match(k)]
    return None


def adapt(rows, a_metrics):
    out = defaultdict(list)
    unmatched = set()
    for row in rows:
        out[row["engine"]]  # make the engine appear even when nothing maps
        target = _targets(row, a_metrics)
        if not target or not target[1]:
            unmatched.add(f'{row["suite"]}/{row["query"]}')
            continue
        suite, metrics = target
        for metric in metrics:
            out[row["engine"]].append(
                {
                    "engine": row["engine"],
                    "suite": suite,
                    "metric": metric,
                    "values_ms": [percentile(row["values_ms"], _q(metric))],
                }
            )
    result = dict(out)
    result["_unmatched"] = sorted(unmatched)
    return result


def write(adapted, results_dir):
    results_dir = Path(results_dir)
    by_file = defaultdict(list)
    for engine, rows in adapted.items():
        if engine == "_unmatched":
            continue
        for r in rows:
            by_file[(engine, r["suite"])].append(r)
    for (engine, suite), rows in by_file.items():
        path = results_dir / f"competitor_{engine}_{suite}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")


def _a_metrics(results_dir):
    names = {}
    for p in sorted(Path(results_dir).glob("*_A_1.json")):
        names.update(json.loads(p.read_text()).get("metrics", {}))
    return names


def main(argv):
    results_dir, files = argv[0], argv[1:]
    rows = [json.loads(line) for f in files for line in open(f, encoding="utf-8") if line.strip()]
    adapted = adapt(rows, _a_metrics(results_dir))
    write(adapted, results_dir)
    print("unmatched (compared through their own suites):", ", ".join(adapted["_unmatched"]) or "none")


if __name__ == "__main__":
    main(sys.argv[1:])
