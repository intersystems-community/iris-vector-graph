"""4.6.0: `UNWIND range(0, $n)` before a write unrolls as `range(0, 3)` does (live: tests/e2e/
test_460_unwind_range_param_create_e2e.py). The literal's statements are the oracle: the same statements, the same
parameters, the generated node ids aside."""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _statements(q, params):
    r = translate_to_sql(parse_query(q), dict(params))
    ids: dict = {}

    def norm(v):
        return _UUID.sub(lambda m: ids.setdefault(m.group(0), f"<id{len(ids)}>"), v) if isinstance(v, str) else v

    return [(norm(s), [norm(p) for p in ps]) for s, ps in zip(r.sql, r.parameters)]


@pytest.mark.parametrize(
    "param_query, params, literal_query",
    [
        ("UNWIND range(0, $n) AS i CREATE (:L {i: i})", {"n": 3}, "UNWIND range(0, 3) AS i CREATE (:L {i: i})"),
        (
            "UNWIND range($a, $b, $s) AS i CREATE (:L {i: i})",
            {"a": 2, "b": 8, "s": 3},
            "UNWIND range(2, 8, 3) AS i CREATE (:L {i: i})",
        ),
        ("UNWIND range(3, $n) AS i CREATE (:L {i: i})", {"n": 0}, "UNWIND range(3, 0) AS i CREATE (:L {i: i})"),
    ],
)
def test_a_parameter_bound_writes_what_the_literal_writes(param_query, params, literal_query):
    assert _statements(param_query, params) == _statements(literal_query, {})


def test_a_bound_that_is_not_an_integer_is_not_unrolled():
    # a string bound is not a number the range can count to: the query keeps the SQL route's reading
    stmts = _statements("UNWIND range(0, $n) AS i CREATE (:L {i: i})", {"n": "3"})
    assert any("IVGRANGE" in s for s, _ in stmts), stmts
