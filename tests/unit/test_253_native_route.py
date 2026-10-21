"""Spec 253: native skips the small queries SQL answers faster.

Both paths give the same answer (the shadow gate), so the choice is speed only. Each query shape (the parsed query and
its parameter types) is first timed natively. A shape native answers in under `native_route_ms` is then timed on the
SQL path the same number of times, and whichever path's median is lower answers it from then on. A shape native takes
`native_route_ms` or more for never runs on SQL, and neither does one whose first timed SQL run takes several times
native's median. Each path's first run is a warm-up (a statement's first SQL run compiles it) and is not counted.
Shadow mode times nothing: every planned query runs on both paths there.
"""

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine import query as query_mod
from iris_vector_graph.cypher.native_route import NativeRoute
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _drive(route, native_ms, sql_ms, calls=20):
    """The paths a route picks over `calls` calls, each path taking the given time."""
    picked = []
    for _ in range(calls):
        path = route.next_path()
        picked.append(path)
        route.record(path, native_ms if path == "native" else sql_ms)
    return picked


def test_a_small_shape_sql_answers_faster_moves_to_sql():
    picked = _drive(NativeRoute(small_ms=50, trials=3), native_ms=2.0, sql_ms=1.0)
    assert picked[:4] == ["native"] * 4  # one warm-up, three timed
    assert picked[4:8] == ["sql"] * 4
    assert set(picked[8:]) == {"sql"}


def test_a_small_shape_native_answers_faster_stays_native():
    picked = _drive(NativeRoute(small_ms=50, trials=3), native_ms=1.0, sql_ms=2.0)
    assert picked[4:8] == ["sql"] * 4
    assert set(picked[8:]) == {"native"}


def test_a_shape_native_takes_long_for_is_never_timed_on_sql():
    assert set(_drive(NativeRoute(small_ms=50, trials=3), native_ms=80.0, sql_ms=1.0)) == {"native"}


def test_a_much_slower_sql_run_ends_the_trial_at_once():
    picked = _drive(NativeRoute(small_ms=50, trials=3), native_ms=10.0, sql_ms=240.0)
    assert picked[4:6] == ["sql", "sql"]  # the warm-up, then one timed run
    assert set(picked[6:]) == {"native"}


def test_the_warm_up_runs_are_not_counted():
    route = NativeRoute(small_ms=50, trials=3)
    times = {"native": [500.0, 2.0, 2.0, 2.0], "sql": [900.0, 1.0, 1.0, 1.0]}
    for _ in range(8):
        path = route.next_path()
        route.record(path, times[path].pop(0))
    assert route.next_path() == "sql"


Q = "MATCH (p:Person) WHERE p.sid = $id RETURN p.firstName"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _engine(native_ms, sql_ms, route_ms=50):
    clock = _Clock()
    e = IRISGraphEngine(MagicMock(), native_cypher=True, native_route_ms=route_ms)
    e._adjacency_indexed_cached = True
    e._in_bulk_load = False
    e._native_privileges = lambda: True

    def native(plan, params):
        clock.t += native_ms / 1000
        return IVGResult(columns=list(plan["columns"]), rows=[["native"]])

    def sql(parsed, params, procedures=None):
        clock.t += sql_ms / 1000
        return IVGResult(columns=["p_firstName"], rows=[["sql"]])

    e._run_native_plan = MagicMock(side_effect=native)
    e._execute_parsed_sql = MagicMock(side_effect=sql)
    return e, clock


def _answers(e, clock, calls=12):
    from iris_vector_graph.cypher.parser import parse_query

    with patch.object(query_mod.time, "perf_counter", clock):
        return [e._execute_parsed(parse_query(Q), {"id": str(i)}).rows[0][0] for i in range(calls)]


def test_the_engine_moves_a_small_shape_to_sql_where_sql_is_faster():
    e, clock = _engine(native_ms=2.0, sql_ms=1.0)
    answers = _answers(e, clock)
    assert answers[:4] == ["native"] * 4 and set(answers[8:]) == {"sql"}
    assert e._native_routed_sql == 1


def test_the_engine_keeps_native_where_native_is_faster():
    e, clock = _engine(native_ms=1.0, sql_ms=2.0)
    assert set(_answers(e, clock)[8:]) == {"native"}
    assert getattr(e, "_native_routed_sql", 0) == 0


def test_route_ms_zero_never_times_sql():
    e, clock = _engine(native_ms=2.0, sql_ms=1.0, route_ms=0)
    assert set(_answers(e, clock)) == {"native"}
    e._execute_parsed_sql.assert_not_called()


def test_shadow_mode_runs_both_paths_on_every_call():
    e, clock = _engine(native_ms=2.0, sql_ms=1.0)
    e.cypher_shadow = True
    e._shadow_compare = MagicMock()
    assert set(_answers(e, clock)) == {"native"}
    assert e._shadow_compare.call_count == 12


def test_an_unplanned_query_is_never_timed():
    from iris_vector_graph.cypher.parser import parse_query

    e, clock = _engine(native_ms=2.0, sql_ms=1.0)
    with patch.object(query_mod.time, "perf_counter", clock):
        for _ in range(5):
            e._execute_parsed(parse_query("MATCH (p:Person) RETURN p.sid ORDER BY p.missing + 1"), {})
    assert getattr(e, "_native_routes", {}) == {}


@pytest.mark.parametrize("value, expected", [("0", 0.0), ("12.5", 12.5)])
def test_the_environment_sets_the_route_threshold(monkeypatch, value, expected):
    monkeypatch.setenv("IVG_NATIVE_ROUTE_MS", value)
    assert IRISGraphEngine(MagicMock()).native_route_ms == expected


def test_the_default_route_threshold(monkeypatch):
    monkeypatch.delenv("IVG_NATIVE_ROUTE_MS", raising=False)
    assert IRISGraphEngine(MagicMock()).native_route_ms == 50.0


@pytest.mark.parametrize("failure", ["error", "raise"])
def test_a_failed_sql_run_gives_the_shape_back_to_native(failure):
    e, clock = _engine(native_ms=2.0, sql_ms=1.0)

    def sql(parsed, params, procedures=None):
        clock.t += 0.001
        if failure == "raise":
            raise RuntimeError("translator defect")
        return IVGResult(columns=[], rows=[], error="SQLCODE -400")

    e._execute_parsed_sql = MagicMock(side_effect=sql)
    answers = _answers(e, clock)
    assert set(answers) == {"native"}  # the failing call itself is answered natively
    assert e._execute_parsed_sql.call_count == 1
