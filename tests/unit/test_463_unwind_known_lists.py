"""4.6.3: an UNWIND of a list known before the query runs keeps its '' elements as ''.

Before 4.6.3 three such lists went through IRIS's JSON_TABLE, which reads a JSON "" as null:

- a list concatenation (`UNWIND ['', 'a'] + [''] AS x`, `UNWIND l1 + l2` over WITH-bound literals): the translator built
  the concatenated JSON text and handed it to JSON_TABLE, so the two '' rows answered null; `UNWIND $l1 + $l2`
  translated to `JSON_TABLE((? + ?), ...)`, numeric addition of two JSON texts, and answered one row, '0';
- a parameter list longer than 900 elements (UNION ALL rows stop there: IRIS refuses 1000 literals in a statement);
- a parameter list holding a non-scalar (`['', [1]]`).

openCypher: UNWIND of a list answers one row per element, each element as it is ('' is a string, not null). The
translator now works out a concatenation of known lists itself, and a known list that still needs JSON_TABLE carries
each '' as a marker its derived table turns back into ''. Live answers: tests/e2e/test_463_unwind_known_lists_e2e.py.
"""

from __future__ import annotations

import json

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sq(cypher, params=None):
    return translate_to_sql(parse_query(cypher), params or {})


def _binds(sq):
    return list(sq.parameters[0]) if sq.parameters else []


def test_a_parameter_concatenation_is_not_added_as_numbers():
    sq = _sq("UNWIND $l1 + $l2 AS x RETURN x", {"l1": ["", "a"], "l2": [""]})
    assert "? + ?" not in sq.sql, sq.sql
    assert _binds(sq) == ["", "a", ""], (sq.sql, _binds(sq))


def test_a_literal_concatenation_holding_an_empty_string_reads_its_values():
    sq = _sq("UNWIND ['', 'a'] + [''] AS x RETURN x")
    assert "JSON_TABLE" not in sq.sql, sq.sql
    assert _binds(sq) == ["", "a", ""], (sq.sql, _binds(sq))


def test_a_concatenation_of_with_bound_literals_reads_its_values():
    sq = _sq("WITH ['', 'a'] AS l1, [''] AS l2 UNWIND l1 + l2 AS x RETURN x")
    assert "JSON_TABLE" not in sq.sql, sq.sql
    assert _binds(sq)[-3:] == ["", "a", ""], (sq.sql, _binds(sq))


def test_a_list_and_a_scalar_concatenate():
    sq = _sq("UNWIND $l + 'b' AS x RETURN x", {"l": ["", "a"]})
    assert _binds(sq) == ["", "a", "b"], (sq.sql, _binds(sq))


def test_a_parameter_concatenation_of_numbers_is_one_json_list():
    sq = _sq("UNWIND $l1 + $l2 AS x RETURN x", {"l1": [1, 2], "l2": [3.5]})
    assert "? + ?" not in sq.sql, sq.sql
    assert [json.loads(b) for b in _binds(sq)] == [[1, 2, 3.5]], (sq.sql, _binds(sq))


def _marked(sq):
    (bind,) = _binds(sq)
    return json.loads(bind)


def test_a_long_parameter_list_marks_its_empty_strings():
    big = [""] + [str(i) for i in range(1000)]
    sq = _sq("UNWIND $l AS x RETURN x", {"l": big})
    assert "JSON_TABLE" in sq.sql, sq.sql
    sent = _marked(sq)
    assert sent[0] == "\x01E" and sent[1:] == big[1:], sent[:3]
    assert "CHAR(1) || 'E'" in sq.sql, sq.sql


def test_a_parameter_list_holding_a_list_is_read_with_its_kinds():
    """4.7.0 row 22: a list beside a scalar is read as `WITH $l AS __l0 UNWIND __l0` (CY_LIST_KINDS), which reads ''
    as '' itself, so it is sent unmarked. Live: ['', [1, '']] answers '', [1, ''] on both routes."""
    sq = _sq("UNWIND $l AS x RETURN x", {"l": ["", [1, ""]]})
    assert _marked(sq) == ["", [1, ""]] and "CY_LIST_KINDS" in sq.sql and "CHAR(1) || 'E'" not in sq.sql, sq.sql


def test_a_parameter_list_without_an_empty_string_is_sent_as_it_is():
    sq = _sq("UNWIND $l AS x RETURN x", {"l": ["a", [1]]})
    assert _marked(sq) == ["a", [1]] and "CHAR(1) || 'E'" not in sq.sql, sq.sql


def test_the_parameters_the_caller_passed_are_not_changed():
    params = {"l1": ["", "a"], "l2": [""], "l": ["", [1]]}
    before = json.dumps(params)
    _sq("UNWIND $l1 + $l2 AS x RETURN x", params)
    _sq("UNWIND $l AS x RETURN x", params)
    assert json.dumps(params) == before
