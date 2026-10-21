"""Spec 253: the deployed executor and the planner agree on the plan format, or native stays off.

On 2026-10-21 a namespace whose Graph.KG.Cypher.Exec predated the planner by one commit answered BI01 wrongly on
every parameter set and ran BI11 past a 120 s timeout: the executor ignored plan fields it did not know. These pin the
handshake on a deployed namespace: the executor reports the planner's version, refuses a plan of another, and an
engine whose planner writes another version answers on the SQL path.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from iris_vector_graph.cypher import native_plan
from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
Q = "MATCH (n:Ver253) WHERE n.k = $k RETURN n.k AS k"


@pytest.fixture
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    e.create_node(f"ver{T}", labels=["Ver253"], properties={"k": T})
    yield e
    e.delete_node(f"ver{T}")


def test_the_deployed_executor_reads_the_planner_s_version(engine):
    assert int(engine._iris_obj().classMethodValue("Graph.KG.Cypher.Exec", "PlanVersion")) == native_plan.PLAN_VERSION


def test_the_executor_refuses_a_plan_of_another_version(engine):
    plan = {"family": "A", "v": native_plan.PLAN_VERSION + 1, "columns": ["k"]}
    with pytest.raises(Exception, match="plan version"):
        engine._iris_obj().classMethodValue("Graph.KG.Cypher.Exec", "Run", json.dumps(plan), "{}")


def test_a_current_executor_answers_natively(engine):
    assert engine.execute_cypher(Q, {"k": T}).rows == [[T]]
    assert getattr(engine, "_native_answers", 0) == 1


def test_a_planner_of_another_version_answers_on_sql(engine, monkeypatch):
    monkeypatch.setattr(native_plan, "PLAN_VERSION", native_plan.PLAN_VERSION + 1)
    assert engine.execute_cypher(Q, {"k": T}).rows == [[T]]
    assert getattr(engine, "_native_answers", 0) == 0
