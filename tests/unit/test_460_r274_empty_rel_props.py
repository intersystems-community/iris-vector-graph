"""4.6.0 R274 (the empty-string relationship property), pinned without a container: the engine reads "\\u0000" inside
a list or map as the empty string, and the relationship readers take a JSON "" for SQL's '' (not NULL).

The answers are proven live in tests/e2e/test_460_r274_empty_rel_props_e2e.py.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from iris_vector_graph._engine.query import _decode_empty_json_strings
from iris_vector_graph.schema import GraphSchema


def _decoded(*values):
    r = SimpleNamespace(rows=[list(values)])
    _decode_empty_json_strings(r)
    return r.rows[0]


@pytest.mark.parametrize(
    "text,want",
    [
        ('["\\u0000"]', [""]),
        ('["a","\\u0000"]', ["a", ""]),
        ('{"s":"","t":"x","u":"\\u0000"}', {"s": "", "t": "x", "u": ""}),
        ('{"type":"R","props":{"u":"\\u0000"}}', {"type": "R", "props": {"u": ""}}),
        ('[ "\\u0000" , "b"]', ["", "b"]),
    ],
)
def test_a_lone_nul_string_in_a_list_or_map_is_the_empty_string(text, want):
    assert json.loads(_decoded(text)[0]) == want


@pytest.mark.parametrize(
    "value",
    [
        '["a\\u0000b"]',  # a NUL inside a longer string
        '["say \\"\\u0000\\""]',  # inside an escaped quote: part of a string
        '["\\\\u0000"]',  # the six characters \u0000
        "\\u0000",  # not a list or map
        "",
        None,
        5,
    ],
)
def test_anything_else_is_left(value):
    assert _decoded(value)[0] == value


def test_a_row_with_nothing_to_decode_is_not_rebuilt():
    r = SimpleNamespace(rows=[("a", 1)])
    _decode_empty_json_strings(r)
    assert r.rows == [("a", 1)]


def _udf(name: str) -> str:
    stmt = next(s for s in GraphSchema.get_procedures_sql_list("Graph_KG") if re.search(rf"FUNCTION \w+\.{name}\(", s))
    return stmt.split("LANGUAGE OBJECTSCRIPT", 1)[1]


def test_the_readers_take_a_json_empty_string_for_sql_s_empty_string():
    assert 'If (val = "") && (obj.%GetTypeOf(key) = "string") { Quit $Char(0) }' in _udf("JSON_VALUE")
    assert 'If (t = "string") && (val = "") { Quit $Char(0) }' in _udf("CY_REL_PROP")
    assert 'If obj.%Get(key) = "" { Quit $Char(0) }' in _udf("CY_REL_TOSTRING")
    assert 'If (t = "string") && (v = $Char(0)) { Set v = "" }' in _udf("CY_RETYPE_MAP")
    for name in ("JSON_VALUE", "CY_REL_PROP", "CY_REL_TOSTRING", "CY_RETYPE_MAP"):
        body = _udf(name)
        assert "'" not in body and not re.search(r":\w", body), name
