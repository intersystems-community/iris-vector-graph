"""vs_competitors.py: IVG baseline / alternative medians next to competitor medians, from result directories."""

import json

from scripts.bench.alt import vs_competitors as vc


def write_run(d, suite, side, rep, metrics):
    (d / f"{suite}_{side}_{rep}.json").write_text(json.dumps({"suite": suite, "side": side, "rep": rep, "status": "ok", "metrics": metrics}))


def comp(engine, suite, query, values, dataset):
    return {"engine": engine, "suite": suite, "query": query, "metric": "latency_ms", "values_ms": values, "params": {"dataset": dataset}}


def test_ivg_medians_are_taken_per_side(tmp_path):
    for rep, (a, b) in enumerate([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)], 1):
        write_run(tmp_path, "bench-q1q6-s", "A", rep, {"S.Q1.ivg-os.hot_p50_ms": a})
        write_run(tmp_path, "bench-q1q6-s", "B", rep, {"S.Q1.ivg-os.hot_p50_ms": b})
    med = vc.ivg_medians(tmp_path)
    assert med[("bench-q1q6-s", "S.Q1.ivg-os.hot_p50_ms")] == {"A": 3.0, "B": 4.0}


def test_traversal_rows_pair_each_ivg_entry_point_with_every_engine(tmp_path):
    write_run(tmp_path, "bench-q1q6-s", "A", 1, {"S.Q2.ivg-os.hot_p50_ms": 8.0, "S.Q2.ivg-arno.hot_p50_ms": 9.0})
    write_run(tmp_path, "bench-q1q6-s", "B", 1, {"S.Q2.ivg-os.hot_p50_ms": 8.5, "S.Q2.ivg-arno.hot_p50_ms": 9.5})
    rows = [comp("neo4j", "bench_q", "Q2", [2.0, 4.0], "S"), comp("memgraph", "bench_q", "Q2", [1.0], "S")]
    table = vc.traversal_rows({"S": vc.ivg_medians(tmp_path)}, rows)
    by = {(r["query"], r["ivg_path"]): r for r in table}
    r = by[("Q2", "ivg-os")]
    assert (r["a"], r["b"]) == (8.0, 8.5) and r["neo4j"] == 3.0 and r["memgraph"] == 1.0
    assert ("Q2", "ivg-arno") in by


def test_rendering_marks_unmeasured_cells_and_states_the_caveat(tmp_path):
    write_run(tmp_path, "bench-q1q6-s", "A", 1, {"S.Q1.ivg-os.hot_p50_ms": 0.4})
    write_run(tmp_path, "bench-q1q6-s", "B", 1, {"S.Q1.ivg-os.hot_p50_ms": 0.5})
    table = vc.traversal_rows({"S": vc.ivg_medians(tmp_path)}, [comp("neo4j", "bench_q", "Q1", [3.0], "S")])
    text = vc.render_traversal(table)
    assert "| S | Q1 | ivg-os | 0.4 | 0.5 | 3 | n/a |" in text


def algo(engine, query, dataset, values, impl=""):
    return {"engine": engine, "suite": "algos", "query": query, "metric": "latency_ms", "values_ms": values, "params": {"dataset": dataset, "implementation": impl}}


def test_algorithm_rows_separate_memgraph_mage_from_memgraph_networkx_legs(tmp_path):
    for rep in (1, 2, 3):
        write_run(tmp_path, "perf-head-to-head", "A", rep, {"er_500.ivg.degree_ms": 3.0, "er_500.ivg.leiden_ms": 30.0})
        write_run(tmp_path, "perf-head-to-head", "B", rep, {"er_500.ivg.degree_ms": 3.2, "er_500.ivg.leiden_ms": 31.0})
    rows = [
        algo("memgraph", "degree_both", "er500", [4.0], "cypher aggregation"),
        algo("memgraph", "leiden", "er500", [40.0], "MAGE leiden_community_detection.get"),
        algo("neo4j", "leiden", "er500", [600.0], "gds.leiden.stream"),
    ]
    out = {(r["dataset"], r["algo"]): r for r in vc.algo_rows(vc.ivg_medians(tmp_path), rows)}
    leiden = out[("er500", "leiden")]
    assert leiden["memgraph_mage"] == 40.0 and leiden["memgraph"] is None and leiden["neo4j"] == 600.0
    degree = out[("er500", "degree_both")]
    assert degree["memgraph"] == 4.0 and degree["memgraph_mage"] is None


def test_the_algorithm_table_has_a_mage_column():
    text = vc.render_algos([{"dataset": "er500", "algo": "leiden", "a": 30.0, "b": 31.0, "neo4j": 600.0, "memgraph": None, "memgraph_mage": 40.0, "networkx": 46.0, "igraph": 80.0}])
    assert "Memgraph (MAGE)" in text and "| 40 |" in text
