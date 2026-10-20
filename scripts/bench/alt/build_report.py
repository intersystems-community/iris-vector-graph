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
from scripts.bench.alt import validity as vl
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
    "final-1", "final-2", "final-3", "final-4", "final-5", "final-6", "final-7", "final-8", "quiet-1", "quiet-2", "quiet-3", "quiet-4", "quiet-5",
    "migration-9", "breadth-index", "breadth-q1q6-l", "breadth-bidir", "breadth-drkg-full",
)
FHIR_METRICS = ("sync_ms", "coverage_ms", "gaps_ms", "ppr_ms", "ppr_group_ms")


def parity_counts(root=None):
    root = root or R
    runs = {name: sm.compare_run(root / name) for name in PARITY_RUNS if (root / name).is_dir()}
    return sm.render(runs)


LATEST_RUNS = ("final-1", "final-2", "final-3", "final-4", "final-5", "final-6", "final-7", "final-8", "quiet-1", "quiet-2", "quiet-3", "quiet-4", "quiet-5")


def latest_counts(root=None, runs=LATEST_RUNS):
    """Verdict counts with every suite taken from the newest run that measured it (a re-run supersedes the earlier one)."""
    root = Path(root or R)
    newest = {}
    rows_by_run = {}
    for run in runs:
        if not (root / run).is_dir():
            continue
        rows_by_run[run] = sm.compare_run(root / run)
        for r in rows_by_run[run]:
            newest[r["suite"]] = run
    counts = {"metrics": 0, "faster": 0, "parity": 0, "slower": 0}
    slower = []
    for run, rows in rows_by_run.items():
        for r in rows:
            if newest[r["suite"]] != run:
                continue
            counts["metrics"] += 1
            verdict = "parity" if r["verdict"].startswith("parity") else r["verdict"]
            counts[verdict] += 1
            if verdict == "slower":
                slower.append({**r, "run": run})
    return counts, slower


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


def validity_tables(root=None):
    """Which bench.py legs returned a real answer: the runs before the harness read answer sizes, and the re-run after the fixes."""
    root = Path(root or R)
    parts = []
    for run, title in (("final-1", "final-1 (before the shortest-path pair fix)"), ("breadth-q1q6-l", "breadth-q1q6-l (the 100,000-node graph)"), ("final-5", "final-5 (after the fixes: real shortest-path pair, Q6 with inline ids)"), ("final-6", "final-6 (the 100,000-node graph again, after the fixes)")):
        if (root / run).is_dir():
            rows = vl.bench_q_rows(root / run)
            if rows:
                parts.append(f"### {title}\n\n" + vl.render(rows))
    return "\n".join(parts)


def _p50(values):
    return statistics.median(values) if values else None


def _num(x):
    if x is None:
        return "n/a"
    return f"{x:.3g}" if x < 1000 else f"{x:,.0f}"


def snb_load_table(results_dir, fast_dir=None):
    """Time to load the SNB graph into each engine (IVG per side; Neo4j and Memgraph at SF0.1 and SF1)."""
    results_dir = Path(results_dir)
    out = ["| engine | scale | nodes | edges | nodes s | edges s | total s | edges/s overall |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    # fast_dir: the same load again with the namespace's classes detected first, so bulk_ingest_edges runs its server-side loader
    # instead of the per-row fallback; the rows say which path they are.
    sources = [(results_dir, "")]
    if fast_dir is not None:
        sources = [(results_dir, " (per-row edge fallback)"), (Path(fast_dir), " (server-side edge loader)")]
    for letter, name in (("A", "IVG baseline"), ("B", "IVG alternative")):
        for directory, suffix in sources:
            for path in sorted(directory.glob(f"load_{letter}_?.json")):
                d = json.loads(path.read_text())
                ph = d["phases"]
                out.append(f"| {name}{suffix} | SF0.1 | {d['nodes']:,} | {d['edges']:,} | {ph['nodes_s']:,.0f} | {ph['edges_s']:,.0f} | {ph['total_s']:,.0f} | {d['edges'] / ph['total_s']:,.0f} |")
    for engine, label in (("neo4j", "Neo4j"), ("memgraph", "Memgraph")):
        for suffix, scale in (("load", "SF0.1"), ("sf1_load", "SF1")):
            path = results_dir / f"{engine}_{suffix}.jsonl"
            if path.exists():
                for line in path.read_text().splitlines():
                    rec = json.loads(line)
                    p = rec["params"]
                    total = rec["values_ms"][0] / 1000
                    out.append(f"| {label} | {scale} | {p['nodes']:,} | {p['edges']:,} | {p['nodes_s']:,.0f} | {p['edges_s']:,.0f} | {total:,.0f} | {p['edges'] / total:,.0f} |")
    return "\n".join(out) + "\n"


def _clean_note(text, width=60):
    """One line of an engine error with the angle brackets IRIS wraps its messages in turned into plain words (markdown reads them as HTML)."""
    one = " ".join(str(text).replace("<", "").replace(">", "").split())
    return one[:width]


def aws_scale_section(results_dir, scale):
    """One scale of the AWS run: the query table, the load table (engine or bulk loader) and, when updates ran, the update table."""
    results_dir = Path(results_dir)
    if not results_dir.is_dir() or not any(results_dir.glob("query_?_*.json")):
        return f"_{scale}: not run yet._\n"
    _merge_query_files(results_dir)
    parts = [f"**{scale}, queries:**\n", snb_table(results_dir)]
    if any(results_dir.glob("bulk_load_?.json")):
        parts += [f"\n**{scale}, IVG bulk load:**\n", snb_bulk_load_table(results_dir)]
    parts += [f"\n**{scale}, loading (engine paths; Neo4j and Memgraph by batched `UNWIND`):**\n", snb_load_table(results_dir).replace("| SF0.1 |", f"| {scale} |")]
    if (results_dir / "updates").is_dir() and any((results_dir / "updates").glob("update_?_?.json")):
        parts += [f"\n**{scale}, interactive updates and deletes:**\n", snb_updates_table(results_dir / "updates")]
    return "\n".join(parts)


def index_effect_table(before_dir, after_dir):
    """p50 per query and side without and with the (key, val) property index, and Neo4j / Memgraph from the no-index run."""
    before_dir, after_dir = Path(before_dir), Path(after_dir)
    if not after_dir.is_dir() or not any(after_dir.glob("query_?_*.json")):
        return "_not run yet._\n"

    def values(d):
        _merge_query_files(d)  # per-query files into query_<side>_<run>.json; read only those, so nothing counts twice
        out = {}
        for f in d.glob("query_?_?.json"):
            letter = f.name.split("_")[1]
            for name, r in json.loads(f.read_text())["results"].items():
                out.setdefault((letter, name), []).extend(r.get("values_ms") or [])
        return out

    b, a = values(before_dir), values(after_dir)
    comp = {}
    for engine in ("neo4j", "memgraph"):
        path = before_dir / f"{engine}_snb.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                comp[(engine, rec["query"])] = _p50(rec["values_ms"])
    out = ["| query | side | without index (ms) | with index (ms) | speed-up | Neo4j (ms) | Memgraph (ms) |", "|---|---|---:|---:|---:|---:|---:|"]
    for letter, name in sorted(a):
        va, vb = _p50(b.get((letter, name))), _p50(a[(letter, name)])
        speed = f"{va / vb:,.0f}x" if va and vb else "n/a"
        out.append(f"| {name} | {letter} | {_num(va)} | {_num(vb)} | {speed} | {_num(comp.get(('neo4j', name)))} | {_num(comp.get(('memgraph', name)))} |")
    return "\n".join(out) + "\n"


def aws_aligned_note(results_dir):
    """One line for the SF1 re-run of the plan-sensitive rows with both sides on the same statistics."""
    results_dir = Path(results_dir)
    if not results_dir.is_dir() or not any(results_dir.glob("query_?_*.json")):
        return "not yet run"
    _merge_query_files(results_dir)
    rows = [ln for ln in snb_table(results_dir).splitlines() if ln.startswith("| ") and not ln.startswith("| query")]
    cells = [[c.strip() for c in ln.split("|")[1:-1]] for ln in rows]
    return "with the same statistics: " + "; ".join(f"`{c[0]}` {c[1]} against {c[2]} ms, B/A {c[3]}" for c in cells)


def _merge_query_files(results_dir):
    """query_<side>_<run>.json from the per-query files a run writes (the worker writes one file per query and leg)."""
    for letter in "AB":
        for run in range(1, 10):
            files = sorted(results_dir.glob(f"query_{letter}_{run}_*.json"))
            if not files:
                continue
            merged = None
            for f in files:
                x = json.loads(f.read_text())
                if merged is None:
                    merged = {k: v for k, v in x.items() if k != "results"}
                    merged["results"] = {}
                merged["results"].update(x["results"])
            (results_dir / f"query_{letter}_{run}.json").write_text(json.dumps(merged))


def snb_bulk_load_table(results_dir):
    """The SF1 graph loaded by scripts/bench/alt/snb_bulk.py (table files through LOAD, adjacency in one pass), per IVG side."""
    results_dir = Path(results_dir)
    out = ["| engine | nodes | edges | property rows | total s | files and tables s | indexes s | tune s |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for letter, name in (("A", "IVG baseline"), ("B", "IVG alternative")):
        path = results_dir / f"bulk_load_{letter}.json"
        if path.exists():
            d = json.loads(path.read_text())
            ph, c = d["phases"], d["summary"]["counts"]
            out.append(f"| {name} | {c['nodes']:,} | {c['rdf_edges']:,} | {c['rdf_props']:,} | {ph['total_s']:,.0f} | {ph.get('stage_s', 0) + ph.get('load_parallel_s', 0):,.0f} | {ph.get('indexes_s', 0):,.0f} | {ph.get('tune_s', 0):,.0f} |")
    return "\n".join(out) + "\n"


def snb_table(results_dir, comp_dir=None):
    """LDBC SNB queries: IVG baseline and alternative p50 (ms), Neo4j and Memgraph p50, and whether the engines agree on the answer."""
    results_dir = Path(results_dir)

    def side(letter):
        runs = sorted(results_dir.glob(f"query_{letter}_?.json"))
        merged = {}
        for path in runs:
            for name, r in json.loads(path.read_text())["results"].items():
                m = merged.setdefault(name, {"values": [], "rows": r.get("rows"), "sigs": r.get("signatures"), "error": r.get("error")})
                m["values"] += r.get("values_ms") or []
                m["error"] = m["error"] or r.get("error")
        return merged

    def comp(engine):
        out = {}
        path = Path(comp_dir or results_dir) / f"{engine}_snb.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                p = rec["params"]
                out[rec["query"]] = {"values": rec["values_ms"], "rows": p.get("rows"), "sigs": p.get("signatures"), "error": p.get("error")}
        return out

    a, b, neo, mem = side("A"), side("B"), comp("neo4j"), comp("memgraph")
    out = ["| query | IVG baseline p50 | IVG alternative p50 | B/A | Neo4j p50 | Memgraph p50 | answers | note |", "|---|---:|---:|---:|---:|---:|---|---|"]
    for name in sorted(set(a) | set(b) | set(neo) | set(mem)):
        va, vb = _p50(a.get(name, {}).get("values")), _p50(b.get(name, {}).get("values"))
        ratio = f"{vb / va:.2f}" if va and vb else "n/a"
        sigs = {k: (v.get("sigs") or None) for k, v in (("A", a.get(name, {})), ("B", b.get(name, {})), ("neo4j", neo.get(name, {})), ("memgraph", mem.get(name, {}))) if v.get("sigs")}
        if sigs:  # the engines may have run different numbers of parameter sets (slow IVG legs run fewer): compare the common prefix
            n = min(len(v) for v in sigs.values())
            sigs = {k: tuple(v[:n]) for k, v in sigs.items()}
            counts = {}
            for v in sigs.values():
                counts[v] = counts.get(v, 0) + 1
            ref = max(counts, key=lambda v: (counts[v], v == next(iter(sigs.values()))))  # the majority answer; ties go to the first engine
        answers = "n/a" if not sigs else ("same" if len(set(sigs.values())) == 1 else "differ: " + ",".join(k for k in sigs if sigs[k] != ref))
        notes = [f"{k}: {_clean_note((v.get(name) or {}).get('error'))}" for k, v in (("A", a), ("B", b)) if (v.get(name) or {}).get("error")]
        out.append(f"| {name} | {_num(va)} | {_num(vb)} | {ratio} | {_num(_p50(neo.get(name, {}).get('values')))} | {_num(_p50(mem.get(name, {}).get('values')))} | {answers} | {'; '.join(notes)} |")
    return "\n".join(out) + "\n"


def snb_updates_table(results_dir, comp_dir=None):
    """The interactive updates, one call each: IVG baseline and alternative p50 (ms), Neo4j and Memgraph p50, and the row-growth check."""
    results_dir = Path(results_dir)
    sides, skipped = {}, None
    for letter in "AB":
        for path in sorted(results_dir.glob(f"update_{letter}_?.json")):
            d = json.loads(path.read_text())
            if d.get("skipped"):
                skipped = d["skipped"]
                continue
            if letter not in sides:
                sides[letter] = d
                continue
            for name, r in d["results"].items():  # a later interleaved run: pool its timings, keep an error
                have = sides[letter]["results"].setdefault(name, {"values_ms": [], "error": None})
                have["values_ms"] = list(have.get("values_ms") or []) + list(r.get("values_ms") or [])
                have["error"] = have.get("error") or r.get("error")
            sides[letter]["ok"] = bool(sides[letter].get("ok")) and bool(d.get("ok"))
            sides[letter]["restored"] = bool(sides[letter].get("restored", True)) and bool(d.get("restored", True))
    comp, growth = {}, {}
    for engine in ("neo4j", "memgraph"):
        path = Path(comp_dir or results_dir) / f"{engine}_updates.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                if rec["query"] == "grew":
                    growth[engine] = growth.get(engine, True) and rec["params"].get("ok") is True and rec["params"].get("restored", True) is True
                elif (engine, rec["query"]) in comp:  # another run of the same update: pool its timings
                    comp[(engine, rec["query"])]["values_ms"] += rec["values_ms"]
                    comp[(engine, rec["query"])]["params"]["error"] = comp[(engine, rec["query"])]["params"].get("error") or rec["params"].get("error")
                else:
                    comp[(engine, rec["query"])] = rec
    names = sorted({n for d in sides.values() for n in d["results"]} | {q for _, q in comp}, key=lambda n: (n.startswith("DEL"), n))
    out = ["| update | IVG baseline p50 (ms) | IVG alternative p50 (ms) | B/A | Neo4j p50 (ms) | Memgraph p50 (ms) | note |", "|---|---:|---:|---:|---:|---:|---|"]
    for name in names:
        va, vb = (_p50(sides[k]["results"].get(name, {}).get("values_ms")) if k in sides else None for k in "AB")
        notes = [f"{k}: {_clean_note(sides[k]['results'][name]['error'], 70)}" for k in "AB" if k in sides and sides[k]["results"].get(name, {}).get("error")]
        notes += [f"{e}: {_clean_note(comp[(e, name)]['params']['error'], 70)}" for e in ("neo4j", "memgraph") if (e, name) in comp and comp[(e, name)]["params"].get("error")]
        ratio = f"{vb / va:.2f}" if va and vb else "n/a"
        cells = [_num(_p50(comp[(e, name)]["values_ms"])) if (e, name) in comp else "n/a" for e in ("neo4j", "memgraph")]
        out.append(f"| {name} | {_num(va)} | {_num(vb)} | {ratio} | {cells[0]} | {cells[1]} | {'; '.join(notes)} |")
    if skipped:
        out.append(f"\nIVG updates skipped: {skipped}.")
    checks = {**{k: bool(sides[k].get("ok")) and bool(sides[k].get("restored", True)) for k in sides}, **growth}
    if checks and all(v is True for v in checks.values()):
        out.append("\nAfter the run every engine added exactly the expected rows (node counts per label and edge counts per type, read back from its own tables), and where deletes ran, removed them again.")
    elif checks:
        out.append("\nRow growth did not match the expectation on: " + ", ".join(k for k, v in checks.items() if v is not True) + ".")
    return "\n".join(out) + "\n"


def algo_scale_table(results_dir):
    """Exact betweenness and harmonic closeness at 5,000 nodes and above: IVG per side (median over every repetition), the references."""
    results_dir = Path(results_dir)
    sizes = sorted({int(p.name.split("_")[0][1:]) for p in results_dir.glob("n*_?_?.json")})
    out = ["| nodes | algorithm | edges | IVG baseline (s) | IVG alternative (s) | B/A | IVG vs igraph (Pearson, worst run) | Neo4j GDS (s) | Memgraph (s) | igraph (s) | networkx (s) | note |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for n in sizes:
        side = {"A": {}, "B": {}}
        edges = None
        for letter in "AB":
            for path in sorted(results_dir.glob(f"n{n}_{letter}_?.json")):
                d = json.loads(path.read_text())
                edges = d.get("edges", edges)
                for name, r in d["algos"].items():
                    h = side[letter].setdefault(name, {"values": [], "error": None})
                    h["values"] += r.get("values_s") or []
                    h["error"] = h["error"] or r.get("error")
        ref = {}
        for path in sorted(results_dir.glob(f"algo{n}_*.jsonl")):
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                h = ref.setdefault((rec["engine"], rec["query"]), {"values": [], "note": None})
                h["values"] += [v / 1000 for v in rec["values_ms"]]
                h["note"] = h["note"] or rec["params"].get("skipped") or rec["params"].get("error")
        for name in sorted({k for letter in side for k in side[letter]}):
            va, vb = _p50(side["A"].get(name, {}).get("values")), _p50(side["B"].get(name, {}).get("values"))
            notes = [f"{k}: {_clean_note(side[k][name]['error'], 70)}" for k in "AB" if side[k].get(name, {}).get("error")]
            cells = []
            for eng in ("neo4j", "memgraph", "igraph", "networkx"):
                r = ref.get((eng, name))
                cells.append(_num(_p50(r["values"])) if r and r["values"] else "n/a")
                if r and r["note"] and not r["values"]:
                    notes.append(f"{eng}: {_clean_note(r['note'], 60)}")
            ratio = f"{vb / va:.2f}" if va and vb else "n/a"
            agree = [json.loads(c.read_text()).get(name) for c in results_dir.glob(f"n{n}_?_?.check.json")]
            agree = [x for x in agree if x]
            worst = f"{min(x['pearson'] for x in agree):.3f}" + ("" if not any(x["foreign_ids"] for x in agree) else " (foreign ids!)") if agree else "not checked"
            out.append(f"| {n:,} | {name} | {edges:,} | {_num(va)} | {_num(vb)} | {ratio} | {worst} | {' | '.join(cells)} | {'; '.join(notes)} |")
    return "\n".join(out) + "\n"


def kgbench_ingest_table(results_dir):
    """Temporal ingest of the 535M-edge RCAEval data, alternating chunks on each side: edges, seconds and edges/s per side."""
    results_dir = Path(results_dir)
    by = {"A": [], "B": []}
    for path in sorted(results_dir.glob("ingest_?_c*.json")):
        d = json.loads(path.read_text())
        p = d["progress"][-1]
        by[path.name.split("_")[1]].append((p["edges"], p["elapsed_s"], p["edges_per_s"], path.name))
    out = ["| side | chunks | edges | seconds | edges/s overall | edges/s per chunk (min / median / max) |", "|---|---:|---:|---:|---:|---|"]
    tot = {}
    for letter in "AB":
        rows = by[letter]
        if rows:
            edges, secs = sum(r[0] for r in rows), sum(r[1] for r in rows)
            rates = [r[2] for r in rows]
            tot[letter] = edges / secs
            out.append(f"| {letter} | {len(rows)} | {edges:,} | {secs:,.0f} | {edges / secs:,.0f} | {min(rates):,.0f} / {statistics.median(rates):,.0f} / {max(rates):,.0f} |")
    if "A" in tot and "B" in tot:
        out.append(f"\nOverall ingest rate, alternative over baseline: {tot['B'] / tot['A']:.2f}.")
    return "\n".join(out) + "\n"


def kgbench_query_table(results_dir):
    """bench_full.py query section (temporal windows, pre-aggregates, bucket groups) on the full graph: ms, median over reps."""
    results_dir = Path(results_dir)
    by = {}
    for path in sorted(results_dir.glob("query_?_*.txt")):
        letter = path.name.split("_")[1]
        for line in path.read_text().splitlines():
            if line.startswith("RES "):
                _, name, ms = line.split()
                by.setdefault(name, {"A": [], "B": []})[letter].append(float(ms))
    out = ["| metric | baseline ms | alternative ms | B/A |", "|---|---:|---:|---:|"]
    for name, sides in sorted(by.items()):
        if sides["A"] and sides["B"]:
            a, b = statistics.median(sides["A"]), statistics.median(sides["B"])
            out.append(f"| {name} | {a:.4g} | {b:.4g} | {b / a:.2f} |")
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
    # newest run that measured each thing: bench.py Q1-Q6 from final-5 (real shortest-path pair, Q6 with inline ids), the 100,000-node
    # graph from final-6, spec-193 hops from final-4, the head-to-head algorithms from final-2
    small, medium, large = _first("final-5", "final-1"), _first("final-5", "confirm-2"), _first("final-6", "breadth-q1q6-l", "core-1")
    ivg = {"S": vc.ivg_medians(small), "M": vc.ivg_medians(medium), "L": vc.ivg_medians(large)}
    parts = [
        "### Traversal: `tests/benchmarks/bench.py` Q1 to Q6\n\n" + vc.render_traversal(vc.traversal_rows(ivg, rows)),
        "### Variable-length paths, spec 193 (`[*1..N]`, dataset S)\n\n" + vc.render_hops(vc.hops_rows(vc.ivg_medians(_first("final-4", "final-1")), rows)),
        "### Count-style k-hop: `KHopNeighbors` against the `neo4j_comparison` queries (10K nodes, 50K edges)\n\n"
        + vc.render_khop(vc.khop_rows(vc.ivg_medians(_first("final-1", "confirm-2")), rows)),
        "### Algorithms: the head-to-head fixtures\n\n"
        + vc.render_algos(vc.algo_rows(vc.ivg_medians(_first("final-2", "confirm-2")), rows)),
        "### LDBC SNB interactive (KNOWS graph, first 10,000 rows, like for like)\n\n" + ldbc_table(rows),
    ]
    return "\n".join(parts)


def ldbc_table(rows):
    three = vc.ivg_medians(_first("final-4", "final-1", "core-4"))
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
        "{{VALIDITY_TABLES}}": validity_tables(),
        "{{LATEST_COUNTS}}": "{metrics} metrics: {faster} faster, {parity} at parity, {slower} slower".format(**latest_counts()[0]),
        "{{SNB_LOAD_TABLE}}": snb_load_table(R / "snb-1", fast_dir=R / "snb-fastload"),
        "{{SNB_TABLE}}": snb_table(R / "snb-1"),
        "{{SNB_SF1_LOAD}}": snb_bulk_load_table(R / "snb-sf1"),
        "{{SNB_QUIET01_TABLE}}": snb_table(R / "snb-quiet01", comp_dir=R / "snb-1"),
        "{{SNB_QUIET1_TABLE}}": snb_table(R / "snb-quiet1", comp_dir=R / "snb-sf1"),
        "{{AWS_SF01}}": aws_scale_section(R / "aws-sf01", "SF0.1"),
        "{{AWS_SF1_ALIGNED_NOTE}}": aws_aligned_note(R / "aws-sf1-aligned"),
        "{{AWS_SF1_INDEX_QUICK}}": index_effect_table(R / "aws-sf1", R / "aws-sf1-quick"),
        "{{AWS_SF1_INDEXED}}": index_effect_table(R / "aws-sf1", R / "aws-sf1-indexed"),
        "{{AWS_SF1}}": aws_scale_section(R / "aws-sf1", "SF1"),
        "{{AWS_SF10}}": aws_scale_section(R / "aws-sf10", "SF10"),
        "{{AWS_SF1_FI}}": aws_scale_section(R / "aws-sf1-fi", "SF1 with the adjacency index"),
        "{{SNB_WEIGHTED_TABLE}}": snb_table(R / "snb-weighted"),
        "{{SNB_BI2_TABLE}}": snb_table(R / "snb-bi2"),
        "{{SNB_BI_TABLE}}": snb_table(R / "snb-bi"),
        "{{ALGO_SCALE_TABLE}}": algo_scale_table(R / "algo-scale"),
        "{{SNB_UPDATES_TABLE}}": snb_updates_table(R / "snb-updates-clean"),
        "{{SNB_UPDATES_INDEXED_TABLE}}": snb_updates_table(R / "snb-updates-clean-indexed", comp_dir=R / "snb-updates-clean"),
        "{{SNB_INDEXED_TABLE}}": snb_table(R / "snb-sf1-indexed", comp_dir=R / "snb-sf1"),
        "{{KG_INGEST_TABLE}}": kgbench_ingest_table(R / "kgbench-1"),
        "{{KG_QUERY_TABLE}}": kgbench_query_table(R / "kgbench-1"),
    }.items():
        body = body.replace(key, value)
    sys.stdout.write(body)


if __name__ == "__main__":
    main()
