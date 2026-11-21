"""Translation of relationship values and list slice parameters."""

import json

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _translate(cypher, params):
    q = translate_to_sql(parse_query(cypher), params)
    return q.sql, q.parameters[0]


@pytest.mark.parametrize(("query", "params", "expected"), [
    ("RETURN $l[$s..$e] AS r", {"s": 1, "e": 3}, "[2,3]"),
    ("RETURN $l[$s..] AS r", {"s": -2}, "[3,4]"),
    ("RETURN $l[..$e] AS r", {"e": 2}, "[1,2]"),
    ("RETURN $l[$s..2] AS r", {"s": 1}, "[2]"),
])
def test_a_known_slice_binds_the_result(query, params, expected):
    sql, bound = _translate(query, {"l": [1, 2, 3, 4], **params})
    assert "JSON_TABLE" not in sql
    assert bound == [expected]


def test_a_null_bound_does_not_bind_the_list():
    sql, bound = _translate("RETURN $l[$s..2] AS r", {"l": [1, 2, 3], "s": None})
    assert "NULL" in sql
    assert bound == []


def test_runtime_slice_binds_each_input_once():
    sql, bound = _translate(
        "WITH $l AS l RETURN l[$s..$e] AS r", {"l": [1, 2, 3, 4], "s": 1, "e": 3}
    )
    assert sql.count("?") == len(bound)
    assert bound == ["[1,2,3,4]", 1, 3]


def _edge_insert(cypher, params):
    q = translate_to_sql(parse_query(cypher), params)
    sql = q.sql if isinstance(q.sql, list) else [q.sql]
    found = [p for s, p in zip(sql, q.parameters) if s.startswith("INSERT INTO") and "rdf_edges" in s]
    assert len(found) == 1, sql
    return found[0]


def _qualifiers(cypher, params):
    row = _edge_insert(cypher, params)
    return json.loads(row[3]) if len(row) == 5 else {}


NEW_ENDS = "CREATE (:N {id: 'a'})-[:T %s]->(:N {id: 'b'})"


def test_a_parameter_value_is_stored():
    assert _qualifiers(NEW_ENDS % "{since: $since}", {"since": 2024}) == {"since": "2024"}


def test_a_list_parameter_is_stored_as_a_list():
    assert _qualifiers(NEW_ENDS % "{tags: $tags}", {"tags": ["p", "q"]}) == {"tags": ["p", "q"]}


def test_a_computed_value_is_worked_out_and_stored():
    assert _qualifiers(NEW_ENDS % "{c: 1 + 2, u: toUpper('ab'), lit: 'x'}", {}) == {"c": "3", "u": "AB", "lit": "x"}


def test_null_is_still_not_stored():
    assert _qualifiers(NEW_ENDS % "{gone: null, kept: 1}", {}) == {"kept": "1"}
    assert _qualifiers(NEW_ENDS % "{gone: $nothing}", {"nothing": None}) == {}


def test_a_value_that_cannot_be_worked_out_is_refused_naming_it():
    from iris_vector_graph.errors import GraphWriteError

    with pytest.raises(GraphWriteError, match="`r`"):
        translate_to_sql(parse_query(NEW_ENDS % "{r: rand()}"), {})
