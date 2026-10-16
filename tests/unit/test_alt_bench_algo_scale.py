"""The graph-algorithm scale-up: a seeded random graph every engine loads, and the timing summary."""

from scripts.bench.alt import algo_scale as a


def test_er_edges_are_deterministic_simple_and_the_requested_size():
    e1, e2 = a.er_edges(500, 6, seed=1), a.er_edges(500, 6, seed=1)
    assert e1 == e2 and e1 != a.er_edges(500, 6, seed=2)
    assert len(e1) == 1500 and len(set(e1)) == 1500
    assert all(u < v and 0 <= u and v < 500 for u, v in e1)


def test_node_ids_are_strings_the_engines_share():
    assert a.node_id(7) == "a7"


def test_summarise_gives_median_and_spread_of_the_repetitions():
    s = a.summarise([3.0, 1.0, 2.0])
    assert s["median_s"] == 2.0 and s["min_s"] == 1.0 and s["max_s"] == 3.0 and s["reps"] == 3


def test_pearson_of_identical_scores_is_one_and_of_a_constant_is_nan():
    x = {"a": 1.0, "b": 2.0, "c": 5.0}
    assert abs(a.pearson(x, x) - 1.0) < 1e-12
    assert a.pearson(x, {"a": 1.0, "b": 1.0, "c": 1.0}) != a.pearson(x, {"a": 1.0, "b": 1.0, "c": 1.0})  # nan


def test_the_worker_can_be_limited_to_some_legs():
    import inspect

    src = inspect.getsource(a.run_worker)
    assert "args.only" in src and "--only" in inspect.getsource(a.main)


def test_compare_counts_a_missing_node_as_zero_and_flags_foreign_ids():
    ref = {"a0": 4.0, "a1": 0.0, "a2": 2.0}
    r = a.compare_to_reference({"a0": 8.0, "a2": 4.0, "a9": 1.0}, ref)
    assert r["foreign_ids"] == 1 and r["scored"] == 3
    assert abs(r["pearson"] - 1.0) < 1e-9 and abs(r["mean_ratio"] - 2.0) < 1e-9


def test_exact_betweenness_lifts_the_source_cap_and_the_default_call_is_kept_apart():
    import inspect

    src = inspect.getsource(a.run_worker)
    assert "BetweennessGlobalParallel" in src and "10**9" in src and "betweenness_default" in src and "delete_nodes_by_prefix" in src
