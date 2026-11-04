"""4.6.0 R261, R263 (the CREATE case): a CREATE writes a computed property value, with its type (offline: the binds).

`UNWIND [1,2] AS i CREATE (:X {f: i * 0.5})`, `{s: toString(i)}` and `CREATE (:L {d: 2.5*2})` raised `<LIST ERROR>`
(2026.1) or `<ARGUMENT ERROR>` (2026.3) and wrote nothing: the UNWIND binds `i` to each element as a literal, and
the property's expression, a constant by then, reached the INSERT as its AST node, bound as a parameter. The value
is now worked out before the INSERT and bound as the Python value it is, as a literal's is: a float binds as a
float (stored as an IRIS double, which reads back as a float), an integer as an integer, a string as a string.

Worked by hand (openCypher): 1 * 0.5 = 0.5, 2 * 0.5 = 1.0 (a float: one operand is), 2.5 * 2 = 5.0, toString(1) =
'1', 1 / 2 = 0 and 2 / 2 = 1 (integer division), -7 / 2 = -3 (truncated toward zero), -7 % 2 = -1 (the dividend's
sign), 1 + 2 = 3, 'a' + 'b' = 'ab'. The live answers, under both driver versions, are in
tests/e2e/test_460_r261_create_computed_values_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _prop_binds(query: str, params=None):
    """{key: [value, ...]} bound by the rdf_props INSERTs of a CREATE, in statement order."""
    r = translate_to_sql(parse_query(query), dict(params or {}))
    stmts = r.sql if isinstance(r.sql, list) else [r.sql]
    binds = r.parameters
    out: dict = {}
    for sql, p in zip(stmts, binds):
        flat = list(p)
        assert not any(isinstance(v, tuple(_AST_TYPES)) for v in flat), (sql, flat)
        if "rdf_props" in sql and sql.lstrip().upper().startswith("INSERT") and "SELECT ?, ?, ?" in sql:
            # SELECT ?, ?, ? binds node id, key, value
            out.setdefault(flat[1], []).append(flat[2])
    return out


_AST_TYPES = [
    getattr(ast, n) for n in dir(ast) if isinstance(getattr(ast, n), type) and getattr(ast, n).__module__ == ast.__name__
]


def _typed(values):
    return [(type(v).__name__, v) for v in values]


@pytest.mark.parametrize(
    "query,key,want",
    [
        ("UNWIND [1, 2] AS i CREATE (:X {f: i * 0.5})", "f", [0.5, 1.0]),
        ("UNWIND [1, 2] AS i CREATE (:X {s: toString(i)})", "s", ["1", "2"]),
        ("CREATE (:L {d: 2.5 * 2})", "d", [5.0]),
        ("UNWIND [1, 2] AS i CREATE (:X {q: i / 2})", "q", [0, 1]),
        ("UNWIND [1, 2] AS i CREATE (:X {n: -i})", "n", [-1, -2]),
        ("CREATE (:L {q: -7 / 2})", "q", [-3]),
        ("CREATE (:L {m: -7 % 2})", "m", [-1]),
        ("CREATE (:L {m: 1 + 2})", "m", [3]),
        ("CREATE (:L {s: 'a' + 'b'})", "s", ["ab"]),
        ("CREATE (:L {s: toString(2.5 * 2)})", "s", ["5.0"]),
        ("CREATE (:L {f: toFloat(3)})", "f", [3.0]),
        ("CREATE (:L {i: toInteger(3.7)})", "i", [3]),
    ],
)
def test_a_constant_property_expression_binds_its_typed_value(query, key, want):
    assert _typed(_prop_binds(query)[key]) == _typed(want)


def test_a_parameter_list_unwound_binds_each_computed_value():
    got = _prop_binds("UNWIND $xs AS i CREATE (:X {f: i * 0.5})", {"xs": [1, 2]})
    assert _typed(got["f"]) == _typed([0.5, 1.0])


def test_a_float_product_that_is_whole_stays_a_float():
    # 2 * 0.5 is 1.0, not 1: the float operand makes it one, whatever its value
    got = _prop_binds("CREATE (:L {f: 2 * 0.5})")
    assert _typed(got["f"]) == [("float", 1.0)]
