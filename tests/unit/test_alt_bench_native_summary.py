"""scripts/bench/alt/native_summary: IVG against Neo4j and Memgraph on the parameter sets both ran.

The IVG harness shortens a slow query's leg to its first few parameter sets (`plan` = [sets, warmup, reps]); the
competitors time all of them. Medians over different sets compare different work: IC6 at SF1 ran 5 sets on IVG, three
of them empty, against Neo4j's 20 (5 ms against 1,770). Both sides' timings are kept per round over the sets in order,
so the competitor's are cut to the sets IVG ran.
"""

from scripts.bench.alt import native_summary as S


def test_a_competitor_s_timings_are_cut_to_the_sets_ivg_ran():
    # 3 rounds over 4 sets; IVG ran the first 2
    values = [10, 20, 30, 40, 11, 21, 31, 41, 12, 22, 32, 42]
    assert S.on_sets(values, sets=4, keep=2) == [10, 20, 11, 21, 12, 22]


def test_all_sets_kept_is_every_value():
    assert S.on_sets([1, 2, 3], sets=3, keep=3) == [1, 2, 3]


def test_a_row_compares_medians_and_answers_on_the_common_sets():
    ivg = {"values_ms": [5, 6, 5, 6], "signatures": ["a", "b"], "plan": [2, 1, 2]}
    neo = {"values_ms": [50, 60, 70, 80, 51, 61, 71, 81], "params": {"signatures": ["a", "x", "c", "d"]}}
    row = S.row("Q", ivg, neo, None)
    assert row["ivg"] == 5.5 and row["neo4j"] == 55.5
    assert (row["agree"], row["compared"]) == (1, 2)


def test_a_query_ivg_did_not_answer_has_no_ratio():
    row = S.row("Q", {"values_ms": [], "signatures": [], "error": "timeout"}, {"values_ms": [1.0], "params": {"signatures": ["a"]}}, None)
    assert row["ratio"] is None and row["error"] == "timeout"
