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
