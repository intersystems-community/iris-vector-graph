"""4.7.0 rows 21 and 22, live: the two TCK statement shapes they broke at Prepare answer, and answer right.

Precedence1 [25]-[28] failed with -400 `<LIST>LoadTableFunction+10^%qTable` (row 21, af2fd993) and the Quantifier9-12
invariants with -1 "`)` expected, VARCHAR found" (row 22, 9af1a20d). tests/unit/test_470_tck_unwind_shapes.py pins the
translation; this file proves the answers, worked out by hand from openCypher's semantics (not taken from IVG):

Precedence. With x = a OR (b IS NULL) (IS NULL binds tighter than OR) and y = (a OR b) IS NULL, over a, b in
[true, false, null]:

    a, b        x      eq: x = x   y      neq: x <> y
    T, T        T      T           F      T
    T, F        T      T           F      T
    T, N        T      T           F      T
    F, T        F      T           F      F
    F, F        F      T           F      F
    F, N        T      T           T      F
    N, T        N      null        F      null
    N, F        N      null        T      null
    N, N        T      T           T      F

collect() skips nulls: eq holds seven trues, neq three trues and four falses, so all(x IN eq WHERE x) AND
any(x IN neq WHERE x) is true.

Quantifier. For the three maps, fixedList is the list when fixed is true, else null; inputList the list when fixed is
false, else [1]: ([2], [1]), ([6], [1]), (null, [1, 2, 3]).

    IVG_NAMESPACE=IVG470TCK IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_tck_unwind_shapes_e2e.py

The queries read no graph data, so the namespace's contents do not matter and nothing is written.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

PAIRS = "UNWIND [true, false, null] AS a UNWIND [true, false, null] AS b "
EQ = "collect((a OR b IS NULL) = (a OR (b IS NULL))) AS eq"
NEQ = "collect((a OR b IS NULL) <> ((a OR b) IS NULL)) AS neq"
PRECEDENCE = f"{PAIRS}WITH {EQ}, {NEQ} RETURN all(x IN eq WHERE x) AND any(x IN neq WHERE x) AS result"
QUANTIFIER = (
    "UNWIND [{list: [2], fixed: true}, {list: [6], fixed: true}, {list: [1, 2, 3], fixed: false}] AS input "
    "WITH CASE WHEN input.fixed THEN input.list ELSE null END AS fixedList, "
    "CASE WHEN NOT input.fixed THEN input.list ELSE [1] END AS inputList "
    "RETURN fixedList, inputList"
)


@pytest.fixture(scope="module")
def engs():
    conn = ns463.connect()
    yield ns463.engines(conn)
    conn.close()


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_precedence_shape_answers_true(engs, route):
    assert ns463.typed(ns463.rows(engs[route], PRECEDENCE)) == [[(True, "bool")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_precedence_collects_list_booleans(engs, route):
    """Row 21's intent kept: each collect() lists true / false, not 1 / 0, and skips the nulls."""
    rows = ns463.rows(engs[route], f"{PAIRS}WITH {EQ}, {NEQ} RETURN eq, neq")
    assert len(rows) == 1
    eq, neq = rows[0]
    assert ns463.typed([sorted(eq)]) == [[(True, "bool")] * 7]
    assert ns463.typed([sorted(neq)]) == [[(False, "bool")] * 4 + [(True, "bool")] * 3]


LIST_PRED = (
    f"{PAIRS}UNWIND [[], [true], [false], [null], [true, false], [true, false, null]] AS c "
    "WITH collect((a {op} b IN c) = (a {op} (b IN c))) AS eq, collect((a {op} b IN c) <> ((a {op} b) IN c)) AS neq "
    "RETURN all(x IN eq WHERE x) AND any(x IN neq WHERE x) AS result"
)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("op", ["=", "<>", "OR", "XOR", "AND"])
def test_list_predicate_shape_answers_true(engs, route, op):
    """Precedence1 [26] / [28]: IN binds tighter than a comparison or a boolean operator, so each eq element is
    x = x (true, or null, which collect() skips), and some row tells the two groupings apart, so neq holds a true:
    for =, a = false, b = false, c = []: false = (false IN []) is true, (false = false) IN [] is false; for OR,
    a = true, b = null, c = []: true OR (null IN []) is true, (true OR null) IN [] is false. The answer is true."""
    assert ns463.typed(ns463.rows(engs[route], LIST_PRED.format(op=op))) == [[(True, "bool")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_list_against_a_number_does_not_order(engs, route):
    """Values of two kinds do not order: null (row 22's intent). Through CY_LIST_KINDS this failed with -14."""
    rows = ns463.rows(engs[route], "UNWIND [[1], [2, 3]] AS e RETURN e < 3 AS r, e AS e")
    assert ns463.typed(rows) == ns463.typed([[None, [1]], [None, [2, 3]]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_reserved_alias_over_mixed_kinds_reads_each_as_itself(engs, route):
    """Row 22 through CY_LIST_KINDS under the reserved alias `input`: its side columns are whole identifiers."""
    rows = ns463.rows(engs[route], "UNWIND ['1', true, [2]] AS input RETURN input")
    assert ns463.typed(rows) == ns463.typed([["1"], [True], [[2]]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_quantifier_shape_prepares_and_reads_each_map(engs, route):
    """The statement row 22 broke prepares and reads one row per map (the third's fixedList null, as fixed is false).
    The whole answer is the strict xfail below."""
    rows = ns463.rows(engs[route], QUANTIFIER)
    assert len(rows) == 3
    assert rows[2][0] is None


@pytest.mark.xfail(strict=True, reason=(
    "Older than 4.7.0 (4.6.5 and row 22's parent answer the same rows): a map property in a boolean context "
    "(CASE WHEN input.fixed) is translated as JSON_VALUE(..) = 'true', and Graph_KG.JSON_VALUE answers '1' for a JSON "
    "true, so fixedList is null and inputList input.list on every row; and that list comes back as its text "
    "('[1,2,3]'). Not rows 21/22's code; reported to the lead"))
@pytest.mark.parametrize("route", ns463.ROUTES)
def test_quantifier_shape_answers_the_lists(engs, route):
    rows = ns463.rows(engs[route], QUANTIFIER)
    assert ns463.typed(rows) == ns463.typed([[[2], [1]], [[6], [1]], [None, [1, 2, 3]]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_run_time_row_through_a_with_keeps_its_boolean(engs, route):
    """The flag of a run-time list's row is read from its JSON text after a WITH (Quantifier10 [4]'s fix): 1 and the
    string 'true' are not true; the boolean is."""
    q = "UNWIND [1, true, 'true'] AS e WITH collect(e) AS l UNWIND l AS x WITH x RETURN x = true AS r"
    assert ns463.typed(ns463.rows(engs[route], q)) == ns463.typed([[False], [True], [False]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_collected_boolean_expression_unwinds_as_booleans(engs, route):
    """collect(k > 1) over k = 1, 2, 3 is [false, true, true]; carried by a WITH, returned and unwound, it is that list
    (row 21's answer, which the SQL now lists as 1 / 0 for IRIS's Prepare)."""
    base = "UNWIND [1, 2, 3] AS k WITH k ORDER BY k WITH collect(k > 1) AS l "
    assert ns463.typed(ns463.rows(engs[route], base + "RETURN l")) == ns463.typed([[[False, True, True]]])
    got = ns463.rows(engs[route], base + "UNWIND l AS x RETURN x")
    assert sorted(ns463.typed(got)) == sorted(ns463.typed([[False], [True], [True]]))
