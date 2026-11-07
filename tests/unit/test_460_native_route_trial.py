"""The native/SQL route's trial picks the path that is faster in steady state (4.6.0 speed gate).

The 4.6.0 gate's cypher_one_hop ran 86-94% slower than a42db224, at the same native executor, because its route
(cypher.native_route) settled on SQL from three runs per path. A path's first runs can be unrepresentative (a statement
just compiled, caches just filled, a busy host), and three runs decided for good. The trial now:

- discards each path's first run (a warm-up);
- interleaves the two paths' timed runs, so a passing disturbance hits both;
- times at least 5 runs per path and compares medians;
- moves off the path it holds (native, until a trial says otherwise) only for a median at least `margin` lower;
- runs again after `recheck` calls, and sooner when the held path's recent median drifts to `drift` times its trial
  median.

A failed SQL run gives the shape to native for good: no later trial times SQL again.
"""

from unittest.mock import MagicMock, patch

from iris_vector_graph._engine import query as query_mod
from iris_vector_graph.cypher.native_route import NativeRoute
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _drive(route, native, sql, calls):
    """The paths a route picks over `calls` calls; `native` and `sql` give a path's time for its n-th run (from 0)."""
    runs = {"native": 0, "sql": 0}
    picked = []
    for _ in range(calls):
        path = route.next_path()
        picked.append(path)
        fn = native if path == "native" else sql
        route.record(path, fn(runs[path]))
        runs[path] += 1
    return picked


def _steady(ms):
    return lambda n: ms


def _fast_first(fast_ms, fast_runs, steady_ms, warm_ms=200.0):
    """A warm-up of `warm_ms`, then `fast_runs` runs of `fast_ms`, then `steady_ms` from then on."""
    return lambda n: warm_ms if n == 0 else (fast_ms if n <= fast_runs else steady_ms)


def test_a_path_whose_first_timed_runs_are_fast_does_not_win_on_them():
    # SQL: compile, then two runs at a third of its steady time (fresh caches), then 3 ms; native 1 ms throughout
    route = NativeRoute(small_ms=50)
    picked = _drive(route, _steady(1.0), _fast_first(0.3, 2, 3.0), calls=60)
    assert route.choice == "native"
    assert set(picked[-20:]) == {"native"}


def test_the_trial_times_at_least_five_runs_a_path_after_a_warm_up():
    route = NativeRoute(small_ms=50)
    trial = []
    while route.choice is None:
        path = route.next_path()
        trial.append(path)
        route.record(path, 2.0 if path == "native" else 1.0)
    assert trial[0] == "native"  # native first: a shape slow on native is never timed on SQL
    assert trial.count("native") >= 6 and trial.count("sql") >= 6  # each: a warm-up and 5 timed runs
    assert route.choice == "sql"


def test_the_timed_runs_are_interleaved():
    route = NativeRoute(small_ms=50)
    picked = []
    while route.choice is None:
        path = route.next_path()
        picked.append(path)
        route.record(path, 2.0 if path == "native" else 1.0)
    first_sql = picked.index("sql")
    after = picked[first_sql + 2:]  # past SQL's warm-up and its first timed run
    assert all(a != b for a, b in zip(after, after[1:])), picked


def test_a_near_tie_keeps_native():
    route = NativeRoute(small_ms=50)
    _drive(route, _steady(1.0), _steady(0.9), calls=40)
    assert route.choice == "native"


def test_a_clear_sql_win_moves_the_shape():
    route = NativeRoute(small_ms=50)
    _drive(route, _steady(1.0), _steady(0.7), calls=40)
    assert route.choice == "sql"


def test_a_misroute_from_unrepresentative_runs_is_corrected_when_the_held_path_drifts():
    # SQL's warm-up, then its first 6 runs look 3x faster than native, then it settles at 3 ms
    route = NativeRoute(small_ms=50)
    picked = _drive(route, _steady(1.0), _fast_first(0.3, 6, 3.0), calls=400)
    assert "sql" in picked[:40]
    assert route.choice == "native"
    assert set(picked[-50:]) == {"native"}


def test_the_shape_is_timed_again_after_recheck_calls():
    # native wins at first; after 100 calls native slows to 5 ms (the graph grew) while SQL stays at 2 ms. Drift is
    # turned off, so only the periodic re-check can see it.
    route = NativeRoute(small_ms=50, recheck=100, drift=None)
    native = lambda n: 1.0 if n < 100 else 5.0  # noqa: E731
    picked = _drive(route, native, _steady(2.0), calls=400)
    assert route.choice == "sql"
    assert picked[-50:].count("sql") >= 40  # the later re-checks time native a few times


def test_a_re_check_needs_the_margin_to_move_off_the_held_path():
    route = NativeRoute(small_ms=50, recheck=50, drift=None)
    _drive(route, _steady(1.0), _steady(0.5), calls=40)
    assert route.choice == "sql"
    # native now a hair faster than SQL: within the margin, SQL keeps the shape through the re-checks
    picked = _drive(route, _steady(0.45), _steady(0.5), calls=300)
    assert route.choice == "sql"
    assert picked.count("native") < 60  # only the re-checks' timed runs


def test_a_failed_sql_run_keeps_the_shape_native_through_re_checks():
    route = NativeRoute(small_ms=50, recheck=20)
    route.sql_failed()
    picked = _drive(route, _steady(5.0), _steady(0.1), calls=200)
    assert set(picked) == {"native"}


def test_a_shape_native_takes_long_for_is_never_timed_on_sql_even_on_re_checks():
    route = NativeRoute(small_ms=50, recheck=20)
    assert set(_drive(route, _steady(80.0), _steady(1.0), calls=200)) == {"native"}


Q = "MATCH (p:Person) WHERE p.sid = $id RETURN p.firstName"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_the_engine_keeps_native_when_sql_only_starts_fast():
    """The gate's shape: SQL's first timed runs are fast, its steady state twice native's."""
    from iris_vector_graph.cypher.parser import parse_query

    clock = _Clock()
    e = IRISGraphEngine(MagicMock(), native_cypher=True, native_route_ms=50)
    e._adjacency_indexed_cached = True
    e._in_bulk_load = False
    e._native_privileges = lambda: True
    e._native_executor_cached = True
    sql_runs = []

    def native(plan, params):
        clock.t += 0.0009
        return IVGResult(columns=list(plan["columns"]), rows=[["native"]])

    def sql(parsed, params, procedures=None):
        n = len(sql_runs)
        sql_runs.append(n)
        clock.t += (0.2 if n == 0 else 0.0006 if n <= 2 else 0.0018)
        return IVGResult(columns=["p_firstName"], rows=[["sql"]])

    e._run_native_plan = MagicMock(side_effect=native)
    e._execute_parsed_sql = MagicMock(side_effect=sql)
    with patch.object(query_mod.time, "perf_counter", clock):
        answers = [e._execute_parsed(parse_query(Q), {"id": str(i)}).rows[0][0] for i in range(80)]
    assert set(answers[-30:]) == {"native"}
    assert getattr(e, "_native_routed_sql", 0) == 0
