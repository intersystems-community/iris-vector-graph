"""4.6.0: a CREATE's computed value over literals is worked out before the query runs, and a CREATE after an UNWIND of a
list the query computes runs once per row (live: tests/e2e/test_460_create_after_unwind_e2e.py).

Each expected value is openCypher's, worked by hand: comparisons and boolean operators are three-valued (null when an
operand is null or the two are of different types and ordered), a simple CASE compares with `=`, a CASE with no
branch that holds and no ELSE is null, coalesce answers its first non-null argument.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.merge_rows import plan_row_create
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _NOT_CONSTANT, _fold_constant


def _value(expr_text: str):
    q = parse_query(f"CREATE (:X {{p: {expr_text}}})")
    return _fold_constant(q.query_parts[0].clauses[0].patterns[0].nodes[0].properties["p"])


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("CASE WHEN 2 > 1 THEN 'big' ELSE 'small' END", "big"),
        ("CASE WHEN 1 > 1 THEN 'big' ELSE 'small' END", "small"),
        ("CASE WHEN 1 > 1 THEN 'big' END", None),
        ("CASE WHEN null THEN 1 ELSE 2 END", 2),
        ("CASE 2 WHEN 1 THEN 'one' WHEN 2 THEN 'two' END", "two"),
        ("CASE 2 WHEN 2.0 THEN 'same' ELSE 'not' END", "same"),
        ("CASE '2' WHEN 2 THEN 'same' ELSE 'not' END", "not"),
        ("CASE null WHEN null THEN 'null' ELSE 'not' END", "not"),
        ("2 >= 3", False),
        ("'a' < 'b'", True),
        ("1 < 'b'", None),
        ("1 = 'a'", False),
        ("null = null", None),
        ("1 <> 2", True),
        ("true AND null", None),
        ("false AND null", False),
        ("true OR null", True),
        ("true XOR false", True),
        ("NOT true", False),
        ("toUpper('ab')", "AB"),
        ("toLower('AB')", "ab"),
        ("trim('  a ')", "a"),
        ("toUpper(null)", None),
        ("coalesce(null, 3, 4)", 3),
        ("coalesce(null, null)", None),
    ],
)
def test_a_computed_value_over_literals(expr, expected):
    value = _value(expr)
    assert value == expected and type(value) is type(expected)


@pytest.mark.parametrize("expr", ["toUpper(1)", "rand()", "CASE WHEN rand() > 0.5 THEN 1 END", "1 =~ 'a'"])
def test_what_is_not_worked_out_is_left(expr):
    assert _value(expr) is _NOT_CONSTANT


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:G) WITH collect(n.node_id) AS ids UNWIND ids AS i CREATE (:C {src: i})",
        "MATCH (n:G) UNWIND [1, 2] AS k CREATE (:C {src: n.node_id, k: k})",
        "MATCH (n:G) WITH n.k AS k UNWIND [k, k * 10] AS v CREATE (:C {v: v})",
    ],
)
def test_a_create_after_an_unwind_of_the_querys_rows_runs_per_row(q):
    plan = plan_row_create(parse_query(q), {})
    assert plan is not None
    # the UNWIND is read in the prefix, and the suffix is the CREATE alone
    assert [type(c).__name__ for p in plan.suffix_parts for c in p.clauses] == ["CreateClause"]


@pytest.mark.parametrize(
    "q",
    [
        # a query that starts with its UNWIND is unrolled statically, one statement set per element
        "UNWIND [1, 2] AS i CREATE (:C {i: i})",
        "UNWIND $rows AS r CREATE (:C {id: r.id})",
        # a MATCH then CREATE names each row by the MATCH's rows
        "MATCH (n:G) CREATE (:C {src: n.node_id})",
    ],
)
def test_the_static_shapes_stay_static(q):
    assert plan_row_create(parse_query(q), {"rows": [{"id": 1}]}) is None
