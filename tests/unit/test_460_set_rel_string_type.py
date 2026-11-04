"""4.6.0: `SET r.code = '007'` stores the string '007', not the number 7.

A relationship property SET reaches `CypherFn_IVGJSONSET(qualifiers, key, value)` with the value as text, and
IVGJSONSET stored any `$IsValidNum` text as a JSON number: `SET r.code = '007'` stored 7, and `RETURN r.code` and
`properties(r)` answered 7. The translator now passes the value's JSON type when it is known before the query runs
(a literal or `$param`): "string" for a string, "number" for a number, "" for anything else (an expression), which
IVGJSONSET decides from the text as before. A string that is a canonical number ('0.95') is stored as the JSON string
and still reads back as the number, as every stored JSON string "0.95" does (docs/KNOWN_ISSUES.md).

The live answers are in `tests/e2e/test_460_set_rel_string_type_e2e.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

ROOT = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"


def _set(value_sql, params=None):
    tr = translate_to_sql(parse_query(f"MATCH (:L {{id: 'a'}})-[r:R]->() SET r.code = {value_sql}"), dict(params or {}))
    sql = tr.sql if isinstance(tr.sql, str) else "\n".join(tr.sql)
    return sql, [p for g in tr.parameters for p in g]


@pytest.mark.parametrize(
    "value_sql, params, text, typ",
    [("'007'", {}, "007", "string"), ("$v", {"v": "007"}, "007", "string"), ("'0.95'", {}, "0.95", "string"),
     ("7", {}, "7", "number"), ("0.95", {}, "0.95", "number"), ("$v", {"v": 7}, "7", "number")],
)
def test_a_static_value_names_its_type(value_sql, params, text, typ):
    sql, bound = _set(value_sql, params)
    assert re.search(r"CypherFn_IVGJSONSET\(qualifiers, \?, \?, \?\)", sql), sql
    assert bound[:3] == ["code", text, typ], bound


def test_an_expression_leaves_the_type_to_the_text():
    sql, bound = _set("r.code + 1")
    assert re.search(r"CypherFn_IVGJSONSET\(COALESCE\(qualifiers, [^)]*\)\)\), \?, CAST", sql), sql


def test_ivgjsonset_takes_the_type():
    fn = (ROOT / "CypherFn.cls").read_text().split("ClassMethod IVGJSONSET(", 1)[1].split("\n}\n", 1)[0]
    assert fn.startswith('json As %String = "", key As %String = "", val As %String = "", typ As %String = "")'), fn
    assert 'If typ = "string" {' in fn and '%Set(key, val, "string")' in fn, fn
    assert '(typ = "number") || ((typ = "") && $IsValidNum(val))' in fn, fn
    fwd = (ROOT / "SqlProcs.cls").read_text().split("ClassMethod CypherFnIVGJSONSET(", 1)[1].split("\n}\n", 1)[0]
    assert "IVGJSONSET(json, key, val, typ)" in fwd, fwd
