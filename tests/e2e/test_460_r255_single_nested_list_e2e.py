"""4.6.0 R255, live: a list whose one element is a list is one row, not the inner list's elements (fixes29).

Before: ``UNWIND [[1,2,3]] AS list WITH [x IN list WHERE x = 2 | x] AS l, count(*) AS c RETURN l, c`` answered two
rows, (``["2"]``, 1) and (``[]``, 2). IRIS's JSON_TABLE iterates the inner list of a one-element list of a list, so
``UNWIND [[1,2,3]] AS list`` gave three rows, list = 1, 2 and 3; the comprehension kept [2] for one and [] for two.

By hand: UNWIND [[1,2,3]] is one row, list = [1, 2, 3]; [x IN list WHERE x = 2 | x] is [2]; count(*) is 1. No graph
is read, so the cases need no data; each runs with the native planner on and off.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]


@pytest.fixture(params=[True, False], ids=["native-on", "native-off"])
def eng(iris_connection, request):
    e = IRISGraphEngine(iris_connection)
    e.native_cypher = request.param
    return e


def _value(cell):
    return json.loads(cell) if isinstance(cell, str) and cell[:1] in "[{" else cell


def _rows(engine, cypher):
    r = engine.execute_cypher(cypher)
    assert not getattr(r, "error", None), r.error
    return [[_value(c) for c in row] for row in r.rows]


@pytest.mark.parametrize(
    "cypher, expected",
    [
        ("UNWIND [[1, 2, 3]] AS list WITH [x IN list WHERE x = 2 | x] AS l, count(*) AS c RETURN l, c", [[[2], 1]]),
        ("UNWIND [[1, 2, 3]] AS list WITH [x IN list WHERE x = 2 | x] AS l RETURN l", [[[2]]]),
        ("UNWIND [[1, 2, 3]] AS list RETURN list", [[[1, 2, 3]]]),
        ("UNWIND [[1, 2, 3]] AS list RETURN size(list) AS s, count(*) AS c", [[3, 1]]),
        ("UNWIND [[]] AS x RETURN x", [[[]]]),
        ("UNWIND [[[1], [2]]] AS x RETURN size(x) AS s", [[2]]),
        ("WITH [[1, 2, 3]] AS ll UNWIND ll AS list RETURN size(list) AS s", [[3]]),
        ("RETURN [x IN [[1, 2]] | size(x)] AS v", [[[2]]]),
        ("UNWIND [[{name: 'a'}]] AS l RETURN size(l) AS s, any(x IN l WHERE x.name = 'a') AS r", [[1, True]]),
        ("WITH [[1, 2]] AS l RETURN [x IN l | size(x)] AS v", [[[2]]]),
        # two elements were iterated right already
        ("UNWIND [[1, 2, 3], [2]] AS list WITH [x IN list WHERE x = 2 | x] AS l, count(*) AS c RETURN l, c",
         [[[2], 2]]),
        ("UNWIND [[1, 2], [3]] AS l RETURN size(l) AS s", [[2], [1]]),
    ],
)
def test_a_one_element_list_of_a_list(eng, cypher, expected):
    assert _rows(eng, cypher) == expected
