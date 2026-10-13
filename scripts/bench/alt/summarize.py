"""Verdict counts and the slower metrics, straight from harness result directories (docs/performance/alt-bench/PROTOCOL.md).

Usage: python -m scripts.bench.alt.summarize RESULTS_DIR [RESULTS_DIR ...] > summary.md

Verdict rule (README and PROTOCOL): B/A is the ratio of medians over reps. parity when B/A <= 1.10 or B's median lies in
A's [min, max]; faster when B/A < 0.90 and B's median is below A's min; otherwise slower. The call-overhead floor: a metric that
would be slower but differs by no more than `floor_ms` (0.25 microseconds) is "parity (floor)". Every number is kept.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

FLOOR_MS = 0.00025


def _load(results_dir):
    runs = defaultdict(lambda: {"A": [], "B": []})
    for path in sorted(Path(results_dir).glob("*_?_*.json")):
        if path.name == "manifest.json" or path.name.startswith("competitor_"):
            continue
        data = json.loads(path.read_text())
        if data.get("status") == "failed" or "side" not in data:
            continue
        for metric, value in (data.get("metrics") or {}).items():
            if isinstance(value, (int, float)):
                runs[(data["suite"], metric)][data["side"]].append(float(value))
    return runs


def compare_run(results_dir, floor_ms=FLOOR_MS):
    rows = []
    for (suite, metric), sides in sorted(_load(results_dir).items()):
        a, b = sides["A"], sides["B"]
        if not a or not b:
            continue
        ma, mb = statistics.median(a), statistics.median(b)
        if ma == 0:
            continue
        ratio = mb / ma
        if ratio < 0.90 and mb < min(a):
            verdict = "faster"
        elif ratio <= 1.10 or min(a) <= mb <= max(a):
            verdict = "parity"
        else:
            verdict = "slower"
        if verdict == "slower" and abs(mb - ma) <= floor_ms:
            verdict = "parity (floor)"
        rows.append(
            {
                "suite": suite,
                "metric": metric,
                "a": ma,
                "b": mb,
                "ratio": ratio,
                "delta": mb - ma,
                "verdict": verdict,
                "a_reps": a,
                "b_reps": b,
            }
        )
    return rows


def render(runs):
    out = ["| run | metrics | faster | parity | parity (floor) | slower |", "|---|---:|---:|---:|---:|---:|"]
    for name, rows in runs.items():
        counts = defaultdict(int)
        for r in rows:
            counts[r["verdict"]] += 1
        out.append(
            f"| {name} | {len(rows)} | {counts['faster']} | {counts['parity']} | {counts['parity (floor)']} | {counts['slower']} |"
        )
    for name, rows in runs.items():
        slow = [r for r in rows if r["verdict"] == "slower"]
        if not slow:
            continue
        out += ["", f"### {name}: slower metrics", "", "| suite | metric | A median | B median | B/A | B-A | A reps | B reps |", "|---|---|---:|---:|---:|---:|---|---|"]
        for r in sorted(slow, key=lambda r: (r["suite"], -r["ratio"])):
            reps_a = ", ".join(f"{v:g}" for v in r["a_reps"])
            reps_b = ", ".join(f"{v:g}" for v in r["b_reps"])
            out.append(
                f"| {r['suite']} | {r['metric']} | {r['a']:g} | {r['b']:g} | {r['ratio']:.2f} | {r['delta']:+.3g} | {reps_a} | {reps_b} |"
            )
    return "\n".join(out) + "\n"


def main(argv):
    runs = {Path(d).name: compare_run(d) for d in argv}
    sys.stdout.write(render(runs))


if __name__ == "__main__":
    main(sys.argv[1:])
