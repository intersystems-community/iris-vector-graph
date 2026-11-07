"""4.6.0 R317: size() of a string built with `+` is the string's length.

Walkthrough round 4 (r4-python): `RETURN size('ab' + 'c')` failed with "SQL Function GRAPH_KG.JSON_ARRAYLENGTH failed
... SQLCODE=-400"; size() read every `+` as a list concatenation. A list on either side keeps the list's length.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q, params=None):
    return translate_to_sql(parse_query(q), params=params or {}).sql


@pytest.mark.parametrize(
    "q, params",
    [
        ("RETURN size('ab' + 'c') AS s", {}),
        ("RETURN size($s + 'c') AS s", {"s": "ab"}),
        ("WITH 'ab' AS s RETURN size(s + 'c') AS z", {}),
        ("MATCH (n) RETURN size(n.name + 'x') AS s", {}),
    ],
)
def test_size_of_a_string_concatenation_counts_characters(q, params):
    sql = _sql(q, params)
    assert "CY_STR_SIZE" in sql and "JSON_ARRAYLENGTH" not in sql, sql


@pytest.mark.parametrize(
    "q",
    [
        "RETURN size([1, 2] + [3]) AS s",
        "MATCH (n) RETURN size(n.tags + ['x']) AS s",
        "RETURN size('a' + [1]) AS s",
    ],
)
def test_size_of_a_list_concatenation_counts_elements(q):
    assert "JSON_ARRAYLENGTH" in _sql(q)
