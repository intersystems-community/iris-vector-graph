"""Spec 253: the native executor may split a large label-scan aggregate across worker processes.

`native_parallel_min` is the estimated work (anchors x the first two hops' sampled fan-out) from which it does
(IVG_NATIVE_PARALLEL_MIN; 0 forces the parallel path, for the shadow gate; a negative value turns it off). The engine
hands it to the executor with each plan.
"""

from unittest.mock import MagicMock

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

Q = "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) RETURN p.sid AS s, count(m) AS n"


def _engine(**kw):
    e = IRISGraphEngine(MagicMock(), native_cypher=True, **kw)
    e._adjacency_indexed_cached = True
    e._in_bulk_load = False
    e._native_privileges = lambda: True
    e._native_executor_cached = True
    e._run_native_plan = MagicMock(side_effect=lambda plan, params: IVGResult(columns=list(plan["columns"]), rows=[]))
    return e


def _sent(e):
    e._try_native(parse_query(Q), {})
    return e._run_native_plan.call_args.args[0]["parallel_min"]


def test_the_default_threshold(monkeypatch):
    monkeypatch.delenv("IVG_NATIVE_PARALLEL_MIN", raising=False)
    assert _sent(_engine()) == 1500


def test_the_environment_sets_it(monkeypatch):
    monkeypatch.setenv("IVG_NATIVE_PARALLEL_MIN", "0")
    assert _sent(_engine()) == 0


def test_an_engine_sets_it_and_a_negative_value_turns_it_off(monkeypatch):
    monkeypatch.delenv("IVG_NATIVE_PARALLEL_MIN", raising=False)
    assert _sent(_engine(native_parallel_min=5)) == 5
    assert _sent(_engine(native_parallel_min=-1)) == -1
