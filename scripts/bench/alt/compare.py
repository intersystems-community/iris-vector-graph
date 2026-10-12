#!/usr/bin/env python3
"""Compare baseline (A) and alternative (B) benchmark results (spec 250).

Reads a results directory written by run_matrix.py::

    <suite>_<side>_<rep>.json      {"suite","side","rep","status","metrics":{name: ms}, ...}
    competitor_<engine>_<suite>.jsonl   one JSON object per line:
                                        {"engine","suite","metric","values_ms":[...]}

and prints a markdown report grouped by suite.

Verdict rule (B/A is the ratio of medians; "spread" is A's min..max over its reps):

* ``faster``  when B/A < 0.90 and B's median is below A's spread
* ``parity``  when B/A <= 1.10, or B's median lies inside A's spread
* ``slower``  otherwise
* ``n/a``     when either side has no values
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# A rep whose timing gate or tests failed still measured something; only a hard failure loses its numbers.
COUNTED = ("ok", "tests-failed")
PARITY_MAX = 1.10
FASTER_BELOW = 0.90
SIDES = ("A", "B")


def verdict(a_vals, b_vals):
    """Return (B/A ratio of medians, verdict word)."""
    if not a_vals or not b_vals:
        return float("nan"), "n/a"
    a_med = statistics.median(a_vals)
    b_med = statistics.median(b_vals)
    if a_med == 0:
        ratio = 1.0 if b_med == 0 else float("inf")
    else:
        ratio = b_med / a_med
    lo, hi = min(a_vals), max(a_vals)
    inside = lo <= b_med <= hi
    if ratio < FASTER_BELOW and not inside:
        return ratio, "faster"
    if ratio <= PARITY_MAX or inside:
        return ratio, "parity"
    return ratio, "slower"


def _rep_files(results_dir: Path):
    for p in sorted(results_dir.glob("*.json")):
        if p.name == "manifest.json" or p.name.startswith("competitor_"):
            continue
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and "suite" in doc and "side" in doc and "rep" in doc:
            yield doc


def load_results(results_dir):
    """{suite: {side: {metric: [values over ok reps, in rep order]}}}"""
    out: dict = {}
    docs = sorted(_rep_files(Path(results_dir)), key=lambda d: (d["suite"], d["side"], int(d["rep"])))
    for d in docs:
        if d.get("status", "ok") not in COUNTED:
            continue
        side = out.setdefault(d["suite"], {}).setdefault(d["side"], {})
        for name, val in (d.get("metrics") or {}).items():
            if isinstance(val, (int, float)):
                side.setdefault(name, []).append(val)
    return out


def load_failures(results_dir):
    """[(suite, side, rep)] for every rep that did not finish ok."""
    return [
        (d["suite"], d["side"], int(d["rep"]))
        for d in sorted(_rep_files(Path(results_dir)), key=lambda d: (d["suite"], d["side"], int(d["rep"])))
        if d.get("status", "ok") != "ok"
    ]


def load_failure_details(results_dir):
    """{(suite, side, rep): (status, [failed test ids])} for every rep that is not plainly ok."""
    return {
        (d["suite"], d["side"], int(d["rep"])): (d.get("status", "ok"), d.get("failed_tests", []))
        for d in _rep_files(Path(results_dir))
        if d.get("status", "ok") != "ok"
    }


def load_competitors(results_dir):
    """{suite: {metric: {engine: [values_ms]}}} from competitor_*.jsonl (bad lines are skipped)."""
    out: dict = {}
    for p in sorted(Path(results_dir).glob("competitor_*.jsonl")):
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not all(k in row for k in ("engine", "suite", "metric", "values_ms")):
                continue
            vals = [v for v in row["values_ms"] if isinstance(v, (int, float))]
            if not vals:
                continue
            out.setdefault(row["suite"], {}).setdefault(row["metric"], {}).setdefault(row["engine"], []).extend(vals)
    return out


def _fmt(x):
    if x is None:
        return "n/a"
    if x != x:  # nan
        return "n/a"
    if x == float("inf"):
        return "inf"
    if abs(x) >= 100:
        return f"{x:.0f}"
    if abs(x) >= 10:
        return f"{x:.1f}"
    return f"{x:.3g}"


def _med(vals):
    return statistics.median(vals) if vals else None


def _min(vals):
    return min(vals) if vals else None


def render_report(results_dir, title=None):
    results_dir = Path(results_dir)
    results = load_results(results_dir)
    comps = load_competitors(results_dir)
    failures = load_failures(results_dir)
    lines = [f"# {title or 'Alternative vs baseline: ' + results_dir.name}", ""]
    manifest = results_dir / "manifest.json"
    if manifest.exists():
        try:
            m = json.loads(manifest.read_text())
            sides = m.get("sides", {})
            for s in SIDES:
                if s in sides:
                    lines.append(f"- side {s}: ref `{sides[s].get('ref')}` commit `{sides[s].get('commit')}`")
            lines.append("")
        except ValueError:
            pass
    lines.append(
        "Times in milliseconds. B/A is the ratio of medians. parity: B/A <= 1.10 or B's median inside A's rep spread; "
        "faster: B/A < 0.90 outside the spread; otherwise slower."
    )
    lines.append("")
    counts = {"parity": 0, "faster": 0, "slower": 0, "n/a": 0}
    suites = sorted(set(results) | set(comps))
    for suite in suites:
        sides = results.get(suite, {})
        metrics = sorted(set(sides.get("A", {})) | set(sides.get("B", {})))
        engines = sorted({e for m in comps.get(suite, {}).values() for e in m})
        lines.append(f"## {suite}")
        lines.append("")
        header = ["metric", "A median", "A min", "B median", "B min", "B/A", "verdict"] + [f"{e} median" for e in engines]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] + ["---:"] * 5 + ["---"] + ["---:"] * len(engines)) + "|")
        for m in metrics:
            a = sides.get("A", {}).get(m, [])
            b = sides.get("B", {}).get(m, [])
            ratio, v = verdict(a, b)
            counts[v] += 1
            row = [m, _fmt(_med(a)), _fmt(_min(a)), _fmt(_med(b)), _fmt(_min(b)), _fmt(ratio), v]
            for e in engines:
                row.append(_fmt(_med(comps.get(suite, {}).get(m, {}).get(e, []))))
            lines.append("| " + " | ".join(row) + " |")
        if not metrics:
            lines.append("| (no metrics) | n/a | n/a | n/a | n/a | n/a | n/a |" + " n/a |" * len(engines))
        lines.append("")
    if failures:
        lines.append("## Failed or incomplete reps")
        lines.append("")
        details = load_failure_details(results_dir)
        for suite, side, rep in failures:
            status, tests = details.get((suite, side, rep), ("failed", []))
            kept = "metrics kept" if status in COUNTED else "no metrics"
            extra = f": {', '.join(tests)}" if tests else ""
            lines.append(f"- {suite} side {side} rep {rep}: {status} ({kept}){extra}")
        lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append(", ".join(f"{k}: {v}" for k, v in counts.items()))
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results_dir")
    ap.add_argument("-o", "--output", help="write the report here instead of stdout")
    ap.add_argument("--title")
    args = ap.parse_args(argv)
    text = render_report(args.results_dir, args.title)
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
