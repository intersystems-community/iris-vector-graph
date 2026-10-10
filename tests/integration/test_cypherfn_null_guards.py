"""The empty-input guards of the SQL helper functions (User.CypherFn, User.SQL, User.funcJSONKEYS).

ObjectScript evaluates strictly left to right, so `json = "" || json = "null"` was
`((json = "") || json) = "null"`: it never matched an empty or "null" input, and each function went on
to parse nothing. Every guard is exercised with both inputs. Runs in USER, where the classes live.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def fn(iris_connection):
    obj = IRISGraphEngine(iris_connection, embedding_dimension=768)._store._iris_obj()

    def call(cls, method, *args):
        out = obj.classMethodValue(cls, method, *args)
        return "" if out is None else out

    return call


CF = "User.CypherFn"
EMPTY_INPUTS = ["", "null"]


@pytest.mark.parametrize("json", EMPTY_INPUTS)
@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("IVGJSONARRAYLENGTH", (), 0),
        ("IVGJSONVALUE", ("$.a",), ""),
        ("IVGJSONKEYS", (), "[]"),
        ("IVGLISTREVERSE", (), "[]"),
        ("IVGLISTHEAD", (), ""),
        ("IVGLISTLAST", (), ""),
        ("IVGLISTTAIL", (), "[]"),
        ("IVGLISTGET", ("0",), ""),
        ("IVGMAPGET", ("k",), ""),
        ("IVGJSONREMOVE", ("k",), "{}"),
    ],
)
def test_a_missing_json_input_gets_the_neutral_answer(fn, json, method, args, expected):
    assert fn(CF, method, json, *args) == expected


@pytest.mark.parametrize("json", EMPTY_INPUTS)
def test_json_set_on_nothing_builds_an_object(fn, json):
    import json as _json

    assert _json.loads(fn(CF, "IVGJSONSET", json, "k", "v")) == {"k": "v"}


@pytest.mark.parametrize("s,p", [("", "a"), ("a", ""), ("", "")])
def test_regex_match_with_an_empty_side_is_no_match(fn, s, p):
    assert int(fn(CF, "IVGREGEXMATCH", s, p)) == 0
    assert int(fn("User.SQL", "REGEXMATCH", s, p)) == 0


def test_regex_match_still_matches(fn):
    assert int(fn(CF, "IVGREGEXMATCH", "abc", "a.c")) == 1
    assert int(fn("User.SQL", "REGEXMATCH", "abc", "x")) == 0


@pytest.mark.parametrize("json", EMPTY_INPUTS)
def test_the_standalone_json_keys_function_has_the_same_guard(fn, json):
    assert fn("User.funcJSONKEYS", "JSONKEYS", json) == "[]"


@pytest.mark.parametrize("idx", ["3", "7", "-4", "-100"])
def test_list_get_out_of_range_is_empty_both_ways(fn, idx):
    assert fn(CF, "IVGLISTGET", "[10,20,30]", idx) == ""


@pytest.mark.parametrize("idx,expected", [("0", "10"), ("2", "30"), ("-1", "30"), ("-3", "10")])
def test_list_get_in_range_including_negative_wraparound(fn, idx, expected):
    assert str(fn(CF, "IVGLISTGET", "[10,20,30]", idx)) == expected


@pytest.mark.parametrize("method", ["IVGLISTHEAD", "IVGLISTLAST"])
def test_head_and_last_of_an_empty_array_are_empty(fn, method):
    assert fn(CF, method, "[]") == ""


def test_tail_of_an_empty_array_is_an_empty_array(fn):
    assert fn(CF, "IVGLISTTAIL", "[]") == "[]"
