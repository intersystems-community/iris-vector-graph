"""Spec 253: native runs only on an executor that reads this planner's plans.

The planner (Python) and the executor (`Graph.KG.Cypher.Exec`, ObjectScript) ship together but are deployed apart:
upgrading the package without redeploying the classes left an executor that ignored plan fields it predates. On
2026-10-21 such an executor answered BI01 wrongly on every parameter set (a substring key read as a whole property)
and ran BI11 past a 120 s timeout (a cycle's closing hop walked as an open one). The engine asks the executor its plan
version once and stays on SQL unless it matches; every plan carries the version, and the executor refuses another.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

from iris_vector_graph.cypher import native_plan
from iris_vector_graph.cypher.native_plan import PLAN_VERSION, native_gate
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _engine(version):
    e = IRISGraphEngine(MagicMock(), native_cypher=True)
    e._adjacency_indexed_cached = True
    e._in_bulk_load = False
    e._native_privileges = lambda: True
    native = MagicMock()
    if isinstance(version, Exception):
        native.classMethodValue.side_effect = version
    else:
        native.classMethodValue.return_value = version
    e._iris_obj = lambda: native
    return e, native


def test_a_matching_executor_opens_the_gate_and_is_asked_once():
    e, native = _engine(PLAN_VERSION)
    assert native_gate(e) is None
    assert native_gate(e) is None
    native.classMethodValue.assert_called_once_with("Graph.KG.Cypher.Exec", "PlanVersion")


def test_an_executor_of_another_version_keeps_the_sql_path():
    e, _ = _engine(PLAN_VERSION - 1)
    reason = native_gate(e)
    assert reason is not None and "redeploy" in reason


def test_an_executor_too_old_to_report_a_version_keeps_the_sql_path():
    e, _ = _engine(RuntimeError("<METHOD DOES NOT EXIST> PlanVersion"))
    reason = native_gate(e)
    assert reason is not None and "redeploy" in reason


def test_the_version_answer_is_compared_as_a_number():
    e, _ = _engine(str(PLAN_VERSION))  # the Native API may hand back a string
    assert native_gate(e) is None


def test_every_plan_sent_carries_the_planner_s_version():
    e, _ = _engine(PLAN_VERSION)
    e._run_native_plan = MagicMock(side_effect=lambda plan, params: IVGResult(columns=list(plan["columns"]), rows=[]))
    assert e._try_native(parse_query("MATCH (p:Person) WHERE p.sid = $id RETURN p.firstName"), {"id": "a"}) is not None
    assert e._run_native_plan.call_args.args[0]["v"] == PLAN_VERSION


def test_the_executor_source_declares_the_planner_s_version():
    """Bumping one side alone is caught without a container."""
    src = (Path(__file__).parents[2] / "iris_src/src/Graph/KG/Cypher/Exec.cls").read_text()
    assert re.search(r"^Parameter PLANVERSION = (\d+);", src, re.M).group(1) == str(PLAN_VERSION)


def test_a_transient_error_keeps_this_call_on_sql_and_asks_again():
    """A dropped connection is not an old executor: only an error naming the missing method or class is remembered."""
    e, native = _engine(RuntimeError("<COMMUNICATION LINK ERROR> Error code: 54"))
    assert native_gate(e) is not None
    native.classMethodValue.side_effect = None
    native.classMethodValue.return_value = PLAN_VERSION
    assert native_gate(e) is None


def test_a_missing_method_is_remembered():
    e, native = _engine(RuntimeError("<METHOD DOES NOT EXIST> *PlanVersion,Graph.KG.Cypher.Exec"))
    assert native_gate(e) is not None and native_gate(e) is not None
    assert native.classMethodValue.call_count == 1


def test_initialize_schema_asks_the_executor_again():
    """The warning says to redeploy (initialize_schema); an engine that does must see the new executor."""
    e, native = _engine(PLAN_VERSION - 1)
    assert native_gate(e) is not None
    native.classMethodValue.return_value = PLAN_VERSION
    e._native_executor_cached = None  # what initialize_schema does, pinned by the source check below
    assert native_gate(e) is None


def test_initialize_schema_resets_the_executor_answer():
    src = (Path(__file__).parents[2] / "iris_vector_graph/_engine/schema.py").read_text()
    assert "self._native_executor_cached = None" in src
