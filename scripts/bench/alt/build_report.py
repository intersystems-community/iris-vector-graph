"""Assemble docs/performance/ALT_VS_BASELINE.md: the tables come from the result files, the prose from the template.

    python -m scripts.bench.alt.build_report > docs/performance/ALT_VS_BASELINE.md
"""

from __future__ import annotations

import glob
import io
import json
import statistics
import sys
from contextlib import redirect_stdout
from pathlib import Path

from scripts.bench.alt import summarize as sm
from scripts.bench.alt import vs_competitors as vc

R = Path("docs/performance/alt-bench/results")
TEMPLATE = Path("docs/performance/alt-bench/REPORT_TEMPLATE.md")


def _capture(fn, *args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


PARITY_RUNS = (
    "core-1", "core-3", "core-4", "confirm-1", "confirm-2", "drift-1",
    "final-1", "final-2", "final-3",
    "migration-9", "breadth-index", "breadth-q1q6-l", "breadth-bidir", "breadth-drkg-full",
)
FHIR_METRICS = ("sync_ms", "coverage_ms", "gaps_ms", "ppr_ms", "ppr_group_ms")


def parity_counts(root=None):
    root = root or R
    runs = {name: sm.compare_run(root / name) for name in PARITY_RUNS if (root / name).is_dir()}
    return sm.render(runs)


def fhir_table(results_dir):
    """FHIR sync/coverage/gaps/PPR timings (scripts/fhir/bench_235.py), median over rounds per side."""
    by = {}
    for path in sorted(glob.glob(str(Path(results_dir) / "fhir_*_?_*.json"))):
        side = Path(path).stem.split("_")[2]
        data = json.loads(Path(path).read_text())
        for metric in FHIR_METRICS:
            if isinstance(data.get(metric), (int, float)):
                by.setdefault((data["set"], metric), {"A": [], "B": []})[side].append(float(data[metric]))
    out = ["| set | metric | baseline median (ms) | alternative median (ms) | B/A | rounds A / B |", "|---|---|---:|---:|---:|---|"]
    for (set_, metric), sides in sorted(by.items()):
        if not sides["A"] or not sides["B"]:
            continue
        a, b = statistics.median(sides["A"]), statistics.median(sides["B"])
        out.append(f"| {set_} | {metric} | {a:.0f} | {b:.0f} | {b / a:.2f} | {len(sides['A'])} / {len(sides['B'])} |")
    return "\n".join(out) + "\n"


MIGRATION_ROWS = ("apply.total", "step.embeddings", "step.docs", "step.edge_vectors", "step.kg_node_stores", "profile.rekey", "profile._reshape")


def migration_table(results_dir):
    """3.x to 4.0.0 upgrade (scripts/bench_migration_4_0_0.py): apply time and its steps, seconds, median over reps."""
    rows = {r["metric"]: r for r in sm.compare_run(results_dir)}
    out = ["| step | baseline s | alternative s | B/A | verdict |", "|---|---:|---:|---:|---|"]
    for metric in MIGRATION_ROWS:
        r = rows.get(metric)
        if r:
            out.append(f"| {metric} | {r['a'] / 1000:.1f} | {r['b'] / 1000:.1f} | {r['ratio']:.2f} | {r['verdict']} |")
    return "\n".join(out) + "\n"


def drkg_table(root=None):
    """Every full-DRKG load (5.87M edges) of either side, pooled: the runs differ more from one another than the sides do."""
    root = Path(root or R)
    totals = {"A": [], "B": []}
    edges = {"A": [], "B": []}
    for path in sorted(glob.glob(str(root / "breadth-drkg-*" / "drkg-load-full_?_*.json"))):
        data = json.loads(Path(path).read_text())
        m = data.get("metrics") or {}
        if data.get("status") == "failed" or "total" not in m:
            continue
        totals[data["side"]].append(m["total"] / 1000)
        if "load_edges" in m:
            edges[data["side"]].append(m["load_edges"] / 1000)
    out = ["| side | runs | total s (min / median / max) | edge load s (median) |", "|---|---:|---|---:|"]
    for side in "AB":
        t = totals[side]
        if t:
            e = statistics.median(edges[side]) if edges[side] else float("nan")
            out.append(f"| {side} | {len(t)} | {min(t):.0f} / {statistics.median(t):.0f} / {max(t):.0f} | {e:.0f} |")
    return "\n".join(out) + "\n"


def _first(*names):
    """The newest run directory that exists (the final-code pass if it was run, else the confirmation pass it replaces)."""
    for name in names:
        if (R / name).is_dir() and any((R / name).glob("*_?_*.json")):
            return R / name
    raise FileNotFoundError(names)


def competitor_tables():
    rows = vc.load_competitors(R / "competitors-1")
    small, medium, large = _first("final-1", "confirm-1"), _first("confirm-2"), _first("breadth-q1q6-l", "core-1")
    ivg = {"S": vc.ivg_medians(small), "M": vc.ivg_medians(medium), "L": vc.ivg_medians(large)}
    parts = [
        "### Traversal: `tests/benchmarks/bench.py` Q1 to Q6\n\n" + vc.render_traversal(vc.traversal_rows(ivg, rows)),
        "### Variable-length paths, spec 193 (`[*1..N]`, dataset S)\n\n" + vc.render_hops(vc.hops_rows(vc.ivg_medians(small), rows)),
        "### Count-style k-hop: `KHopNeighbors` against the `neo4j_comparison` queries (10K nodes, 50K edges)\n\n"
        + vc.render_khop(vc.khop_rows(vc.ivg_medians(_first("final-1", "confirm-2")), rows)),
        "### Algorithms: the head-to-head fixtures\n\n"
        + vc.render_algos(vc.algo_rows(vc.ivg_medians(_first("final-2", "confirm-2")), rows)),
        "### LDBC SNB interactive (KNOWS graph, first 10,000 rows, like for like)\n\n" + ldbc_table(rows),
    ]
    return "\n".join(parts)


def ldbc_table(rows):
    three = vc.ivg_medians(_first("final-1", "core-4"))
    mapping = [("ic2 1-hop knows", "IC2"), ("ic3 2-hop knows", "IC3"), ("ic13 shortestpath", "IC13"), ("count friends", "COUNT_friends")]
    comp = {(r["query"], r["engine"]): statistics.median(r["values_ms"]) for r in rows if r["suite"] == "ldbc" and r["params"].get("dataset") == "LDBC10_10k"}
    out = ["| query | IVG baseline p50 | IVG alternative p50 | Neo4j p50 | Memgraph p50 |", "|---|---:|---:|---:|---:|"]
    for metric, q in mapping:
        sides = three.get(("three-engine", metric), {})
        out.append(f"| {q} | {vc._fmt(sides.get('A'))} | {vc._fmt(sides.get('B'))} | {vc._fmt(comp.get((q, 'neo4j')))} | {vc._fmt(comp.get((q, 'memgraph')))} |")
    return "\n".join(out) + "\n"


def micro_table():
    text = (R / "reader-micro-1" / "summary.md").read_text()
    return "\n".join(line for line in text.splitlines() if line.startswith("|"))


def main():
    body = TEMPLATE.read_text()
    for key, value in {
        "{{PARITY_COUNTS}}": parity_counts(),
        "{{MICRO_TABLE}}": micro_table(),
        "{{COMPETITOR_TABLES}}": competitor_tables(),
        "{{FHIR_TABLE}}": fhir_table(R / "fhir-1"),
        "{{DRKG_TABLE}}": drkg_table(),
        "{{MIGRATION_TABLE}}": migration_table(R / "migration-9"),
    }.items():
        body = body.replace(key, value)
    sys.stdout.write(body)


if __name__ == "__main__":
    main()
