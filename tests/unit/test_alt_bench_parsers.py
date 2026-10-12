"""Metric extraction from the real output formats of the registered suites (spec 250). No IRIS."""

from scripts.bench.alt import run_matrix as rm
from scripts.bench.alt import suites as su

CTX = su.context("/nonexistent", "/nonexistent", 0)

PARSER_BENCH_OUT = """\
Cypher Parser Benchmarks (Recursive-Descent)
------------------------------------------------------------
Query: MATCH (a:Account) RETURN a.node_id LIMIT 5
  Avg: 0.0021 ms
  P95: 0.0002 ms
------------------------------------------------------------
Query: MATCH (t:Transaction) RETURN count(t), sum(t.amount), avg(t.amount)
  Avg: 0.0009 ms
  P95: 0.0001 ms
"""


def test_parser_bench_output_gives_avg_and_p95_per_query():
    m = su.PARSERS["parser-bench"](PARSER_BENCH_OUT, CTX)
    assert m["q1.avg"] == 0.0021 and m["q1.p95"] == 0.0002
    assert m["q2.avg"] == 0.0009 and m["q2.p95"] == 0.0001


def test_p227_stdout_line():
    line = (
        "[227 SC-010] 300 vectors of width 768 in Graph_KG.kg_NodeEmbeddings: "
        "3.2.0 scan median 4.1 ms, 4.0.0 scoped median 4.3 ms, ratio 1.05"
    )
    m = su.PARSERS["227-latency"](line, CTX)
    assert m["legacy_scan_median"] == 4.1 and m["scoped_median"] == 4.3


def test_communities_nfr_lines():
    m = su.PARSERS["communities"]("NFR-002 scc 10000n 49872e: 1.250s\n", CTX)
    assert m["nfr-002 scc"] == 1250.0


def test_cypher_benchmark_p50_lines_attach_to_the_running_test():
    out = "tests/unit/test_cypher_benchmark.py::TestX::test_point_lookup p50=1.5 ms  p95=2.5 ms  rows=1\nPASSED\n"
    m = su.PARSERS["p50p95"](out, CTX)
    assert m["test_point_lookup.p50"] == 1.5 and m["test_point_lookup.p95"] == 2.5


def test_drkg_load_phases():
    out = "[12.5s] loaded 97,238 nodes\n[100.0s] loaded 500,000 edges via bulk_load_session\nTOTAL: 97,238 nodes, 500,000 edges in 130.5s\n"
    m = su.PARSERS["drkg-load"](out, CTX)
    assert m == {"load_nodes": 12500.0, "load_edges": 100000.0, "total": 130500.0}


def test_227_scale_line():
    out = (
        "[227 SC-012] 100 routes: created in 20.6s (0.206s each, baseline 5.9s for bare tables), "
        "inventory read in 2.44s, erased in 3.4s (baseline 1.2s)"
    )
    m = su.PARSERS["227-scale"](out, CTX)
    assert (m["create_routes"], m["inventory"], m["erase"]) == (20600.0, 2440.0, 3400.0)


def test_index_maintenance_json_becomes_times(tmp_path):
    import json

    d = tmp_path / "benchmarks"
    d.mkdir()
    (d / "index_maintenance_strategy_20000.json").write_text(
        json.dumps({"deferred": {"total_s": 1.0, "insert_s": 0.6, "sync_s": 0.4, "edges_per_s": 20000.0}, "per_row": {"edges_per_s": 2000.0}})
    )
    ctx = su.context(tmp_path, tmp_path, 0)
    m = su.PARSERS["index-maintenance"]("", ctx)
    assert m["deferred_total"] == 1000.0
    assert m["per_row_ms_per_1k_edges"] == 500.0


def test_bench_193_json_gives_p50_and_p90_per_section(tmp_path):
    import json

    doc = {
        "meta": {"seed": 42, "runs": 5},
        "s1": {"S": {"seed": "node_0", "hops": {"2": {"count": 2841, "orig": {"p50": 9.8, "p90": 9.9, "min": 9.7, "runs": 5}, "speedup_p50": 0.34}}}},
        "s2": {"S": {"hops": {"2": {"nkg": {"p50": 1.8, "p90": 1.9}, "arno": {"p50": 10.2, "p90": 10.3}, "nkg_vs_arno_speedup": 5.65}}}},
        "gates": {"x": False},
    }
    (tmp_path / "bench_193_20261012_222137.json").write_text(json.dumps(doc))
    m = su.PARSERS["bench-193"]("", su.context(tmp_path, tmp_path, 0))
    assert m["s1.S.hops.2.orig.p50"] == 9.8 and m["s1.S.hops.2.orig.p90"] == 9.9
    assert m["s2.S.hops.2.nkg.p50"] == 1.8
    assert not any(k.endswith("min") or "speedup" in k or k.startswith("meta") for k in m)


def test_gate_exit_codes_keep_the_numbers():
    script = su.SUITES_BY_ID["bench-193-full-s"]
    assert script.gate_rc == (1,)
    assert rm.status_for(1, script) == "tests-failed"
    assert rm.status_for(2, script) == "failed"


TEMPORAL_OUT = """\
  traces.tsv CALLS_AT                         28,056 edges/sec  SC-004: ❌ FAIL
                                          stdev=   5,794  all=['38,268', '28,056']
  rcaeval.tsv EMITS_METRIC_AT                 20,672 edges/sec  SC-004: ❌ FAIL
  synthetic 50K                               17,673 edges/sec  SC-004: ❌ FAIL
  ✅ GetAggregate_1b_5min                      0.2002ms  SC-005 <0.5ms
  ✅ GetBucketGroups_288b_24hr                 0.4674ms
"""


def test_temporal_preagg_rates_become_times_and_latencies_are_kept():
    m = su.PARSERS["temporal-preagg"](TEMPORAL_OUT, CTX)
    assert m["ingest.traces.tsv 100k edges"] == round(100000 / 28056 * 1000, 1)
    assert m["ingest.synthetic 100k edges"] == round(100000 / 17673 * 1000, 1)
    assert m["GetAggregate_1b_5min"] == 0.2002 and m["GetBucketGroups_288b_24hr"] == 0.4674


def test_leiden_json_keeps_ivg_means_per_fixture_and_label(tmp_path):
    import json

    d = tmp_path / "benchmarks"
    d.mkdir()
    (d / "leiden_4way_20261012_183805.json").write_text(
        json.dumps(
            [
                {
                    "fixture": "karate_club",
                    "engines": [
                        {"label": "IVG (arno+leiden-rs)", "mean_s": 0.3761, "kernel_only_s": 0.0037},
                        {"label": "networkx Louvain", "mean_s": 0.0007},
                    ],
                }
            ]
        )
    )
    m = su.PARSERS["leiden"]("", su.context(tmp_path, tmp_path, 0))
    assert m["karate_club.IVG (arno+leiden-rs).mean"] == 376.1
    assert m["karate_club.IVG (arno+leiden-rs).kernel"] == 3.7
    assert not any("networkx" in k for k in m)


def test_betweenness_json_keeps_ivg_means_per_fixture(tmp_path):
    import json

    d = tmp_path / "benchmarks"
    d.mkdir()
    (d / "betweenness_vs_gds_20261012_223858.json").write_text(
        json.dumps({"fixtures": [{"fixture": "er_500", "engines": [{"label": "IVG BetweennessGlobal", "mean_ms": 169.91}, {"label": "GDS exact", "mean_ms": 19.0}]}]})
    )
    m = su.PARSERS["betweenness"]("", su.context(tmp_path, tmp_path, 0))
    assert m["er_500.IVG BetweennessGlobal"] == 169.91
    assert not any("GDS" in k for k in m)


def test_to_ms_units():
    from scripts.bench.alt import metrics as mx

    assert mx.to_ms(2, "s") == 2000.0
    assert mx.to_ms(5, "ms") == 5.0
    assert mx.to_ms(314, "µs") == 0.314
    assert mx.to_ms(314, "us") == 0.314


NKG_OUT = """\
Benchmarking (5 warmup + 20 reps)...
  Q1  1-hop: p50=178µs  p95=251µs
  Q2a 2-hop: p50=276µs  p95=21.24ms
  Q3  BFS 1..3: p50=752µs  p95=876µs
"""


def test_nkg_enterprise_khop_lines():
    m = su.PARSERS["nkg-khop"](NKG_OUT, CTX)
    assert m["q1.p50"] == 0.178 and m["q1.p95"] == 0.251
    assert m["q2a.p95"] == 21.24
    assert m["q3.p50"] == 0.752


SCALE_OUT = """\
Bulk load completed in 16.77s (596 nodes/sec)
Batch size    1:   41.26ms (total) |   41.26ms (per node)
Batch size 1000:  708.99ms (total) |    0.71ms (per node)
Search for 'Node Name 500': 11 matches | 61.45ms
Search for 'indexing capabilities': 10000 matches | 16.72ms
create_node latency: 34.54ms
"""


def test_scale_benchmark_lines():
    m = su.PARSERS["scale-benchmark"](SCALE_OUT, CTX)
    assert m["bulk_load"] == 16770.0
    assert m["get_nodes.batch1"] == 41.26 and m["get_nodes.batch1000"] == 708.99
    assert m["search.1"] == 61.45 and m["search.2"] == 16.72
    assert m["create_node"] == 34.54


THREE_OUT = """\
Query                                IVG-SQL    IVG-Arno/fjall  IVG-Arno/globals
IC2: 1-hop KNOWS                      2.99ms              SKIP              SKIP
IC3: 2-hop KNOWS                      1.06ms              SKIP              SKIP
IC13: shortestPath                     314µs              SKIP              SKIP
COUNT friends                         2.84ms              SKIP              SKIP
"""


def test_three_engine_summary_table_gives_the_ivg_sql_column():
    m = su.PARSERS["three-engine"](THREE_OUT, CTX)
    assert m == {"ic2 1-hop knows": 2.99, "ic3 2-hop knows": 1.06, "ic13 shortestpath": 0.314, "count friends": 2.84}


def test_pytest_parser_merges_durations_and_bench_metric_lines():
    out = "0.50s call     tests/x.py::test_a\nBENCH_METRIC extra = 3\n"
    m = su.PARSERS["pytest"](out, CTX)
    assert m == {"tests/x.py::test_a": 500.0, "extra": 3.0}
