"""Spec 253: the native path translates a statement once per query and parameter types, not on every call.

The translation only names and types the native answer (FR-002): its columns and decoders depend on the statement's
shape, never on a parameter's value, so a repeated query reuses it. The plan, which carries the values, is built per
call.
"""

from unittest.mock import MagicMock, patch

from iris_vector_graph._engine import query as query_mod
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

Q = "MATCH (p:Person) WHERE p.sid = $id RETURN p.firstName"


def _engine():
    e = IRISGraphEngine(MagicMock(), native_cypher=True)
    e._adjacency_indexed_cached = True
    e._in_bulk_load = False
    e._native_privileges = lambda: True
    e._native_executor_cached = True
    e._run_native_plan = MagicMock(side_effect=lambda plan, params: IVGResult(columns=list(plan["columns"]), rows=[]))
    return e


def test_a_repeated_query_is_translated_once_and_planned_each_time():
    e = _engine()
    with patch.object(query_mod, "translate_to_sql", wraps=query_mod.translate_to_sql) as tr:
        for sid in ("a", "b", "c"):
            assert e._try_native(parse_query(Q), {"id": sid}) is not None
    assert tr.call_count == 1
    plans = [c.args[0] for c in e._run_native_plan.call_args_list]
    assert [p["nodes"][0]["props"][0][1]["v"] for p in plans] == ["a", "b", "c"]


def test_another_parameter_type_is_translated_again():
    e = _engine()
    with patch.object(query_mod, "translate_to_sql", wraps=query_mod.translate_to_sql) as tr:
        e._try_native(parse_query(Q), {"id": "a"})
        e._try_native(parse_query(Q), {"id": "b", "extra": 1})
    assert tr.call_count == 2


def test_answers_from_one_translation_share_no_mutable_state():
    e = _engine()
    _, first = e._try_native(parse_query(Q), {"id": "a"})
    _, second = e._try_native(parse_query(Q), {"id": "b"})
    assert first.metadata is not second.metadata
    assert first.metadata.warnings is not second.metadata.warnings
    assert first.bolt_column_types is not second.bolt_column_types
