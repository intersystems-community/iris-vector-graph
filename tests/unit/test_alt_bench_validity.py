"""Did the benchmark measure real answers? Reads the raw bench.py JSON of each run and flags empty and failing queries."""

import json

from scripts.bench.alt import validity as v


def _run(tmp_path, side, rep, rows):
    out = tmp_path / f"bench-q1q6-s_{side}_{rep}.out"
    out.mkdir()
    (out / "bench_x.json").write_text(json.dumps({"results": rows, "meta": {}, "correctness": {}}))


def _row(q, path, count, errors=0, p50=1.0):
    return {"dataset": "S", "query": q, "path": path, "result_count": count, "errors": errors, "hot_p50_ms": p50}


def test_classifies_real_empty_and_failing_legs(tmp_path):
    for side in "AB":
        for rep in (1, 2):
            _run(tmp_path, side, rep, [_row("Q1", "ivg-os", 186), _row("Q6", "ivg-os", 0), _row("Q3", "ivg-arno", 0, errors=20), _row("Q2", "ivg-os", 550)])
    rows = {(r["query"], r["path"]): r for r in v.bench_q_rows(tmp_path)}
    assert rows[("Q1", "ivg-os")]["status"] == "real" and rows[("Q1", "ivg-os")]["count_A"] == 186
    assert rows[("Q6", "ivg-os")]["status"] == "empty on both sides"
    assert rows[("Q3", "ivg-arno")]["status"] == "errors on both sides"


def test_a_difference_between_the_sides_is_reported_first(tmp_path):
    _run(tmp_path, "A", 1, [_row("Q1", "ivg-os", 186)])
    _run(tmp_path, "B", 1, [_row("Q1", "ivg-os", 185)])
    assert v.bench_q_rows(tmp_path)[0]["status"] == "answers differ"


def test_render_lists_only_legs_that_are_not_real_work_when_asked(tmp_path):
    _run(tmp_path, "A", 1, [_row("Q1", "ivg-os", 186), _row("Q6", "ivg-os", 0)])
    _run(tmp_path, "B", 1, [_row("Q1", "ivg-os", 186), _row("Q6", "ivg-os", 0)])
    out = v.render(v.bench_q_rows(tmp_path), only_problems=True)
    assert "Q6" in out and "Q1" not in out
