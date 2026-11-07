"""4.6.0 R308: tests/oracle/crosstype.py answers openCypher's cross-type rules (the answers worked by hand from
openCypher 9 and the TCK's Comparison1-4, Mathematical and List features, not from the oracle)."""

import math

import pytest

from tests.oracle import crosstype as ct


@pytest.mark.parametrize("a, op, b, expected", [
    (1, "<", "a", None), (1, "<=", "a", None), ("a", ">", 1, None), (1, ">=", "a", None),
    (1, "=", "a", False), (1, "<>", "a", True), (1, "=", 1.0, True), (1, "<", 1.5, True), (2.5, ">=", 2, True),
    ("a", "<", "b", True), ("b", "<=", "a", False),
    (False, "<", True, True), (True, "<=", False, False), (False, "=", 0, False), (True, "<", 1, None),
    ([1], "<", [1, 2], True), ([1, 2], ">", [1], True), ([2], "<", [1, 2], False), ([1], "=", [1, 2], False),
    ([1, None], "<", [1, 2], None), ([2, None], "<", [1, 2], False), ([1, None], "=", [1, 2], None),
    ([1, None], "=", [2, 2], False), ([1], "<", ["a"], None), ([1], "<", 1, None), ([1], "=", 1, False),
    ({"k": 1}, "<", {"k": 2}, None), ({"k": 1}, "=", {"k": 1}, True), ({"k": 1}, "<>", {"k": 2}, True),
    ({"k": 1}, "<", 1, None), ({"k": 1}, "=", [1], False),
    (None, "=", None, None), (None, "<", 1, None), (1, "<>", None, None), ("a", "=", None, None),
])
def test_comparison(a, op, b, expected):
    assert ct.compare(a, op, b) is expected


@pytest.mark.parametrize("fn", ct.MATH)
@pytest.mark.parametrize("v", ["a", True, False, [1], {"k": 1}])
def test_a_math_function_of_a_non_number_is_a_type_error(fn, v):
    with pytest.raises(ct.CypherTypeError):
        ct.math_fn(fn, v)


@pytest.mark.parametrize("fn", ct.MATH)
def test_a_math_function_of_null_is_null(fn):
    assert ct.math_fn(fn, None) is None


@pytest.mark.parametrize("fn, v, expected", [
    ("abs", -2, 2), ("abs", -0.5, 0.5), ("ceil", 0.5, 1.0), ("floor", 0.5, 0.0), ("round", 0.5, 1.0),
    ("round", 2.4, 2.0), ("sign", -0.5, -1), ("sign", 0, 0), ("sqrt", 4, 2.0), ("exp", 0, 1.0), ("log", 1, 0.0),
    ("log10", 100, 2.0), ("sin", 0, 0.0), ("cos", 0, 1.0), ("tan", 0, 0.0), ("asin", 1, math.pi / 2),
    ("acos", 1, 0.0), ("atan", 1, math.pi / 4), ("cot", math.pi / 4, 1.0), ("degrees", math.pi, 180.0),
    ("radians", 180, math.pi), ("haversin", math.pi, 1.0),
])
def test_a_math_function_of_a_number(fn, v, expected):
    assert ct.math_fn(fn, v) == pytest.approx(expected)


@pytest.mark.parametrize("v, expected", [("abc", 3), ("", 0), ([1, 2], 2), ([], 0), (None, None)])
def test_size(v, expected):
    assert ct.size_fn(v) == expected


@pytest.mark.parametrize("v", [1, 0.5, True, False, {"k": 1}])
def test_size_of_anything_else_is_a_type_error(v):
    with pytest.raises(ct.CypherTypeError):
        ct.size_fn(v)


def test_every_math_value_is_inside_every_functions_domain():
    for k in ("int", "float"):
        for fn in ct.MATH:
            assert math.isfinite(ct.math_fn(fn, ct.MATH_VALUES[k])), (fn, k)


def test_the_cases_cover_the_grid_once_each():
    cases = ct.all_cases()
    assert len({(c.family, c.form, c.text, repr(c.params)) for c in cases}) == len(cases)
    pairs = {(c.label, c.form) for c in cases if c.family == "comparison"}
    for ka in ct.KINDS:
        for kb in ct.KINDS:
            for form in ct.FORMS:
                skipped = (form == "collected" and "null" in (ka, kb)) or (form == "stored" and "map" in (ka, kb))
                assert ((f"{ka} vs {kb}", form) in pairs) is not skipped, (ka, kb, form)
    for k in ct.KINDS:
        for form in ct.FORMS:
            sizes = [c for c in cases if c.family == "size" and c.label == f"size of {k}" and c.form == form]
            skipped = (form == "collected" and k == "null") or (form == "stored" and k == "map")
            assert len(sizes) == (0 if skipped else 1), (k, form)


@pytest.mark.parametrize("text, expected", [
    ("RETURN 1 < 'b' AS c0, 1 <= 'b' AS c1, 1 > 'b' AS c2, 1 >= 'b' AS c3, 1 = 'b' AS c4, 1 <> 'b' AS c5",
     [(None, None, None, None, False, True)]),
    ("WITH [false, true] AS l RETURN l[0] < l[1] AS c0, l[0] <= l[1] AS c1, l[0] > l[1] AS c2, l[0] >= l[1] AS c3, "
     "l[0] = l[1] AS c4, l[0] <> l[1] AS c5", [(True, True, False, False, False, True)]),
    ("UNWIND [[1], [1, 2]] AS e WITH collect(e) AS l RETURN l[0] < l[1] AS c0, l[0] <= l[1] AS c1, "
     "l[0] > l[1] AS c2, l[0] >= l[1] AS c3, l[0] = l[1] AS c4, l[0] <> l[1] AS c5",
     [(True, True, False, False, False, True)]),
])
def test_a_case_holds_the_hand_answer(text, expected):
    by_text = {c.text: c for c in ct.all_cases()}
    assert by_text[text].expected == expected


def test_a_type_error_case_reads_one_function_of_one_value():
    c = {x.text: x for x in ct.all_cases()}["WITH 'a' AS x RETURN log(x) AS c"]
    assert c.expected == ct.TYPE_ERROR
    assert {x.text: x for x in ct.all_cases()}["RETURN size({k: 1}) AS c"].expected == ct.TYPE_ERROR
    assert {x.text: x for x in ct.all_cases()}["MATCH (x:ZZT308 {k: 'intM'}) RETURN size(x.v) AS c"].stored == [
        ("intM", 1)]


def test_the_stored_values_hold_every_kind_but_a_map():
    s = ct.stored_values()
    assert s["boolL"] is False and s["boolR"] is True and s["listR"] == [1, 2] and s["nullL"] is None
    assert s["floatM"] == 0.5 and not any(k.startswith("map") for k in s)
