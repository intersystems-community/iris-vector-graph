"""IVG baseline / alternative next to Neo4j, Memgraph and the in-process references, from result directories.

    python -m scripts.bench.alt.vs_competitors --ivg S=RUNDIR M=RUNDIR L=RUNDIR --competitors DIR [--hth RUNDIR]
        [--three RUNDIR] [--v193 RUNDIR] > comparison.md

IVG numbers are the medians over reps of the harness runs (side A the 4.2.3 baseline, side B the alternative); the competitor
numbers are the medians of the runs in the competitor jsonl files. Same graphs, same start nodes (see
scripts/bench/competitors/README.md). A cell with no measurement says n/a; nothing is estimated.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from scripts.bench.alt.competitor_adapter import percentile

ENGINES = ("neo4j", "memgraph")


def ivg_medians(results_dir):
    """{(suite, metric): {"A": median, "B": median}} over the reps in a harness result directory."""
    values = defaultdict(lambda: {"A": [], "B": []})
    for path in sorted(Path(results_dir).glob("*_?_*.json")):
        data = json.loads(path.read_text())
        if "side" not in data or data.get("status") == "failed":
            continue
        for metric, value in (data.get("metrics") or {}).items():
            if isinstance(value, (int, float)):
                values[(data["suite"], metric)][data["side"]].append(float(value))
    return {key: {s: statistics.median(v) for s, v in sides.items() if v} for key, sides in values.items()}


def load_competitors(directory):
    rows = []
    for path in sorted(glob.glob(str(Path(directory) / "*.jsonl"))):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _p50(values):
    return percentile(values, 50) if values else None


def _fmt(x):
    if x is None:
        return "n/a"
    return f"{x:.3g}"


def traversal_rows(ivg_by_dataset, competitor_rows):
    """One row per (dataset, query, IVG entry point): A, B, and each engine's p50."""
    comp = {}
    for r in competitor_rows:
        if r["suite"] == "bench_q":
            comp[(r["params"].get("dataset"), r["query"], r["engine"])] = _p50(r["values_ms"])
    rows = []
    rx = re.compile(r"^([SML])\.(Q[1-6])\.(ivg-[a-z]+)\.hot_p50_ms$")
    for dataset, med in ivg_by_dataset.items():
        for (suite, metric), sides in sorted(med.items()):
            m = rx.match(metric)
            if not m or m.group(1) != dataset:
                continue
            q, path = m.group(2), m.group(3)
            rows.append(
                {
                    "dataset": dataset,
                    "query": q,
                    "ivg_path": path,
                    "a": sides.get("A"),
                    "b": sides.get("B"),
                    **{e: comp.get((dataset, q, e)) for e in ENGINES},
                }
            )
    return sorted(rows, key=lambda r: (r["dataset"], r["query"], r["ivg_path"]))


def render_traversal(rows):
    out = [
        "| dataset | query | IVG path | IVG baseline p50 | IVG alternative p50 | Neo4j p50 | Memgraph p50 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {r['dataset']} | {r['query']} | {r['ivg_path']} | {_fmt(r['a'])} | {_fmt(r['b'])} | {_fmt(r['neo4j'])} | {_fmt(r['memgraph'])} |"
        )
    return "\n".join(out) + "\n"


def hops_rows(ivg, competitor_rows):
    """Spec-193 `[*1..N]`: IVG direct and orig p50 for N = 2..4 against Neo4j and Memgraph."""
    comp = {}
    for r in competitor_rows:
        m = re.fullmatch(r"B193_h(\d+)", r["query"])
        if r["suite"] == "bench193" and m and r["params"].get("dataset") == "S":
            comp[(int(m.group(1)), r["engine"])] = _p50(r["values_ms"])
    rows = []
    for hops in (2, 3, 4):
        for variant in ("direct", "orig"):
            sides = ivg.get(("bench-193-full-s", f"s1.S.hops.{hops}.{variant}.p50"), {})
            rows.append({"hops": hops, "variant": variant, "a": sides.get("A"), "b": sides.get("B"), **{e: comp.get((hops, e)) for e in ENGINES}})
    return rows


def render_hops(rows):
    out = ["| hops | IVG variant | baseline p50 | alternative p50 | Neo4j p50 | Memgraph p50 |", "|---|---|---:|---:|---:|---:|"]
    for r in rows:
        out.append(f"| {r['hops']} | {r['variant']} | {_fmt(r['a'])} | {_fmt(r['b'])} | {_fmt(r['neo4j'])} | {_fmt(r['memgraph'])} |")
    return "\n".join(out) + "\n"


#: head-to-head metric -> competitor algorithm name
HTH_ALGO = {"degree_ms": "degree_both", "betweenness_ms": "betweenness_exact", "closeness_ms": "closeness_harmonic", "leiden_ms": "leiden"}
HTH_DATASET = {"karate": "karate", "er_500": "er500", "er_2000": "er2000"}


def _algo_engine(row):
    """Memgraph rows from the MAGE image are told apart from the networkx-inside-the-server legs by their implementation note."""
    if row["engine"] == "memgraph" and "MAGE" in str((row.get("params") or {}).get("implementation", "")):
        return "memgraph_mage"
    return row["engine"]


def algo_rows(ivg, competitor_rows):
    comp = {}
    for r in competitor_rows:
        if r["suite"] == "algos":
            comp[(r["params"].get("dataset"), r["query"], _algo_engine(r))] = _p50(r["values_ms"])
    rows = []
    for ivg_ds, comp_ds in HTH_DATASET.items():
        for metric, algo in HTH_ALGO.items():
            sides = ivg.get(("perf-head-to-head", f"{ivg_ds}.ivg.{metric}"), {})
            rows.append(
                {
                    "dataset": comp_ds,
                    "algo": algo,
                    "a": sides.get("A"),
                    "b": sides.get("B"),
                    **{e: comp.get((comp_ds, algo, e)) for e in ("neo4j", "memgraph", "memgraph_mage", "networkx", "igraph")},
                }
            )
    return rows


def render_algos(rows):
    out = [
        "| dataset | algorithm | IVG baseline | IVG alternative | Neo4j GDS | Memgraph | Memgraph (MAGE) | networkx | igraph |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {r['dataset']} | {r['algo']} | {_fmt(r['a'])} | {_fmt(r['b'])} | {_fmt(r['neo4j'])} | {_fmt(r['memgraph'])} | "
            f"{_fmt(r.get('memgraph_mage'))} | {_fmt(r['networkx'])} | {_fmt(r['igraph'])} |"
        )
    return "\n".join(out) + "\n"


KHOP_PAIRS = (("Q1", "q1", "NC_Q1", "1-hop"), ("Q2a", "q2a", "NC_Q2a", "2-hop"), ("Q3", "q3", "NC_Q3", "3-hop"))


def khop_rows(ivg, competitor_rows):
    """IVG's `KHopNeighbors` (`nkg-enterprise-khop`) against the Q1/Q2a/Q3 of `benchmarks/neo4j_comparison.py` on the same 10K/50K graph:
    the repo's own pairing, a count-style question on both sides."""
    comp = {}
    for r in competitor_rows:
        if r["suite"] == "neo4j_comparison":
            comp[(r["query"], r["engine"])] = _p50(r["values_ms"])
    rows = []
    for label, ivg_key, comp_key, hops in KHOP_PAIRS:
        sides = ivg.get(("nkg-enterprise-khop", f"{ivg_key}.p50"), {})
        rows.append({"query": label, "hops": hops, "a": sides.get("A"), "b": sides.get("B"), **{e: comp.get((comp_key, e)) for e in ENGINES}})
    return rows


def render_khop(rows):
    out = ["| query | what | IVG baseline p50 | IVG alternative p50 | Neo4j p50 | Memgraph p50 |", "|---|---|---:|---:|---:|---:|"]
    for r in rows:
        out.append(f"| {r['query']} | {r['hops']} | {_fmt(r['a'])} | {_fmt(r['b'])} | {_fmt(r['neo4j'])} | {_fmt(r['memgraph'])} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ivg", nargs="+", required=True, help="DATASET=RUNDIR for the traversal tables, e.g. S=results/confirm-1")
    ap.add_argument("--competitors", required=True)
    ap.add_argument("--v193")
    ap.add_argument("--hth")
    args = ap.parse_args(argv)
    competitor_rows = load_competitors(args.competitors)
    by_ds = {}
    for item in args.ivg:
        ds, path = item.split("=", 1)
        by_ds[ds] = ivg_medians(path)
    sys.stdout.write("## Traversal (bench.py Q1 to Q6)\n\n" + render_traversal(traversal_rows(by_ds, competitor_rows)))
    if args.v193:
        sys.stdout.write("\n## Variable-length paths (spec 193, S)\n\n" + render_hops(hops_rows(ivg_medians(args.v193), competitor_rows)))
    if args.hth:
        sys.stdout.write("\n## Algorithms (head-to-head fixtures)\n\n" + render_algos(algo_rows(ivg_medians(args.hth), competitor_rows)))


if __name__ == "__main__":
    main()
