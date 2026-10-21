"""Spec 253, T010: what keeps a query on the SQL path.

The planner answers None (SQL path, unchanged) for anything it does not support: every write, a procedure call,
UNION, an unsupported function. The engine runs a plan natively only when every gate holds: `native_cypher` on
(FR-005), the adjacency index declared (FR-003), not inside `bulk_load_session` (FR-014), and SELECT on the four
tables (FR-011).
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.native_plan import native_gate, plan_query
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (n:A)",
        "MATCH (n) SET n.x = 1",
        "MATCH (n) DETACH DELETE n",
        "MERGE (n:A {id: 'x'})",
        "CALL db.labels()",
        "MATCH (n) RETURN n UNION MATCH (m) RETURN m",
        "MATCH (n) RETURN someUnknownFunction(n)",
    ],
)
def test_the_planner_declines(cypher):
    assert plan_query(parse_query(cypher), {}) is None


def _engine(**kw):
    kw.setdefault("native_cypher", True)
    e = IRISGraphEngine(MagicMock(), **kw)
    e._adjacency_indexed_cached = True
    e._in_bulk_load = False
    e._native_privileges = lambda: True
    e._native_executor_cached = True
    return e


def test_every_gate_open_lets_a_plan_run():
    assert native_gate(_engine()) is None


def test_native_cypher_false_closes_it():
    assert native_gate(_engine(native_cypher=False)) == "native_cypher=False"


def test_no_adjacency_index_closes_it():
    e = _engine()
    e._adjacency_indexed_cached = False
    assert native_gate(e) == "adjacency index not declared"


def test_a_bulk_load_session_closes_it():
    e = _engine()
    e._in_bulk_load = True
    assert native_gate(e) == "inside bulk_load_session"


def test_a_missing_privilege_closes_it():
    e = _engine()
    e._native_privileges = lambda: False
    assert native_gate(e) == "missing SELECT on a Graph_KG table"


def test_the_option_is_on_once_the_shadow_gate_is_green(monkeypatch):
    # FR-010: on by default since the TCK (3896/3896) and the 40 SNB queries at SF0.1 are equal in shadow mode
    monkeypatch.delenv("IVG_NATIVE_CYPHER", raising=False)
    assert IRISGraphEngine(MagicMock()).native_cypher is True


def test_the_environment_or_the_engine_can_turn_it_off(monkeypatch):
    monkeypatch.setenv("IVG_NATIVE_CYPHER", "0")
    assert IRISGraphEngine(MagicMock()).native_cypher is False
    monkeypatch.delenv("IVG_NATIVE_CYPHER", raising=False)
    assert IRISGraphEngine(MagicMock(), native_cypher=False).native_cypher is False
