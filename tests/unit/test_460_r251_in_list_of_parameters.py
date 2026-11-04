"""4.6.0 R251: ``WHERE n.id IN [$a, $b]`` answers its rows instead of failing.

A list literal on the right of IN bound each element that was not a literal as itself: the parameter's AST node, which
the driver refused with ``<ARGUMENT ERROR> Bad argument given; Details: Unsupported argument type``, on the native
executor and the SQL path alike (the native planner declines IN, so both ran the same SQL; fixes26 saw it natively).
``IN $list`` and ``IN ['a', 'b']`` worked. Now a parameter element binds its value, as a literal element does (a null
one is a null element, translated as ``IN [.., null]`` is), and any other element (``n.x``) is translated.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _q(q, params):
    r = translate_to_sql(parse_query(q), params)
    return r.sql if isinstance(r.sql, str) else r.sql[-1], [p for ps in r.parameters for p in ps]


def _no_ast(params):
    """No bound value is a Cypher AST node (what the driver refused)."""
    return not any(type(p).__module__ == ast.__name__ for p in params)


@pytest.mark.parametrize(
    "q, params, bound",
    [
        ("MATCH (n:L) WHERE n.s IN [$a, $b] RETURN n.s", {"a": "x", "b": "y"}, ["x", "y"]),
        ("MATCH (n:L) WHERE n.s IN [$a, 'z'] RETURN n.s", {"a": "x"}, ["x", "z"]),
        ("MATCH (n:L) WHERE n.k IN [$a, $b] RETURN n.k", {"a": 1, "b": 2}, [1, 2]),
    ],
)
def test_a_parameter_element_binds_its_value(q, params, bound):
    sql, ps = _q(q, params)
    assert _no_ast(ps), ps
    assert all(b in ps for b in bound), (ps, bound)
    assert sql.count("?") == len(ps), (sql, ps)


def test_a_null_parameter_is_a_null_element_as_a_literal_null_is():
    with_param, ps = _q("MATCH (n:L) WHERE n.s IN [$a, $b] RETURN n.s", {"a": "x", "b": None})
    with_literal, _ = _q("MATCH (n:L) WHERE n.s IN [$a, null] RETURN n.s", {"a": "x"})
    assert _no_ast(ps) and None not in ps, ps
    assert with_param == with_literal


def test_a_property_element_is_translated():
    sql, ps = _q("MATCH (n:L), (m:M) WHERE n.s IN [m.s, $a] RETURN n.s", {"a": "x"})
    assert _no_ast(ps), ps
    assert sql.count("?") == len(ps), (sql, ps)
