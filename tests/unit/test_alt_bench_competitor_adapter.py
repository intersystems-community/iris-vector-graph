"""The adapter that maps competitor rows onto the harness's suite ids and metric names.

Competitors report {engine, suite, query, metric: latency_ms, values_ms, params.dataset}. The comparer wants
competitor_<engine>_<suite id>.jsonl with one row per IVG metric name, value = the percentile of the competitor's runs.
"""

import json

from scripts.bench.alt import competitor_adapter as ca

A_METRICS = {
    "S.Q1.ivg-os.hot_p50_ms": 0.4,
    "S.Q1.ivg-os.hot_p90_ms": 0.6,
    "S.Q2.ivg-arno.hot_p50_ms": 9.0,
    "S.Q2.ivg-os.hot_p50_ms": 8.0,
    "S.Q5.ivg-os.hot_p50_ms": 1.0,
    "wall_total": 3.0,
    "s1.S.hops.2.direct.p50": 5.0,
    "s1.S.hops.2.direct.p90": 6.0,
    "s1.S.hops.2.orig.p50": 7.0,
    "s1.S.hops.3.direct.p50": 9.0,
}


def row(engine, suite, query, values, dataset="S"):
    return {"engine": engine, "suite": suite, "query": query, "metric": "latency_ms", "values_ms": values, "params": {"dataset": dataset}}


def test_percentiles_use_nearest_rank_on_the_competitor_runs():
    values = list(range(1, 11))  # 1..10
    assert ca.percentile(values, 50) == 5.5
    assert ca.percentile(values, 90) == 9.1
    assert ca.percentile([4.0], 90) == 4.0


def test_q_rows_map_to_every_ivg_engine_metric_of_that_query():
    out = ca.adapt([row("neo4j", "bench_q", "Q2", [4.0, 6.0])], A_METRICS)
    got = {(r["suite"], r["metric"]): r["values_ms"] for r in out["neo4j"]}
    assert got[("bench-q1q6-s", "S.Q2.ivg-arno.hot_p50_ms")] == [5.0]
    assert got[("bench-q1q6-s", "S.Q2.ivg-os.hot_p50_ms")] == [5.0]
    assert ("bench-q1q6-s", "S.Q1.ivg-os.hot_p50_ms") not in got


def test_p90_metric_gets_the_competitors_p90():
    out = ca.adapt([row("neo4j", "bench_q", "Q1", list(range(1, 11)))], A_METRICS)
    got = {r["metric"]: r["values_ms"] for r in out["neo4j"]}
    assert got["S.Q1.ivg-os.hot_p90_ms"] == [9.1]


def test_bench193_hops_map_to_direct_and_orig_metrics():
    out = ca.adapt([row("memgraph", "bench193", "B193_h2", [10.0])], A_METRICS)
    got = {(r["suite"], r["metric"]): r["values_ms"] for r in out["memgraph"]}
    assert got[("bench-193-full-s", "s1.S.hops.2.direct.p50")] == [10.0]
    assert got[("bench-193-full-s", "s1.S.hops.2.orig.p50")] == [10.0]
    assert ("bench-193-full-s", "s1.S.hops.3.direct.p50") not in got


def test_rows_with_no_ivg_counterpart_are_dropped_and_reported():
    out = ca.adapt([row("neo4j", "bench_q", "Q5_distinct", [1.0]), row("neo4j", "algos", "pagerank", [1.0])], A_METRICS)
    assert out["neo4j"] == []
    assert sorted(out["_unmatched"]) == ["algos/pagerank", "bench_q/Q5_distinct"]


def test_write_produces_one_file_per_engine_and_suite(tmp_path):
    out = ca.adapt([row("neo4j", "bench_q", "Q1", [2.0])], A_METRICS)
    ca.write(out, tmp_path)
    path = tmp_path / "competitor_neo4j_bench-q1q6-s.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines[0] == {"engine": "neo4j", "suite": "bench-q1q6-s", "metric": "S.Q1.ivg-os.hot_p50_ms", "values_ms": [2.0]}
