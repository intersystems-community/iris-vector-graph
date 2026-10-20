"""Spec 253, T013/T014: the native path's plumbing, before any query shape is planned natively.

The planner declines every shape until Phase 2, so these tests hand the engine a plan for one query. They pin:
results come back in pages (FR-015: 25,000 rows intact, never one 3.6 MB-capped string); an executor exception falls
back to the SQL path with one warning per reason (FR-007); the gate keeps a plan from running when native_cypher is
off (FR-005); shadow mode raises on a native answer that differs from SQL and is silent when they agree (FR-006).

Run against `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import logging
import os
import uuid

import pytest

from iris_vector_graph.cypher import native_plan
from iris_vector_graph.cypher.native_shadow import NativeMismatch
from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T = uuid.uuid4().hex[:6]
Q = "MATCH (n:Plumb253 {id: $x}) RETURN n.id AS i"


@pytest.fixture
def engine(iris_connection, monkeypatch):
    e = IRISGraphEngine(iris_connection)
    e.create_node(f"pl{T}", labels=["Plumb253"])
    e._native_privileges_cached = True
    plans = {}

    def fake_plan(query, params):
        return plans.get("plan")

    monkeypatch.setattr(native_plan, "plan_query", fake_plan)
    yield e, plans
    e.delete_node(f"pl{T}")


def test_a_large_native_result_comes_back_in_pages(engine):
    e, plans = engine
    plans["plan"] = {"columns": ["i"], "ordered": True, "ops": [{"op": "range", "n": 25000}]}
    r = e.execute_cypher(Q, {"x": f"pl{T}"})
    assert r.columns == ["i"]
    assert [row[0] for row in r.rows] == list(range(25000))


def test_an_executor_error_falls_back_to_sql_with_one_warning(engine, caplog):
    e, plans = engine
    plans["plan"] = {"columns": ["i"], "ops": [{"op": "no-such-op"}]}
    with caplog.at_level(logging.WARNING):
        first = e.execute_cypher(Q, {"x": f"pl{T}"})
        second = e.execute_cypher(Q, {"x": f"pl{T}"})
    assert first.rows == second.rows == [[f"pl{T}"]]
    assert len([r for r in caplog.records if "fell back to SQL" in r.getMessage()]) == 1


def test_native_cypher_off_never_runs_a_plan(engine):
    e, plans = engine
    e.native_cypher = False
    plans["plan"] = {"columns": ["i"], "ordered": True, "ops": [{"op": "range", "n": 3}]}
    assert e.execute_cypher(Q, {"x": f"pl{T}"}).rows == [[f"pl{T}"]]


def test_shadow_mode_raises_on_a_different_answer(engine):
    e, plans = engine
    e.cypher_shadow = True
    plans["plan"] = {"columns": ["i"], "ordered": True, "ops": [{"op": "range", "n": 1}]}
    with pytest.raises(NativeMismatch):
        e.execute_cypher(Q, {"x": f"pl{T}"})


def test_shadow_mode_is_silent_when_the_answers_agree(engine):
    e, plans = engine
    e.cypher_shadow = True
    plans["plan"] = {"columns": ["i"], "ordered": True, "ops": [{"op": "rows", "rows": [[f"pl{T}"]]}]}
    assert e.execute_cypher(Q, {"x": f"pl{T}"}).rows == [[f"pl{T}"]]
