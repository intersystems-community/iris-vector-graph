"""Assemble docs/performance/ALT_VS_BASELINE.md: the tables come from the result files, the prose from the template.

    python -m scripts.bench.alt.build_report > docs/performance/ALT_VS_BASELINE.md
"""

from __future__ import annotations

import io
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


def parity_counts():
    runs = {name: sm.compare_run(R / name) for name in ("core-1", "core-3", "core-4", "confirm-1", "confirm-2", "drift-1")}
    return sm.render(runs)


def competitor_tables():
    rows = vc.load_competitors(R / "competitors-1")
    ivg = {"S": vc.ivg_medians(R / "confirm-1"), "M": vc.ivg_medians(R / "confirm-2"), "L": vc.ivg_medians(R / "core-1")}
    parts = [
        "### Traversal: `tests/benchmarks/bench.py` Q1 to Q6\n\n" + vc.render_traversal(vc.traversal_rows(ivg, rows)),
        "### Variable-length paths, spec 193 (`[*1..N]`, dataset S)\n\n"
        + vc.render_hops(vc.hops_rows(vc.ivg_medians(R / "confirm-1"), rows)),
        "### Algorithms: the head-to-head fixtures\n\n" + vc.render_algos(vc.algo_rows(vc.ivg_medians(R / "confirm-2"), rows)),
        "### LDBC SNB interactive (KNOWS graph, first 10,000 rows, like for like)\n\n" + ldbc_table(rows),
    ]
    return "\n".join(parts)


def ldbc_table(rows):
    three = vc.ivg_medians(R / "core-4")
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
    }.items():
        body = body.replace(key, value)
    sys.stdout.write(body)


if __name__ == "__main__":
    main()
