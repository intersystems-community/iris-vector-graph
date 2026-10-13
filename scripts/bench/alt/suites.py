"""Registry of the suites the matrix runner can execute (spec 250).

Every id here is documented in docs/performance/alt-bench/CATALOG.md, together with the suites that are NOT
registered because they cannot run (missing data, hard-wired to another namespace, destructive, stale).

Placeholders in ``argv`` (expanded per run by run_matrix.expand_argv):
``{py}`` venv python, ``{out}`` artefact directory of this run, ``{ns}`` side namespace, ``{port}``, ``{tree}``.

Metrics are milliseconds, lower is better. Anything that is a rate in the suite's own output is turned into a time
by its parser (so compare.py never has to know a direction).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from scripts.bench.alt import metrics as mx

PYTEST = ["{py}", "-m", "pytest", "-p", "no:cacheprovider", "--durations=0", "--junitxml={out}/junit.xml"]


@dataclass(frozen=True)
class Suite:
    id: str
    argv: list
    env: dict = field(default_factory=dict)
    data: str | None = None  # key into DATASETS: the dataset that must be resident before the run
    parser: str = "stdout"  # key into PARSERS
    collect: tuple = ()  # globs, relative to the side tree, of result files the run writes
    quick: bool = False  # under about 5 minutes at the smallest size
    wipes: bool = False  # destroys or dirties graph content in the namespace (invalidates loaded datasets)
    needs_neo4j: bool = False
    needs_arno: bool = False
    timeout_s: int = 3600
    note: str = ""
    gate_rc: tuple = ()  # exit codes of a non-pytest script that mean "an acceptance gate failed"; its numbers are valid
    blocked: str = ""  # non-empty: why the suite hangs or fails on this machine today; skipped unless --include-blocked
    reset_derived: bool = False  # clear ^KG, ^NKG and the algorithm caches in the side's namespace before every run


@dataclass(frozen=True)
class Dataset:
    key: str
    load_argv: list
    expect_nodes: int  # SELECT COUNT(*) FROM SQLUser.nodes must equal this for the dataset to count as loaded
    note: str = ""


def _rmat(label, nodes):
    return Dataset(
        key=f"rmat-{label.lower()}",
        load_argv=["{py}", "tests/benchmarks/bench.py", "--datasets", label, "--runs", "1", "--warmup", "0", "--outdir", "{out}"],
        expect_nodes=nodes,
        note="tests/benchmarks/bench.py loads R-MAT (seed 42) into SQLUser.* and builds ^KG and ^NKG; the load run itself is not timed for the record",
    )


DATASETS = {d.key: d for d in (_rmat("S", 1_000), _rmat("M", 10_000), _rmat("L", 100_000))}


# ---------------------------------------------------------------------------------------------- parsers


def _ctx_files(ctx, patterns):
    out = []
    for pat in patterns:
        for p in Path(ctx.tree).glob(pat):
            if p.stat().st_mtime >= ctx.start - 1:
                out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def p_stdout(stdout, ctx):
    return mx.metrics_from_stdout(stdout)


def p_pytest(stdout, ctx):
    return {**mx.metrics_from_pytest_durations(stdout), **mx.metrics_from_stdout(stdout)}


def p_bench_193(stdout, ctx):
    """bench_193_full.py: sections s1..s5 hold per-hop `{p50, p90, min, max}` blocks; keep p50 and p90."""
    out = mx.metrics_from_stdout(stdout)
    p = mx.newest_json(ctx.outdir, "bench_193_*.json")
    if p:
        doc = json.loads(p.read_text())
        doc.pop("meta", None)
        doc.pop("gates", None)
        out.update(mx.flatten_numbers(doc, include=("p50", "p90")))
    return out


def p_bench_json(stdout, ctx):
    return {**mx.bench_py_json(ctx.outdir), **mx.metrics_from_stdout(stdout)}


_P227 = re.compile(r"scan median ([\d.]+)\s*ms, .*?scoped median ([\d.]+)\s*ms")


def p_227_latency(stdout, ctx):
    out = p_pytest(stdout, ctx)
    m = _P227.search(stdout)
    if m:
        out["legacy_scan_median"] = float(m.group(1))
        out["scoped_median"] = float(m.group(2))
    return out


_NFR = re.compile(r"NFR-(\d+)\s+(\S+)\s+\d+n\s+\d+e:\s+([\d.]+)s")


def p_communities(stdout, ctx):
    out = p_pytest(stdout, ctx)
    for m in _NFR.finditer(stdout):
        out[f"nfr-{m.group(1)} {m.group(2)}"] = float(m.group(3)) * 1000.0
    return out


_P50 = re.compile(r"p50=([\d.]+)\s*ms\s+p95=([\d.]+)\s*ms")
_TESTNAME = re.compile(r"::(test_\w+)")


def p_p50p95(stdout, ctx):
    out = p_pytest(stdout, ctx)
    current = None
    for line in stdout.splitlines():
        t = _TESTNAME.search(line)
        if t:
            current = t.group(1)
        m = _P50.search(line)
        if m and current:
            out[f"{current}.p50"] = float(m.group(1))
            out[f"{current}.p95"] = float(m.group(2))
    return out


_SC012 = re.compile(r"created in ([\d.]+)\s*s\b.*?inventory read in ([\d.]+)\s*s, erased in ([\d.]+)\s*s")


def p_227_scale(stdout, ctx):
    out = p_pytest(stdout, ctx)
    m = _SC012.search(stdout)
    if m:
        out["create_routes"], out["inventory"], out["erase"] = (float(x) * 1000.0 for x in m.groups())
    return out


def p_head_to_head(stdout, ctx):
    out = p_pytest(stdout, ctx)
    for p in _ctx_files(ctx, ("benchmarks/head_to_head_*.json",)):
        doc = json.loads(p.read_text())
        for gname, g in doc.get("graphs", {}).items():
            for k, v in (g.get("engines", {}).get("ivg", {}) or {}).items():
                if k.endswith("_ms") and isinstance(v, (int, float)):
                    out[f"{gname}.ivg.{k}"] = float(v)
    return out


def p_leiden(stdout, ctx):
    """leiden_4way_*.json is a list of fixtures; keep the IVG engines, keyed by label (arno and leidenalg paths differ)."""
    out = p_pytest(stdout, ctx)
    for p in _ctx_files(ctx, ("benchmarks/leiden_4way_*.json",)):
        for fx in json.loads(p.read_text()):
            for e in fx.get("engines", []):
                label = str(e.get("label", ""))
                if not label.startswith("IVG"):
                    continue
                if isinstance(e.get("mean_s"), (int, float)):
                    out[f"{fx.get('fixture', '?')}.{label}.mean"] = round(e["mean_s"] * 1000.0, 3)
                if isinstance(e.get("kernel_only_s"), (int, float)):
                    out[f"{fx.get('fixture', '?')}.{label}.kernel"] = round(e["kernel_only_s"] * 1000.0, 3)
    return out


def p_betweenness(stdout, ctx):
    out = p_pytest(stdout, ctx)
    for p in _ctx_files(ctx, ("benchmarks/betweenness_vs_gds_*.json",)):
        for fx in json.loads(p.read_text()).get("fixtures", []):
            for e in fx.get("engines", []):
                if str(e.get("label", "")).startswith("IVG") and isinstance(e.get("mean_ms"), (int, float)):
                    out[f"{fx.get('fixture', '?')}.{e['label']}"] = float(e["mean_ms"])
    return out


def p_ledger(stdout, ctx):
    out = p_pytest(stdout, ctx)
    for p in _ctx_files(ctx, ("tests/benchmarks/results/ledger_*.json",)):
        out.update(mx.flatten_numbers(json.loads(p.read_text()), include=None, prefix="ledger"))
    return {k: v for k, v in out.items() if k.endswith("_ms") or "::" in k or k == "wall_total"}


def p_index_maintenance(stdout, ctx):
    out = mx.metrics_from_stdout(stdout)
    for p in _ctx_files(ctx, ("benchmarks/index_maintenance_strategy_*.json",)):
        d = json.loads(p.read_text())
        out["deferred_total"] = d["deferred"]["total_s"] * 1000.0
        out["deferred_insert"] = d["deferred"]["insert_s"] * 1000.0
        out["deferred_sync"] = d["deferred"]["sync_s"] * 1000.0
        # Per-row time for 1000 edges (a rate turned into a time).
        out["per_row_ms_per_1k_edges"] = 1000.0 * 1000.0 / d["per_row"]["edges_per_s"]
    return out


_THREE = re.compile(r"^(IC2: 1-hop KNOWS|IC3: 2-hop KNOWS|IC13: shortestPath|COUNT friends)\s+([\d.]+)(ms|µs|us|s)\b")


def p_three_engine(stdout, ctx):
    """The summary table of three_engine_benchmark.py; its first column is the IVG-SQL engine, the only leg that runs."""
    out = mx.metrics_from_stdout(stdout)
    for line in stdout.splitlines():
        m = _THREE.match(line)
        if m:
            out[re.sub(r"[:]", "", m.group(1)).lower()] = mx.to_ms(m.group(2), m.group(3))
    return out


_NKG = re.compile(r"^\s*(Q\w+)\s+[^:]+:\s+p50=([\d.]+)(µs|us|ms|s)\s+p95=([\d.]+)(µs|us|ms|s)")


def p_nkg_khop(stdout, ctx):
    out = mx.metrics_from_stdout(stdout)
    for line in stdout.splitlines():
        m = _NKG.match(line)
        if m:
            q = m.group(1).lower()
            out[f"{q}.p50"] = mx.to_ms(m.group(2), m.group(3))
            out[f"{q}.p95"] = mx.to_ms(m.group(4), m.group(5))
    return out


def p_scale_benchmark(stdout, ctx):
    """tests/performance/scale_benchmark.py prints bulk-load seconds, get_nodes batches, substring searches, create_node."""
    out = mx.metrics_from_stdout(stdout)
    searches = 0
    for line in stdout.splitlines():
        if m := re.match(r"Bulk load completed in ([\d.]+)s", line):
            out["bulk_load"] = float(m.group(1)) * 1000.0
        elif m := re.match(r"Batch size\s+(\d+):\s+([\d.]+)ms \(total\)", line):
            out[f"get_nodes.batch{m.group(1)}"] = float(m.group(2))
        elif m := re.match(r"Search for .*\| ([\d.]+)ms", line):
            searches += 1
            out[f"search.{searches}"] = float(m.group(1))
        elif m := re.match(r"create_node latency: ([\d.]+)ms", line):
            out["create_node"] = float(m.group(1))
    return out


def p_parser_bench(stdout, ctx):
    """tests/benchmark_parser.py prints `Query: ...` then `  Avg: x ms` and `  P95: y ms` per query; q1, q2, ... in order."""
    out = mx.metrics_from_stdout(stdout)
    n = 0
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("Query:"):
            n += 1
        m = re.match(r"(Avg|P95):\s*([\d.]+)\s*ms", s)
        if m and n:
            out[f"q{n}.{m.group(1).lower()}"] = float(m.group(2))
    return out


_TEMPORAL_INGEST = re.compile(r"^\s+(traces\.tsv|rcaeval\.tsv|synthetic)\b.*?\s([\d,]+) edges/sec")
_TEMPORAL_QUERY = re.compile(r"(Get\w+)\s+([\d.]+)ms")


def p_temporal_preagg(stdout, ctx):
    """Ingest rates (edges/s) become the time for 100K edges, so lower is better everywhere."""
    out = mx.metrics_from_stdout(stdout)
    for line in stdout.splitlines():
        m = _TEMPORAL_INGEST.match(line)
        if m:
            rate = float(m.group(2).replace(",", ""))
            out[f"ingest.{m.group(1)} 100k edges"] = round(100000.0 / rate * 1000.0, 1)
            continue
        m = _TEMPORAL_QUERY.search(line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


_DRKG = re.compile(r"\[([\d.]+)s\] loaded ([\d,]+) (nodes|edges)")
_DRKG_TOTAL = re.compile(r"TOTAL: .* in ([\d.]+)s")


def p_drkg_load(stdout, ctx):
    out = {}
    for m in _DRKG.finditer(stdout):
        out[f"load_{m.group(3)}"] = float(m.group(1)) * 1000.0
    m = _DRKG_TOTAL.search(stdout)
    if m:
        out["total"] = float(m.group(1)) * 1000.0
    return out


PARSERS = {
    "stdout": p_stdout,
    "pytest": p_pytest,
    "bench-json": p_bench_json,
    "bench-193": p_bench_193,
    "227-latency": p_227_latency,
    "communities": p_communities,
    "p50p95": p_p50p95,
    "227-scale": p_227_scale,
    "head-to-head": p_head_to_head,
    "leiden": p_leiden,
    "betweenness": p_betweenness,
    "ledger": p_ledger,
    "index-maintenance": p_index_maintenance,
    "three-engine": p_three_engine,
    "nkg-khop": p_nkg_khop,
    "scale-benchmark": p_scale_benchmark,
    "parser-bench": p_parser_bench,
    "drkg-load": p_drkg_load,
    "temporal-preagg": p_temporal_preagg,
}


# ---------------------------------------------------------------------------------------------- registry


_EXECUTEMANY_HANG = (
    "its edge generator repeats (s, p, o) rows (i % n_nodes), so most of the one big cursor.executemany violates the unique "
    "constraint; on IRIS 2026.3.0AI build 113 each rejected row costs about 40 ms and is not raised, so 20,000 edges take "
    "about 13 minutes before the timed phase even ends (repro: scripts/bench/alt/repro_executemany_hang.py). The script "
    "needs a generator of distinct edges before it measures what it says"
)


def _bench_q1q6(label):
    return Suite(
        id=f"bench-q1q6-{label.lower()}",
        argv=["{py}", "tests/benchmarks/bench.py", "--datasets", label, "--runs", "20", "--warmup", "5", "--skip-load", "--outdir", "{out}"],
        data=f"rmat-{label.lower()}",
        parser="bench-json",
        collect=(),
        quick=(label == "S"),
        needs_arno=False,
        timeout_s=900 if label == "S" else 5400,
        note="ivg-arno rows exist only where Arno answers; Arno readers walk USER's ^KG (scripts/arno_reader_gate.py), so treat them as suspect outside USER",
    )


def _pytest(id, targets, *, env=None, parser="pytest", quick=False, wipes=False, needs_arno=False, collect=(), extra=(), timeout=3600, note="", marker=None, reset_derived=False):
    argv = list(PYTEST) + (["-m", marker] if marker else []) + ["-s"] + list(extra) + list(targets)
    return Suite(id=id, argv=argv, env=env or {}, parser=parser, collect=collect, quick=quick, wipes=wipes, needs_arno=needs_arno, timeout_s=timeout, note=note, reset_derived=reset_derived)


SUITES = [
    # --- R-MAT traversal family (needs the R-MAT dataset resident) -----------------------------------
    _bench_q1q6("S"),
    _bench_q1q6("M"),
    _bench_q1q6("L"),
    Suite(
        id="bench-193-full-s",
        argv=["{py}", "tests/benchmarks/bench_193_full.py", "--datasets", "S", "--runs", "5", "--warmup", "2", "--skip-neo4j", "--skip-load", "--outdir", "{out}"],
        data="rmat-s",
        parser="bench-193",
        gate_rc=(1,),
        quick=False,
        timeout_s=1800,
        note="namespace hard-coded USER in the script; the connection shim redirects it. Neo4j section skipped (no loader writes the shape it queries)",
    ),
    # --- scripts under benchmarks/ and tests/ that load their own data --------------------------------
    Suite(
        id="nkg-enterprise-khop",
        argv=["{py}", "benchmarks/_load_and_bench_enterprise.py", "--reps", "20"],
        parser="nkg-khop",
        quick=True,
        wipes=True,
        needs_arno=True,
        timeout_s=900,
        note="loads its own 10K/50K graph into Graph_KG; hard-coded USER, redirected by the shim",
    ),
    Suite(
        id="index-maintenance-20k",
        argv=["{py}", "benchmarks/bench_index_maintenance_strategy.py", "--edges", "20000"],
        parser="index-maintenance",
        collect=("benchmarks/index_maintenance_strategy_20000.json",),
        quick=False,
        timeout_s=900,
        note="uses iris_devtester attach (container from IVG_TEST_CONTAINER); scratch prefix cleaned up by the script; "
        "runs now that the script generates distinct edges (it used to repeat triples and hang in executemany)",
        wipes=True,
    ),
    Suite(
        id="index-maintenance-100k",
        argv=["{py}", "benchmarks/bench_index_maintenance_strategy.py", "--edges", "100000"],
        parser="index-maintenance",
        collect=("benchmarks/index_maintenance_strategy_100000.json",),
        quick=False,
        timeout_s=1800,
        wipes=True,
    ),
    Suite(
        id="three-engine",
        argv=["{py}", "benchmarks/three_engine_benchmark.py", "--edges", "10000", "--reps", "20", "--host", "127.0.0.1", "--port", "{port}", "--namespace", "{ns}"],
        parser="three-engine",
        collect=("benchmarks/results/three_engine_latest.json",),
        quick=True,
        wipes=True,
        timeout_s=1200,
        note="only the IVG-SQL leg runs; the Arno stores need arno_graph and /opt/iris20252cust, which are absent. Reads the SF10 KNOWS CSV via a symlink in the side tree",
    ),
    Suite(
        id="cypher-parser-bench",
        argv=["{py}", "tests/benchmark_parser.py"],
        parser="parser-bench",
        quick=True,
        timeout_s=300,
        note="no IRIS: a client-side control, A and B should agree",
    ),
    Suite(
        id="perf-scale-benchmark",
        argv=["{py}", "tests/performance/scale_benchmark.py", "--entities", "10000"],
        parser="scale-benchmark",
        quick=True,
        wipes=True,
        timeout_s=900,
        note="hard-coded localhost:1972/USER, redirected by the shim; leaves 10,000 BENCH:NODE rows",
    ),
    Suite(
        id="temporal-preagg",
        argv=["{py}", "scripts/bench/bench_temporal_preagg.py"],
        env={"IVG_BENCH_CONTAINER": "ivg-iris-enterprise"},
        parser="temporal-preagg",
        quick=False,
        wipes=True,
        timeout_s=3600,
        note="attaches a container by a hard-coded name (redirected by the shim) and calls TemporalIndex.Purge; reads ~/ws/iris-datasets/re2-tt/*.tsv",
    ),
    Suite(
        id="drkg-load-500k",
        argv=["{py}", "scripts/load_drkg.py", "--host", "localhost", "--port", "{port}", "--edges-limit", "500000", "--skip-deploy"],
        parser="drkg-load",
        quick=False,
        wipes=True,
        timeout_s=3600,
        note="needs <tree>/data/drkg (symlink to the main checkout); namespace hard-coded USER, redirected by the shim; sets the embedding width to 400",
    ),
    Suite(
        id="drkg-load-full",
        argv=["{py}", "scripts/load_drkg.py", "--host", "localhost", "--port", "{port}", "--skip-deploy"],
        parser="drkg-load",
        quick=False,
        wipes=True,
        timeout_s=7200,
        note="5.87M edges, 10 to 20 minutes per run",
    ),
    # --- tests/perf and friends (pytest) --------------------------------------------------------------
    _pytest("perf-227-single-graph-latency", ["tests/perf/test_227_single_graph_latency.py"], parser="227-latency", quick=True),
    _pytest(
        "perf-betweenness-quick", ["tests/perf/test_betweenness_vs_gds.py"], marker="perf", extra=["-k", "karate or er500"], quick=True, wipes=True, needs_arno=False, parser="betweenness", note="karate and ER(500) only; the JSON is written only by the full suite"
    ),
    _pytest(
        "perf-betweenness-full", ["tests/perf/test_betweenness_vs_gds.py"], marker="perf", wipes=True, collect=("benchmarks/betweenness_vs_gds_*.json",), parser="betweenness", timeout=7200
    ),
    Suite(
        id="perf-head-to-head",
        argv=list(PYTEST) + ["-s", "tests/perf/test_head_to_head.py"],
        env={"IVG_HEADTOHEAD": "1"},
        parser="head-to-head",
        collect=("benchmarks/head_to_head_*.json",),
        quick=True,
        wipes=True,
        timeout_s=1800,
        note="networkx is the in-process competitor; Neo4j GDS legs skip (driver not installed)",
    ),
    _pytest("perf-leiden-four-way", ["tests/perf/test_leiden_four_way.py"], marker="perf", wipes=True, collect=("benchmarks/leiden_4way_*.json",), parser="leiden", timeout=7200),
    _pytest("e2e-communities-perf", ["tests/e2e/test_communities_perf.py"], marker="perf", wipes=True, parser="communities", timeout=7200),
    _pytest("integration-ledger-perf-quick", ["tests/integration/test_ledger_perf.py"], marker="perf", extra=["-k", "sc004 or sc005"], wipes=True, collect=("tests/benchmarks/results/ledger_*.json",), parser="ledger", quick=True),
    _pytest("integration-ledger-perf-full", ["tests/integration/test_ledger_perf.py"], marker="perf", wipes=True, collect=("tests/benchmarks/results/ledger_*.json",), parser="ledger", timeout=7200),
    _pytest("unit-cypher-benchmark", ["tests/unit/test_cypher_benchmark.py"], marker="perf", parser="p50p95", quick=True),
    _pytest("e2e-stress-ingest", ["tests/e2e/test_stress_ingest.py"], env={"IVG_PORT": "31972"}, quick=True, note="leaves about 11k prefixed nodes per run"),
    _pytest("e2e-stress-traversal", ["tests/e2e/test_stress_traversal.py"], quick=True),
    _pytest("e2e-stress-search", ["tests/e2e/test_stress_search.py"], quick=True),
    _pytest("e2e-227-scale", ["tests/e2e/test_227_scale.py"], parser="227-scale", quick=True),
    _pytest(
        "e2e-gates-kernels",
        [
            "tests/e2e/test_ic3_exact_count.py",
            "tests/e2e/test_ic13_shortest_path.py",
            "tests/e2e/test_approx_count_distinct.py",
            "tests/e2e/test_cypher_vl_path_bfs.py",
            "tests/e2e/test_230_bulk_ingest_upsert.py",
            "tests/e2e/test_plaid_search_e2e.py",
        ],
        quick=True,
        needs_arno=True,
        note="timing gates (pass/fail) plus pytest durations; KHop2CountExact and ShortestPathNKG are the paths the alternative changes",
    ),
    _pytest(
        "e2e-gates-analytics",
        ["tests/e2e/test_analytics_dispatch.py", "tests/e2e/test_msbfs_closeness.py", "tests/e2e/test_betweenness_neighborhood_e2e.py"],
        quick=True,
        wipes=True,
        needs_arno=True,
    ),
    _pytest("integration-nodepk-graph-analytics", ["tests/integration/test_nodepk_graph_analytics.py"], marker="performance", quick=True, note="SQL-level: a noise-floor control"),
    _pytest("integration-nodepk-performance", ["tests/integration/test_nodepk_performance.py"], marker="performance", quick=True, note="SQL-level: a noise-floor control"),
    _pytest("integration-nodepk-advanced", ["tests/integration/test_nodepk_advanced_benchmarks.py"], marker="performance", env={}, quick=False, note="loose gates; reads IRIS_NAMESPACE, which the runner sets"),
    _pytest(
        "integration-bidirectional-ppr-perf",
        ["tests/integration/test_bidirectional_ppr.py"],
        extra=["-k", "TestPerformance or fallback_without_index"],
        quick=True,
        wipes=True,
        reset_derived=True,
        note="deletes its rows by SQL and leaves ^KG; on the baseline the residue accumulates across reps and the walk shrinks, so both sides are reset before every rep",
    ),
]

SUITES_BY_ID = {s.id: s for s in SUITES}


def quick_ids():
    return [s.id for s in SUITES if s.quick and not s.needs_neo4j and not s.blocked]


def context(outdir, tree, start, extra=None):
    return SimpleNamespace(outdir=outdir, tree=tree, start=start, **(extra or {}))
