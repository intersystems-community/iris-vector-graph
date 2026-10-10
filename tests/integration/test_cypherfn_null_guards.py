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
