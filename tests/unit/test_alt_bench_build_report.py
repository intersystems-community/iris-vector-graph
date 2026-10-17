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


def test_snb_load_table_adds_the_server_side_edge_loader_rows_when_given(tmp_path):
    fast = tmp_path / "fast"
    fast.mkdir()
    _write(tmp_path / "load_A_1.json", {"ns": "SNBBENCHA", "nodes": 10, "edges": 100, "phases": {"nodes_s": 1.0, "edges_s": 90.0, "total_s": 100.0}})
    _write(fast / "load_A_1.json", {"ns": "SNBFASTA", "nodes": 10, "edges": 100, "phases": {"nodes_s": 1.0, "edges_s": 9.0, "total_s": 10.0}})
    out = br.snb_load_table(tmp_path, fast_dir=fast)
    assert "| IVG baseline (per-row edge fallback) | SF0.1 |" in out
    assert "| IVG baseline (server-side edge loader) | SF0.1 |" in out
    assert "| IVG baseline | SF0.1 |" in br.snb_load_table(tmp_path)


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


def test_validity_tables_cover_the_runs_that_exist(tmp_path):
    out = tmp_path / "final-1" / "bench-q1q6-s_A_1.out"
    out.mkdir(parents=True)
    (out / "bench_x.json").write_text(json.dumps({"results": [{"dataset": "S", "query": "Q6", "path": "ivg-os", "result_count": 0, "errors": 0}]}))
    out_b = tmp_path / "final-1" / "bench-q1q6-s_B_1.out"
    out_b.mkdir()
    (out_b / "bench_x.json").write_text(json.dumps({"results": [{"dataset": "S", "query": "Q6", "path": "ivg-os", "result_count": 0, "errors": 0}]}))
    text = br.validity_tables(tmp_path)
    assert "final-1" in text and "empty on both sides" in text and "final-4" not in text


def _suite_run(root, run, suite, a, b):
    for side, val in (("A", a), ("B", b)):
        for rep in (1, 2, 3):
            _write(root / run / f"{suite}_{side}_{rep}.json", {"suite": suite, "side": side, "rep": rep, "status": "ok", "metrics": {"m": val}})


def test_latest_counts_take_each_suite_from_its_newest_run(tmp_path):
    _suite_run(tmp_path, "final-1", "s1", 1.0, 1.0)  # superseded below
    _suite_run(tmp_path, "final-1", "s2", 1.0, 2.0)  # slower, only measured in final-1
    _suite_run(tmp_path, "final-4", "s1", 1.0, 0.5)  # faster
    counts, slower = br.latest_counts(tmp_path, runs=("final-1", "final-4"))
    assert counts == {"metrics": 2, "faster": 1, "parity": 0, "slower": 1}
    assert [(r["suite"], r["run"]) for r in slower] == [("s2", "final-1")]


def test_kgbench_query_table_reads_repetitions_numbered_above_nine(tmp_path):
    for rep in (1, 10, 12):
        for letter, ms in (("A", "1.0"), ("B", "2.0")):
            (tmp_path / f"query_{letter}_{rep}.txt").write_text(f"RES m {ms}\n")
    # three reps per side: the median is over all of them, not just rep 1
    (tmp_path / "query_B_12.txt").write_text("RES m 100.0\n")
    out = br.kgbench_query_table(tmp_path)
    assert "| m | 1 | 2 | 2.00 |" in out


def test_snb_bulk_load_table_lists_both_sides_phases(tmp_path):
    for letter, total in (("A", 364.0), ("B", 400.0)):
        _write(tmp_path / f"bulk_load_{letter}.json", {"side": letter, "ns": f"SNB1{letter}", "phases": {"stage_s": 5.0, "load_parallel_s": 293.0, "indexes_s": 61.0, "tune_s": 4.0, "total_s": total},
                                                  "summary": {"counts": {"nodes": 3727429, "rdf_props": 25003650, "rdf_edges": 21140212}}})
    out = br.snb_bulk_load_table(tmp_path)
    assert "| IVG baseline | 3,727,429 | 21,140,212 | 25,003,650 | 364 |" in out and "| IVG alternative |" in out and "400" in out


def test_snb_updates_table_lists_each_update_with_both_sides_and_the_competitors(tmp_path):
    grew = {"nodes": {"Person": 3}, "edges": {"KNOWS": 3}}
    for letter, vals in (("A", [10.0, 12.0, 11.0]), ("B", [9.0, 9.0, 10.0])):
        _write(tmp_path / f"update_{letter}_1.json", {"ns": "X", "n": 3, "ok": True, "grew": grew, "expected": grew,
                                                      "results": {"IU1_add_person": {"values_ms": vals, "error": None}, "IU8_add_friendship": {"values_ms": [], "error": "IntegrityError boom"}}})
    (tmp_path / "neo4j_updates.jsonl").write_text(_rec("neo4j", "IU1_add_person", [2.0, 3.0, 2.5], error=None) + _rec("neo4j", "grew", [0.0], ok=True, grew=grew, expected=grew))
    out = br.snb_updates_table(tmp_path)
    row = next(line for line in out.splitlines() if line.startswith("| IU1_add_person"))
    assert "| 11 |" in row and "| 9 |" in row and "0.82" in row and "| 2.5 |" in row
    bad = next(line for line in out.splitlines() if line.startswith("| IU8_add_friendship"))
    assert "IntegrityError" in bad
    assert "every engine added exactly the expected rows" in out


def test_snb_updates_table_pools_every_interleaved_run_of_a_side(tmp_path):
    grew = {"nodes": {"Person": 1}, "edges": {}}
    for run, (a, b) in enumerate((([10.0], [20.0]), ([30.0], [40.0]), ([50.0], [60.0])), 1):
        for letter, vals in (("A", a), ("B", b)):
            _write(tmp_path / f"update_{letter}_{run}.json", {"ns": "X", "n": 1, "ok": True, "grew": grew, "expected": grew,
                                                              "results": {"IU1_add_person": {"values_ms": vals, "error": None}}})
    row = next(line for line in br.snb_updates_table(tmp_path).splitlines() if line.startswith("| IU1_add_person"))
    assert "| 30 |" in row and "| 40 |" in row  # medians over the three runs


def test_snb_updates_table_pools_competitor_runs_and_flags_a_failed_growth_check(tmp_path):
    grew = {"nodes": {}, "edges": {}}
    _write(tmp_path / "update_A_1.json", {"ns": "X", "n": 1, "ok": True, "grew": grew, "expected": grew, "results": {"IU1_add_person": {"values_ms": [5.0], "error": None}}})
    (tmp_path / "memgraph_updates.jsonl").write_text(
        _rec("memgraph", "IU1_add_person", [1.0, 3.0], error=None) + _rec("memgraph", "grew", [0.0], ok=True)
        + _rec("memgraph", "IU1_add_person", [5.0, 7.0], error=None) + _rec("memgraph", "grew", [0.0], ok=False))
    out = br.snb_updates_table(tmp_path)
    row = next(line for line in out.splitlines() if line.startswith("| IU1_add_person"))
    assert "| 4 |" in row  # median of 1, 3, 5, 7
    assert "memgraph" in out.split("did not match")[1]


def test_snb_updates_table_can_take_the_competitor_timings_from_another_directory(tmp_path):
    grew = {"nodes": {}, "edges": {}}
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    theirs.mkdir()
    _write(mine / "update_A_1.json", {"ns": "X", "n": 1, "ok": True, "grew": grew, "expected": grew, "results": {"IU1_add_person": {"values_ms": [5.0], "error": None}}})
    (theirs / "neo4j_updates.jsonl").write_text(_rec("neo4j", "IU1_add_person", [2.0], error=None) + _rec("neo4j", "grew", [0.0], ok=True))
    row = next(line for line in br.snb_updates_table(mine, comp_dir=theirs).splitlines() if line.startswith("| IU1_add_person"))
    assert "| 2 |" in row


def test_algo_scale_table_lines_up_both_sides_and_the_references_per_size(tmp_path):
    for letter, vals in (("A", [1.0, 1.2]), ("B", [1.1, 1.3])):
        for rnd in (1, 2):
            _write(tmp_path / f"n5000_{letter}_{rnd}.json", {"n": 5000, "edges": 15000, "arno_loaded": True,
                   "algos": {"betweenness_exact": {"values_s": vals, "error": None}, "closeness_harmonic": {"values_s": [], "error": "ValueError: boom"}}})
    (tmp_path / "algo5000_neo4j.jsonl").write_text(
        json.dumps({"engine": "neo4j", "suite": "algos", "query": "betweenness_exact", "metric": "latency_ms", "values_ms": [2000.0, 2200.0], "params": {"dataset": "algo5000"}}) + "\n")
    (tmp_path / "algo5000_refs.jsonl").write_text(
        json.dumps({"engine": "igraph", "suite": "algos", "query": "betweenness_exact", "metric": "latency_ms", "values_ms": [1500.0], "params": {"dataset": "algo5000"}}) + "\n"
        + json.dumps({"engine": "memgraph", "suite": "algos", "query": "closeness_harmonic", "metric": "latency_ms", "values_ms": [], "params": {"dataset": "algo5000", "skipped": "needs the MAGE image"}}) + "\n")
    out = br.algo_scale_table(tmp_path)
    row = next(line for line in out.splitlines() if line.startswith("| 5,000 | betweenness_exact"))
    assert "| 1.1 |" in row and "| 1.2 |" in row and "1.09" in row and "| 2.1 |" in row and "| 1.5 |" in row
    bad = next(line for line in out.splitlines() if "closeness_harmonic" in line)
    assert "boom" in bad


def test_aws_section_says_not_run_when_a_scale_has_no_results(tmp_path):
    out = br.aws_scale_section(tmp_path / "aws-sf10", "SF10")
    assert "not run" in out.lower() and "|" not in out


def test_aws_section_lists_queries_load_and_updates_when_present(tmp_path):
    d = tmp_path / "aws-sf01"
    _snb_side(d, "A", 1, {"Q": {"values_ms": [10.0], "rows": [1], "signatures": ["a"], "error": None}})
    _snb_side(d, "B", 1, {"Q": {"values_ms": [11.0], "rows": [1], "signatures": ["a"], "error": None}})
    _write(d / "load_A_1.json", {"ns": "X", "nodes": 1, "edges": 2, "phases": {"nodes_s": 1.0, "edges_s": 1.0, "total_s": 2.0}})
    out = br.aws_scale_section(d, "SF0.1")
    assert "| Q |" in out and "| IVG baseline | SF0.1 |" in out


def test_snb_updates_table_reports_a_skipped_ivg_side_as_skipped_not_as_a_mismatch(tmp_path):
    for letter in "AB":
        _write(tmp_path / f"update_{letter}_1.json", {"ns": "SNB10" + letter, "n": 3, "skipped": "hours at SF10", "results": {}})
    (tmp_path / "neo4j_updates.jsonl").write_text(_rec("neo4j", "IU1_add_person", [2.0], error=None) + _rec("neo4j", "grew", [0.0], ok=True, restored=True))
    out = br.snb_updates_table(tmp_path)
    assert "did not match" not in out and "IVG updates skipped: hours at SF10" in out
    assert "| IU1_add_person | n/a | n/a | n/a | 2 |" in out
