"""Metric extraction from the real output formats of the registered suites (spec 250). No IRIS."""

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
    out = "[227 SC-012] 100 routes: created in 5.9 s (x), inventory read in 0.4 s, erased in 1.2 s"
    m = su.PARSERS["227-scale"](out, CTX)
    assert (m["create_routes"], m["inventory"], m["erase"]) == (5900.0, 400.0, 1200.0)


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


def test_pytest_parser_merges_durations_and_bench_metric_lines():
    out = "0.50s call     tests/x.py::test_a\nBENCH_METRIC extra = 3\n"
    m = su.PARSERS["pytest"](out, CTX)
    assert m == {"tests/x.py::test_a": 500.0, "extra": 3.0}
