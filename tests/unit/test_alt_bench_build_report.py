"""The report's breadth tables come from result files; these pin the readers on small synthetic result trees."""

import json

from scripts.bench.alt import build_report as br


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_fhir_table_gives_median_per_side_and_ratio(tmp_path):
    for side, vals in (("A", (100.0, 300.0)), ("B", (110.0, 130.0))):
        for rnd, v in enumerate(vals, 1):
            _write(tmp_path / f"fhir_fixture_{side}_{rnd}.json", {"set": "fixture", "sync_ms": v, "coverage_ms": 10.0, "repo_resources": 5})
    out = br.fhir_table(tmp_path)
    row = next(line for line in out.splitlines() if line.startswith("| fixture | sync_ms"))
    assert "200" in row and "120" in row and "0.60" in row
    assert "repo_resources" not in out  # counts are not timings


def test_fhir_table_reports_nothing_when_a_side_is_missing(tmp_path):
    _write(tmp_path / "fhir_fixture_A_1.json", {"set": "fixture", "sync_ms": 1.0})
    assert "sync_ms" not in br.fhir_table(tmp_path)


def test_drkg_rows_pool_every_run_of_a_side(tmp_path):
    for name, side, total in (("breadth-drkg-fresh-1-B", "B", 680000.0), ("breadth-drkg-fresh-2-A", "A", 548000.0), ("breadth-drkg-fresh-3-A", "A", 509000.0), ("breadth-drkg-full", "B", 734000.0)):
        _write(tmp_path / name / f"drkg-load-full_{side}_1.json", {"suite": "drkg-load-full", "side": side, "status": "ok", "metrics": {"total": total, "load_edges": total - 5000}})
    out = br.drkg_table(tmp_path)
    assert "548" in out and "509" in out and "680" in out and "734" in out
    assert "| A | 2 |" in out and "| B | 2 |" in out  # one count per side


def test_migration_table_lists_the_headline_steps_with_both_sides(tmp_path):
    for side, total in (("A", 49000.0), ("B", 45000.0)):
        for rep in (1, 2, 3):
            _write(tmp_path / f"migration-4-0-0_{side}_{rep}.json", {"suite": "migration-4-0-0", "side": side, "rep": rep, "status": "ok",
                   "metrics": {"apply.total": total, "step.embeddings": 10000.0, "profile._plan": 200.0}})
    out = br.migration_table(tmp_path)
    assert "| apply.total | 49.0 | 45.0 | 0.92 |" in out
    assert "step.embeddings" in out
    assert "profile._plan" not in out  # only the headline rows


def _snb_side(path, letter, run, results):
    _write(path / f"query_{letter}_{run}.json", {"ns": f"SNBBENCH{letter}", "params": [], "results": results})


def _rec(engine, query, values, **params):
    return json.dumps({"engine": engine, "suite": "snb", "query": query, "metric": "latency_ms", "values_ms": values, "params": params}) + "\n"


def test_snb_table_compares_sides_engines_and_answers(tmp_path):
    ok = {"values_ms": [100.0, 120.0, 110.0], "rows": [10, 10], "signatures": ["a", "b"], "error": None}
    _snb_side(tmp_path, "A", 1, {"Q1": ok, "Q2": {"values_ms": [], "rows": [], "signatures": [], "error": "timeout: first call still running after 60s"}})
    _snb_side(tmp_path, "B", 1, {"Q1": {**ok, "values_ms": [105.0, 125.0, 115.0]}, "Q2": {"values_ms": [], "rows": [], "signatures": [], "error": "timeout: first call still running after 60s"}})
    (tmp_path / "neo4j_snb.jsonl").write_text(_rec("neo4j", "Q1", [2.0, 3.0], rows=[10, 10], signatures=["a", "b"], error=None) + _rec("neo4j", "Q2", [5.0], rows=[3], signatures=["z"], error=None))
    (tmp_path / "memgraph_snb.jsonl").write_text(_rec("memgraph", "Q1", [1.0], rows=[10, 11], signatures=["a", "c"], error=None))
    out = br.snb_table(tmp_path)
    q1 = next(line for line in out.splitlines() if line.startswith("| Q1 "))
    assert "110" in q1 and "115" in q1 and "1.05" in q1 and "2.5" in q1 and "differ: memgraph" in q1  # memgraph answered differently
    (tmp_path / "memgraph_snb.jsonl").write_text(_rec("memgraph", "Q1", [1.0], rows=[10, 10], signatures=["a", "b"], error=None))
    assert "same" in next(line for line in br.snb_table(tmp_path).splitlines() if line.startswith("| Q1 "))
    q2 = next(line for line in out.splitlines() if line.startswith("| Q2 "))
    assert "timeout" in q2 and "5" in q2


def test_kgbench_ingest_table_pools_chunks_per_side(tmp_path):
    for letter, rate in (("A", 80000.0), ("B", 90000.0)):
        for c in range(2):
            _write(tmp_path / f"ingest_{letter}_c{c:03d}.json", {"ns": f"KGBENCH{letter}", "files": ["re2-tt/converted/all_traces.tsv"], "progress": [{"edges": 20_000_000, "elapsed_s": 20_000_000 / rate, "edges_per_s": rate}]})
    out = br.kgbench_ingest_table(tmp_path)
    assert "40,000,000" in out and "80,000" in out and "90,000" in out and "1.12" in out


def test_kgbench_query_table_reads_res_lines(tmp_path):
    for letter, ms in (("A", "0.100"), ("B", "0.110")):
        (tmp_path / f"query_{letter}_1.txt").write_text(f"noise\nRES window.5min {ms}\nRES agg.count.5min 0.02\n")
    out = br.kgbench_query_table(tmp_path)
    assert "| window.5min | 0.1 | 0.11 | 1.10 |" in out


def test_snb_load_table_lists_every_engine_and_scale(tmp_path):
    _write(tmp_path / "load_A_1.json", {"ns": "SNBBENCHA", "nodes": 448684, "edges": 2164444, "phases": {"nodes_s": 100.0, "edges_s": 900.0, "total_s": 1000.0}})
    _write(tmp_path / "load_B_1.json", {"ns": "SNBBENCHB", "nodes": 448684, "edges": 2164444, "phases": {"nodes_s": 90.0, "edges_s": 800.0, "total_s": 890.0}})
    (tmp_path / "neo4j_load.jsonl").write_text(_rec("neo4j", "load_total", [60000.0], nodes=448684, edges=2164444, nodes_s=20.0, edges_s=40.0))
    (tmp_path / "memgraph_sf1_load.jsonl").write_text(_rec("memgraph", "load_total", [400000.0], nodes=3727429, edges=21140212, nodes_s=72.0, edges_s=328.0))
    out = br.snb_load_table(tmp_path)
    assert "| IVG baseline | SF0.1 |" in out and "1,000" in out
    assert "| IVG alternative | SF0.1 |" in out and "890" in out
    assert "| Neo4j | SF0.1 |" in out and "| Memgraph | SF1 |" in out and "21,140,212" in out


def test_snb_table_compares_answers_on_the_parameter_sets_every_engine_ran(tmp_path):
    full = {"values_ms": [1.0], "rows": [3, 4, 5, 6], "signatures": ["a", "b", "c", "d"], "error": None}
    short = {"values_ms": [1.0], "rows": [3], "signatures": ["a"], "error": None}
    _snb_side(tmp_path, "A", 1, {"Q": short})
    _snb_side(tmp_path, "B", 1, {"Q": short})
    (tmp_path / "neo4j_snb.jsonl").write_text(_rec("neo4j", "Q", [1.0], rows=[3, 4, 5, 6], signatures=["a", "b", "c", "d"], error=None))
    row = next(line for line in br.snb_table(tmp_path).splitlines() if line.startswith("| Q "))
    assert "same" in row  # the long run agrees with the short one on the sets both ran
    _snb_side(tmp_path, "A", 1, {"Q": {**short, "signatures": ["x"]}})
    row = next(line for line in br.snb_table(tmp_path).splitlines() if line.startswith("| Q "))
    assert "differ: A" in row
